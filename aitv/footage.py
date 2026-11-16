"""The footage index (pipeline v2, part G): real footage found by what's said or shown in it.

The promos worked because their footage was found by its words ("more power", "it's a custom of my
people") and cut on them. This indexes video files the same way, in the background:

- scenes: the file's own cuts (scene detection), each with a keyframe thumbnail;
- what's said: a Whisper transcript with word times (the shared `transcripts` cache), searchable
  through the `footage_text` full-text index;
- a title and tags (the show or file name, the library asset's tags), for "what's shown" searches.

Sources: ai_tv's library footage (stock, Internet Archive downloads), plus opt-in folders from the
media library ([footage] roots, read only). The worker indexes one file per job at low priority and
waits while someone is watching ChupTV; a sweep queues what's new.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import media, transcribe
from .config import Config
from .db import now

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".wmv", ".ts"}


def settings(cfg: Config) -> dict:
    return cfg.section("footage")


def enabled(cfg: Config) -> bool:
    return bool(settings(cfg).get("enabled", False))


# ---------------------------------------------------------------- indexing

def register(conn: sqlite3.Connection, path: Path, asset_id: int | None = None, title: str = "", tags: str = "") -> int:
    """Add a file to the index as pending (or return the row it already has)."""
    row = conn.execute("SELECT id FROM footage WHERE path = ?", (str(path),)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO footage (path, asset_id, fingerprint, title, tags) VALUES (?, ?, ?, ?, ?)",
                        (str(path), asset_id, transcribe.fingerprint(path), title or _title(path), tags)).lastrowid


def _title(path: Path) -> str:
    """A searchable name: the show folder and the file name, cleaned of release noise. A "Season 2" or
    "Disc 1" folder isn't the show: the folder above it is."""
    name = re.sub(r"[._]+", " ", path.stem)
    name = re.sub(r"\[[^\]]*\]|\([^)]*(rip|720p|1080p|x26[45])[^)]*\)", " ", name, flags=re.I)
    folders = [path.parent.name]
    if re.match(r"(season|series|disc|dvd|s\d)", path.parent.name, re.I):
        folders.insert(0, path.parent.parent.name)
    return re.sub(r"\s+", " ", " - ".join(folders + [name])).strip()


def index(conn: sqlite3.Connection, cfg: Config, footage_id: int, log=lambda line: None) -> dict:
    """Scenes, thumbnails and transcript for one registered file."""
    row = conn.execute("SELECT * FROM footage WHERE id = ?", (footage_id,)).fetchone()
    path = Path(row["path"])
    try:
        info = media.probe(path)
        if info["media"] != "video":
            raise ValueError("not a video")
        duration = info["duration_s"] or 0.0
        from .qc import scene_changes

        cuts = scene_changes(path, float(settings(cfg).get("scene_threshold", 0.3)))
        bounds = [0.0, *[c for c in cuts if 0.2 < c < duration - 0.2], duration]
        thumbs = cfg.library / "derived" / "footage" / row["fingerprint"][:16]
        thumbs.mkdir(parents=True, exist_ok=True)
        conn.execute("DELETE FROM footage_scenes WHERE footage_id = ?", (footage_id,))
        for a, b in zip(bounds, bounds[1:]):
            if b - a < 0.1:
                continue
            thumb = thumbs / f"{a:09.2f}.jpg"
            if not thumb.is_file():
                media.thumbnail(path, thumb, 240, seek=(a + b) / 2)
            conn.execute("INSERT OR REPLACE INTO footage_scenes (footage_id, start_s, end_s, thumb) VALUES (?, ?, ?, ?)",
                         (footage_id, round(a, 3), round(b, 3), str(thumb.relative_to(cfg.library))))
        said = transcribe.transcribe(conn, cfg, path, log) if info.get("has_audio") else {"segments": [], "words": []}
        conn.execute("DELETE FROM footage_text WHERE footage_id = ?", (footage_id,))
        for a, b, text in said["segments"]:
            conn.execute("INSERT INTO footage_text (text, footage_id, start_s, end_s) VALUES (?, ?, ?, ?)",
                         (text, footage_id, a, b))
        conn.execute("UPDATE footage SET status = 'indexed', error = NULL, duration_s = ?, indexed_at = ? WHERE id = ?",
                     (duration, now(), footage_id))
        log(f"indexed {path.name}: {len(bounds) - 1} scene(s), {len(said['segments'])} line(s) of speech")
        return {"scenes": len(bounds) - 1, "segments": len(said["segments"])}
    except Exception as exc:
        conn.execute("UPDATE footage SET status = 'failed', error = ? WHERE id = ?", (f"{type(exc).__name__}: {exc}", footage_id))
        raise


def sweep(conn: sqlite3.Connection, cfg: Config, limit: int = 50) -> list[int]:
    """Register what's new (library footage, files under [footage] roots) and return pending ids to index."""
    for a in conn.execute("SELECT a.id, a.path, a.title, a.tags FROM assets a WHERE a.media = 'video' "
                          "AND a.status = 'ready' AND a.category = 'footage' "
                          "AND NOT EXISTS (SELECT 1 FROM footage f WHERE f.asset_id = a.id)").fetchall():
        path = cfg.library / a["path"]
        if path.is_file():
            register(conn, path, a["id"], a["title"] or "", " ".join(_tags(a["tags"])))
    for root in settings(cfg).get("roots", []):
        base = Path(root).expanduser()
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            if path.suffix.lower() in VIDEO_EXT and path.is_file():
                if not conn.execute("SELECT 1 FROM footage WHERE path = ?", (str(path),)).fetchone():
                    register(conn, path)
    return [r["id"] for r in conn.execute("SELECT id FROM footage WHERE status = 'pending' ORDER BY id LIMIT ?", (limit,))]


def _tags(raw) -> list[str]:
    import json

    try:
        return [str(t) for t in json.loads(raw or "[]")]
    except (ValueError, TypeError):
        return str(raw or "").split()


# ---------------------------------------------------------------- search

def _fts_query(text: str, phrase: bool) -> str:
    words = transcribe.norm_words(text)
    if not words:
        return ""
    quoted = [f'"{w}"' for w in words]
    return f'"{" ".join(words)}"' if phrase else " AND ".join(quoted)


def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    """Footage for a shot's query, best first. A quoted query ("more power") or any words that are said
    in a file find the spoken line, word-timed; otherwise the title and tags find a file and its scenes.
    Each hit: {footage_id, path, title, kind: said|shown, start, end, text, duration}."""
    said = re.findall(r'"([^"]+)"', query)
    text = " ".join(said) or query
    hits, seen = [], set()
    for phrase in (True, False):
        q = _fts_query(text, phrase)
        if not q:
            break
        for r in conn.execute(
                "SELECT t.footage_id, t.start_s, t.end_s, t.text, f.path, f.title, f.duration_s, f.fingerprint "
                "FROM footage_text t JOIN footage f ON f.id = t.footage_id WHERE footage_text MATCH ? "
                "AND f.status = 'indexed' ORDER BY rank LIMIT ?", (q, limit * 2)).fetchall():
            key = (r["footage_id"], round(float(r["start_s"]), 1))
            if key in seen:
                continue
            seen.add(key)
            start, end = float(r["start_s"]), float(r["end_s"])
            words = _words(conn, r["fingerprint"], start - 1.0, end + 1.0)
            found = transcribe.find_phrase(words, text, 0.6)
            if found:  # word-timed: the shot starts on the first word, not the segment
                start, end = found[0][0], found[0][1]
            hits.append({"footage_id": r["footage_id"], "path": r["path"], "title": r["title"], "kind": "said",
                         "start": round(start, 2), "end": round(end, 2), "text": r["text"], "duration": r["duration_s"],
                         "exact": bool(found)})
        if len(hits) >= limit or said:
            break
    if len(hits) < limit and not said:
        like = [f"%{w}%" for w in transcribe.norm_words(query)[:6]]
        if like:
            cond = " OR ".join(["f.title LIKE ? OR f.tags LIKE ?"] * len(like))
            args = [x for w in like for x in (w, w)]
            for r in conn.execute(f"SELECT f.id, f.path, f.title, f.duration_s FROM footage f WHERE f.status = 'indexed' "
                                  f"AND ({cond}) LIMIT ?", (*args, limit)).fetchall():
                scene = conn.execute("SELECT start_s, end_s FROM footage_scenes WHERE footage_id = ? AND end_s - start_s >= 2 "
                                     "ORDER BY start_s LIMIT 1 OFFSET 1", (r["id"],)).fetchone()
                start, end = (scene["start_s"], scene["end_s"]) if scene else (0.0, min(5.0, r["duration_s"] or 5.0))
                hits.append({"footage_id": r["id"], "path": r["path"], "title": r["title"], "kind": "shown",
                             "start": start, "end": end, "text": "", "duration": r["duration_s"], "exact": False})
    return hits[:limit]


def _words(conn: sqlite3.Connection, fingerprint: str, start: float, end: float) -> list:
    row = conn.execute("SELECT words FROM transcripts WHERE fingerprint = ?", (fingerprint,)).fetchone()
    if not row:
        return []
    import json

    return [w for w in json.loads(row["words"]) if w[1] > start and w[0] < end]


def scenes(conn: sqlite3.Connection, footage_id: int, start: float, end: float) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT start_s, end_s, thumb FROM footage_scenes WHERE footage_id = ? AND end_s > ? AND start_s < ? ORDER BY start_s",
        (footage_id, start, end))]
