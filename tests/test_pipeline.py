import json
import shutil

import pytest

from aitv import db, llm, pipeline
from aitv.jobs import Blocked


def test_existing_subject_falls_back_to_new_when_world_is_empty(cfg, conn):
    pid = pipeline.create_production(conn, cfg, "parody_commercial",
                                     pins={"ad_type": "product", "subject": "existing"}, seed=1)
    taken = db.loads(conn.execute("SELECT rolls FROM productions WHERE id = ?", (pid,)).fetchone()["rolls"])
    assert taken["subject"]["value"] == "new"
    assert "no existing product" in taken["subject"]["reason"]


def test_existing_subject_prefers_least_used_and_logs_usage(cfg, conn):
    soap = conn.execute("INSERT INTO entities (type, name) VALUES ('product', 'SudsCo Max')").lastrowid
    picks = []
    for seed in range(6):
        pid = pipeline.create_production(conn, cfg, "parody_commercial",
                                         pins={"ad_type": "product", "subject": "existing"}, seed=seed)
        taken = db.loads(conn.execute("SELECT rolls FROM productions WHERE id = ?", (pid,)).fetchone()["rolls"])
        picks.append(taken["subject"]["entity_id"])
    assert picks == [soap] * 6
    assert conn.execute("SELECT COUNT(*) FROM usage WHERE entity_id = ?", (soap,)).fetchone()[0] == 6


def test_manual_backend_waits_then_continues_with_pasted_answers(cfg, conn):
    pid = pipeline.create_production(conn, cfg, "parody_commercial", pins={"ad_type": "product"}, seed=3)
    with pytest.raises(pipeline.WaitingOnYou, match="outline"):
        pipeline.run_production(conn, cfg, pid)
    prod = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
    assert prod["status"] == "waiting_on_you" and prod["stage"] == "outline"
    [req] = pipeline.pending_requests(cfg, pid)
    assert req["stage"] == "outline" and "invent a product" in req["request"]

    with pytest.raises(ValueError, match="new_subject"):
        pipeline.save_answer(cfg, pid, "outline", {"title": "x", "logline": "y", "beats": []}, pipeline.OUTLINE_SCHEMA)
    pipeline.save_answer(cfg, pid, "outline", OUTLINE, pipeline.OUTLINE_SCHEMA)
    with pytest.raises(pipeline.WaitingOnYou, match="screenplay"):
        pipeline.run_production(conn, cfg, pid)

    product = conn.execute("SELECT * FROM entities WHERE type = 'product'").fetchone()
    assert product["name"] == "Forget-O" and db.loads(product["attributes"])["tagline"] == "Clean slate."
    prod = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
    assert db.loads(prod["rolls"])["subject"]["entity_id"] == product["id"] and prod["title"] == "Forget-O"

    pipeline.save_answer(cfg, pid, "screenplay", SCREENPLAY, pipeline.SCREENPLAY_SCHEMA)
    with pytest.raises(pipeline.WaitingOnYou, match="pick voices for: Announcer"):
        pipeline.run_production(conn, cfg, pid)
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM documents WHERE production_id = ? ORDER BY id", (pid,))]
    assert kinds == ["outline", "screenplay"]  # the outline wasn't saved twice on the second run


def test_api_backend_answers_are_cached(cfg, conn):
    fake = llm.Fake({"outline": OUTLINE, "screenplay": SCREENPLAY})
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    with pytest.raises(pipeline.WaitingOnYou, match="pick voices"):
        pipeline.run_production(conn, cfg, pid, llm=fake)
    assert [c["schema"]["title"] for c in fake.calls] == ["outline", "screenplay"]
    pipeline.rewind(conn, cfg, pid, "screenplay")
    with pytest.raises(pipeline.WaitingOnYou, match="pick voices"):
        pipeline.run_production(conn, cfg, pid, llm=fake)
    assert [c["schema"]["title"] for c in fake.calls][-1] == "screenplay" and len(fake.calls) == 3


OUTLINE = {"title": "Forget-O", "logline": "Detergent that erases memories",
           "new_subject": {"name": "Forget-O", "description": "laundry detergent", "tagline": "Clean slate."},
           "beats": [{"summary": "A stain. A memory.", "act_break_after": False}]}
SCREENPLAY = {"title": "Forget-O", "cast": [{"character": "Announcer", "description": "warm", "voice": None}],
              "scenes": [{"setting": "Laundry room", "description": "A washer.", "act_break_after": False,
                          "beats": [{"type": "voiceover", "character": "Announcer", "text": "Forget-O.", "delivery": None},
                                    {"type": "sfx", "character": None, "text": "washer spin", "delivery": None}]}]}


def _voice(conn, cfg, name, tone):
    """A voice entity with a ready recording whose reference is the given file."""
    from aitv.ingest import REFERENCE_METHOD

    vid = conn.execute("INSERT INTO entities (type, name) VALUES ('voice', ?)", (name,)).lastrowid
    rel = f"store/{name}.wav"
    (cfg.library / rel).write_bytes(tone.read_bytes())
    meta = {"derived": {"reference": rel}, "reference_method": REFERENCE_METHOD}
    aid = conn.execute("INSERT INTO assets (category, media, sha256, path, status, meta) VALUES "
                       "('voice', 'audio', ?, ?, 'ready', ?)", (name, rel, db.dumps(meta))).lastrowid
    conn.execute("INSERT INTO asset_entities (asset_id, entity_id, role) VALUES (?, ?, 'voice_reference')", (aid, vid))
    return vid


def test_cast_waits_for_unvoiced_roles_then_voices_every_line(cfg, conn, tmp_path):
    from .conftest import make_media

    tone = make_media("tone", tmp_path / "tone.wav")
    fake = tmp_path / "pocket-tts"
    fake.write_text(f'#!/bin/sh\nwhile [ "$1" != "--output-path" ]; do shift; done\ncp {tone} "$2"\n')
    fake.chmod(0o755)
    cfg.raw["tools"]["pocket_tts"] = str(fake)
    rasmir = _voice(conn, cfg, "Rasmir", tone)
    _voice(conn, cfg, "Poots", tone)

    script = {**SCREENPLAY, "cast": [{"character": "Announcer", "description": "warm", "voice": None},
                                     {"character": "Gary", "description": "nervous", "voice": "Rasmir"},
                                     {"character": "Extra", "description": "silent", "voice": None}]}
    script["scenes"] = [{**SCREENPLAY["scenes"][0], "beats": SCREENPLAY["scenes"][0]["beats"] + [
        {"type": "line", "character": "Gary", "text": "I don't trust it.", "delivery": "nervous"}]}]
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    pipeline.save_answer(cfg, pid, "outline", OUTLINE, pipeline.OUTLINE_SCHEMA)
    pipeline.save_answer(cfg, pid, "screenplay", script, pipeline.SCREENPLAY_SCHEMA)

    with pytest.raises(pipeline.WaitingOnYou, match="pick voices for: Announcer"):
        pipeline.run_production(conn, cfg, pid)
    proposal = db.loads(pipeline.casting_paths(cfg, pid)["proposal"].read_text())
    assert {r["character"]: r["voice"] for r in proposal["roles"]} == {"Announcer": None, "Gary": "Rasmir", "Extra": None}

    pipeline.save_answer(cfg, pid, "casting", {"roles": [{"character": "Announcer", "voice": "Poots"},
                                                         {"character": "Gary", "voice": "Rasmir"},
                                                         {"character": "Extra", "voice": None}]}, pipeline.CASTING_SCHEMA)
    with pytest.raises(pipeline.WaitingOnYou, match="plan"):
        pipeline.run_production(conn, cfg, pid)

    gary = conn.execute("SELECT id FROM entities WHERE type = 'character' AND name = 'Gary'").fetchone()["id"]
    assert conn.execute("SELECT to_id FROM entity_links WHERE from_id = ? AND relation = 'voiced_by'",
                        (gary,)).fetchone()["to_id"] == rasmir
    voice = db.loads(conn.execute("SELECT body FROM documents WHERE production_id = ? AND kind = 'voice'",
                                  (pid,)).fetchone()["body"])
    assert [l["character"] for l in voice["lines"]] == ["Announcer", "Gary"]
    assert (cfg.path("productions") / voice["draft"]).is_file()
    assert voice["total_s"] > 5.5  # two 3 s lines plus the gap

    # A second production casts Gary automatically from the World.
    pid2 = pipeline.create_production(conn, cfg, "parody_commercial", seed=4)
    assert pipeline.propose_casting(conn, {**script, "cast": [{"character": "Gary", "description": "", "voice": None}]}) \
        == {"roles": [{"character": "Gary", "voice": "Rasmir"}]}


def test_title_cards_are_read_aloud_only_where_nobody_speaks(cfg, conn, tmp_path):
    from .conftest import make_media

    tone = make_media("tone", tmp_path / "tone.wav")
    fake = tmp_path / "pocket-tts"
    fake.write_text(f'#!/bin/sh\nwhile [ "$1" != "--output-path" ]; do shift; done\ncp {tone} "$2"\n')
    fake.chmod(0o755)
    cfg.raw["tools"]["pocket_tts"] = str(fake)
    _voice(conn, cfg, "Poots", tone)
    card = lambda text: {"type": "title_card", "character": None, "text": text, "delivery": None}  # noqa: E731
    first = SCREENPLAY["scenes"][0]
    script = {**SCREENPLAY, "scenes": [{**first, "beats": first["beats"] + [card("Forget-O")]},
                                       {**first, "beats": [card("Forget-O\nClean slate")]}]}
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    pipeline.save_answer(cfg, pid, "outline", OUTLINE, pipeline.OUTLINE_SCHEMA)
    pipeline.save_answer(cfg, pid, "screenplay", script, pipeline.SCREENPLAY_SCHEMA)
    pipeline.save_answer(cfg, pid, "casting", {"roles": [{"character": "Announcer", "voice": "Poots"}]},
                         pipeline.CASTING_SCHEMA)
    with pytest.raises(pipeline.WaitingOnYou, match="plan"):
        pipeline.run_production(conn, cfg, pid)
    voice = db.loads(conn.execute("SELECT body FROM documents WHERE production_id = ? AND kind = 'voice'",
                                  (pid,)).fetchone()["body"])
    # The first scene's card has the voiceover over it; the second scene's card is read by the voiceover.
    assert [(l["character"], l["text"], l.get("card", False)) for l in voice["lines"]] == [
        ("Announcer", "Forget-O.", False), ("Announcer", "Forget-O. Clean slate.", True)]


def test_plan_render_assemble_and_look_make_a_finished_video(cfg, conn, tmp_path, monkeypatch):
    from aitv import comfy, media

    from .conftest import make_media

    tone = make_media("tone", tmp_path / "tone.wav")
    fake = tmp_path / "pocket-tts"
    fake.write_text(f'#!/bin/sh\nwhile [ "$1" != "--output-path" ]; do shift; done\ncp {tone} "$2"\n')
    fake.chmod(0o755)
    cfg.raw["tools"]["pocket_tts"] = str(fake)
    _voice(conn, cfg, "Poots", tone)
    renders = []

    def fake_render(cfg_, graph, out_dir):
        renders.append(graph)
        return [make_media("image", out_dir / f"r{len(renders)}.png")]

    from aitv import online

    washer = make_media("tone", tmp_path / "washer.wav")
    footage = make_media("clip", tmp_path / "laundromat.mp4")
    monkeypatch.setattr(online, "find_sound", lambda cfg_, kind, text, limit: {
        "source": "openverse", "kind": "audio", "id": "w1", "title": "Washer spin cycle", "url": "https://x/washer.wav",
        "seconds": 3.0, "landing": None, "query": "washer spin"})
    monkeypatch.setattr(online, "search", lambda cfg_, source, kind, q, limit=24: [
        {"source": "archive", "kind": "video", "id": "laundromat", "title": "Laundromat 1958", "url": None, "seconds": None}])
    monkeypatch.setattr(online, "_archive_file", lambda cfg_, ident, kind: ("https://x/laundromat.mp4", 2.0))
    monkeypatch.setattr(online, "_download", lambda cfg_, url, dst: shutil.copy(
        {"https://x/washer.wav": washer, "https://x/laundromat.mp4": footage}[url], dst) and dst)
    monkeypatch.setattr(comfy, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(comfy, "render", fake_render)
    monkeypatch.setattr(comfy, "shutdown", lambda: None)

    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    pipeline.save_answer(cfg, pid, "outline", OUTLINE, pipeline.OUTLINE_SCHEMA)
    # The closing card is a title_card beat (no card without one); the voiceover speaks in that scene, so it isn't read.
    first = SCREENPLAY["scenes"][0]
    titled = {**SCREENPLAY, "scenes": [{**first, "beats": first["beats"] + [
        {"type": "title_card", "character": None, "text": "Forget-O", "delivery": None}]}]}
    pipeline.save_answer(cfg, pid, "screenplay", titled, pipeline.SCREENPLAY_SCHEMA)
    pipeline.save_answer(cfg, pid, "casting", {"roles": [{"character": "Announcer", "voice": "Poots"}]},
                         pipeline.CASTING_SCHEMA)
    with pytest.raises(pipeline.WaitingOnYou, match="plan"):
        pipeline.run_production(conn, cfg, pid)

    shot = {"scene": 0, "lines": [], "hold_s": 1.0, "source": "generate", "prompt": "a washing machine",
            "characters": [], "action": None, "clip_sound": None,
            "asset_id": None, "card_text": None, "caption": None, "motion": "push_in", "grade": "colour"}
    # Forgetting a line is caught before anything is rendered.
    pipeline.save_answer(cfg, pid, "plan", {"title_style": "hard_sell", "shots": [shot]}, pipeline.PLAN_SCHEMA)
    with pytest.raises(pipeline.WaitingOnYou, match="every line once"):
        pipeline.run_production(conn, cfg, pid)

    plan = {"title_style": "hard_sell",
            "shots": [{**shot, "lines": [1], "caption": "Forget-O: $4.99", "grade": "bw"},
                      {**shot, "source": "stock", "prompt": "laundromat"},
                      {**shot, "source": "card", "prompt": None, "card_text": "Forget-O\n1-800-FORGET", "motion": "still"}]}
    pipeline.save_answer(cfg, pid, "plan", plan, pipeline.PLAN_SCHEMA)
    assert pipeline.run_production(conn, cfg, pid)["completed"][-1] == "publish"
    assert len(renders) == 1  # the card isn't sent to the image model
    report = db.loads(conn.execute("SELECT body FROM documents WHERE production_id = ? AND kind = 'qc' "
                                   "ORDER BY version DESC", (pid,)).fetchone()["body"])
    assert report.get("error") is None, report
    assert all(c.get("error") is None for c in report["checks"].values()), report["checks"]

    final = media.probe(cfg.path("productions") / f"{pid:04d}" / "final.mp4")
    assert final["has_audio"] and (final["width"], final["height"]) == (640, 480)
    expected = (0.25 + 3.0 + 0.35 + 1.0) + (0.25 + 1.0) + pipeline.CARD_MIN_S  # each shot: lead, line and pause, hold
    assert abs(final["duration_s"] - expected) < 0.2
    rows = conn.execute("SELECT body FROM documents WHERE production_id = ? AND kind = 'assemble'", (pid,)).fetchall()
    assert db.loads(rows[-1]["body"])["missing_sound"] == []
    sound = db.loads(conn.execute("SELECT body FROM documents WHERE production_id = ? AND kind = 'sound'",
                                  (pid,)).fetchone()["body"])
    [cue] = [c for c in sound["cues"] if c["beat"] >= 0]  # beat -1 is the music the format always adds
    footage_id = conn.execute("SELECT id FROM assets WHERE category = 'footage'").fetchone()["id"]
    for aid in (cue["asset_id"], footage_id):  # both downloads are protected from the cleanup
        assert db.loads(conn.execute("SELECT meta FROM assets WHERE id = ?", (aid,)).fetchone()["meta"])["used_by"] == [pid]

    # Re-running reuses the rendered still.
    pipeline.rewind(conn, cfg, pid, "plan")
    pipeline.save_answer(cfg, pid, "plan", plan, pipeline.PLAN_SCHEMA)
    pipeline.run_production(conn, cfg, pid)
    assert len(renders) == 1


def test_plan_keeps_character_portraits_out_of_shots():
    voice = {"lines": [{"n": 1, "file": "a.wav", "duration_s": 1.0}]}
    shot = {"scene": 0, "lines": [1], "hold_s": 0.5, "source": "generate", "prompt": "a hallway", "characters": [],
            "action": "a door opens", "clip_sound": None, "asset_id": None, "card_text": None, "caption": None, "motion": "still",
            "grade": "colour"}
    assert pipeline.time_plan({"shots": [shot]}, voice, {31}, {"Tarman"})[0]["seconds"] == 2.1
    with pytest.raises(ValueError, match="reference portrait"):
        pipeline.time_plan({"shots": [{**shot, "source": "asset", "asset_id": 31}]}, voice, {31}, {"Tarman"})
    wife = {"name": "Wife", "position": "right", "size": "waist"}
    with pytest.raises(ValueError, match="describe them in prompt"):
        pipeline.time_plan({"shots": [{**shot, "characters": [wife]}]}, voice, {31}, {"Tarman"})


def test_refused_continuation_frame_restarts_from_an_earlier_frame(cfg, conn, tmp_path, monkeypatch):
    from aitv import videogen
    from tests.conftest import make_media

    cfg.raw["videogen"].update(enabled=True, durations=[3, 5], max_segments=3)
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    ctx = pipeline.StageContext(conn, cfg, pipeline.load_format(cfg, "parody_commercial"),
                                conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone(), lambda line: None)
    frame = make_media("image", tmp_path / "frame.png")
    sample = make_media("clip", tmp_path / "sample.mp4")
    submitted = []

    def submit(cfg_, start, prompt, duration):
        submitted.append(start.name)
        return {"id": f"job{len(submitted)}", "duration": duration}

    def wait(cfg_, job_id, dst, log=None):
        if job_id == "job2":  # the second piece's starting frame (the first piece's last frame) is refused
            raise videogen.VideoGenError("video job job2 failed: the starting image was refused by the content policy")
        shutil.copy(sample, dst)
        return 0.025

    monkeypatch.setattr(videogen, "submit", submit)
    monkeypatch.setattr(videogen, "wait", wait)
    shot = {"n": 1, "seconds": 7.0, "action": "a man waves", "prompt": "a hallway", "motion": "still",
            "grade": "colour", "caption": None}
    folder = tmp_path / "animated"
    folder.mkdir()
    cost = pipeline._animate(ctx, [(shot, frame, tmp_path / "clip.mp4")], folder, "", 30, 640, 480)
    assert cost == 0.05 and len(submitted) == 3
    assert submitted[1].endswith(".start.png") and submitted[2] == submitted[1]  # retried from an earlier frame
    [cut] = folder.glob("*_0.cut")
    assert float(cut.read_text()) == 2.5  # the first piece is cut where the second one starts
    from aitv import media
    assert abs(media.probe(tmp_path / "clip.mp4")["duration_s"] - 7.0) < 0.1


def test_render_sends_characters_and_sound_to_the_talkie_model_and_the_rest_to_mischief(cfg, conn, tmp_path, monkeypatch):
    from aitv import comfy, videogen
    from tests.conftest import make_media

    cfg.raw["videogen"].update(enabled=True, durations=[3, 5], max_segments=3)
    monkeypatch.setattr(comfy, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(comfy, "render", lambda cfg_, graph, out_dir: [make_media("image", out_dir / "r.png")])
    monkeypatch.setattr(comfy, "shutdown", lambda: None)
    # A silent World character with a reference picture, named in the pitch but never in the cast.
    ref = make_media("image", cfg.library / "robot.png")
    rid = conn.execute("INSERT INTO entities (type, name, description) VALUES ('character', 'Robot Bob (Mascot)', 'a tin robot')").lastrowid
    aid = conn.execute("INSERT INTO assets (category, media, sha256, path, status) VALUES "
                       "('image', 'image', 'robot', 'robot.png', 'ready')").lastrowid
    conn.execute("INSERT INTO asset_entities (asset_id, entity_id, role) VALUES (?, ?, 'reference')", (aid, rid))
    idea = conn.execute("INSERT INTO ideas (title, body, format) VALUES ('Towels', 'Robot-Bob folds towels.', "
                        "'parody_commercial')").lastrowid
    pid = pipeline.create_production(conn, cfg, "parody_commercial", idea_id=idea, seed=3)
    ctx = pipeline.StageContext(conn, cfg, pipeline.load_format(cfg, "parody_commercial"),
                                conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone(), lambda line: None)
    assert pipeline._pasteable_cast(ctx) == {"Robot Bob": rid}

    base = {"scene": 0, "lines": [], "placed": [], "start": 0.0, "seconds": 4.0, "source": "generate", "prompt": "a laundry room",
            "characters": [], "action": "a towel falls", "clip_sound": None, "asset_id": None, "card_text": None,
            "caption": None, "motion": "still", "grade": "colour"}
    bob = [{"name": "Robot Bob", "position": "center", "size": "waist"}]
    ctx.save("plan", {"title_style": "hard_sell", "shots": [
        {**base, "n": 1},
        {**base, "n": 2, "start": 4.0, "clip_sound": "a washer thuds"},
        {**base, "n": 3, "start": 8.0, "seconds": 7.0, "characters": bob}]})
    calls = []

    def place(cfg_, refs, prompt, dst):
        calls.append(("place", [r.name for r in refs]))
        make_media("image", dst)
        return 0.024

    def submit(cfg_, frame, prompt, duration):
        calls.append(("mischief", duration))
        return {"id": f"m{len(calls)}", "duration": duration}

    def submit_talkie(cfg_, frame, prompt, duration):
        calls.append(("talkie", duration, prompt))
        return {"id": f"t{len(calls)}", "duration": duration}

    def wait(cfg_, job_id, dst, log=None):
        make_media("clip", dst)
        return 0.05

    monkeypatch.setattr(videogen, "place", place)
    monkeypatch.setattr(videogen, "submit", submit)
    monkeypatch.setattr(videogen, "submit_talkie", submit_talkie)
    monkeypatch.setattr(videogen, "wait", wait)
    pipeline.stage_render(ctx)

    assert calls[0] == ("place", ["robot.png"])  # only the shot with a character is placed
    assert ("mischief", 5) in calls
    talkies = [c for c in calls if c[0] == "talkie"]
    assert [c[1] for c in talkies] == [5, 7]  # one piece each, never shorter than 5 s
    assert "Sound: a washer thuds. Nobody speaks." in talkies[0][2]
    render = ctx.latest("render")
    assert [bool(s.get("talkie")) for s in render["shots"]] == [False, True, True]
    for s in render["shots"][1:]:  # the talkie's own soundtrack is kept for the mix
        assert (cfg.path("productions") / s["clip"]).with_suffix(".wav").is_file()
    assert render["video_cost_usd"] == round(0.024 + 3 * 0.05, 4)


def test_plan_caps_cards_and_long_generated_shots():
    voice = {"lines": [{"n": 1, "file": "a.wav", "duration_s": 1.0}]}
    shot = {"scene": 0, "lines": [1], "hold_s": 0.5, "source": "generate", "prompt": "a hallway", "characters": [],
            "action": "a door opens", "clip_sound": None, "asset_id": None, "card_text": None, "caption": None,
            "motion": "still", "grade": "colour"}
    card = {**shot, "lines": [], "source": "card", "prompt": None, "card_text": "THE END", "hold_s": 18}
    shots = pipeline.time_plan({"shots": [shot, card]}, voice, max_shot_s=15, max_cards=1)
    assert shots[1]["seconds"] == pipeline.CARD_MAX_S  # an 18 s hold is cut to the card limit
    with pytest.raises(ValueError, match="title_card beat"):
        pipeline.time_plan({"shots": [shot, card]}, voice, max_shot_s=15, max_cards=0)
    with pytest.raises(ValueError, match="split it into more shots"):
        pipeline.time_plan({"shots": [{**shot, "hold_s": 18}]}, voice, max_shot_s=15, max_cards=0)


def test_preflight_and_stop(cfg, conn, monkeypatch):
    from aitv import jobs

    fmt = pipeline.load_format(cfg, "parody_commercial")
    assert pipeline.preflight(cfg, fmt, ["outline", "dance"]) == ["stage 'dance' isn't built yet"]
    cfg.raw["videogen"].update(enabled=True, api_key_env="AITV_TEST_NO_SUCH_KEY")
    assert any("AITV_TEST_NO_SUCH_KEY" in p for p in pipeline.preflight(cfg, fmt, ["render"]))

    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    conn.execute("UPDATE productions SET status = 'cancelled' WHERE id = ?", (pid,))
    ctx = pipeline.StageContext(conn, cfg, fmt, conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone(),
                                lambda line: None)
    with pytest.raises(jobs.Stopped):
        pipeline.check_stop(ctx)


def test_new_run_and_clear_remove_dead_jobs(cfg, conn):
    from aitv import jobs

    pipeline.create_production(conn, cfg, "parody_commercial", seed=3)  # production 1

    old = jobs.enqueue(conn, "production.run", production_id=1)
    jobs.fail(conn, old, "pick voices", status="blocked")
    tool = jobs.enqueue(conn, "voice.transcribe")
    jobs.fail(conn, tool, "Whisper isn't installed yet", status="blocked")
    broken = jobs.enqueue(conn, "ingest")
    jobs.fail(conn, broken, "boom")
    new = jobs.enqueue(conn, "production.run", production_id=1)
    ids = {r["id"] for r in conn.execute("SELECT id FROM jobs")}
    assert old not in ids and {tool, broken, new} <= ids  # the new run replaces the blocked one
    jobs.clear_dead(conn)
    assert {r["id"] for r in conn.execute("SELECT id FROM jobs")} == {tool, new}  # tool-blocked jobs stay


def test_worker_notices_code_changes(cfg, tmp_path, monkeypatch):
    from aitv import worker

    first = worker.code_fingerprint(cfg)
    assert first == worker.code_fingerprint(cfg)
    assert worker._imports_cleanly() is None


def test_stop_during_talkie_submits_still_waits_for_every_paid_job(cfg, conn, tmp_path, monkeypatch):
    from aitv import jobs, videogen
    from tests.conftest import make_media

    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    ctx = pipeline.StageContext(conn, cfg, pipeline.load_format(cfg, "parody_commercial"),
                                conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone(), lambda line: None)
    submitted, waited = [], []

    def submit_talkie(cfg_, frame, prompt, duration):
        submitted.append(prompt)
        conn.execute("UPDATE productions SET status = 'cancelled' WHERE id = ?", (pid,))  # Stop pressed mid-submit
        return {"id": f"t{len(submitted)}", "duration": duration}

    def wait(cfg_, job_id, dst, log=None):
        waited.append(job_id)
        make_media("clip", dst)
        return 0.05

    monkeypatch.setattr(videogen, "submit_talkie", submit_talkie)
    monkeypatch.setattr(videogen, "wait", wait)
    frame = make_media("image", tmp_path / "frame.png")
    shot = {"n": 1, "seconds": 5.0, "action": "a door opens", "prompt": "a hallway", "clip_sound": None,
            "motion": "still", "grade": "colour", "caption": None}
    pending = [(shot, frame, tmp_path / "a.mp4"), ({**shot, "n": 2}, frame, tmp_path / "b.mp4")]
    folder = tmp_path / "animated"
    folder.mkdir()
    pipeline._talkie(ctx, pending, folder, "", 30, 640, 480)
    assert waited == ["t1", "t2"]  # both submitted jobs were collected, none left paid-for and lost
    with pytest.raises(jobs.Stopped):  # the stop takes effect at the next check
        pipeline.check_stop(ctx)


def test_a_card_carries_only_its_own_reading():
    voice = {"lines": [{"n": 1, "file": "a.wav", "duration_s": 12.0},
                       {"n": 2, "file": "b.wav", "duration_s": 2.0, "card": True}]}
    card = {"scene": 0, "lines": [1], "hold_s": 1.0, "source": "card", "prompt": None, "characters": [], "action": None,
            "clip_sound": None, "asset_id": None, "card_text": "Toxic Horror Droplets", "caption": None,
            "motion": "still", "grade": "colour"}
    with pytest.raises(ValueError, match="spoken over it"):  # a 12 s pitch turned a card into 13 s of text
        pipeline.time_plan({"shots": [card, {**card, "lines": [2]}]}, voice, max_cards=2)
    ok = pipeline.time_plan({"shots": [{**card, "source": "generate", "prompt": "a jar"}, {**card, "lines": [2]}]},
                            voice, max_cards=1)
    assert ok[1]["placed"][0]["n"] == 2


def test_prices_and_phone_numbers_are_written_out_for_the_voice():
    from aitv.tts import spoken

    assert spoken("Just $3.99. Call 1-800-555-DROOL.") == "Just three ninety-nine. Call one, eight hundred, five five five, drool."
    assert spoken("$3, $1,200 or $0.99") == "three dollars, one thousand two hundred dollars or ninety-nine cents"
    assert spoken("1-800-COPE-BALDY") == "one, eight hundred, cope baldy"
    assert spoken("It's 100-percent ham since 1988") == "It's 100-percent ham since 1988"  # not a number to dial


def test_music_beats_in_one_scene_make_one_piece(cfg, conn, monkeypatch):
    music = lambda text: {"type": "music", "character": None, "text": text, "delivery": None}  # noqa: E731
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    prod = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
    fmt = pipeline.load_format(cfg, "parody_commercial")
    fmt["music"]["source"] = "midigen"  # the test config scores from the library
    ctx = pipeline.StageContext(conn, cfg, fmt, prod, lambda line: None, None)
    first = SCREENPLAY["scenes"][0]
    ctx.save("screenplay", {**SCREENPLAY, "scenes": [{**first, "beats": [music("Spooky muzak begins")] + first["beats"]
                                                      + [music("Muzak fades out with a final sting")]}]})
    ctx.save("plan", {"shots": [{"scene": 0, "start": 0.0, "seconds": 10.0}]})
    asked = []
    monkeypatch.setattr(pipeline, "_compose", lambda ctx_, si, bi, text, seconds, card: asked.append(text) or None)
    pipeline.stage_sound(ctx)
    assert asked == ["Spooky muzak begins Later in the same piece: Muzak fades out with a final sting"]


def _make(conn, cfg, label, tags=None):
    conn.execute("INSERT INTO ideas (title, body, format) VALUES ('Cope', 'A hotline for bald men.', 'freeform')")
    idea = conn.execute("SELECT MAX(id) FROM ideas").fetchone()[0]
    return pipeline.create_production(conn, cfg, "freeform", idea_id=idea, pins=tags, roll=False,
                                      options={"format_label": label})


def test_make_form_production_gives_the_writer_only_the_prompt_label_and_tags(cfg, conn):
    outline = {**OUTLINE, "new_subject": {**OUTLINE["new_subject"], "kind": "hotline"}}
    fake = llm.Fake({"outline": outline, "screenplay": SCREENPLAY})
    pid = _make(conn, cfg, "Parody commercial", {"mood": "sad"})
    with pytest.raises(pipeline.WaitingOnYou):
        pipeline.run_production(conn, cfg, pid, llm=fake)
    first = fake.calls[0]
    assert first["system"] == "You write for a homemade AI television station."  # no house rules, even by name
    assert "Format: Parody commercial\n" in first["prompt"] and "Tags the producer set:\n- mood: sad" in first["prompt"]
    assert "Only if this piece advertises or promotes something" in first["prompt"]
    rolls = json.loads(conn.execute("SELECT rolls FROM productions WHERE id = ?", (pid,)).fetchone()["rolls"])
    assert rolls["ad_type"] == {"value": "hotline", "pinned": False, "from": "outline"}
    assert conn.execute("SELECT type FROM entities WHERE name = 'Forget-O'").fetchone()["type"] == "hotline"


def test_make_form_piece_that_sells_nothing_has_no_subject_and_no_format_line(cfg, conn):
    fake = llm.Fake({"outline": {**OUTLINE, "new_subject": None}, "screenplay": SCREENPLAY})
    pid = _make(conn, cfg, "")
    with pytest.raises(pipeline.WaitingOnYou):
        pipeline.run_production(conn, cfg, pid, llm=fake)
    assert not fake.calls[0]["prompt"].startswith("Format:") and "Tags" not in fake.calls[0]["prompt"]
    rolls = json.loads(conn.execute("SELECT rolls FROM productions WHERE id = ?", (pid,)).fetchone()["rolls"])
    assert "ad_type" not in rolls and "entity_id" not in rolls["subject"]


def test_a_type_tag_decides_the_kind(cfg, conn):
    fake = llm.Fake({"outline": OUTLINE, "screenplay": SCREENPLAY})
    pid = _make(conn, cfg, "trailer", {"Type": "Fake Show"})
    with pytest.raises(pipeline.WaitingOnYou):
        pipeline.run_production(conn, cfg, pid, llm=fake)
    assert "Name the fake_show this piece is for" in fake.calls[0]["prompt"]
    assert "- Type: Fake Show" in fake.calls[0]["prompt"]
    rolls = json.loads(conn.execute("SELECT rolls FROM productions WHERE id = ?", (pid,)).fetchone()["rolls"])
    assert rolls["ad_type"]["value"] == "fake_show"
    assert conn.execute("SELECT type FROM entities WHERE name = 'Forget-O'").fetchone()["type"] == "fake_show"


def test_a_productions_model_pick_beats_config_but_never_for_music(cfg):
    cfg.raw["llm"]["stage_models"] = {"review": "google/gemini-3.8-flash", "music": "x/composer"}
    picks = {"models": {"outline": "deepseek/deepseek-v4-flash", "review": "qwen/omni", "music": "nope"}}
    assert pipeline.stage_model(cfg, "outline", picks) == "deepseek/deepseek-v4-flash"
    assert pipeline.stage_model(cfg, "review_2", picks) == "qwen/omni"
    assert pipeline.stage_model(cfg, "review_2", {}) == "google/gemini-3.8-flash"
    assert pipeline.stage_model(cfg, "music_0_-1", picks) == "x/composer"
    cfg.raw["llm"]["backend"] = "openai_compat"
    assert pipeline.effective_model(cfg, "describe", {}) == cfg.raw["llm"]["openai_compat"]["model_small"]
