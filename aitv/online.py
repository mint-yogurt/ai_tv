"""Free online sources: Internet Archive, Wikimedia Commons and Openverse. No keys, no accounts.

search() returns plain result dicts the web page can show and hand back to fetch(), which
downloads the smallest usable copy, shrinks video to the station's 480 lines, and files it in
the library like an upload. Every online asset remembers where it came from (meta.online), so
cull() can delete unused downloads to stay under the storage cap and they can be fetched again.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import db, ingest, media
from .config import Config

SOURCES = {
    "archive": {"name": "Internet Archive", "kinds": ["video", "audio", "image"]},
    "commons": {"name": "Wikimedia Commons", "kinds": ["image", "audio"]},
    "openverse": {"name": "Openverse", "kinds": ["audio", "image"]},
}
ONLINE_SOURCES = tuple(SOURCES)
DEFAULT_CATEGORY = {"video": "footage", "audio": "sfx", "image": "image"}


class OnlineError(RuntimeError):
    pass


def _settings(cfg: Config) -> dict:
    return cfg.section("online")


def _get(cfg: Config, url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _settings(cfg).get("user_agent", "aitv-hobby/0.1")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode(errors="replace")
        raise OnlineError(f"{urllib.parse.urlsplit(url).netloc}: HTTP {exc.code} {detail}") from exc
    except OSError as exc:
        raise OnlineError(f"{urllib.parse.urlsplit(url).netloc}: {exc}") from exc


def _json(cfg: Config, url: str, **params) -> dict:
    return json.loads(_get(cfg, url + "?" + urllib.parse.urlencode(params, doseq=True)))


# ---------------------------------------------------------------- search

def search(cfg: Config, source: str, kind: str, query: str, limit: int = 24) -> list[dict]:
    if kind not in SOURCES.get(source, {}).get("kinds", []):
        raise OnlineError(f"{SOURCES.get(source, {}).get('name', source)} doesn't do {kind}")
    return {"archive": _search_archive, "commons": _search_commons, "openverse": _search_openverse}[source](
        cfg, kind, query, limit)


def _result(source, kind, rid, title, thumb=None, preview=None, url=None, landing=None, seconds=None) -> dict:
    return {"source": source, "kind": kind, "id": str(rid), "title": (title or str(rid))[:200], "thumb": thumb,
            "preview": preview, "url": url, "landing": landing, "seconds": seconds}


def _search_archive(cfg, kind, query, limit):
    mediatype = {"video": "movies", "audio": "audio", "image": "image"}[kind]
    data = _json(cfg, "https://archive.org/advancedsearch.php", q=f"({query}) AND mediatype:{mediatype}",
                 **{"fl[]": ["identifier", "title"]}, rows=limit, output="json")
    return [_result("archive", kind, d["identifier"], d.get("title"),
                    thumb=f"https://archive.org/services/img/{d['identifier']}",
                    landing=f"https://archive.org/details/{d['identifier']}")
            for d in data.get("response", {}).get("docs", [])]


def _search_commons(cfg, kind, query, limit):
    types = {"image": ("BITMAP", "DRAWING"), "audio": ("AUDIO",)}[kind]
    data = _json(cfg, "https://commons.wikimedia.org/w/api.php", action="query", generator="search",
                 gsrnamespace=6, gsrsearch=query, gsrlimit=min(limit * 2, 50), prop="imageinfo",
                 iiprop="url|size|mime|mediatype", iiurlwidth=640, format="json")
    out = []
    for page in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mediatype") not in types:
            continue
        title = page["title"].removeprefix("File:")
        # Images download as Commons' own 640-wide thumbnail, never the (often huge) original.
        url = info.get("thumburl") if kind == "image" else info.get("url")
        out.append(_result("commons", kind, page["pageid"], title, thumb=info.get("thumburl") if kind == "image" else None,
                           preview=url, url=url, landing=info.get("descriptionurl")))
    return out[:limit]


def _search_openverse(cfg, kind, query, limit):
    endpoint = {"audio": "audio", "image": "images"}[kind]
    # Anonymous requests may ask for at most 20 results a page.
    data = _json(cfg, f"https://api.openverse.org/v1/{endpoint}/", q=query, page_size=min(limit, 20))
    out = []
    for r in data.get("results", []):
        url = r.get("thumbnail") if kind == "image" and r.get("thumbnail") else r.get("url")
        seconds = round(r["duration"] / 1000, 1) if r.get("duration") else None
        out.append(_result("openverse", kind, r["id"], r.get("title"), thumb=r.get("thumbnail"), preview=r.get("url"),
                           url=url, landing=r.get("foreign_landing_url"), seconds=seconds))
    return out


# ---------------------------------------------------------------- fetch

# Internet Archive derivative formats, best first. Small h.264 copies exist for nearly every video.
ARCHIVE_PREFS = {
    "video": ["h.264", "512Kb MPEG4", "MPEG4", "h.264 IA", "Ogg Video"],
    "audio": ["VBR MP3", "MP3", "64Kbps MP3", "128Kbps MP3", "Ogg Vorbis"],
    "image": ["JPEG", "PNG"],
}


def _archive_file(cfg: Config, identifier: str, kind: str) -> tuple[str, float | None]:
    meta = _json(cfg, f"https://archive.org/metadata/{identifier}")
    files = meta.get("files", [])
    if not files:
        raise OnlineError(f"archive item {identifier} has no files (removed or private)")

    def length(f):
        try:
            if ":" in str(f.get("length", "")):
                m, s = str(f["length"]).split(":")[-2:]
                return int(m) * 60 + float(s)
            return float(f.get("length"))
        except (TypeError, ValueError):
            return None

    for fmt in ARCHIVE_PREFS[kind]:
        found = [f for f in files if f.get("format") == fmt and not f["name"].startswith("__")]
        if kind == "video":  # at least 240 lines; the smallest of those
            found = [f for f in found if int(f.get("height") or 480) >= 240]
        if found:
            best = min(found, key=lambda f: int(f.get("size") or 0))
            return f"https://archive.org/download/{identifier}/{urllib.parse.quote(best['name'])}", length(best)
    raise OnlineError(f"archive item {identifier} has no {kind} file this can use")


def _download(cfg: Config, url: str, dst: Path) -> Path:
    cap = int(_settings(cfg).get("max_download_mb", 150)) * 1024 * 1024
    req = urllib.request.Request(url, headers={"User-Agent": _settings(cfg).get("user_agent", "aitv-hobby/0.1")})
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dst, "wb") as out:
            while chunk := resp.read(1 << 16):
                got += len(chunk)
                if got > cap:
                    raise OnlineError(f"download is over the {cap >> 20} MB cap ([online] max_download_mb)")
                out.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise
    return dst


def _suffix(url: str, kind: str) -> str:
    ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    return ext if re.fullmatch(r"\.[a-z0-9]{2,5}", ext or "") else {"video": ".mp4", "audio": ".mp3", "image": ".jpg"}[kind]


def fetch(conn: sqlite3.Connection, cfg: Config, result: dict, category: str | None = None,
          tags: list[str] | None = None, entity_id: int | None = None, log=lambda line: None) -> int:
    """Download one search result into the library. Returns the new asset id (ingest finishes it)."""
    kind, settings = result["kind"], _settings(cfg)
    url, seconds = result.get("url"), result.get("seconds")
    if result["source"] == "archive":
        url, seconds = _archive_file(cfg, result["id"], kind)
    if not url:
        raise OnlineError("this result has no downloadable file")
    max_minutes = float(settings.get("max_video_minutes", 20))
    if kind == "video" and seconds and seconds > max_minutes * 60:
        raise OnlineError(f"video runs {seconds / 60:.0f} min, over [online] max_video_minutes = {max_minutes:g}")

    work = cfg.uploads / f"online_{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    raw = _download(cfg, url, work / ("download" + _suffix(url, kind)))
    log(f"downloaded {raw.stat().st_size / 1e6:.1f} MB from {urllib.parse.urlsplit(url).netloc} "
        f"in {time.monotonic() - started:.1f}s")
    if kind == "video":
        # Keep only a 480-line copy: what the station broadcasts at, a few MB a minute.
        small = media.shrink_video(raw, work / "video.mp4", int(settings.get("video_height", 480)))
        log(f"shrunk to {small.stat().st_size / 1e6:.1f} MB")
        raw.unlink()
        raw = small
    name = re.sub(r"[^\w.-]+", "_", result["title"])[:60] + raw.suffix
    final = raw.rename(work / name)
    online = {"source": result["source"], "id": result["id"], "url": url, "landing": result.get("landing"),
              "title": result["title"], "kind": kind, "fetched_at": db.now()}
    return ingest.register(conn, final, category or DEFAULT_CATEGORY[kind], title=result["title"], original_name=name,
                           source=result["source"], entity_id=entity_id, tags=tags or [],
                           extra_meta={"online": online})


# ---------------------------------------------------------------- finding sounds for script cues

STOPWORDS = set("a an the of and or in on to with kicks kick in up out over under then into sung sound sounds "
                "effect sfx music cue plays playing slow fast".split())


def cue_query(text: str, words: int = 3) -> str:
    """Script cue text to search terms: 'Jingle, sung: "HamRest!"' -> 'jingle'."""
    text = re.sub(r"\"[^\"]*\"|“[^”]*”", " ", text.lower())
    kept = [w for w in re.findall(r"[a-z]+", text) if w not in STOPWORDS]
    return " ".join(kept[:words])


def find_sound(cfg: Config, kind: str, text: str, max_seconds: float) -> dict | None:
    """The first online sound for a script cue that's short enough, trying broader searches as it goes."""
    words = cue_query(text, 4).split()
    for n in range(len(words), 0, -1):
        query = " ".join(words[:n])
        try:
            results = _search_openverse(cfg, "audio", query, 20)
        except OnlineError:
            results = []
        for r in results:
            if r["url"] and r["seconds"] and 0.3 <= r["seconds"] <= max_seconds:
                return {**r, "query": query}
    return None


# ---------------------------------------------------------------- storage cap

def mark_used(conn: sqlite3.Connection, asset_id: int, production_id: int) -> None:
    """Record that a production used an asset. Used assets are never culled."""
    row = conn.execute("SELECT meta FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        return
    meta = db.loads(row["meta"])
    used = set(meta.get("used_by", []))
    if production_id not in used:
        meta["used_by"] = sorted(used | {production_id})
        conn.execute("UPDATE assets SET meta = ? WHERE id = ?", (db.dumps(meta), asset_id))


def _size(cfg: Config, asset: sqlite3.Row) -> int:
    meta = db.loads(asset["meta"])
    files = [cfg.library / asset["path"]] if asset["path"] else []
    files += [cfg.library / p for p in meta.get("derived", {}).values()]
    return sum(f.stat().st_size for f in files if f.is_file())


def storage(conn: sqlite3.Connection, cfg: Config) -> dict:
    rows = conn.execute(f"SELECT * FROM assets WHERE status = 'ready' AND source IN ({','.join('?' * len(ONLINE_SOURCES))})",
                        ONLINE_SOURCES).fetchall()
    return {"count": len(rows), "bytes": sum(_size(cfg, r) for r in rows),
            "cap_bytes": int(float(_settings(cfg).get("cap_gb", 20)) * 1e9)}


def cull(conn: sqlite3.Connection, cfg: Config, log=lambda line: None) -> dict:
    """Delete online downloads nothing uses: anything older than cull_after_days, then the oldest
    until under the cap. Kept: assets a production used, assets linked to a World entity.
    A culled asset keeps its row and source, so it can be fetched again."""
    import shutil
    from datetime import datetime, timedelta, timezone

    settings = _settings(cfg)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=float(settings.get("cull_after_days", 30)))
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        f"SELECT a.* FROM assets a WHERE a.status = 'ready' AND a.source IN ({','.join('?' * len(ONLINE_SOURCES))}) "
        "AND NOT EXISTS (SELECT 1 FROM asset_entities ae WHERE ae.asset_id = a.id) ORDER BY a.created_at",
        ONLINE_SOURCES).fetchall()
    candidates = [r for r in rows if not db.loads(r["meta"]).get("used_by")]
    total, cap = storage(conn, cfg)["bytes"], int(float(settings.get("cap_gb", 20)) * 1e9)
    culled, freed = [], 0
    for asset in candidates:
        if asset["created_at"] >= cutoff and total - freed <= cap:
            continue
        size = _size(cfg, asset)
        (cfg.library / asset["path"]).unlink(missing_ok=True)
        if asset["sha256"]:
            shutil.rmtree(cfg.store / "derived" / asset["sha256"], ignore_errors=True)
        meta = db.loads(asset["meta"])
        meta.pop("derived", None)
        meta["culled_at"] = db.now()
        # sha256 is cleared so fetching it again isn't merged into this culled row as a duplicate.
        conn.execute("UPDATE assets SET status = 'culled', sha256 = NULL, meta = ? WHERE id = ?",
                     (db.dumps(meta), asset["id"]))
        freed += size
        culled.append(asset["id"])
        log(f"culled asset {asset['id']} ({size / 1e6:.1f} MB): {asset['title']}")
    log(f"culled {len(culled)} asset(s), freed {freed / 1e6:.1f} MB; online downloads now {(total - freed) / 1e9:.2f} GB "
        f"of {cap / 1e9:.0f} GB")
    return {"culled": culled, "freed_bytes": freed}


def fetch_now(conn: sqlite3.Connection, cfg: Config, result: dict, category: str, tags: list[str] | None = None,
              log=lambda line: None) -> int:
    """fetch() and finish the ingest straight away, for a production run that needs the file now.
    Safe because there's one worker: the queued ingest job can't be running elsewhere."""
    asset_id = fetch(conn, cfg, result, category, tags, log=log)
    conn.execute("DELETE FROM jobs WHERE kind = 'ingest' AND asset_id = ? AND status = 'queued'", (asset_id,))
    out = ingest.process(conn, cfg, asset_id, log)
    return out.get("duplicate_of", asset_id)


def library_match(conn: sqlite3.Connection, category: str, query: str, media_kind: str | None = None) -> int | None:
    """The ready library asset whose title and tags share the most words with query (at least half)."""
    words = set(query.lower().split())
    if not words:
        return None
    sql, args = "SELECT id, title, tags, meta FROM assets WHERE status = 'ready' AND category = ?", [category]
    if media_kind:
        sql += " AND media = ?"
        args.append(media_kind)
    best, best_key = None, None
    for r in conn.execute(sql, args):
        have = set(re.findall(r"[a-z]+", (r["title"] + " " + " ".join(db.loads(r["tags"]))).lower()))
        hits = len(words & have)
        if hits * 2 < len(words) or hits == 0:
            continue
        key = (hits, -len(db.loads(r["meta"]).get("used_by", [])), -r["id"])  # more hits, then least used
        if best_key is None or key > best_key:
            best, best_key = r["id"], key
    return best
