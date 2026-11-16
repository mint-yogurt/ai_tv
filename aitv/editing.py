"""The editor, the checks and the reviewer (pipeline v2, parts C to E): three stages and the EDL assembly.

- edit (after render): writes the production's EDL. [edit] mode = "off" skips it (today's assembly),
  "auto" makes the EDL that equals today's cut, "llm" has an editor model write it from contact sheets
  of every clip, the script, the voice lines' word times, the sounds and the music's beat grid.
- assemble: with mode "off", today's stage_assemble; otherwise the EDL is rendered (edl.render).
- qc (after look): qc.run on the final, saved as the "qc" document. Never stops the run.
- review (after qc): a video model watches a small copy of the final and writes notes with fixes as
  EDL patches; the patches that validate are applied, then assemble, look and qc run again, up to
  [review] max_rounds. Every round's video, notes and EDL are kept.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import edl as edl_mod
from . import media, qc, titles, transcribe
from .jobs import Blocked, Stopped


def mode(cfg) -> str:
    return str(cfg.section("edit").get("mode", "off"))


def _pdir(ctx) -> Path:
    from .pipeline import production_dir

    return production_dir(ctx.cfg, ctx.production["id"])


def _bounds(ctx, default: dict) -> tuple[float, float]:
    """How long an edit may run: around the planned length ([edit] min_ratio .. max_ratio of it)."""
    from .pipeline import length_range

    opts = ctx.cfg.section("edit")
    total = edl_mod.seconds(default)
    lo, hi = round(total * float(opts.get("min_ratio", 0.75)), 2), round(total * float(opts.get("max_ratio", 1.25)), 2)
    want = length_range(ctx)
    if want:  # the producer's range, as far as the edit can stretch or squeeze the plan toward it
        spoken = float((ctx.latest("voice") or {}).get("total_s") or 0)
        a, b = max(lo, want[0] - 0.5), min(hi, max(want[1] + 0.5, spoken + 1))  # never shorter than the lines
        if a > b:  # out of reach: get as close as the edit may
            a, b = (lo, min(hi, lo + 1)) if want[1] < lo else (max(lo, hi - 1), hi)
        lo, hi = round(a, 2), round(b, 2)
    return lo, hi


# ---------------------------------------------------------------- the edit stage

PICTURE_EVENT = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "src", "in", "dur", "speed", "motion", "grade", "transition", "transition_s", "sound_db"],
    "properties": {
        "id": {"type": "string"}, "src": {"type": "string"},
        "in": {"type": "number", "minimum": 0}, "dur": {"type": "number", "exclusiveMinimum": 0},
        "speed": {"type": "number", "minimum": 0.25, "maximum": 4},
        "motion": {"enum": edl_mod.MOTIONS}, "grade": {"enum": list(edl_mod.GRADES)},
        "transition": {"enum": edl_mod.TRANSITIONS}, "transition_s": {"type": "number", "minimum": 0},
        "sound_db": {"type": ["number", "null"]},
    },
}
TITLE_EVENT = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "text", "style", "role", "position", "start", "dur", "anim"],
    "properties": {
        "id": {"type": "string"}, "text": {"type": "string"}, "style": {"enum": list(titles.STYLES)},
        "role": {"enum": edl_mod.TITLE_ROLES}, "position": {"enum": edl_mod.POSITIONS},
        "start": {"type": "number", "minimum": 0}, "dur": {"type": "number", "exclusiveMinimum": 0},
        "anim": {"enum": edl_mod.ANIMS},
    },
}
AUDIO_EVENT = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "src", "bus", "start", "in", "dur", "gain_db", "fade_in", "fade_out"],
    "properties": {
        "id": {"type": "string"}, "src": {"type": "string"}, "bus": {"enum": edl_mod.BUSES},
        "start": {"type": "number", "minimum": 0}, "in": {"type": "number", "minimum": 0},
        "dur": {"type": ["number", "null"]}, "gain_db": {"type": "number", "minimum": -40, "maximum": 12},
        "fade_in": {"type": "number", "minimum": 0}, "fade_out": {"type": "number", "minimum": 0},
    },
}
MIX = {
    "type": "object", "additionalProperties": False,
    "required": ["duck", "duck_db", "duck_ramp_s", "music_db", "sfx_db"],
    "properties": {"duck": {"enum": edl_mod.DUCKS}, "duck_db": {"type": "number", "minimum": -12, "maximum": 0},
                   "duck_ramp_s": {"type": "number", "minimum": 0.05, "maximum": 2},
                   "music_db": {"type": "number", "minimum": -30, "maximum": 0},
                   "sfx_db": {"type": "number", "minimum": -30, "maximum": 6}},
}
EDIT_SCHEMA = {
    "title": "edit", "type": "object", "additionalProperties": False,
    "required": ["notes", "picture", "titles", "audio", "mix"],
    "properties": {"notes": {"type": "string"},
                   "picture": {"type": "array", "minItems": 1, "items": PICTURE_EVENT},
                   "titles": {"type": "array", "items": TITLE_EVENT},
                   "audio": {"type": "array", "items": AUDIO_EVENT}, "mix": MIX},
}

EDIT_RULES = """You are the editor. The shots are already made; you decide the cut. Write the whole edit
decision list (EDL) as JSON.

- picture: the shots in order. Each starts where the one before ends (no start times, no gaps). Use
  any picture source (clip, footage, still, card), as often as you like: trim it with in and dur, slow
  it or speed it (speed), push in or pan on it (motion), grade it, and choose the transition into it
  (cut; dissolve, flash or dip with transition_s). sound_db keeps a source's own sound under the shot
  (sources marked "has sound", at speed 1 only), else null.
- A clip that runs out holds on its last frame: don't ask for more of a source than it has.
- titles: text laid over the picture at an absolute time: role title (card lettering) or caption (the
  bottom super), a style from the list, a position and an animation (fade, slam, slide, typewriter).
  Cards and captions in the shot plan are already burned into the clips; don't title them again,
  and never lay a title over a card shot.
  A title is on screen at least 1.5 s and about 0.3 s per word.
- audio: every voice line (L sources) exactly once, on the voice bus, at the time it should be heard,
  with in = 0 and dur null. Sounds (A, K, F sources) go on the music or sfx bus, with in, dur (null =
  to the end of the source), fades and gain_db (0 = the level the mix sets).
- Never put a line where nothing on screen fits it; move the picture to the words, not the words off
  their pictures. Cut on the beat grid where there's music. Let gags breathe for a beat; cut away
  early from anything that drags.
- mix: duck "expr" dips the music and effects by duck_db under every line with duck_ramp_s ramps
  (-10 to -12: the words always win; anything shallower is raised to -10); "sidechain" is the old
  compressor. music_db and sfx_db set the beds under the voices. However you mix, any line still
  under the bed gets the bed pulled down further when it's rendered.
- Keep the ids of events you keep; give new events new ids (p, t, a + a number).
- notes: two or three sentences on what you changed and why.
"""


def stage_edit(ctx) -> None:
    m = mode(ctx.cfg)
    if m == "off":
        ctx.log("edit: [edit] mode is off, today's assembly cuts this piece")
        return
    default = edl_mod.from_plan(ctx)
    base = edl_mod.base_key(default)
    latest = ctx.latest("edl")
    if latest and latest.get("base_key") == base and latest.get("mode") == m:
        # Same clips, lines and sounds as last time: keep that edit (and any review fixes made to it).
        ctx.log(f"edit: nothing it was made from has changed, keeping it ({latest.get('made_by')})")
        return
    if m != "llm":
        ctx.save("edl", {**default, "made_by": "auto", "base_key": base, "mode": m})
        ctx.log(f"edit: the default EDL, {edl_mod.seconds(default):.1f}s")
        return
    from .pipeline import writing_paths

    answer = writing_paths(ctx.cfg, ctx.production["id"], "edit")["answer"]
    made_from = answer.with_suffix(".inputs")
    if answer.exists() and (not made_from.is_file() or made_from.read_text() != base):
        ctx.log("edit: the clips or lines changed since the saved edit was written; asking again")
        answer.unlink()
    edl, made_by = _llm_edit(ctx, default)
    made_from.write_text(base)
    version = ctx.save("edl", {**edl, "made_by": made_by, "base_key": base, "mode": m})
    ctx.log(f"edit v{version}: {made_by}, {len(edl['picture'])} picture event(s), {edl_mod.seconds(edl):.1f}s")


def _llm_edit(ctx, default: dict) -> tuple[dict, str]:
    from .pipeline import _system, save_answer

    work = _pdir(ctx) / "edit"
    work.mkdir(parents=True, exist_ok=True)
    edl = json.loads(json.dumps(default))
    _add_footage_candidates(ctx, edl)
    sheets = _clip_sheets(edl, work / "sheets")
    bounds = _bounds(ctx, default)
    script = ctx.latest("screenplay") or {}
    prompt = "\n\n".join([
        f"Screenplay:\n{json.dumps(script, indent=1, ensure_ascii=False)}",
        f"Sources (key, kind, length; the contact sheets show 6 frames of each picture source):\n"
        f"{edl_mod.sources_brief(edl)}",
        "Voice lines, word by word (seconds into the line):\n" + _line_words(ctx, edl),
        "Music beat grid:\n" + _beats(ctx, default),
        "Title styles:\n" + "\n".join(f"- {n}: {s['hint']}" for n, s in titles.STYLES.items()),
        f"Today's assembly as an EDL (your starting point):\n{edl_mod.brief(default)}",
        f"The cut must run {bounds[0]:.1f}-{bounds[1]:.1f}s.",
        "Write the edit.",
    ])
    system = (_system(ctx, "edit") or "").strip() + "\n\n" + EDIT_RULES
    answer = ctx.write("edit", system, prompt, EDIT_SCHEMA, images=sheets)
    candidate = {**edl, "picture": answer["picture"], "titles": answer["titles"], "audio": answer["audio"],
                 "mix": {**edl["mix"], **answer["mix"]}, "editor_notes": answer["notes"]}
    errs = edl_mod.validate(candidate, bounds=bounds)
    if errs and ctx.llm is not None:
        ctx.log(f"edit: the EDL doesn't work yet, asking for a fix: {'; '.join(errs[:5])}")
        answer = ctx._ask(system, f"{prompt}\n\nYour previous answer:\n{json.dumps(answer)}\n\nIt can't be rendered: "
                          f"{'; '.join(errs)}\nReply with the whole corrected edit.", EDIT_SCHEMA, "strong",
                          stage="edit", images=sheets)
        save_answer(ctx.cfg, ctx.production["id"], "edit", answer, EDIT_SCHEMA)
        candidate = {**edl, "picture": answer["picture"], "titles": answer["titles"], "audio": answer["audio"],
                     "mix": {**edl["mix"], **answer["mix"]}, "editor_notes": answer["notes"]}
        errs = edl_mod.validate(candidate, bounds=bounds)
    if errs:
        # The piece still gets made: today's cut, with the editor's attempt kept for a look.
        ctx.log(f"edit: the editor's EDL still can't be rendered ({'; '.join(errs[:5])}); using today's cut")
        (work / "rejected_edl.json").write_text(json.dumps(candidate, indent=1))
        return default, "auto (the editor's EDL was rejected)"
    _tidy_titles(candidate, ctx.log)
    return candidate, "llm"


def _tidy_titles(edl: dict, log) -> None:
    """The editor's titles, in place: direction labels taken off their text, and none over a card shot
    (the card's own lettering is already there; two texts on top of each other read as neither)."""
    cards = {b["src"] for b in edl.get("baked_titles", []) if b.get("role") == "card"}
    spans = [(t, t + p["dur"]) for p, t in zip(edl["picture"], edl_mod.starts(edl)) if p["src"] in cards]
    keep = []
    for title in edl.get("titles", []):
        title["text"] = titles.clean_text(title["text"]) or title["text"]
        a, b = title["start"], title["start"] + title["dur"]
        if any(min(b, e) - max(a, s) > 0.1 for s, e in spans):
            log(f"edit: dropped title {title['id']} ({title['text'][:40]!r}): it was over a card")
            continue
        keep.append(title)
    edl["titles"] = keep


def _clip_sheets(edl: dict, out: Path, per_sheet: int = 5) -> list[Path]:
    """Contact sheets of every clip and footage source: one row of 6 frames per source, 5 rows a sheet."""
    keys = [k for k, s in edl["sources"].items() if s["kind"] in ("clip", "footage") and s.get("path")]
    sheets = []
    for i in range(0, len(keys), per_sheet):
        chunk = keys[i:i + per_sheet]
        rows, labels = [], []
        for k in chunk:
            s = edl["sources"][k]
            a, b = s.get("window", (0.0, s.get("duration") or 1.0))
            rows.append([a + (b - a) * (j + 0.5) / 6 for j in range(6)])
            labels.append(k)
        sheet = out / f"sheet_{i // per_sheet + 1}.jpg"
        if not sheet.is_file():
            # one video per row: build each row separately, then stack
            parts = [qc.contact_sheet(Path(edl["sources"][k]["path"]), [r], out / f"row_{k}.jpg", [k])
                     for k, r in zip(chunk, rows)]
            _stack(parts, sheet)
        sheets.append(sheet)
    return sheets


def _stack(parts: list[Path], dst: Path) -> Path:
    from PIL import Image

    imgs = [Image.open(p).convert("RGB") for p in parts]
    out = Image.new("RGB", (max(i.width for i in imgs), sum(i.height for i in imgs)), (16, 16, 16))
    y = 0
    for img in imgs:
        out.paste(img, (0, y))
        y += img.height
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, quality=85)
    return dst


def _line_words(ctx, edl: dict) -> str:
    """Each voice line's words with their times, from Whisper on the line's own file (exact: one voice,
    nothing under it). Without Whisper, just the line and its length."""
    out = []
    for key, s in edl["sources"].items():
        if s["kind"] != "line":
            continue
        text = f"{key} ({s.get('duration') or 0:.2f}s) {s.get('character', '')}: {s.get('text', '')}"
        if transcribe.available(ctx.cfg):
            try:
                words = transcribe.transcribe(ctx.conn, ctx.cfg, Path(s["path"]))["words"]
                text += "\n    " + " ".join(f"{w[0]:.2f} {w[2]}" for w in words)
            except Exception as exc:  # noqa: BLE001  word times are a nicety
                ctx.log(f"edit: no word times for {key}: {exc}")
        out.append(text)
    return "\n".join(out) or "(no lines)"


def _beats(ctx, edl: dict) -> str:
    """Beat and bar times of every MIDIgen piece in the cut, from its composition's tempo."""
    from .pipeline import writing_paths

    sound = ctx.latest("sound") or {"cues": []}
    starts = {a["src"]: a["start"] for a in edl["audio"]}
    out = []
    for c in sound["cues"]:
        if c.get("found") != "midigen" or not c.get("asset_id"):
            continue
        comp_path = writing_paths(ctx.cfg, ctx.production["id"], f"music_{c['scene']}_{c['beat']}")["answer"]
        start = starts.get(f"A{c['asset_id']}")
        if start is None or not comp_path.is_file():
            continue
        comp = json.loads(comp_path.read_text())
        tempo = comp.get("tempo")
        if not isinstance(tempo, (int, float)) or tempo <= 0:
            continue
        per_bar = int(str(comp.get("time_signature", "4/4")).split("/")[0])
        beat = 60.0 / tempo
        bars = [round(start + i * per_bar * beat, 2) for i in range(int(comp.get("bars", 8)) + 1)]
        out.append(f"A{c['asset_id']} ({comp.get('title', 'music')}, {tempo} bpm, {per_bar}/4): beat every {beat:.3f}s; "
                   f"bars at {', '.join(f'{b:.2f}' for b in bars)}")
    return "\n".join(out) or "(no composed music)"


def _add_footage_candidates(ctx, edl: dict) -> None:
    """For each footage shot, the next-best search hits as extra sources (F1, F2...) the editor can swap in."""
    from . import footage

    plan = ctx.latest("plan") or {"shots": []}
    if not footage.enabled(ctx.cfg):
        return
    n = 0
    for shot in plan["shots"]:
        if shot.get("source") != "footage" or not shot.get("prompt"):
            continue
        for hit in footage.search(ctx.conn, shot["prompt"], int(ctx.cfg.section("footage").get("candidates", 3)) + 1)[1:]:
            n += 1
            a, b = max(0.0, hit["start"] - 2), hit["end"] + 3
            edl["sources"][f"F{n}"] = {"kind": "footage", "path": hit["path"], "duration": hit["duration"],
                                       "has_audio": True, "window": (a, b),
                                       "label": f"candidate for shot {shot['n']} ('{shot['prompt']}'): {hit['title']}, "
                                                f"{hit['kind']} at {hit['start']:.2f}s" + (f": {hit['text']}" if hit["text"] else "")}


# ---------------------------------------------------------------- assembly from the EDL

def assemble_edl(ctx) -> None:
    """Render the production's EDL (or the default one, if the edit stage hasn't made one) into
    assembled.mp4, the file the look stage finishes."""
    from .pipeline import SOUND_CUES

    edl = ctx.latest("edl")
    if edl is None:
        edl = {**edl_mod.from_plan(ctx), "made_by": "auto"}
        ctx.save("edl", edl)
    pdir = _pdir(ctx)
    result = edl_mod.render(edl, pdir / "edit", pdir / "assembled.mp4", ctx.log)
    root = ctx.cfg.path("productions")
    script, sound = ctx.latest("screenplay") or {"scenes": []}, ctx.latest("sound") or {"cues": []}
    placed_cues = {(c["scene"], c["beat"]) for c in sound["cues"] if c["asset_id"]}
    cues = [f"{b['type']}: {b['text']}" for si, sc in enumerate(script["scenes"]) for bi, b in enumerate(sc["beats"])
            if b["type"] in SOUND_CUES and (si, bi) not in placed_cues]
    body = {"file": str((pdir / "assembled.mp4").relative_to(root)), "seconds": result["seconds"],
            "chapters": edl.get("chapters", []), "missing_sound": cues, "edl": True}
    version = ctx.save("assemble", body)
    ctx.log(f"assemble v{version}: rendered the EDL ({edl.get('made_by', '?')}), {result['seconds']:.1f}s, "
            f"{result['segments_rendered']} segment(s) rendered")


# ---------------------------------------------------------------- QC

def stage_qc(ctx) -> None:
    opts = ctx.cfg.section("qc")
    if not opts.get("enabled", True):
        ctx.log("qc: off ([qc] enabled = false)")
        return
    try:
        report = run_qc(ctx)
    except Stopped:
        raise
    except Exception as exc:  # noqa: BLE001  QC never stops a run
        ctx.log(f"qc: couldn't run: {type(exc).__name__}: {exc}")
        ctx.save("qc", {"ok": None, "error": f"{type(exc).__name__}: {exc}", "issues": []})
        return
    ctx.save("qc", report)


def run_qc(ctx, fix: bool = True) -> dict:
    look = ctx.latest("look")
    if not look:
        raise Blocked("QC needs a finished piece")
    root, pdir = ctx.cfg.path("productions"), _pdir(ctx)
    final = root / look["file"]
    if mode(ctx.cfg) == "off":
        edl = edl_mod.from_plan(ctx)
        stems = {"voice": pdir / "audio" / "bus_voice.wav", "mix": pdir / "audio" / "mix.wav"}
    else:
        edl = ctx.latest("edl") or edl_mod.from_plan(ctx)
        stems = {"voice": pdir / "edit" / "voice.wav", "bed": pdir / "edit" / "bed.wav"}
    transcript = None
    if ctx.cfg.section("qc").get("whisper", True) and transcribe.available(ctx.cfg):
        try:
            transcript = transcribe.transcribe(ctx.conn, ctx.cfg, final, ctx.log)
        except Exception as exc:  # noqa: BLE001  the stem margins still run
            ctx.log(f"qc: Whisper failed, checking lines by level only: {exc}")
    spec = dict(media.LOOKS.get(look.get("preset", "clean"), media.LOOKS["clean"]))
    report = qc.run(ctx.cfg, final, edl, stems, pdir / "qc", ctx.log, transcript,
                    {"loudness": spec["loudness"], "true_peak": spec["true_peak"]})
    if transcript:
        report["transcript"] = " ".join(t for _, _, t in transcript["segments"])
    from .pipeline import length_range

    want = length_range(ctx)
    if want and not want[0] - 0.5 <= report["seconds"] <= want[1] + 0.5:
        asked = f"{want[0]:g}s" if want[0] == want[1] else f"{want[0]:g}-{want[1]:g}s"
        report["issues"].append({"check": "length", **qc.issue(None, "length", f"runs {report['seconds']:.1f}s; asked for {asked}",
                                                              "shorten the script or the holds")})
    # The one mechanical fix: peaks over the target get the look again with a peak limiter (no model).
    fixes = {i.get("fix") for i in report["issues"] if i["check"] == "loudness"}
    if fix and "limit" in fixes:
        assembled = ctx.latest("assemble")
        ctx.log("qc: peaks over the target, finishing again with a peak limiter")
        media.look(root / assembled["file"], final, look.get("preset", "clean"), limit_tp=spec["true_peak"] - 0.3)
        lufs, issues = qc._loudness(final, {"loudness": spec["loudness"], "true_peak": spec["true_peak"]}, qc.settings(ctx.cfg))
        report["checks"]["loudness"] = {"ok": not issues, **lufs, "fixed": True}
        report["issues"] = [i for i in report["issues"] if i["check"] != "loudness"] + [{"check": "loudness", **i} for i in issues]
        report["ok"] = all(c.get("ok") is not False for c in report["checks"].values())
    (pdir / "qc").mkdir(parents=True, exist_ok=True)
    (pdir / "qc" / "qc.json").write_text(json.dumps(report, indent=1))
    return report


# ---------------------------------------------------------------- review

CRITERIA = ["lines_audible", "titles_readable", "pacing", "cut_quality", "gags_land", "matches_script", "looks_finished"]
PATCH_OP = {
    "type": "object", "additionalProperties": False,
    "required": ["op", "target", "field", "value", "after", "title", "audio"],
    "properties": {
        "op": {"enum": ["set", "remove", "move", "add_title", "add_audio"]},
        "target": {"type": ["string", "null"]}, "field": {"type": ["string", "null"]},
        "value": {"type": ["number", "string", "boolean", "null"]}, "after": {"type": ["string", "null"]},
        "title": {"anyOf": [{"type": "null"}, TITLE_EVENT]}, "audio": {"anyOf": [{"type": "null"}, AUDIO_EVENT]},
    },
}
REVIEW_SCHEMA = {
    "title": "review", "type": "object", "additionalProperties": False,
    "required": ["summary", "scores", "notes", "regenerate"],
    "properties": {
        "summary": {"type": "string"},
        "scores": {"type": "object", "additionalProperties": False, "required": CRITERIA,
                   "properties": {c: {"type": "integer", "minimum": 1, "maximum": 5} for c in CRITERIA}},
        "notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["t", "problem", "severity", "fix"],
            "properties": {"t": {"type": ["number", "null"]}, "problem": {"type": "string"},
                           "severity": {"enum": ["blocking", "minor"]}, "fix": {"type": "array", "items": PATCH_OP}}}},
        "regenerate": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["event", "why", "new_action"],
            "properties": {"event": {"type": "string"}, "why": {"type": "string"}, "new_action": {"type": "string"}}}},
    },
}
REVIEW_RULES = """You are the reviewer. Watch (and listen to) the attached video: a finished piece of
slightly cursed television. The AI-made look is part of the joke; judge whether it WORKS: can every
line be heard, can every title be read, does the pacing hold, are the cuts clean, do the gags land,
does it follow the script, does it look finished.

- scores: 1-5 per criterion. summary: two sentences.
- notes: each problem you see or hear, with its time (seconds) and severity: blocking (a viewer would
  notice: a buried line, a title too short to read, a dead stretch, a bad cut) or minor (taste).
- fix: the EDL patch operations that would fix it, using the event ids in the EDL below:
  {"op": "set", "target": "<id or mix>", "field": "<field>", "value": <new value>} changes one field. Changing a
  picture event's dur (or removing it) ripples: every title and sound after it moves with it, so shortening a
  shot by 2 s pulls everything after it 2 s earlier
  (picture: in, dur, speed, motion, grade, transition, transition_s, sound_db, src; titles: text, start,
  dur, style, anim, position, role; audio: start, in, dur, gain_db, fade_in, fade_out, src, bus;
  mix: duck, duck_db, duck_ramp_s, music_db, sfx_db), "remove" drops an event, "move" puts a picture
  event after another (after = its id, null = first), "add_title" / "add_audio" add a whole event.
  Unused fields are null. Leave fix empty when no edit can fix it.
- regenerate: shots that no edit can save and that need a new clip (event id, why, a new one-sentence
  action for the video model). Use it rarely: a new clip costs money and needs approval.
- QC's automatic checks are below; don't repeat them unless you agree, and don't contradict their
  measurements (they're exact; you see one frame a second).
"""


def stage_review(ctx) -> None:
    from .pipeline import stage_assemble, stage_look

    opts = ctx.cfg.section("review")
    if not opts.get("enabled", False):
        ctx.log("review: off ([review] enabled = false)")
        return
    from .pipeline import writing_paths

    root, pdir = ctx.cfg.path("productions"), _pdir(ctx)
    previous = ctx.latest("review") or {}
    if previous.get("final_after") == transcribe.fingerprint(root / ctx.latest("look")["file"]):
        ctx.log("review: this exact cut was already reviewed")
        return
    # A new cut (a re-render, a changed edit): earlier rounds and their saved answers no longer apply.
    for old in writing_paths(ctx.cfg, ctx.production["id"], "review_1")["answer"].parent.glob("review_*.json"):
        old.unlink()
    rounds = []
    max_rounds = int(opts.get("max_rounds", 2))
    patching = mode(ctx.cfg) != "off"
    while len(rounds) < max_rounds:
        r = len(rounds) + 1
        look = ctx.latest("look")
        final = root / look["file"]
        keep = pdir / "review" / f"round_{r - 1}.mp4"
        keep.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final, keep)
        proxy = review_proxy(final, pdir / "review" / f"round_{r - 1}_proxy.mp4", int(opts.get("proxy_height", 360)))
        edl = ctx.latest("edl") or edl_mod.from_plan(ctx)
        answer = ctx.write(f"review_{r}", REVIEW_RULES, _review_prompt(ctx, edl, opts), REVIEW_SCHEMA, video=proxy)
        blocking = [n for n in answer["notes"] if n["severity"] == "blocking"]
        round_doc = {"round": r, "video": str(keep.relative_to(root)), "summary": answer["summary"],
                     "scores": answer["scores"], "notes": answer["notes"],
                     "regenerate": answer["regenerate"], "applied": [], "rejected": []}
        if answer["regenerate"]:
            # Spending on new clips isn't built: the shots are flagged for a person (max_regen_usd is for later).
            ctx.log(f"review: {len(answer['regenerate'])} shot(s) flagged for a new clip: "
                    + "; ".join(f"{g['event']}: {g['why']}" for g in answer["regenerate"]))
        ops = [op for n in answer["notes"] for op in n["fix"]]
        if not blocking or not patching or not ops:
            rounds.append(round_doc)
            ctx.save("review", {"rounds": rounds})
            ctx.log(f"review round {r}: {len(answer['notes'])} note(s), {len(blocking)} blocking"
                    + ("" if patching else " (notes only: [edit] mode is off)") + "; done")
            break
        lo, hi = _bounds(ctx, edl)
        now_s = edl_mod.seconds(edl)  # a cut already outside the limits may still take fixes that don't push it further out
        new, applied, rejected = edl_mod.apply_patches(edl, ops, bounds=(min(lo, now_s), max(hi, now_s)))
        round_doc.update(applied=applied, rejected=rejected)
        rounds.append(round_doc)
        ctx.save("review", {"rounds": rounds})
        ctx.log(f"review round {r}: {len(blocking)} blocking note(s), {len(applied)} fix(es) applied, {len(rejected)} rejected")
        if not applied:
            break
        ctx.save("edl", {**new, "made_by": f"{edl.get('made_by', '?')} + review {r}"})
        stage_assemble(ctx)
        stage_look(ctx)
        stage_qc(ctx)
    ctx.save("review", {"rounds": rounds, "final_after": transcribe.fingerprint(root / ctx.latest("look")["file"])})
    if rounds and len(rounds) == max_rounds:
        ctx.log(f"review: stopped after {max_rounds} round(s)")


def review_proxy(src: Path, dst: Path, height: int = 360) -> Path:
    """A small copy for the reviewer (sent as base64): low bitrate picture, mono sound."""
    media._run([media.FFMPEG, "-y", "-v", "error", "-i", str(src), "-vf", f"scale=-2:{height}", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                "-movflags", "+faststart", str(dst)])
    return dst


def _review_prompt(ctx, edl: dict, opts: dict) -> str:
    script = ctx.latest("screenplay") or {}
    report = ctx.latest("qc") or {}
    issues = "\n".join(f"- {i.get('t')}s {i['category']} ({i['severity']}): {i['problem']}" for i in report.get("issues", []))
    parts = [f"Screenplay:\n{json.dumps(script, indent=1, ensure_ascii=False)}",
             f"The EDL (event ids for your fixes):\n{edl_mod.brief(edl)}",
             f"Sources:\n{edl_mod.sources_brief(edl)}",
             f"QC's checks:\n{issues or '(no problems found)'}"]
    if opts.get("audio_as_text", False):  # for a reviewer that can't hear the video's sound
        lines = report.get("checks", {}).get("lines", {}).get("lines", [])
        parts.append("What the sound measures (you may not hear it):\n" + "\n".join(
            f"- {l['id']} @{l['start']:.2f}s, voice {l['margin_db']} dB above the bed"
            + (f", Whisper heard {l['heard']:.0%}" if l.get("heard") is not None else "") + f": {l['text']}" for l in lines)
            + (f"\nWhisper's transcript of the whole piece: {report['transcript']}" if report.get("transcript") else ""))
    parts.append("Review it.")
    return "\n\n".join(parts)
