"""Turns a dropped or uploaded file into a library asset.

Flow: file lands in library/inbox/ -> register() makes a 'pending' asset and queues an
'ingest' job -> the worker calls process() -> the file is hashed, moved into
library/store/, previews are made, and the asset becomes 'ready'.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import sqlite3
import time
from pathlib import Path

from . import jobs, media
from .config import INBOX_FOLDERS, Config
from .db import dumps, loads

ACCEPTS = {
    "image": {"image"},
    "footage": {"video"},
    "voice": {"audio", "video"},  # a phone video of you talking works; the audio is used
    "sfx": {"audio", "video"},
    "music": {"audio", "video"},
}
SKIP_NAMES = {"about.txt", ".DS_Store", "Thumbs.db"}
MIN_VOICE_SECONDS = 1.0
GOOD_VOICE_SECONDS = 8.0  # below this the clone is noticeably less stable
REFERENCE_METHOD = "original_15s"  # bump when the way references are cut changes


def safe_name(name: str) -> str:
    stem = Path(name).name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "file"
    return stem[:120]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def register(
    conn: sqlite3.Connection,
    file: Path,
    category: str,
    *,
    title: str = "",
    original_name: str = "",
    source: str = "upload",
    entity_id: int | None = None,
    role: str = "reference",
    tags: list[str] | None = None,
    voice_name: str = "",
    voice_description: str = "",
    extra_meta: dict | None = None,
) -> int:
    """Create a pending asset for a file sitting in the inbox and queue its ingest."""
    original_name = original_name or file.name
    meta = {**(extra_meta or {}), "inbox_path": str(file)}
    if voice_name:
        meta["voice_name"] = voice_name
    if voice_description:
        meta["voice_description"] = voice_description
    cur = conn.execute(
        "INSERT INTO assets (category, title, original_name, source, tags, meta) VALUES (?, ?, ?, ?, ?, ?)",
        (category, title or Path(original_name).stem, original_name, source, dumps(tags or []), dumps(meta)),
    )
    asset_id = cur.lastrowid
    if entity_id:
        conn.execute(
            "INSERT OR IGNORE INTO asset_entities (asset_id, entity_id, role) VALUES (?, ?, ?)",
            (asset_id, entity_id, role),
        )
    jobs.enqueue(conn, "ingest", {"asset_id": asset_id}, asset_id=asset_id, priority=5)
    return asset_id


def scan_inbox(conn: sqlite3.Connection, cfg: Config) -> list[int]:
    """Register files dropped straight into library/inbox/<folder>/ (e.g. over SFTP)."""
    settle = float(cfg.section("worker").get("inbox_settle_seconds", 10))
    known = {
        row[0]
        for row in conn.execute(
            "SELECT json_extract(meta, '$.inbox_path') FROM assets WHERE status IN ('pending', 'processing', 'failed')"
        )
    }
    created = []
    for folder, category in INBOX_FOLDERS.items():
        base = cfg.inbox / folder
        if not base.is_dir():
            continue
        for file in sorted(base.rglob("*")):
            rel = file.relative_to(base)
            if not file.is_file() or file.name in SKIP_NAMES or any(p.startswith(".") for p in rel.parts):
                continue
            if str(file) in known or time.time() - file.stat().st_mtime < settle:
                continue
            voice_name = voice_description = ""
            if category == "voice":
                # inbox/voices/<name>/clip.wav -> voice "<name>"; inbox/voices/clip.wav -> voice "clip"
                voice_name = rel.parts[0] if len(rel.parts) > 1 else file.stem
                about = base / rel.parts[0] / "about.txt" if len(rel.parts) > 1 else None
                if about and about.is_file():
                    voice_description = about.read_text(errors="replace").strip()
            created.append(
                register(conn, file, category, source="inbox", voice_name=voice_name, voice_description=voice_description)
            )
    return created


def _unique_entity_name(conn: sqlite3.Connection, etype: str, name: str) -> str:
    candidate, n = name, 2
    while conn.execute("SELECT 1 FROM entities WHERE type = ? AND name = ?", (etype, candidate)).fetchone():
        candidate, n = f"{name} {n}", n + 1
    return candidate


def process(conn: sqlite3.Connection, cfg: Config, asset_id: int, log=lambda line: None) -> dict:
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if asset is None:
        return {"skipped": "asset no longer exists"}
    meta = loads(asset["meta"])
    src = Path(meta.get("inbox_path", ""))
    if not src.is_file():
        raise FileNotFoundError(f"inbox file is gone: {src}")

    conn.execute("UPDATE assets SET status = 'processing' WHERE id = ?", (asset_id,))
    log(f"hashing {src.name}")
    digest = sha256_file(src)

    dup = conn.execute("SELECT id FROM assets WHERE sha256 = ? AND id != ?", (digest, asset_id)).fetchone()
    if dup:
        # Same bytes already in the library: keep the old asset, carry over any entity links.
        conn.execute(
            "INSERT OR IGNORE INTO asset_entities (asset_id, entity_id, role) "
            "SELECT ?, entity_id, role FROM asset_entities WHERE asset_id = ?",
            (dup["id"], asset_id),
        )
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        src.unlink(missing_ok=True)
        log(f"duplicate of asset {dup['id']}; merged")
        return {"duplicate_of": dup["id"]}

    info = media.probe(src)
    category = asset["category"]
    if info["media"] not in ACCEPTS[category]:
        raise ValueError(f"a {info['media']} file can't be filed as {category}")
    if category in ("voice", "sfx", "music") and not info["has_audio"]:
        raise ValueError("no audio track found")

    derived_dir = cfg.store / "derived" / digest
    derived_dir.mkdir(parents=True, exist_ok=True)
    derived: dict[str, str] = {}

    def keep(name: str, path: Path) -> None:
        derived[name] = str(path.relative_to(cfg.library))

    if category == "voice":
        # Checked before the file leaves the inbox, so a rejected recording can simply be replaced.
        log("cutting a ~15 s voice reference from the original recording")
        reference, ref_seconds = media.voice_reference(src, derived_dir / "reference.wav")
        if ref_seconds < MIN_VOICE_SECONDS:
            shutil.rmtree(derived_dir, ignore_errors=True)
            raise ValueError(
                f"only {ref_seconds:.1f}s of speech left after trimming silence; "
                f"the recording seems silent or too quiet (need at least {MIN_VOICE_SECONDS:.0f}s)"
            )
        keep("reference", reference)
        meta["reference_duration_s"] = ref_seconds
        meta["reference_method"] = REFERENCE_METHOD
        if ref_seconds < GOOD_VOICE_SECONDS:
            log(f"note: only {ref_seconds:.1f}s of speech; 10-20 s clones much more steadily")

    ext = src.suffix.lower() or mimetypes.guess_extension(asset["mime"] or "") or ""
    rel = Path("store") / digest[:2] / f"{digest}{ext}"
    dst = cfg.library / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    log(f"stored as {rel}")
    if src.parent.name.startswith("gen_") and not any(src.parent.iterdir()):
        src.parent.rmdir()  # a finished image-generation batch

    if info["media"] == "image":
        keep("thumb", media.thumbnail(dst, derived_dir / "thumb.jpg"))
    elif info["media"] == "video" and category == "footage":
        keep("thumb", media.thumbnail(dst, derived_dir / "thumb.jpg", seek=min(1.0, (info["duration_s"] or 0) / 2)))
        log("making a 480p browser proxy")
        keep("proxy", media.proxy_video(dst, derived_dir / "proxy.mp4"))
    if category in ("voice", "sfx", "music"):
        keep("waveform", media.waveform(dst, derived_dir / "waveform.png"))
        keep("preview", media.preview_audio(dst, derived_dir / "preview.mp3"))

    meta.pop("inbox_path", None)
    meta.update({"derived": derived, "probe": info})
    conn.execute(
        "UPDATE assets SET status = 'ready', sha256 = ?, path = ?, media = ?, mime = ?, duration_s = ?, "
        "width = ?, height = ?, size_bytes = ?, meta = ? WHERE id = ?",
        (
            digest, str(rel), info["media"], mimetypes.guess_type(dst.name)[0], info["duration_s"],
            info["width"], info["height"], dst.stat().st_size, dumps(meta), asset_id,
        ),
    )

    result = {"stored": str(rel), "media": info["media"]}
    if category == "voice":
        result["voice_entity_id"] = _attach_voice(conn, asset_id, asset["title"], meta)
    return result


def _attach_voice(conn: sqlite3.Connection, asset_id: int, title: str, meta: dict) -> int:
    """Every voice recording belongs to a voice entity; make one if the upload didn't name one."""
    row = conn.execute(
        "SELECT e.id FROM asset_entities ae JOIN entities e ON e.id = ae.entity_id "
        "WHERE ae.asset_id = ? AND e.type = 'voice'",
        (asset_id,),
    ).fetchone()
    if row:
        voice_id = row["id"]
    else:
        wanted = meta.get("voice_name") or title or f"voice {asset_id}"
        existing = conn.execute("SELECT id FROM entities WHERE type = 'voice' AND name = ?", (wanted,)).fetchone()
        if existing:
            voice_id = existing["id"]  # another clip for a voice we already have
        else:
            voice_id = conn.execute(
                "INSERT INTO entities (type, name, description) VALUES ('voice', ?, ?)",
                (_unique_entity_name(conn, "voice", wanted), meta.get("voice_description", "")),
            ).lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO asset_entities (asset_id, entity_id, role) VALUES (?, ?, 'voice_reference')",
            (asset_id, voice_id),
        )
        # A clip uploaded for a character means "this is how they sound".
        conn.execute(
            "INSERT OR IGNORE INTO entity_links (from_id, to_id, relation) "
            "SELECT entity_id, ?, 'voiced_by' FROM asset_entities WHERE asset_id = ? AND entity_id != ?",
            (voice_id, asset_id, voice_id),
        )
    # The parts that need local models. They wait as 'blocked' until those are installed.
    for kind in ("voice.transcribe", "voice.preview"):
        jobs.enqueue(conn, kind, {"asset_id": asset_id, "voice_id": voice_id}, asset_id=asset_id)
    return voice_id
