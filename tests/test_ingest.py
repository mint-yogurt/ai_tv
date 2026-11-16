import shutil

from aitv import db, ingest, jobs, worker

from .conftest import make_media


def run_queue(conn, cfg, limit=20):
    for _ in range(limit):
        job = jobs.claim(conn)
        if job is None:
            return
        worker.run_one(conn, cfg, job)


def test_image_upload_becomes_ready_asset_linked_to_character(cfg, conn):
    dave = conn.execute("INSERT INTO entities (type, name) VALUES ('character', 'Dave')").lastrowid
    src = make_media("image", cfg.uploads / "dave.png")
    aid = ingest.register(conn, src, "image", entity_id=dave, role="headshot")
    run_queue(conn, cfg)

    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
    assert asset["status"] == "ready"
    assert (asset["width"], asset["height"]) == (640, 480)
    assert (cfg.library / asset["path"]).is_file()
    assert (cfg.library / db.loads(asset["meta"])["derived"]["thumb"]).is_file()
    assert not src.exists()  # moved out of the inbox
    link = conn.execute("SELECT role FROM asset_entities WHERE asset_id = ? AND entity_id = ?", (aid, dave)).fetchone()
    assert link["role"] == "headshot"


def test_voice_drop_creates_voice_and_blocks_on_missing_tools(cfg, conn):
    folder = cfg.inbox / "voices" / "mint_announcer"
    folder.mkdir(parents=True)
    (folder / "about.txt").write_text("deep late-night announcer")
    make_media("tone", folder / "take1.wav")

    created = ingest.scan_inbox(conn, cfg)
    assert len(created) == 1
    run_queue(conn, cfg)

    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (created[0],)).fetchone()
    assert asset["status"] == "ready"
    meta = db.loads(asset["meta"])
    assert meta["reference_duration_s"] < asset["duration_s"]  # leading silence trimmed
    voice = conn.execute("SELECT * FROM entities WHERE type = 'voice'").fetchone()
    assert voice["name"] == "mint_announcer"
    assert voice["description"] == "deep late-night announcer"
    blocked = {r["kind"]: r["status"] for r in conn.execute("SELECT kind, status FROM jobs WHERE kind LIKE 'voice.%'")}
    assert blocked == {"voice.transcribe": "blocked", "voice.preview": "blocked"}

    assert ingest.scan_inbox(conn, cfg) == []  # nothing registered twice


def test_footage_gets_proxy_and_duplicates_merge(cfg, conn):
    clip = make_media("clip", cfg.uploads / "clip.mp4")
    copy = cfg.uploads / "clip_copy.mp4"
    shutil.copy(clip, copy)
    first = ingest.register(conn, clip, "footage")
    second = ingest.register(conn, copy, "footage")
    run_queue(conn, cfg)

    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (first,)).fetchone()
    assert asset["media"] == "video"
    assert "proxy" in db.loads(asset["meta"])["derived"]
    assert conn.execute("SELECT 1 FROM assets WHERE id = ?", (second,)).fetchone() is None


def test_silent_voice_recording_is_rejected(cfg, conn):
    src = make_media("silence", cfg.uploads / "nothing.wav")
    aid = ingest.register(conn, src, "voice")
    run_queue(conn, cfg)
    job = conn.execute("SELECT * FROM jobs WHERE asset_id = ? AND kind = 'ingest'", (aid,)).fetchone()
    assert job["status"] == "failed" and "seems silent" in job["error"]
    assert conn.execute("SELECT COUNT(*) FROM entities WHERE type = 'voice'").fetchone()[0] == 0


def test_wrong_category_fails_cleanly(cfg, conn):
    src = make_media("image", cfg.uploads / "not_a_voice.png")
    aid = ingest.register(conn, src, "voice")
    run_queue(conn, cfg)
    assert conn.execute("SELECT status FROM assets WHERE id = ?", (aid,)).fetchone()["status"] == "failed"
    job = conn.execute("SELECT * FROM jobs WHERE asset_id = ? AND kind = 'ingest'", (aid,)).fetchone()
    assert job["status"] == "failed" and "can't be filed as voice" in job["error"]


def test_voice_preview_uses_pocket_tts_and_cuts_15s_reference(cfg, conn, tmp_path):
    # A stand-in for pocket-tts: copies a tone to --output-path.
    tone = make_media("tone", tmp_path / "tone.wav")
    fake = tmp_path / "pocket-tts"
    fake.write_text(f'#!/bin/sh\nwhile [ "$1" != "--output-path" ]; do shift; done\ncp {tone} "$2"\n')
    fake.chmod(0o755)
    cfg.raw["tools"]["pocket_tts"] = str(fake)

    long_take = cfg.uploads / "long.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=200:d=30", str(long_take)], check=True)
    aid = ingest.register(conn, long_take, "voice", voice_name="Rasmir")
    run_queue(conn, cfg)

    meta = db.loads(conn.execute("SELECT meta FROM assets WHERE id = ?", (aid,)).fetchone()["meta"])
    assert 14 <= meta["reference_duration_s"] <= 15.5
    assert (cfg.library / meta["derived"]["tts_preview"]).is_file()
    status = {r["kind"]: r["status"] for r in conn.execute("SELECT kind, status FROM jobs WHERE kind LIKE 'voice.%'")}
    assert status == {"voice.transcribe": "blocked", "voice.preview": "done"}


def test_image_generate_files_renders_as_generated_assets(cfg, conn, monkeypatch):
    from aitv import comfy

    graphs = []
    monkeypatch.setattr(comfy, "ensure_running", lambda cfg, log=None: None)

    def fake_render(cfg_, graph, out_dir, timeout=1800):
        graphs.append(graph)
        import subprocess
        paths = []
        for i in range(graph["latent"]["inputs"]["batch_size"]):  # distinct images, or de-dup would merge them
            path = out_dir / f"aitv_{i}.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x{i * 40:02x}2040:s=640x480",
                            "-frames:v", "1", str(path)], check=True)
            paths.append(path)
        return paths

    monkeypatch.setattr(comfy, "render", fake_render)
    old_man = conn.execute("INSERT INTO entities (type, name) VALUES ('character', 'Grandpa Earl')").lastrowid
    jobs.enqueue(conn, "image.generate", {"prompt": "grumpy old man", "preset": "draft", "count": 2, "seed": "7",
                                          "entity_id": old_man, "role": "headshot"})
    run_queue(conn, cfg)

    assert graphs[0]["sample"]["inputs"]["steps"] == 6 and "lora" in graphs[0]  # draft = LCM
    rows = conn.execute("SELECT a.* FROM assets a JOIN asset_entities ae ON ae.asset_id = a.id "
                        "WHERE ae.entity_id = ? AND ae.role = 'headshot'", (old_man,)).fetchall()
    assert len(rows) == 2 and all(r["status"] == "ready" and r["source"] == "generated" for r in rows)
    gen = db.loads(rows[0]["meta"])["generation"]
    assert gen["seed"] == 7 and gen["prompt"] == "grumpy old man"
    assert not any(p.name.startswith("gen_") for p in cfg.uploads.iterdir())  # batch folder cleaned up


def test_final_preset_has_no_lora():
    from aitv import comfy

    images = {"checkpoint": "x", "vae": "v", "lcm_lora": "l",
              "presets": {"final": {"steps": 20, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras", "lcm": False}}}
    g = comfy.workflow(images, "final", "a room", "", 640, 480, 1, 1, "p")
    assert "lora" not in g and g["sample"]["inputs"]["model"] == ["ckpt", 0]
