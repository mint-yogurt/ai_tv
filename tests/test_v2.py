"""Pipeline v2 (docs/pipeline-v2-plan.md): spend and budgets, the EDL, QC, the editor and reviewer
stages with a fake model, and the footage index. Synthetic media only; no models, no paid calls."""

import copy
import shutil

import pytest

from aitv import db, edl, llm, media, pipeline, spend, transcribe
from aitv.jobs import Blocked

from .test_pipeline import OUTLINE, SCREENPLAY, _voice


def _production(cfg, conn, tmp_path, monkeypatch, edit_mode="off"):
    """The test_pipeline production (three shots: a captioned generated shot with the line, stock footage,
    a card), run to the end with the given [edit] mode. Returns its id."""
    from aitv import comfy, online

    from .conftest import make_media

    cfg.raw.setdefault("edit", {})["mode"] = edit_mode
    tone = make_media("tone", tmp_path / "tone.wav")
    fake = tmp_path / "pocket-tts"
    fake.write_text(f'#!/bin/sh\nwhile [ "$1" != "--output-path" ]; do shift; done\ncp {tone} "$2"\n')
    fake.chmod(0o755)
    cfg.raw["tools"]["pocket_tts"] = str(fake)
    _voice(conn, cfg, "Poots", tone)
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
    monkeypatch.setattr(comfy, "render", lambda cfg_, graph, out_dir: [make_media("image", out_dir / "r.png")])
    monkeypatch.setattr(comfy, "shutdown", lambda: None)
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=3)
    pipeline.save_answer(cfg, pid, "outline", OUTLINE, pipeline.OUTLINE_SCHEMA)
    first = SCREENPLAY["scenes"][0]
    titled = {**SCREENPLAY, "scenes": [{**first, "beats": first["beats"] + [
        {"type": "title_card", "character": None, "text": "Forget-O", "delivery": None}]}]}
    pipeline.save_answer(cfg, pid, "screenplay", titled, pipeline.SCREENPLAY_SCHEMA)
    pipeline.save_answer(cfg, pid, "casting", {"roles": [{"character": "Announcer", "voice": "Poots"}]},
                         pipeline.CASTING_SCHEMA)
    shot = {"scene": 0, "lines": [], "hold_s": 1.0, "source": "generate", "prompt": "a washing machine",
            "characters": [], "action": None, "clip_sound": None,
            "asset_id": None, "card_text": None, "caption": None, "motion": "push_in", "grade": "colour"}
    plan = {"title_style": "hard_sell",
            "shots": [{**shot, "lines": [1], "caption": "Forget-O: $4.99", "grade": "bw"},
                      {**shot, "source": "stock", "prompt": "laundromat"},
                      {**shot, "source": "card", "prompt": None, "card_text": "Forget-O\n1-800-FORGET", "motion": "still"}]}
    pipeline.save_answer(cfg, pid, "plan", plan, pipeline.PLAN_SCHEMA)
    pipeline.run_production(conn, cfg, pid)
    return pid


def _latest(conn, pid, kind):
    row = conn.execute("SELECT body FROM documents WHERE production_id = ? AND kind = ? ORDER BY version DESC",
                       (pid, kind)).fetchone()
    return db.loads(row["body"]) if row else None


def _ctx(cfg, conn, pid, llm_backend=None):
    prod = conn.execute("SELECT * FROM productions WHERE id = ?", (pid,)).fetchone()
    return pipeline.StageContext(conn, cfg, pipeline.load_format(cfg, prod["format"]), prod, lambda line: None, llm_backend)


# ---------------------------------------------------------------- A: spend and budgets

class Paid(llm.Fake):
    """A fake backend that reports a price, like OpenRouter's usage.cost."""

    def complete_json(self, *a, **kw):
        out = super().complete_json(*a, **kw)
        self.last_usage = {"model": kw.get("model") or "fake/model", "provider": "fake", "prompt_tokens": 100,
                           "completion_tokens": 50, "reasoning_tokens": None, "cost_usd": 0.004}
        return out


def test_paid_calls_are_recorded_and_a_spent_budget_blocks(cfg, conn):
    pid = pipeline.create_production(conn, cfg, "parody_commercial", pins={"ad_type": "product"}, seed=3)
    conn.execute("UPDATE productions SET budget_usd = 0.005 WHERE id = ?", (pid,))
    cfg.raw["llm"]["stage_models"] = {"outline": "fake/strong-writer"}
    backend = Paid({"outline": OUTLINE})
    ctx = _ctx(cfg, conn, pid, backend)
    ctx.stage = "outline"
    ctx._ask("s", "p", {"title": "outline"}, "strong", stage="outline")
    assert backend.calls[-1]["model"] == "fake/strong-writer"  # the stage's own model
    [row] = conn.execute("SELECT * FROM spend WHERE production_id = ?", (pid,)).fetchall()
    assert (row["stage"], row["what"], row["cost_usd"], row["tokens_in"]) == ("outline", "llm", 0.004, 100)
    ctx._ask("s", "p", {"title": "outline"}, "strong", stage="outline")
    assert conn.execute("SELECT spent_usd FROM productions WHERE id = ?", (pid,)).fetchone()[0] == pytest.approx(0.008)
    with pytest.raises(Blocked, match="budget is spent"):
        ctx._ask("s", "p", {"title": "outline"}, "strong", stage="outline")
    assert spend.by_stage(conn, pid) == [{"stage": "outline", "what": "llm", "calls": 2, "cost_usd": 0.008}]


def test_stage_models_match_numbered_stages(cfg, conn):
    pid = pipeline.create_production(conn, cfg, "parody_commercial", pins={"ad_type": "product"}, seed=3)
    cfg.raw["llm"]["stage_models"] = {"music": "m/music", "review": "m/eyes"}
    ctx = _ctx(cfg, conn, pid)
    assert ctx.stage_model("music_0_-1") == "m/music" and ctx.stage_model("review_2") == "m/eyes"
    assert ctx.stage_model("screenplay") is None


# ---------------------------------------------------------------- C: the EDL

def _tiny_edl(clip, line):
    return {"version": 1, "fps": 30, "width": 640, "height": 480,
            "sources": {"S1": {"kind": "clip", "path": str(clip), "duration": 2.0, "has_audio": True},
                        "L1": {"kind": "line", "path": str(line), "duration": 3.0, "text": "hello there", "has_audio": True}},
            "picture": [{"id": "p1", "src": "S1", "in": 0.0, "dur": 1.0, "speed": 1.0, "motion": "none", "grade": "none",
                         "transition": "cut", "transition_s": 0.0, "sound_db": None},
                        {"id": "p2", "src": "S1", "in": 0.5, "dur": 1.5, "speed": 1.0, "motion": "push_in", "grade": "warm",
                         "transition": "dissolve", "transition_s": 0.4, "sound_db": None}],
            "titles": [], "audio": [{"id": "a1", "src": "L1", "bus": "voice", "start": 0.0, "in": 0.0, "dur": None,
                                     "gain_db": 0.0, "fade_in": 0.0, "fade_out": 0.0}],
            "mix": {"duck": "expr", "duck_db": -2.5, "duck_ramp_s": 0.3, "music_db": -10.0, "sfx_db": -4.0,
                    "voice_lufs": -20.0, "limit": 0.35}}


def test_edl_validation_names_what_is_wrong(tmp_path):
    e = _tiny_edl(tmp_path / "c.mp4", tmp_path / "l.wav")
    assert edl.validate(e) == []
    assert edl.seconds(e) == 2.5 and edl.starts(e) == [0.0, 1.0]
    bad = copy.deepcopy(e)
    bad["picture"][1].update({"in": 0.0, "dur": 3.0})
    bad["picture"][1]["grade"] = "sepia"
    bad["audio"] = []
    errs = " | ".join(edl.validate(bad, bounds=(2.0, 3.0)))
    assert "needs 3.00s of S1" in errs and "grade must be" in errs and "missing L1" in errs and "runs 4.00s" in errs


def test_patches_apply_one_at_a_time_and_bad_ones_are_rejected(tmp_path):
    e = _tiny_edl(tmp_path / "c.mp4", tmp_path / "l.wav")
    ops = [{"op": "set", "target": "p1", "field": "dur", "value": 0.8},
           {"op": "set", "target": "p2", "field": "dur", "value": 9.0},          # longer than the source
           {"op": "set", "target": "a1", "field": "gain_db", "value": 3.0},
           {"op": "remove", "target": "a1"},                                    # every line must stay
           {"op": "move", "target": "p2", "after": None},
           {"op": "add_title", "title": {"text": "HELLO", "style": "hard_sell", "role": "title", "position": "center",
                                         "start": 0.2, "dur": 1.6, "anim": "slam"}},
           {"op": "set", "target": "mix", "field": "duck_db", "value": -3.0}]
    new, applied, rejected = edl.apply_patches(e, ops)
    assert [o["op"] for o in rejected] == ["set", "remove"]
    assert [p["id"] for p in new["picture"]] == ["p2", "p1"] and new["picture"][1]["dur"] == 0.8
    assert new["titles"][0]["id"] == "t1" and new["mix"]["duck_db"] == -3.0 and new["audio"][0]["gain_db"] == 3.0
    assert e["picture"][0]["dur"] == 1.0  # the original is untouched


def test_trimming_a_shot_ripples_the_sound_and_titles_after_it(tmp_path):
    e = _tiny_edl(tmp_path / "c.mp4", tmp_path / "l.wav")
    e["audio"][0]["start"] = 1.5  # the line plays over p2 (1.0-2.5)
    e["titles"] = [{"id": "t1", "text": "HI", "style": "hard_sell", "role": "title", "position": "center",
                    "start": 0.2, "dur": 0.6, "anim": "none"}]
    new, applied, _ = edl.apply_patches(e, [{"op": "set", "target": "p1", "field": "dur", "value": 0.9}], require_lines=False)
    assert applied and new["audio"][0]["start"] == 1.4 and new["titles"][0]["start"] == 0.2  # after the cut: moved; before: kept


def test_burned_in_titles_follow_their_shot_through_the_edit(tmp_path):
    e = _tiny_edl(tmp_path / "c.mp4", tmp_path / "l.wav")
    src = e["picture"][1]["src"] = "S2"
    e["sources"]["S2"] = dict(e["sources"][e["picture"][0]["src"]])
    e["baked_titles"] = [{"src": src, "start": 1.0, "dur": 1.5, "text": "CALL NOW", "style": None, "role": "caption",
                          "position": "lower"}]
    e["picture"] = [e["picture"][1], e["picture"][0]]  # the editor moved the shot first
    e["picture"][0]["dur"] = 0.8
    baked = [t for t in edl.facts(e)["titles"] if t["text"] == "CALL NOW"]
    assert [(t["start"], t["dur"]) for t in baked] == [(0.0, 0.8)]
    e["sources"]["S2"]["calm"] = True
    e["picture"][0]["motion"] = "still"  # the camera move that isn't one: still meant to stand still
    assert edl.facts(e)["calm"][0] == (0.0, 0.8)


def test_edl_renders_dissolves_moves_titles_and_the_duck(tmp_path):
    from .conftest import make_media

    clip = make_media("clip", tmp_path / "c.mp4")
    line = make_media("tone", tmp_path / "l.wav")
    e = _tiny_edl(clip, line)
    e["titles"] = [{"id": "t1", "text": "Forget-O", "style": "hard_sell", "role": "title", "position": "center",
                    "start": 0.2, "dur": 1.8, "anim": "slam"},
                   {"id": "t2", "text": "$4.99", "style": "hard_sell", "role": "caption", "position": "lower",
                    "start": 1.0, "dur": 1.4, "anim": "typewriter"}]
    e["picture"].append({"id": "p3", "src": "S1", "in": 0.0, "dur": 0.6, "speed": 2.0, "motion": "none", "grade": "bw",
                         "transition": "flash", "transition_s": 0.1, "sound_db": None})
    # A dissolve after a cut (a concat's output into an xfade) once failed on mismatched timebases.
    e["picture"].append({"id": "p4", "src": "S1", "in": 0.0, "dur": 0.8, "speed": 1.0, "motion": "none", "grade": "none",
                         "transition": "dissolve", "transition_s": 0.3, "sound_db": None})
    out = edl.render(e, tmp_path / "work", tmp_path / "out.mp4")
    info = media.probe(tmp_path / "out.mp4")
    assert info["has_audio"] and abs(info["duration_s"] - 3.9) < 0.15 and out["segments_rendered"] == 4
    assert (tmp_path / "work" / "voice.wav").is_file() and (tmp_path / "work" / "bed.wav").is_file()
    # One changed event re-renders one segment.
    e["picture"][1]["grade"] = "punchy"
    assert edl.render(e, tmp_path / "work", tmp_path / "out.mp4")["segments_rendered"] == 1


def test_default_edl_matches_todays_assembly(cfg, conn, tmp_path, monkeypatch):
    pid = _production(cfg, conn, tmp_path, monkeypatch, "auto")
    made = _latest(conn, pid, "edl")
    assert made["made_by"] == "auto" and edl.validate(made) == []
    assert [a["bus"] for a in made["audio"]].count("voice") == 1
    assert made["baked_titles"][0]["text"] == "Forget-O: $4.99"
    assert _latest(conn, pid, "assemble")["edl"] is True
    final = cfg.path("productions") / f"{pid:04d}" / "final.mp4"
    expected = (0.25 + 3.0 + 0.35 + 1.0) + (0.25 + 1.0) + pipeline.CARD_MIN_S
    assert abs(media.probe(final)["duration_s"] - expected) < 0.2
    report = _latest(conn, pid, "qc")
    assert all(c.get("error") is None for c in report["checks"].values()), report["checks"]
    assert report["issues"] == [], report["issues"]  # a clean piece raises no false alarms


# ---------------------------------------------------------------- B: QC

def test_qc_catches_a_buried_line_and_black(cfg, conn, tmp_path, monkeypatch):
    from aitv import editing

    pid = _production(cfg, conn, tmp_path, monkeypatch, "auto")
    ctx = _ctx(cfg, conn, pid)
    broken = copy.deepcopy(_latest(conn, pid, "edl"))
    line = next(a for a in broken["audio"] if a["bus"] == "voice")
    line["gain_db"] = -30.0                                            # buried
    noise = tmp_path / "noise.wav"
    media._run([media.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=8:a=0.5", str(noise)])
    broken["sources"]["N1"] = {"kind": "sound", "path": str(noise), "duration": 8.0, "has_audio": True}
    broken["audio"].append({"id": "a99", "src": "N1", "bus": "music", "start": 0.0, "in": 0.0, "dur": None,
                            "gain_db": 6.0, "fade_in": 0.0, "fade_out": 0.0})
    broken["sources"]["B1"] = {"kind": "black"}
    broken["picture"].insert(1, {"id": "p99", "src": "B1", "in": 0.0, "dur": 1.2, "speed": 1.0, "motion": "none",
                                 "grade": "none", "transition": "cut", "transition_s": 0.0, "sound_db": None})
    ctx.save("edl", broken)
    pipeline.stage_assemble(ctx)
    pipeline.stage_look(ctx)
    report = editing.run_qc(ctx, fix=False)
    cats = {i["category"] for i in report["issues"]}
    assert "masked_line" in cats, report["issues"]
    # A black picture event is planned (calm: 4.6-5.8 s), so it isn't flagged as dead picture.
    assert not [i for i in report["issues"] if i["category"] == "black" and 4.5 < (i["t"] or 0) < 5.9]


def test_transcript_matching_is_forgiving_about_spelling():
    words = [[1.0, 1.3, "Never"], [1.3, 1.5, "mind"], [1.5, 1.7, "the"], [1.7, 2.1, "curtains."], [5, 5.4, "Prime"],
             [5.4, 5.9, "Cheese."]]
    assert transcribe.match_score("Never mind the curtains!", transcribe.words_between(words, 0, 3)) == 1.0
    assert transcribe.find_phrase(words, "prime cheese") == [(5, 5.9, 1.0)]
    assert transcribe.match_score("only on chuptv", ["only", "on", "chuck", "tv"]) == pytest.approx(2 / 3)


# ---------------------------------------------------------------- D, E: the editor and the reviewer (fake models)

def test_llm_editor_writes_the_edl_and_a_bad_one_falls_back(cfg, conn, tmp_path, monkeypatch):
    from aitv import editing

    pid = _production(cfg, conn, tmp_path, monkeypatch, "auto")
    cfg.raw["edit"]["mode"] = "llm"
    default = _latest(conn, pid, "edl")
    answer = {"notes": "tighter", "picture": copy.deepcopy(default["picture"]), "titles": [
        {"id": "t1", "text": "FORGET-O", "style": "hard_sell", "role": "title", "position": "center", "start": 0.5,
         "dur": 2.0, "anim": "slam"}], "audio": [{k: v for k, v in a.items() if k in editing.AUDIO_EVENT["required"]}
                                                 for a in default["audio"]],
        "mix": {"duck": "expr", "duck_db": -3.0, "duck_ramp_s": 0.3, "music_db": -12.0, "sfx_db": -4.0}}
    answer["picture"][0]["dur"] = round(answer["picture"][0]["dur"] - 0.3, 2)
    backend = Paid({"edit": answer})
    ctx = _ctx(cfg, conn, pid, backend)
    ctx.stage = "edit"
    editing.stage_edit(ctx)
    made = _latest(conn, pid, "edl")
    assert made["made_by"] == "llm" and made["titles"][0]["anim"] == "slam" and made["mix"]["duck"] == "expr"
    assert backend.calls[0]["images"] and backend.calls[0]["model"] == "anthropic/claude-sonnet-5.5"
    # Nothing it was made from has changed: the edit is kept, no new call.
    editing.stage_edit(ctx)
    assert len(backend.calls) == 1 and _latest(conn, pid, "edl")["made_by"] == "llm"
    # New clips (here: the saved edit marked as made from something else): the old answer is thrown away
    # and the editor asked again. This answer drops a voice line, so after one repair round today's cut is used.
    pipeline.writing_paths(cfg, pid, "edit")["answer"].with_suffix(".inputs").write_text("an older render")
    ctx.save("edl", {**made, "base_key": "an older render"})
    backend.answers["edit"] = {**answer, "audio": [a for a in answer["audio"] if a["bus"] != "voice"]}
    editing.stage_edit(ctx)
    assert _latest(conn, pid, "edl")["made_by"].startswith("auto (the editor's EDL was rejected)")
    assert len(backend.calls) == 3


def test_review_applies_validated_fixes_and_re_renders(cfg, conn, tmp_path, monkeypatch):
    from aitv import editing

    pid = _production(cfg, conn, tmp_path, monkeypatch, "auto")
    cfg.raw["review"] = {"enabled": True, "max_rounds": 2, "proxy_height": 120}
    line = next(a for a in _latest(conn, pid, "edl")["audio"] if a["bus"] == "voice")
    fix = {"op": "set", "target": line["id"], "field": "gain_db", "value": 4.0, "after": None, "title": None, "audio": None}
    nonsense = {"op": "set", "target": "p1", "field": "dur", "value": 99.0, "after": None, "title": None, "audio": None}
    scores = {c: 3 for c in editing.CRITERIA}
    backend = Paid({"review": {"summary": "line is quiet", "scores": scores, "regenerate": [],
                               "notes": [{"t": 0.3, "problem": "quiet line", "severity": "blocking", "fix": [fix, nonsense]}]}})
    ctx = _ctx(cfg, conn, pid, backend)
    ctx.stage = "review"
    editing.stage_review(ctx)
    rounds = _latest(conn, pid, "review")["rounds"]
    assert len(rounds) == 2 and len(rounds[0]["applied"]) == 1 and rounds[0]["rejected"][0]["field"] == "dur"
    assert backend.calls[0]["video"] is not None and backend.calls[0]["model"] == cfg.section("llm")["stage_models"]["review"]
    assert "review 1" in _latest(conn, pid, "edl")["made_by"]
    assert (cfg.path("productions") / f"{pid:04d}" / "review" / "round_0.mp4").is_file()
    assert conn.execute("SELECT COUNT(*) FROM spend WHERE production_id = ? AND stage = 'review'", (pid,)).fetchone()[0] == 2
    # The same cut again: not reviewed twice.
    editing.stage_review(ctx)
    assert len(backend.calls) == 2
    # A new cut (the edit changed underneath, as after a re-render): the old rounds are dropped and it's reviewed again.
    ctx.save("edl", {**_latest(conn, pid, "edl"), "base_key": "an older render"})
    edited = _latest(conn, pid, "edl")
    edited["picture"][0]["dur"] = round(edited["picture"][0]["dur"] - 0.2, 2)
    ctx.save("edl", edited)
    pipeline.stage_assemble(ctx)
    pipeline.stage_look(ctx)
    cfg.raw["review"]["max_rounds"] = 1
    editing.stage_review(ctx)
    assert len(backend.calls) == 3 and len(_latest(conn, pid, "review")["rounds"]) == 1


# ---------------------------------------------------------------- G: footage

def test_the_planner_is_offered_footage_only_when_the_index_is_on(cfg):
    off = pipeline._plan_schema_without_footage()["properties"]["shots"]["items"]["properties"]["source"]["enum"]
    assert "footage" not in off and "footage" in pipeline.PLAN_SCHEMA["properties"]["shots"]["items"]["properties"]["source"]["enum"]


def test_footage_search_finds_the_spoken_line_word_timed(cfg, conn, tmp_path):
    from aitv import footage

    from .conftest import make_media

    clip = make_media("clip", tmp_path / "Home Improvement S01E02.mp4")
    fid = footage.register(conn, clip, title="Home Improvement - S01E02")
    fp = conn.execute("SELECT fingerprint FROM footage WHERE id = ?", (fid,)).fetchone()[0]
    # A transcript already in the shared cache, so no Whisper is needed.
    conn.execute("INSERT INTO transcripts (fingerprint, path, duration_s, model, language, segments, words) VALUES "
                 "(?, ?, 2, 'test', 'en', ?, ?)", (fp, str(clip), db.dumps([[0.2, 1.9, "Okay. More power!"]]),
                                                   db.dumps([[0.2, 0.5, "Okay."], [0.9, 1.2, "More"], [1.2, 1.8, "power!"]])))
    cfg.raw["tools"]["whisper"] = "/bin/true"  # "installed"; the cache answers first
    footage.index(conn, cfg, fid)
    [hit] = footage.search(conn, '"more power"')
    assert (hit["kind"], hit["start"], hit["end"], hit["exact"]) == ("said", 0.9, 1.8, True)
    assert footage.search(conn, "home improvement")[0]["kind"] == "shown"
    assert footage.search(conn, '"never mind the curtains"') == []


def test_the_make_forms_length_cast_and_voices_reach_the_stages(cfg, conn, monkeypatch):
    from aitv import editing

    vid = conn.execute("INSERT INTO entities (type, name, description) VALUES ('voice', 'Gravel', 'low')").lastrowid
    cid = conn.execute("INSERT INTO entities (type, name, description) VALUES ('character', 'Dr. Follicle', 'bald')").lastrowid
    pid = pipeline.create_production(conn, cfg, "parody_commercial", pins={"length_s": "20"},
                                     options={"length": {"min": 15, "max": 25}, "characters": [cid], "voices": [vid]})
    ctx = _ctx(cfg, conn, pid)
    assert pipeline.length_range(ctx) == (15.0, 25.0)
    picks = pipeline._producer_picks(ctx)
    assert "between 15 and 25 seconds" in picks and "Dr. Follicle: bald" in picks and "Gravel: low" in picks
    # The one unvoiced speaking role gets the one chosen voice.
    monkeypatch.setattr(pipeline, "_voice_id", lambda conn, name: 1 if name == "Gravel" else None)
    assert pipeline.propose_casting(conn, SCREENPLAY, ["Gravel"])["roles"][0]["voice"] == "Gravel"
    # The edit's limits: the plan's 0.75-1.25 window, narrowed to the asked range (with half a second's give).
    monkeypatch.setattr(edl, "seconds", lambda e: 29.0)
    assert editing._bounds(ctx, {}) == (21.75, 25.5)
    monkeypatch.setattr(edl, "seconds", lambda e: 40.0)  # out of reach: as short as the edit may go
    assert editing._bounds(ctx, {}) == (30.0, 31.0)
    monkeypatch.setattr(edl, "seconds", lambda e: 29.0)  # the lines alone run 26 s: the cut can't be shorter
    ctx.save("voice", {"lines": [], "total_s": 26.0})
    assert editing._bounds(ctx, {}) == (21.75, 27.0)
    # A production from before the form: no range, the old window.
    old = _ctx(cfg, conn, pipeline.create_production(conn, cfg, "parody_commercial"))
    assert pipeline.length_range(old) is None and pipeline._producer_picks(old) == ""


def test_review_fixes_still_apply_to_a_cut_longer_than_asked(cfg, conn, tmp_path, monkeypatch):
    from aitv import editing

    pid = _production(cfg, conn, tmp_path, monkeypatch, "auto")
    conn.execute("UPDATE productions SET options = ? WHERE id = ?", (db.dumps({"length": {"min": 1, "max": 1}}), pid))
    cfg.raw["review"] = {"enabled": True, "max_rounds": 1, "proxy_height": 120}
    line = next(a for a in _latest(conn, pid, "edl")["audio"] if a["bus"] == "voice")
    fix = {"op": "set", "target": line["id"], "field": "gain_db", "value": 4.0, "after": None, "title": None, "audio": None}
    backend = Paid({"review": {"summary": "quiet", "scores": {c: 3 for c in editing.CRITERIA}, "regenerate": [],
                               "notes": [{"t": 0.3, "problem": "quiet line", "severity": "blocking", "fix": [fix]}]}})
    ctx = _ctx(cfg, conn, pid, backend)
    ctx.stage = "review"
    editing.stage_review(ctx)
    assert len(_latest(conn, pid, "review")["rounds"][0]["applied"]) == 1


def test_every_line_ends_up_clear_of_the_bed_whatever_the_duck(tmp_path):
    from aitv import qc

    from .conftest import make_media

    clip = make_media("clip", tmp_path / "c.mp4")
    line = make_media("tone", tmp_path / "l.wav")
    noise = tmp_path / "noise.wav"
    media._run([media.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=3:a=0.5", str(noise)])
    e = _tiny_edl(clip, line)
    e["sources"]["N1"] = {"kind": "sound", "path": str(noise), "duration": 3.0, "has_audio": True}
    e["audio"].append({"id": "a2", "src": "N1", "bus": "sfx", "start": 0.0, "in": 0.0, "dur": None,
                       "gain_db": 6.0, "fade_in": 0.0, "fade_out": 0.0})
    for duck in ("expr", "sidechain"):  # sidechain never touched the effects bus
        e["mix"]["duck"] = duck
        edl.render(e, tmp_path / duck, tmp_path / f"{duck}.mp4")
        v, rate = qc._read_mono(tmp_path / duck / "voice.wav")
        b, _ = qc._read_mono(tmp_path / duck / "bed.wav")
        end = int(edl.facts(e)["lines"][0]["end"] * rate)
        assert qc._rms_db(v[:end]) - qc._rms_db(b[:end]) >= 6.5, duck


def test_the_editors_titles_never_cover_a_card():
    from aitv import editing

    e = {"picture": [{"src": "S1", "dur": 3.0}, {"src": "S2", "dur": 4.0}],
         "baked_titles": [{"src": "S2", "role": "card"}],
         "titles": [{"id": "t1", "text": "Super: ONLY $3.99", "start": 0.5, "dur": 2.0},
                    {"id": "t2", "text": "$3.99 - 1-800-555-DROOL", "start": 3.5, "dur": 2.0}]}
    editing._tidy_titles(e, lambda line: None)
    assert [(t["id"], t["text"]) for t in e["titles"]] == [("t1", "ONLY $3.99")]
