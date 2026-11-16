"""The edit decision list (pipeline v2, part C): a production's whole edit as JSON, and its renderer.

The promos' hand-written build.py, made general. An EDL holds:

- sources: everything the edit can use, by key: the shot clips ("S3"), voice lines ("L4"), sounds
  ("A71", library assets), talkie soundtracks ("K2"), footage ("F1"), stills and cards. Each has an
  absolute path (cards have text instead), its length and whether it has sound.
- picture: the shots in order, each with a source, an in point, a length, speed, a camera move, a
  grade and the transition into it. Picture events have no start time: each starts where the one
  before ends, so there can never be a gap or an overlap.
- titles: text over the picture at an absolute time, in a titles.py style, with an animation.
- audio: voice lines, effects and music at absolute times on three buses (voice, music, sfx), with
  in points, lengths, fades and a gain.
- mix: how the voices are kept clear (today's sidechain duck, or a subtle volume dip with ramps, as on
  the promos), the music and effects levels, the final limiter.
- chapters: chapter markers at act breaks (shows only).

Every event has an id (p1, t1, a1...), so the editor and the reviewer's patches can name it.
The renderer is deterministic: the same EDL makes the same file. Picture segments are cached by
content, so changing one event re-renders one segment, then the cheap join and mix.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from . import media, titles

VERSION = 1  # bump when rendering changes, so cached segments are redone

MOTIONS = ["none", *media.MOTIONS]  # none = the footage as it is; the rest are slow camera moves
GRADES = {
    "none": "",
    "bw": "hue=s=0",
    "warm": "colorbalance=rs=0.06:gs=0.02:bs=-0.06,eq=saturation=1.1",
    "cool": "colorbalance=rs=-0.05:bs=0.07",
    "punchy": "eq=contrast=1.12:saturation=1.35",
    "faded": "eq=contrast=0.88:saturation=0.75:brightness=0.03",
    "glow": "split[g0][g1];[g1]gblur=sigma=8[g2];[g0][g2]blend=all_mode=screen:all_opacity=0.35",
}
TRANSITIONS = ["cut", "dissolve", "flash", "dip"]  # flash: through white; dip: through black
ANIMS = ["none", "fade", "slam", "slide", "typewriter"]
TITLE_ROLES = ["title", "caption"]  # title: card lettering over the picture; caption: the bottom super
POSITIONS = ["center", "upper", "lower"]
BUSES = ["voice", "music", "sfx"]
DUCKS = ["sidechain", "expr", "off"]
PICTURE_KINDS = {"clip", "footage", "still", "card", "black"}
AUDIO_KINDS = {"line", "sound", "track", "clip", "footage"}

# What a patch may change, per kind of target.
SETTABLE = {
    "picture": {"src", "in", "dur", "speed", "motion", "grade", "transition", "transition_s", "sound_db"},
    "titles": {"text", "style", "role", "position", "start", "dur", "anim"},
    "audio": {"src", "bus", "start", "in", "dur", "gain_db", "fade_in", "fade_out"},
    "mix": {"duck", "duck_db", "duck_ramp_s", "music_db", "sfx_db"},
}
EPS = 0.05


class EDLError(ValueError):
    pass


# ---------------------------------------------------------------- shape

def seconds(edl: dict) -> float:
    return round(sum(p["dur"] for p in edl["picture"]), 3)


def starts(edl: dict) -> list[float]:
    """Each picture event's start on the timeline."""
    out, t = [], 0.0
    for p in edl["picture"]:
        out.append(round(t, 3))
        t += p["dur"]
    return out


def cuts(edl: dict) -> list[dict]:
    """Every boundary between picture events: {"t", "transition", "before", "after"}."""
    st = starts(edl)
    return [{"t": st[i], "transition": p["transition"], "before": edl["picture"][i - 1]["id"], "after": p["id"]}
            for i, p in enumerate(edl["picture"]) if i]


def validate(edl: dict, bounds: tuple[float, float] | None = None, require_lines: bool = True) -> list[str]:
    """Everything wrong with an EDL, as plain sentences (empty when it can be rendered). bounds: the
    shortest and longest the whole piece may be."""
    errs = []
    src = edl.get("sources", {})
    ids = [e.get("id") for k in ("picture", "titles", "audio") for e in edl.get(k, [])]
    if len(ids) != len(set(ids)):
        errs.append("event ids must be unique")
    if not edl.get("picture"):
        return errs + ["the picture track is empty"]
    total = seconds(edl)
    prev = None
    for p in edl["picture"]:
        name = f"picture {p.get('id')}"
        s = src.get(p.get("src"))
        if s is None:
            errs.append(f"{name}: unknown source {p.get('src')!r}")
            prev = p
            continue
        if s["kind"] not in PICTURE_KINDS:
            errs.append(f"{name}: source {p['src']} is {s['kind']}, not a picture")
        if not p.get("dur") or p["dur"] < 1 / edl.get("fps", 30):
            errs.append(f"{name}: dur must be at least one frame")
        if not 0.25 <= p.get("speed", 1) <= 4:
            errs.append(f"{name}: speed must be between 0.25 and 4")
        if p.get("in", 0) < 0:
            errs.append(f"{name}: in can't be negative")
        if s["kind"] in ("clip", "footage") and s.get("duration") and p.get("dur"):
            used = p.get("in", 0) + p["dur"] * p.get("speed", 1)
            if used > s["duration"] + 0.5:
                errs.append(f"{name}: needs {used:.2f}s of {p['src']}, which is {s['duration']:.2f}s long")
        if p.get("motion") not in MOTIONS:
            errs.append(f"{name}: motion must be one of {', '.join(MOTIONS)}")
        if p.get("grade") not in GRADES:
            errs.append(f"{name}: grade must be one of {', '.join(GRADES)}")
        if p.get("transition") not in TRANSITIONS:
            errs.append(f"{name}: transition must be one of {', '.join(TRANSITIONS)}")
        elif p["transition"] != "cut":
            d = p.get("transition_s") or 0
            if d <= 0:
                errs.append(f"{name}: a {p['transition']} needs transition_s")
            elif prev is not None and p["transition"] == "dissolve" and d > min(p["dur"], prev["dur"]) * 0.5 + EPS:
                errs.append(f"{name}: a {d}s dissolve is longer than half of a shot on either side of it")
        if p.get("sound_db") is not None and (not s.get("has_audio") or abs(p.get("speed", 1) - 1) > 0.01):
            errs.append(f"{name}: sound_db only works on a source with sound, at speed 1")
        prev = p
    if bounds and not bounds[0] - EPS <= total <= bounds[1] + EPS:
        errs.append(f"the picture runs {total:.2f}s; it must be {bounds[0]:.1f}-{bounds[1]:.1f}s")
    for t in edl.get("titles", []):
        name = f"title {t.get('id')}"
        if not (t.get("text") or "").strip() or len(t["text"]) > 200:
            errs.append(f"{name}: text must be 1-200 characters")
        if t.get("style") not in titles.STYLES:
            errs.append(f"{name}: style must be one of {', '.join(titles.STYLES)}")
        if t.get("anim") not in ANIMS or t.get("role") not in TITLE_ROLES or t.get("position") not in POSITIONS:
            errs.append(f"{name}: anim, role or position isn't one of the allowed values")
        if t.get("start", -1) < 0 or not t.get("dur") or t["start"] + t["dur"] > total + EPS:
            errs.append(f"{name}: must start at 0 or later and end by {total:.2f}s")
    lines_used = []
    for a in edl.get("audio", []):
        name = f"audio {a.get('id')}"
        s = src.get(a.get("src"))
        if s is None:
            errs.append(f"{name}: unknown source {a.get('src')!r}")
            continue
        if s["kind"] not in AUDIO_KINDS:
            errs.append(f"{name}: source {a['src']} is {s['kind']}, it has no sound")
        if s["kind"] == "line":
            lines_used.append(a["src"])
            if a.get("bus") != "voice":
                errs.append(f"{name}: voice lines go on the voice bus")
        if a.get("bus") not in BUSES:
            errs.append(f"{name}: bus must be one of {', '.join(BUSES)}")
        if not 0 <= a.get("start", -1) < total:
            errs.append(f"{name}: start must be inside the piece (0-{total:.2f}s)")
        if a.get("in", 0) < 0 or (s.get("duration") and a.get("in", 0) >= s["duration"]):
            errs.append(f"{name}: in must be inside the source")
        if a.get("dur") is not None and a["dur"] <= 0:
            errs.append(f"{name}: dur must be positive, or null for the rest of the source")
        if not -40 <= a.get("gain_db", 0) <= 12:
            errs.append(f"{name}: gain_db must be between -40 and 12")
        if a.get("fade_in", 0) < 0 or a.get("fade_out", 0) < 0:
            errs.append(f"{name}: fades can't be negative")
    if require_lines:
        every = sorted(k for k, s in src.items() if s["kind"] == "line")
        if sorted(lines_used) != every:
            missing = sorted(set(every) - set(lines_used))
            twice = sorted({k for k in lines_used if lines_used.count(k) > 1})
            errs.append("every voice line must be used exactly once"
                        + (f"; missing {', '.join(missing)}" if missing else "") + (f"; twice {', '.join(twice)}" if twice else ""))
    mix = edl.get("mix", {})
    if mix.get("duck") not in DUCKS:
        errs.append(f"mix.duck must be one of {', '.join(DUCKS)}")
    if not -12 <= mix.get("duck_db", -2.5) <= 0 or not 0.05 <= mix.get("duck_ramp_s", 0.3) <= 2:
        errs.append("mix.duck_db must be -12..0 and duck_ramp_s 0.05..2")
    return errs


def check(edl: dict, **kw) -> dict:
    errs = validate(edl, **kw)
    if errs:
        raise EDLError("; ".join(errs))
    return edl


# ---------------------------------------------------------------- patches (the reviewer's fixes)

def _find(edl: dict, target: str) -> tuple[str, dict] | None:
    if target == "mix":
        return "mix", edl.setdefault("mix", {})
    for kind in ("picture", "titles", "audio"):
        for e in edl.get(kind, []):
            if e["id"] == target:
                return kind, e
    return None


def _next_id(edl: dict, kind: str) -> str:
    prefix = {"picture": "p", "titles": "t", "audio": "a"}[kind]
    used = {e["id"] for e in edl.get(kind, [])}
    n = len(used) + 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def ripple(edl: dict, at: float, delta: float) -> None:
    """Move every title and sound that starts at or after `at` by delta seconds, as an editing program's ripple
    edit does when a shot gets longer or shorter. Anything that would land before `at` lands on it."""
    for kind in ("titles", "audio"):
        for e in edl.get(kind, []):
            if e["start"] >= at - 0.01:
                e["start"] = round(max(at + min(0.0, delta), e["start"] + delta), 3)


def _apply_one(edl: dict, op: dict) -> None:
    kind = op["op"]
    if kind == "set":
        found = _find(edl, op["target"])
        if not found:
            raise EDLError(f"no event {op['target']}")
        where, event = found
        if op["field"] not in SETTABLE[where]:
            raise EDLError(f"{op['field']} can't be changed on {where}")
        if where == "picture" and op["field"] == "dur" and isinstance(op["value"], (int, float)):
            # A longer or shorter shot moves everything after it, so sound stays with its picture.
            end = starts(edl)[edl["picture"].index(event)] + event["dur"]
            ripple(edl, end, float(op["value"]) - event["dur"])
        event[op["field"]] = op["value"]
    elif kind == "remove":
        found = _find(edl, op["target"])
        if not found or found[0] == "mix":
            raise EDLError(f"no event {op['target']}")
        if found[0] == "picture":
            begin = starts(edl)[edl["picture"].index(found[1])]
            ripple(edl, begin + found[1]["dur"], -found[1]["dur"])
        edl[found[0]].remove(found[1])
    elif kind == "move":  # reorder a picture event: after another one, or first when after is null
        found = _find(edl, op["target"])
        if not found or found[0] != "picture":
            raise EDLError(f"only picture events move; {op['target']} isn't one")
        edl["picture"].remove(found[1])
        at = 0 if not op.get("after") else next(
            (i + 1 for i, p in enumerate(edl["picture"]) if p["id"] == op["after"]), None)
        if at is None:
            raise EDLError(f"no picture event {op['after']}")
        edl["picture"].insert(at, found[1])
    elif kind in ("add_title", "add_audio"):
        where = "titles" if kind == "add_title" else "audio"
        event = dict(op.get("title" if where == "titles" else "audio") or {})
        if not event:
            raise EDLError(f"{kind} needs the event")
        event["id"] = _next_id(edl, where)
        edl.setdefault(where, []).append(event)
    else:
        raise EDLError(f"unknown op {kind!r}")


def apply_patches(edl: dict, ops: list[dict], **validate_kw) -> tuple[dict, list[dict], list[dict]]:
    """Apply ops one at a time, keeping each only if the EDL still validates afterwards.
    Returns (new edl, applied ops, rejected ops with a "why")."""
    out, applied, rejected = copy.deepcopy(edl), [], []
    for op in ops:
        trial = copy.deepcopy(out)
        try:
            _apply_one(trial, op)
            errs = validate(trial, **validate_kw)
            if errs:
                raise EDLError("; ".join(errs[:3]))
        except (EDLError, KeyError, TypeError) as exc:
            rejected.append({**op, "why": str(exc)})
            continue
        out = trial
        applied.append(op)
    return out, applied, rejected


# ---------------------------------------------------------------- what QC needs to know

def facts(edl: dict) -> dict:
    """The timeline as QC reads it: when each line is spoken, where the planned cuts are, every title,
    and the stretches that are meant to be black or still."""
    st, src = starts(edl), edl["sources"]
    lines = []
    for a in edl.get("audio", []):
        s = src.get(a["src"], {})
        if s.get("kind") == "line":
            dur = a["dur"] if a.get("dur") is not None else (s.get("duration") or 0) - a.get("in", 0)
            lines.append({"id": a["id"], "src": a["src"], "start": a["start"], "end": round(a["start"] + dur, 3),
                          "text": s.get("text", ""), "character": s.get("character", "")})
    calm = []  # meant to be black or not moving: cards, black, stills without a move
    for p, t in zip(edl["picture"], st):
        kind = src.get(p["src"], {}).get("kind")
        if kind in ("card", "black") or (kind in ("still", "clip") and p["motion"] in ("none", "still")
                                          and (kind == "still" or src[p["src"]].get("calm"))):
            calm.append((t, t + p["dur"]))
    return {"seconds": seconds(edl), "lines": sorted(lines, key=lambda l: l["start"]), "cuts": cuts(edl),
            "titles": [dict(t) for t in edl.get("titles", [])] + _baked(edl, st),
            "calm": calm,
            "cards": [(t, t + p["dur"], src[p["src"]].get("text", "")) for p, t in zip(edl["picture"], st)
                      if src.get(p["src"], {}).get("kind") == "card"]}


def _baked(edl: dict, st: list[float]) -> list[dict]:
    """Titles burned into a shot's clip, where the edit shows them: on every picture event that uses
    that shot, for as long as it's on screen. (One with no shot source keeps its planned time.)"""
    out = []
    for t in edl.get("baked_titles", []):
        if not t.get("src"):
            out.append({**t, "id": None})
            continue
        out += [{**t, "id": None, "start": at, "dur": p["dur"]} for p, at in zip(edl["picture"], st) if p["src"] == t["src"]]
    return out


def brief(edl: dict) -> str:
    """A compact text view of an EDL for a model to read (the editor's starting point, the reviewer)."""
    st = starts(edl)
    out = ["PICTURE (each starts where the one before ends):"]
    for p, t in zip(edl["picture"], st):
        out.append(f"  {p['id']} @{t:.2f}s {p['dur']:.2f}s {p['src']} in={p['in']:.2f} speed={p['speed']} "
                   f"motion={p['motion']} grade={p['grade']} transition={p['transition']}"
                   + (f"/{p['transition_s']}s" if p["transition"] != "cut" else "")
                   + (f" sound={p['sound_db']}dB" if p.get("sound_db") is not None else ""))
    out.append("TITLES:" if edl.get("titles") else "TITLES: none")
    for t in edl.get("titles", []):
        out.append(f"  {t['id']} @{t['start']:.2f}s {t['dur']:.2f}s {t['role']} {t['style']} {t['anim']} "
                   f"{t['position']}: {t['text']!r}")
    out.append("AUDIO:")
    for a in sorted(edl.get("audio", []), key=lambda a: a["start"]):
        out.append(f"  {a['id']} @{a['start']:.2f}s {a['bus']} {a['src']} in={a['in']:.2f} "
                   f"dur={_dur_text(a.get('dur'))} gain={a['gain_db']}dB"
                   + (f" fades={a['fade_in']}/{a['fade_out']}" if a.get("fade_in") or a.get("fade_out") else ""))
    m = edl.get("mix", {})
    out.append(f"MIX: duck={m.get('duck')} {m.get('duck_db')}dB ramps {m.get('duck_ramp_s')}s, "
               f"music {m.get('music_db')}dB, sfx {m.get('sfx_db')}dB")
    return "\n".join(out)


def _dur_text(dur: float | None) -> str:
    return "rest" if dur is None else f"{dur:.2f}"


def sources_brief(edl: dict) -> str:
    out = []
    for key, s in edl["sources"].items():
        bits = [key, s["kind"]]
        if s.get("duration"):
            bits.append(f"{s['duration']:.2f}s")
        if s.get("has_audio"):
            bits.append("has sound")
        label = s.get("text") or s.get("label") or ""
        if s["kind"] == "line":
            label = f"{s.get('character', '')}: {label}"
        out.append(" ".join(bits) + (f" - {label}" if label else ""))
    return "\n".join(out)


# ---------------------------------------------------------------- the default EDL: today's assembly

def from_plan(ctx) -> dict:
    """The EDL that reproduces stage_assemble's cut: the rendered clips end to end, every line at its
    planned time, the cue placements, the talkie soundtracks, today's sidechain duck and the chapters."""
    from .pipeline import _cue_placements

    plan, render, script, voice = ctx.latest("plan"), ctx.latest("render"), ctx.latest("screenplay"), ctx.latest("voice")
    if not plan or not render:
        raise EDLError("an EDL needs a shot plan and rendered clips")
    root = ctx.cfg.path("productions")
    levels, rcfg = ctx.cfg.section("mix"), ctx.cfg.section("render")
    images = ctx.cfg.section("images")
    sources, picture, audio = {}, [], []
    plan_shots = {s["n"]: s for s in plan["shots"]}
    for r in render["shots"]:
        key, path = f"S{r['n']}", root / r["clip"]
        shot = plan_shots.get(r["n"], {})
        sources[key] = {"kind": "clip", "path": str(path), "duration": r["seconds"], "has_audio": False,
                        "label": _shot_label(shot)}
        if (not r.get("animated") and not r.get("asset_id") and shot.get("motion") == "still") or shot.get("source") == "card":
            sources[key]["calm"] = True  # a card or a still with no camera move: meant to stand still
        picture.append({"id": f"p{r['n']}", "src": key, "in": 0.0, "dur": r["seconds"], "speed": 1.0, "motion": "none",
                        "grade": "none", "transition": "cut", "transition_s": 0.0, "sound_db": None})
        track = path.with_suffix(".wav")
        if (r.get("talkie") or r.get("own_sound")) and track.is_file():
            sources[f"K{r['n']}"] = {"kind": "track", "path": str(track), "duration": r["seconds"], "has_audio": True,
                                     "label": f"shot {r['n']}'s own soundtrack", "level": False}
            audio.append(_audio(f"K{r['n']}", "sfx", shot.get("start", 0.0), dur=r["seconds"],
                                gain_db=float(levels.get("clip_db", -3.0)), fade_out=0.4))
    by_n = {l["n"]: l for l in (voice or {}).get("lines", [])}
    for s in plan["shots"]:
        for p in s["placed"]:
            line = by_n.get(p["n"], {})
            sources[f"L{p['n']}"] = {"kind": "line", "path": str(root / p["file"]), "duration": line.get("duration_s"),
                                     "has_audio": True, "text": line.get("text", ""), "character": line.get("character", ""),
                                     "n": p["n"]}
            audio.append(_audio(f"L{p['n']}", "voice", p["at"]))
    total = sum(s["seconds"] for s in plan["shots"])
    sound = ctx.latest("sound") or {"cues": []}
    for path, at, kind, limit in _cue_placements(ctx, script, plan, voice, sound, total):
        key = _sound_key(ctx, path, sources)
        audio.append(_audio(key, "music" if kind == "music" else "sfx", at, dur=limit, fade_out=0.4))
    for i, a in enumerate(audio, 1):
        a["id"] = f"a{i}"
    duck = levels.get("duck", {})
    edl = {"version": VERSION, "fps": int(rcfg.get("fps", 30)), "width": int(images.get("width", 640)),
           "height": int(images.get("height", 480)), "sources": sources, "picture": picture, "titles": [], "audio": audio,
           "mix": {"duck": "sidechain", "duck_db": -2.5, "duck_ramp_s": 0.3,
                   # Floors the editor can't go under: the expr duck at least this deep, and every line at
                   # least this far over the bed (a little over QC's own limit).
                   "min_duck_db": float(levels.get("min_duck_db", -10.0)),
                   "voice_margin_db": float(ctx.cfg.section("qc").get("min_voice_margin_db", 6.0)) + 1.0,
                   "music_db": float(levels.get("music_db", -10.0)), "sfx_db": float(levels.get("sfx_db", -4.0)),
                   "voice_lufs": float(levels.get("voice_lufs", -20.0)), "limit": float(duck.get("limit", 0.35)),
                   "sidechain": {k: duck[k] for k in ("threshold", "ratio", "attack_ms", "release_ms") if k in duck}},
           "chapters": _chapters(script, plan, total),
           # Cards and captions today's render burns into the shot clips: not drawn again, but QC checks them.
           "baked_titles": [{"src": f"S{s['n']}", "start": s["start"], "dur": s["seconds"], "text": s["card_text"] if s["source"] == "card"
                             else s["caption"], "style": plan.get("title_style"),
                             "role": "card" if s["source"] == "card" else "caption", "position": "lower"}
                            for s in plan["shots"] if (s["source"] == "card" and s.get("card_text")) or s.get("caption")]}
    return edl


def base_key(edl: dict) -> str:
    """What an edit was made from: the default EDL's sources and events. A new render, voice or sound
    changes it, which is how a saved edit or review knows it's out of date."""
    return _key(edl["sources"], edl["picture"], edl.get("audio", []), edl.get("chapters", []))


def _audio(src: str, bus: str, start: float, dur: float | None = None, gain_db: float = 0.0,
           fade_out: float = 0.0) -> dict:
    """An audio event. Today's mix fades every placement with a length limit out over its last 0.4 s."""
    return {"id": "", "src": src, "bus": bus, "start": round(start, 3), "in": 0.0,
            "dur": None if dur is None else round(dur, 3), "gain_db": gain_db, "fade_in": 0.0, "fade_out": fade_out}


def _sound_key(ctx, path: Path, sources: dict) -> str:
    row = ctx.conn.execute("SELECT id, title, category, duration_s FROM assets WHERE path = ?",
                           (str(Path(path).relative_to(ctx.cfg.library)),)).fetchone()
    key = f"A{row['id']}" if row else f"X{len(sources) + 1}"
    sources.setdefault(key, {"kind": "sound", "path": str(path), "duration": row["duration_s"] if row else None,
                             "has_audio": True, "label": f"{row['category']}: {row['title']}" if row else Path(path).name})
    return key


def _shot_label(shot: dict) -> str:
    what = shot.get("card_text") or shot.get("action") or shot.get("prompt") or ""
    return f"shot {shot.get('n')} ({shot.get('source')}, scene {shot.get('scene')}): {what}"[:160]


def _chapters(script: dict | None, plan: dict, total: float) -> list[dict]:
    """The same chapter rule as stage_assemble: a chapter ends after the last shot of every scene marked
    act_break_after."""
    if not script:
        return []
    chapters, start = [], 0.0
    breaks = {i for i, sc in enumerate(script["scenes"]) if sc.get("act_break_after")}
    for i, shot in enumerate(plan["shots"]):
        last_of_scene = i + 1 == len(plan["shots"]) or plan["shots"][i + 1]["scene"] != shot["scene"]
        if shot["scene"] in breaks and last_of_scene:
            end = shot["start"] + shot["seconds"]
            chapters.append({"start": start, "end": end, "title": f"Part {len(chapters) + 1}"})
            start = end
    if chapters and start < total:
        chapters.append({"start": start, "end": total, "title": f"Part {len(chapters) + 1}"})
    return chapters


# ---------------------------------------------------------------- rendering

def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _file_id(path: str | None) -> list:
    if not path:
        return []
    st = Path(path).stat()
    return [path, st.st_size, st.st_mtime_ns]


def render(edl: dict, work: Path, out: Path, log=lambda line: None, **validate_kw) -> dict:
    """Render an EDL to an mp4 (picture, titles, mixed sound, chapters). Writes the stems next to the mix
    in work/ (voice.wav, bed.wav, mix.wav) for QC. Returns {"file", "seconds", "segments_rendered"}."""
    check(edl, **validate_kw)
    work.mkdir(parents=True, exist_ok=True)
    (work / "segments").mkdir(exist_ok=True)
    picture, made = _picture(edl, work, log)
    if edl.get("titles"):
        picture = _titles(edl, picture, work, log)
    mix = _mix(edl, work, log)
    chapters = [(c["start"], c["end"], c["title"]) for c in edl.get("chapters", [])] or None
    media.mux(picture, mix, out, chapters)
    return {"file": str(out), "seconds": seconds(edl), "segments_rendered": made}


def _picture(edl: dict, work: Path, log) -> tuple[Path, int]:
    fps, w, h, src = edl["fps"], edl["width"], edl["height"], edl["sources"]
    events = edl["picture"]
    segs, made = [], 0
    for i, p in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        pre = p["transition_s"] / 2 if i and p["transition"] == "dissolve" else 0.0
        post = nxt["transition_s"] / 2 if nxt and nxt["transition"] == "dissolve" else 0.0
        # Flash and dip are drawn inside the two shots either side of the cut.
        fin = (p["transition"], p["transition_s"]) if i and p["transition"] in ("flash", "dip") else None
        fout = (nxt["transition"], nxt["transition_s"]) if nxt and nxt["transition"] in ("flash", "dip") else None
        s = src[p["src"]]
        key = _key(VERSION, fps, w, h, s, _file_id(s.get("path")), {k: v for k, v in p.items() if k != "id"},
                   pre, post, fin, fout)
        dst = work / "segments" / f"{key}.mp4"
        if not dst.is_file():
            _segment(s, p, pre, post, fin, fout, fps, w, h, dst, work)
            made += 1
        segs.append((dst, pre, post, p))
    log(f"picture: {len(segs)} segment(s), {made} rendered, {len(segs) - made} from the cache")
    out = work / "picture.mp4"
    if all(p["transition"] != "dissolve" for _, _, _, p in segs):
        return media.concat_video([d for d, *_ in segs], out), made
    # Dissolves: join in one graph, concat at cuts, xfade at dissolves.
    args = [media.FFMPEG, "-y", "-v", "error"]
    for d, *_ in segs:
        args += ["-i", str(d)]
    # One timebase for every input and every join: xfade refuses inputs whose timebases differ (a concat's
    # output and a file's).
    graph = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[s{i}]" for i in range(len(segs))]
    acc, t = "[s0]", segs[0][3]["dur"]
    for i in range(1, len(segs)):
        p = segs[i][3]
        label = f"[j{i}]"
        if p["transition"] == "dissolve":
            d = p["transition_s"]
            graph.append(f"{acc}[s{i}]xfade=transition=fade:duration={d:.3f}:offset={t - d / 2:.3f},settb=AVTB{label}")
        else:
            graph.append(f"{acc}[s{i}]concat=n=2:v=1:a=0,settb=AVTB{label}")
        acc, t = label, t + p["dur"]
    graph.append(f"{acc}format=yuv420p[out]")
    media._run(args + ["-filter_complex", ";".join(graph), "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "16", "-r", str(fps), str(out)])
    return out, made


def _segment(s: dict, p: dict, pre: float, post: float, fin, fout, fps: int, w: int, h: int, dst: Path, work: Path) -> None:
    """One picture event as a silent clip of exactly (pre + dur + post) seconds."""
    length = pre + p["dur"] + post
    frames = max(1, round(length * fps))
    kind = s["kind"]
    if kind in ("still", "card", "black"):
        if kind == "card":
            still = work / "segments" / f"card_{_key(s.get('text'), s.get('style'), titles.VERSION, w, h)}.png"
            if not still.is_file():
                titles.card(s.get("text", ""), s.get("style"), still, w, h)
        elif kind == "black":
            still = work / "segments" / f"black_{w}x{h}.png"
            if not still.is_file():
                from PIL import Image

                Image.new("RGB", (w, h), (0, 0, 0)).save(still)
        else:
            still = Path(s["path"])
        move = p["motion"] if p["motion"] != "none" else "still"
        z, x, y = (e.replace("N", str(frames)) for e in media.MOTIONS[move])
        vf = (f"scale={w * 3}:{h * 3}:force_original_aspect_ratio=increase,crop={w * 3}:{h * 3},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}")
        inputs = ["-loop", "1", "-i", str(still)]
    else:
        speed = p["speed"]
        start = p["in"] - pre * speed
        hold_head = max(0.0, -start)  # not enough source before the in point: hold the first frame
        vf = f"setpts=(PTS-STARTPTS)/{speed},fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
        if p["motion"] != "none":
            z, x, y = (e.replace("N", str(frames)) for e in media.MOTIONS[p["motion"]])
            vf += f",scale={w * 2}:{h * 2},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps}"
        if hold_head:
            vf += f",tpad=start_mode=clone:start_duration={hold_head:.3f}"
        vf += f",tpad=stop_mode=clone:stop_duration={length:.3f}"  # short source: hold the last frame
        inputs = ["-ss", f"{max(0.0, start):.3f}", "-i", s["path"]]
    grade = GRADES[p["grade"]]
    graph = f"[0:v]{vf}" + (f",{grade}" if grade and not grade.startswith("split") else "")
    if grade.startswith("split"):
        graph += f"[pre];[pre]{grade}"
    for fade, at_start in ((fin, True), (fout, False)):
        if fade:
            colour = "white" if fade[0] == "flash" else "black"
            d = min(fade[1], length / 2)
            st = 0 if at_start else max(0.0, length - d)
            graph += f",fade=t={'in' if at_start else 'out'}:st={st:.3f}:d={d:.3f}:color={colour}"
    graph += ",format=yuv420p,setsar=1[out]"
    media._run([media.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]", "-an",
                "-frames:v", str(frames), "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(dst)])


def _titles(edl: dict, picture: Path, work: Path, log) -> Path:
    """Draw every title frame by frame with Pillow (animations included) into a transparent track, then
    lay it over the picture."""
    from PIL import Image

    fps, w, h = edl["fps"], edl["width"], edl["height"]
    total = seconds(edl)
    key = _key(VERSION, titles.VERSION, fps, w, h, total, edl["titles"])
    track = work / "segments" / f"titles_{key}.mov"
    if not track.is_file():
        layers = {}
        for t in edl["titles"]:
            if t["role"] == "caption":
                tmp = work / "segments" / f"cap_{_key(t['text'], t['style'], titles.VERSION, w, h)}.png"
                if not tmp.is_file():
                    titles.caption(t["text"], t["style"], tmp, w, h)
                layers[t["id"]] = Image.open(tmp).convert("RGBA")
            else:
                layers[t["id"]] = titles.overlay(t["text"], t["style"], w, h, t["position"])
        frames = round(total * fps)
        proc = subprocess.Popen([media.FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
                                 "-r", str(fps), "-i", "-", "-c:v", "png", "-pix_fmt", "rgba", str(track)],
                                stdin=subprocess.PIPE)
        blank = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for f in range(frames):
            now = f / fps
            frame = blank
            for t in edl["titles"]:
                age = now - t["start"]
                if 0 <= age < t["dur"]:
                    frame = Image.alpha_composite(frame, _animate(layers[t["id"]], t, age, w, h))
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise media.MediaError("drawing the title track failed")
        log(f"titles: drew {len(edl['titles'])} title(s) over {frames} frames")
    out = work / "picture_titled.mp4"
    media._run([media.FFMPEG, "-y", "-v", "error", "-i", str(picture), "-i", str(track), "-filter_complex",
                "[0:v][1:v]overlay=0:0:eof_action=pass,format=yuv420p[out]", "-map", "[out]", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "16", str(out)])
    return out


def _animate(layer, t: dict, age: float, w: int, h: int):
    """One frame of a title: the layer faded, slammed (scaled down from big with a bounce), slid in from
    the side, or typed out, then faded out at the end."""
    from PIL import Image

    def scaled(img, k: float):
        """The layer scaled about the frame's centre, cropped back to the frame."""
        if abs(k - 1) < 0.001:
            return img
        sw, sh = max(1, round(w * k)), max(1, round(h * k))
        big = img.resize((sw, sh), Image.LANCZOS)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if k <= 1:
            canvas.alpha_composite(big, ((w - sw) // 2, (h - sh) // 2))
        else:
            left, top = (sw - w) // 2, (sh - h) // 2
            canvas.alpha_composite(big.crop((left, top, left + w, top + h)))
        return canvas

    def faded(img, a: float):
        if a >= 0.999:
            return img
        out = img.copy()
        out.putalpha(img.getchannel("A").point(lambda v: round(v * max(0.0, a))))
        return out

    dur, anim = t["dur"], t["anim"]
    tail = min(0.3, dur / 3)
    alpha = min(1.0, (dur - age) / tail) if anim != "none" else 1.0  # every animated title fades out
    img = layer
    if anim == "fade":
        alpha = min(alpha, age / min(0.4, dur / 3))
    elif anim == "slam":  # 1.9x -> 0.96x -> 1.0x over the first 0.25 s, as on the promos
        k = 1.9 - 0.94 * min(1.0, age / 0.15) if age < 0.15 else 0.96 + 0.04 * min(1.0, (age - 0.15) / 0.1)
        img = scaled(img, k)
        alpha = min(alpha, age / 0.06)
    elif anim == "slide":  # in from the left over 0.35 s, easing out
        k = min(1.0, age / 0.35)
        dx = round(-w * (1 - k) ** 3)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.alpha_composite(img.crop((-dx, 0, w, h)))  # dx <= 0: the layer's left part is still off screen
        img = canvas
    elif anim == "typewriter":  # revealed left to right over 60% of its time (or 2 s at most)
        k = min(1.0, age / min(2.0, dur * 0.6))
        box = layer.getbbox() or (0, 0, w, h)
        edge = round(box[0] + (box[2] - box[0]) * k)
        mask = Image.new("L", (w, h), 0)
        mask.paste(255, (0, 0, edge, h))
        img = layer.copy()
        img.putalpha(Image.composite(layer.getchannel("A"), mask, mask))
    return faded(img, alpha)


# ---------------------------------------------------------------- sound

def _mix(edl: dict, work: Path, log) -> Path:
    """Three buses (voice, music, sfx), every file levelled to the voice loudness first (unless its source
    says level: false), the voices kept clear by the mix's duck, then one limiter. Writes voice.wav,
    bed.wav (music + effects, ducked) and mix.wav."""
    m, src, total = edl["mix"], edl["sources"], seconds(edl)
    target = float(m.get("voice_lufs", -20.0))
    gains: dict[str, float] = {}

    def level(path: str) -> float:
        if path not in gains:
            measured = media.loudness(Path(path)).get("input_i")
            gains[path] = 0.0 if measured is None else max(-30.0, min(30.0, target - measured))
        return gains[path]

    buses = {b: [] for b in BUSES}
    for a in edl.get("audio", []):
        s = src[a["src"]]
        offset = {"voice": 0.0, "music": float(m.get("music_db", -10.0)), "sfx": float(m.get("sfx_db", -4.0))}[a["bus"]]
        gain = a["gain_db"] if s.get("level") is False else level(s["path"]) + offset + a["gain_db"]
        buses[a["bus"]].append((s["path"], a, round(gain, 2), s.get("duration")))
    # Picture events that keep their source's own sound go on the effects bus, under their shot.
    for p, t in zip(edl["picture"], starts(edl)):
        if p.get("sound_db") is not None:
            s = src[p["src"]]
            ev = {"start": t, "in": p["in"], "dur": p["dur"], "fade_in": 0.05, "fade_out": 0.05}
            buses["sfx"].append((s["path"], ev, float(p["sound_db"]), s.get("duration")))
    files = {b: _bus(items, total, work / f"bus_{b}.wav") for b, items in buses.items()}
    voice = files["voice"]
    duck = m.get("duck", "sidechain")
    args = [media.FFMPEG, "-y", "-v", "error", "-i", str(voice), "-i", str(files["music"]), "-i", str(files["sfx"])]
    if duck == "sidechain":  # today's: a compressor on the music keyed on the voices; effects untouched
        d = m.get("sidechain", {})
        graph = (f"[1:a][0:a]sidechaincompress=threshold={d.get('threshold', 0.03)}:"
                 f"ratio={d.get('ratio', 4)}:attack={d.get('attack_ms', 20)}:release={d.get('release_ms', 400)}:detection=rms[mu];"
                 "[mu][2:a]amix=inputs=2:duration=first:normalize=0[out]")
    elif duck == "expr":  # the promos': everything else dips under each line, with soft ramps
        windows = _voice_windows(edl)
        g = 1 - 10 ** (min(float(m.get("duck_db", -2.5)), float(m.get("min_duck_db", -10.0))) / 20)
        r = float(m.get("duck_ramp_s", 0.3))
        terms = "+".join(f"clip(min((t-{a:.3f})/{r},({b:.3f}-t)/{r}),0,1)" for a, b in windows) or "0"
        graph = (f"[1:a][2:a]amix=inputs=2:duration=first:normalize=0,"
                 f"volume='1-{g:.4f}*min(1,{terms})':eval=frame[out]")
    else:
        graph = "[1:a][2:a]amix=inputs=2:duration=first:normalize=0[out]"
    bed = work / "bed.wav"
    media._run(args + ["-filter_complex", graph, "-map", "[out]", str(bed)])
    _clear_voices(edl, voice, bed, float(m.get("voice_margin_db", 7.0)), float(m.get("duck_ramp_s", 0.3)), log)
    mix = work / "mix.wav"
    media._run([media.FFMPEG, "-y", "-v", "error", "-i", str(voice), "-i", str(bed), "-filter_complex",
                f"[0:a][1:a]amix=inputs=2:duration=first:normalize=0,"
                f"alimiter=limit={m.get('limit', 0.35)}:attack=5:release=80:level=false[out]", "-map", "[out]", str(mix)])
    shutil.copyfile(voice, work / "voice.wav")
    log(f"mix: {sum(len(v) for v in buses.values())} sound(s), duck {duck}")
    return mix


def _clear_voices(edl: dict, voice: Path, bed: Path, margin_db: float, ramp: float, log) -> None:
    """Whatever the duck did, every line ends up at least margin_db over the bed under it (QC's measure:
    RMS over the line): where it doesn't, the bed is turned down further for that line, ramped so it's
    already down when the line starts. Rewrites bed.wav."""
    from . import qc

    v, rate = qc._read_mono(voice)
    b, _ = qc._read_mono(bed)
    dips = []
    for line in facts(edl)["lines"]:
        i, j = int(line["start"] * rate), int(line["end"] * rate)
        short = margin_db - (qc._rms_db(v[i:j]) - qc._rms_db(b[i:j]))
        if short > 0.5:
            dips.append((line["start"], line["end"], min(short, 30.0)))
    if not dips:
        return
    r = max(0.05, ramp)
    gain = "*".join(f"(1-{1 - 10 ** (-d / 20):.4f}*clip(min((t-{a - r:.3f})/{r},({e + r:.3f}-t)/{r}),0,1))"
                    for a, e, d in dips)
    tmp = bed.with_name("bed_clear.wav")
    media._run([media.FFMPEG, "-y", "-v", "error", "-i", str(bed), "-af", f"volume='{gain}':eval=frame", str(tmp)])
    tmp.replace(bed)
    log(f"mix: turned the bed down under {len(dips)} line(s) to keep them {margin_db:g} dB clear "
        f"(up to {max(d for *_, d in dips):.1f} dB more)")


def _voice_windows(edl: dict) -> list[tuple[float, float]]:
    """When the voices talk, merged: a gap shorter than a second stays ducked."""
    spans = sorted((f["start"], f["end"]) for f in facts(edl)["lines"])
    out = []
    for a, b in spans:
        if out and a - out[-1][1] < 1.0:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _bus(items: list, total: float, dst: Path, rate: int = 48000) -> Path:
    """Lay files on a silent mono track: each (path, event, gain dB, source length), from the event's in
    point for its dur (or the rest of the source), with its fades."""
    args = [media.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono:d={total:.3f}"]
    parts, labels = [], ["[0:a]"]
    for i, (path, ev, gain, length) in enumerate(items, 1):
        start_in = float(ev.get("in", 0.0))
        dur = ev.get("dur")
        room = total - ev["start"]
        dur = min(dur if dur is not None else room, room)
        chain = f"atrim={start_in:.3f}:{start_in + dur:.3f},asetpts=PTS-STARTPTS"
        fade_in, fade_out = float(ev.get("fade_in", 0)), float(ev.get("fade_out", 0))
        if fade_in:
            chain += f",afade=t=in:st=0:d={fade_in:.3f}"
        if fade_out:
            chain += f",afade=t=out:st={max(0.0, dur - fade_out):.3f}:d={fade_out:.3f}"
        args += ["-i", str(path)]
        parts.append(f"[{i}:a]aresample={rate},aformat=channel_layouts=mono,{chain},volume={gain}dB,"
                     f"adelay={round(ev['start'] * 1000)}:all=1[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(parts + [f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[out]"])
    media._run(args + ["-filter_complex", graph, "-map", "[out]", str(dst)])
    return dst
