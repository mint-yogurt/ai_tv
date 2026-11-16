import shutil
import subprocess

import pytest

from aitv import db, online

from .conftest import make_media


def fake_downloads(monkeypatch, files: dict):
    """Serve downloads from local files keyed by URL; no network in tests."""
    monkeypatch.setattr(online, "_download", lambda cfg, url, dst: shutil.copy(files[url], dst) and dst)


def test_cue_query_drops_quotes_and_filler():
    assert online.cue_query('Jingle, sung: "HamRest! Sleep like you just ate."') == "jingle"
    assert online.cue_query("Upbeat synth-brass fanfare kicks in") == "upbeat synth brass"
    assert online.cue_query("Cash register ding") == "cash register ding"


def test_fetch_shrinks_archive_video_and_remembers_the_source(cfg, conn, tmp_path, monkeypatch):
    clip = make_media("clip", tmp_path / "big.mp4")
    url = "https://archive.org/download/tupperware/tupperware.mp4"
    monkeypatch.setattr(online, "_archive_file", lambda cfg_, ident, kind: (url, 2.0))
    fake_downloads(monkeypatch, {url: clip})
    result = {"source": "archive", "kind": "video", "id": "tupperware", "title": "Tupperware Commercial #1",
              "landing": "https://archive.org/details/tupperware", "url": None, "seconds": None}

    aid = online.fetch_now(conn, cfg, result, "footage", ["stock", "tupperware"])
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
    assert asset["status"] == "ready" and asset["source"] == "archive" and asset["media"] == "video"
    assert db.loads(asset["meta"])["online"]["url"] == url
    assert not conn.execute("SELECT 1 FROM jobs WHERE kind = 'ingest' AND asset_id = ? AND status = 'queued'", (aid,)).fetchone()
    assert online.library_match(conn, "footage", "tupperware party", "video") == aid
    assert online.library_match(conn, "footage", "space rocket launch", "video") is None


def test_long_videos_and_big_downloads_are_refused(cfg, conn, monkeypatch):
    monkeypatch.setattr(online, "_archive_file", lambda cfg_, ident, kind: ("https://x/y.mp4", 3600.0))
    with pytest.raises(online.OnlineError, match="max_video_minutes"):
        online.fetch(conn, cfg, {"source": "archive", "kind": "video", "id": "film", "title": "film"})


def test_cull_keeps_used_and_linked_assets_and_respects_age(cfg, conn, tmp_path, monkeypatch):
    def add(name, created):
        f = tmp_path / f"{name}.wav"  # distinct bytes, or ingest merges them as duplicates
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f={200 + len(name) * 50 + ord(name[0])}:d=2",
                        str(f)], check=True)
        url = f"https://example/{name}.wav"
        fake_downloads(monkeypatch, {url: f})
        aid = online.fetch_now(conn, cfg, {"source": "openverse", "kind": "audio", "id": name, "title": name,
                                           "url": url, "seconds": 3}, "sfx")
        conn.execute("UPDATE assets SET created_at = ? WHERE id = ?", (created, aid))
        return aid

    old, used, linked, fresh = (add(n, c) for n, c in [("old", "2020-01-01T00:00:00Z"), ("used", "2020-01-01T00:00:00Z"),
                                                       ("linked", "2020-01-01T00:00:00Z"), ("fresh", db.now())])
    online.mark_used(conn, used, 1)
    ent = conn.execute("INSERT INTO entities (type, name) VALUES ('prop', 'bell')").lastrowid
    conn.execute("INSERT INTO asset_entities (asset_id, entity_id, role) VALUES (?, ?, 'reference')", (linked, ent))
    path = cfg.library / conn.execute("SELECT path FROM assets WHERE id = ?", (old,)).fetchone()["path"]

    assert online.cull(conn, cfg)["culled"] == [old]
    assert not path.exists()
    row = conn.execute("SELECT status, sha256, meta FROM assets WHERE id = ?", (old,)).fetchone()
    assert row["status"] == "culled" and row["sha256"] is None and db.loads(row["meta"])["online"]["id"] == "old"

    # Over the cap, even recent unused downloads go, oldest first.
    cfg.raw["online"]["cap_gb"] = 0
    assert online.cull(conn, cfg)["culled"] == [fresh]
