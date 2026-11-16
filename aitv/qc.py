"""QC (pipeline v2, part B): automatic checks on a finished piece. Local and free.

Runs after the look, on the final mp4 and the mix's stems, against the edit (the production's EDL, or
the one today's assembly amounts to):

1. lines heard: each line's voice measured against everything else under it (works for any voice,
   even one Whisper can't read), and Whisper's transcript of the final matched to the script;
2. loudness: integrated and true peak against the look's targets;
3. cuts: scene detection against the planned cuts (a source's own edit inside a shot, flash frames);
4. dead picture and sound: black, frozen picture and silence that the edit didn't ask for;
5. titles: inside the title-safe area, and on screen long enough to read;
6. a contact sheet of the frames either side of every cut, for the editor, the reviewer and you.

The result is qc.json: per check, pass or fail with its numbers, and issues with a time, a category,
a severity (blocking or minor) and a hint. QC never stops a run; problems show on the production page.
"""

from __future__ import annotations

import math
import re
import subprocess
import wave
from pathlib import Path

from . import edl as edl_mod
from . import media, titles
from .config import Config

DEFAULTS = {
    "min_voice_margin_db": 6.0,   # a line's voice at least this far above the bed under it
    "min_line_score": 0.6,        # share of a line's words Whisper must hear
    "loudness_tolerance_lu": 1.0,
    "scene_threshold": 0.3,
    "flash_frames": 3,            # a shot shorter than this that the edit didn't ask for
    "black_min_s": 0.3,
    "freeze_min_s": 1.5,
    "silence_db": -50,
    "silence_min_s": 1.5,
    "read_s_per_word": 0.3,
    "read_min_s": 1.5,
    "safe_area": 0.8,             # the middle 80% of the frame, both ways
}


def settings(cfg: Config) -> dict:
    return {**DEFAULTS, **cfg.section("qc")}


def run(cfg: Config, final: Path, edl: dict, stems: dict, out_dir: Path, log=lambda line: None,
        transcript: dict | None = None, look: dict | None = None) -> dict:
    """Every check on one finished file. stems: {"voice": path, "bed": path} or {"voice": path, "mix": path}
    (the bed is then the mix minus the voice). transcript: Whisper's words for the final, if available.
    look: the look's {"loudness", "true_peak"} targets."""
    opts = settings(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    f = edl_mod.facts(edl)
    fps = edl.get("fps", 30)
    report = {"file": str(final), "seconds": f["seconds"], "checks": {}, "issues": []}

    def check(name: str, fn, *args):
        try:
            result, issues = fn(*args)
            report["checks"][name] = {"ok": not any(i["severity"] == "blocking" for i in issues), **result}
            report["issues"] += [{"check": name, **i} for i in issues]
        except Exception as exc:  # noqa: BLE001  one broken check never hides the others
            report["checks"][name] = {"ok": None, "error": f"{type(exc).__name__}: {exc}"}
            log(f"qc: the {name} check failed to run: {exc}")

    check("lines", _lines, f, stems, transcript, opts)
    check("loudness", _loudness, final, look or {}, opts)
    check("cuts", _cuts, final, f, fps, opts)
    check("dead", _dead, final, f, opts)
    check("titles", _titles, f, edl, opts)
    check("contact_sheet", _contact_sheet, final, f, fps, out_dir)
    report["issues"].sort(key=lambda i: (i.get("t") is None, i.get("t") or 0))
    report["ok"] = all(c.get("ok") is not False for c in report["checks"].values())
    log(f"qc: {'pass' if report['ok'] else 'problems'}, {len(report['issues'])} issue(s)")
    return report


def issue(t: float | None, category: str, problem: str, hint: str, severity: str = "minor", **extra) -> dict:
    return {"t": None if t is None else round(t, 2), "category": category, "severity": severity, "problem": problem,
            "hint": hint, **extra}


# ---------------------------------------------------------------- 1. lines

def _read_mono(path: Path):
    """A WAV as float samples in -1..1, and its rate (the mix writes 16-bit mono WAVs)."""
    import numpy as np

    with wave.open(str(path), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: expected 16-bit audio")
    samples = np.frombuffer(data, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _delay(voice, mix, lines: list, rate: int, most: int = 1200) -> int:
    """Samples the mix lags the voice stem by: the mix's limiter looks ahead (5 ms = 240 samples at 48 kHz).
    Found by cross-correlation over the first 3 s of speech."""
    import numpy as np

    if not lines:
        return 0
    a = int(lines[0]["start"] * rate)
    b = min(a + 3 * rate, len(voice) - most, len(mix) - most)
    if b <= a:
        return 0
    v = voice[a:b]
    return max(range(most), key=lambda lag: abs(float(np.dot(mix[a + lag:b + lag], v))))


def _rms_db(samples) -> float:
    import numpy as np

    if len(samples) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype="float64"))))
    return 20 * math.log10(rms) if rms > 1e-6 else -120.0


def _lines(f: dict, stems: dict, transcript: dict | None, opts: dict) -> tuple[dict, list]:
    from . import transcribe

    voice = bed = None
    if stems.get("voice") and Path(stems["voice"]).is_file():  # older productions have no voice stem
        voice, rate = _read_mono(Path(stems["voice"]))
        if stems.get("bed"):
            bed, _ = _read_mono(Path(stems["bed"]))
        else:  # the bed is worked out per line from the mix (below)
            mix, _ = _read_mono(Path(stems["mix"]))
            mix = mix[_delay(voice, mix, f["lines"], rate):]
    if voice is None and transcript is None:
        return {"lines": [], "skipped": "no voice stem and no Whisper"}, []
    rows, issues = [], []
    for line in f["lines"]:
        margin = None
        if voice is not None:
            a, b = int(line["start"] * rate), int(line["end"] * rate)
            v = voice[a:b]
            if bed is not None:
                under = bed[a:b]
            else:
                # The mix's limiter turns the voice down by some amount, so mix - voice would leave voice in the
                # "bed". The least-squares scale of the voice inside the mix finds how much, per line.
                import numpy as np

                m = mix[a:a + len(v)]
                v = v[:len(m)]
                g = float(np.dot(m, v) / max(float(np.dot(v, v)), 1e-9))
                under, v = m - g * v, g * v
            margin = round(_rms_db(v) - _rms_db(under), 1)
        row = {"id": line["id"], "start": line["start"], "text": line["text"], "margin_db": margin}
        masked = margin is not None and margin < opts["min_voice_margin_db"]
        heard = None
        if transcript is not None:
            heard = round(transcribe.match_score(line["text"], transcribe.words_between(
                transcript["words"], line["start"] - 0.5, line["end"] + 1.0)), 2)
            row["heard"] = heard
        unheard = heard is not None and heard < opts["min_line_score"]
        if masked or unheard:
            # Whisper alone is only worth a listen (it can't read every voice).
            why = []
            if masked:
                why.append(f"the voice is only {margin} dB above the sound under it")
            if unheard:
                why.append(f"Whisper caught {heard:.0%} of its words")
            issues.append(issue(line["start"], "masked_line", f"line {line['id']} ({line['text'][:60]!r}): " + " and ".join(why),
                                f"raise {line['id']}, or lower or move the music and effects under it",
                                # blocking: the voice under the floor (the mix keeps every line above it, so
                                # this is real), or no voice stem and Whisper caught next to nothing
                                "blocking" if masked or (unheard and margin is None and heard < 0.3) else "minor",
                                event=line["id"]))
        rows.append(row)
    return {"lines": rows}, issues


# ---------------------------------------------------------------- 2. loudness

def _loudness(final: Path, look: dict, opts: dict) -> tuple[dict, list]:
    m = media.loudness(final)
    lufs, tp = m.get("input_i"), m.get("input_tp")
    want_i, want_tp = look.get("loudness", -16.0), look.get("true_peak", -1.5)
    issues = []
    if lufs is None:
        issues.append(issue(None, "loudness", "the piece is silent", "check the mix", "blocking"))
    elif abs(lufs - want_i) > opts["loudness_tolerance_lu"]:
        issues.append(issue(None, "loudness", f"{lufs:.1f} LUFS, aiming for {want_i}",
                            "the look's loudness pass didn't reach its target: check the mix for a very loud stretch"))
    if tp is not None and tp > want_tp + 0.5:
        issues.append(issue(None, "loudness", f"true peak {tp:.1f} dBFS, over {want_tp}", "limit the peaks", fix="limit"))
    return {"lufs": lufs, "true_peak": tp, "lra": m.get("input_lra")}, issues


# ---------------------------------------------------------------- 3. cuts

def scene_changes(path: Path, threshold: float) -> list[float]:
    """Times FFmpeg's scene detection sees a cut (showinfo prints at info level)."""
    proc = subprocess.run([media.FFMPEG, "-v", "info", "-i", str(path), "-an", "-vf",
                           f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                          capture_output=True, text=True, timeout=1800)
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def _cuts(final: Path, f: dict, fps: int, opts: dict) -> tuple[dict, list]:
    seen = scene_changes(final, opts["scene_threshold"])
    frame = 1 / fps
    hard = [c["t"] for c in f["cuts"] if c["transition"] in ("cut", "flash", "dip")]
    soft = [c["t"] for c in f["cuts"] if c["transition"] == "dissolve"]
    unplanned = [t for t in seen if not any(abs(t - p) <= 2 * frame for p in hard)
                 and not any(abs(t - p) <= 1.0 for p in soft)]
    missed = [p for p in hard if not any(abs(t - p) <= 2 * frame for t in seen)]
    issues = [issue(t, "unplanned_cut", f"a cut at {t:.2f}s that the edit didn't make (the source's own edit?)",
                    "trim the shot so it ends before this, or start it after") for t in unplanned]
    planned_gaps = sorted(hard + soft)
    for a, b in zip(seen, seen[1:]):
        if (b - a) < opts["flash_frames"] * frame and not any(a - frame <= p <= b + frame for p in planned_gaps):
            issues.append(issue(a, "flash_frame", f"a {round((b - a) * fps)}-frame flash at {a:.2f}s", "trim it out"))
    return {"seen": len(seen), "planned": len(hard) + len(soft), "unplanned": len(unplanned),
            "not_seen": len(missed)}, issues


# ---------------------------------------------------------------- 4. black, frozen, silent

def _detect(path: Path, args: list[str], start_key: str, end_key: str) -> list[tuple[float, float]]:
    proc = subprocess.run([media.FFMPEG, "-v", "info", "-i", str(path), *args, "-f", "null", "-"],
                          capture_output=True, text=True, timeout=1800)
    starts = [float(x) for x in re.findall(start_key + r"[:=]\s*([0-9.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(end_key + r"[:=]\s*([0-9.]+)", proc.stderr)]
    return list(zip(starts, ends + [float("inf")] * (len(starts) - len(ends))))


def _inside(span: tuple[float, float], calm: list[tuple[float, float]], slack: float = 0.2) -> bool:
    return any(a - slack <= span[0] and span[1] <= b + slack for a, b in calm)


def _dead(final: Path, f: dict, opts: dict) -> tuple[dict, list]:
    total = f["seconds"]
    black = _detect(final, ["-an", "-vf", f"blackdetect=d={opts['black_min_s']}:pix_th=0.1"], "black_start", "black_end")
    frozen = _detect(final, ["-an", "-vf", f"freezedetect=n=0.003:d={opts['freeze_min_s']}"],
                     "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end")
    silent = media.silences(final, int(opts["silence_db"]), float(opts["silence_min_s"]))
    issues = []
    for a, b in black:
        if not _inside((a, min(b, total)), f["calm"]):
            issues.append(issue(a, "black", f"{min(b, total) - a:.1f}s of black at {a:.2f}s", "fill it or cut it out",
                                "blocking" if min(b, total) - a > 1.0 else "minor"))
    for a, b in frozen:
        if not _inside((a, min(b, total)), f["calm"] + black):  # black is reported as black, not twice
            issues.append(issue(a, "frozen", f"the picture stands still for {min(b, total) - a:.1f}s at {a:.2f}s "
                                "(a held last frame?)", "shorten the shot or slow it, or add a camera move"))
    for a, b in silent:
        b = min(b, total)
        if b - a >= opts["silence_min_s"] and not (a < 0.3 or b > total - 0.3):
            issues.append(issue(a, "silence", f"{b - a:.1f}s of dead air at {a:.2f}s", "lay a sound or room tone under it"))
    return {"black": len(black), "frozen": len(frozen), "silent": len(silent)}, issues


# ---------------------------------------------------------------- 5. titles

def _titles(f: dict, edl: dict, opts: dict) -> tuple[dict, list]:
    from PIL import Image

    w, h = edl.get("width", 640), edl.get("height", 480)
    margin = (1 - opts["safe_area"]) / 2
    safe = (w * margin - 1, h * margin - 1, w * (1 - margin) + 1, h * (1 - margin) + 1)
    issues, checked = [], 0
    every = [(t["start"], t["dur"], t["text"], t.get("style"), t["role"], t.get("position", "center"), t["id"])
             for t in f["titles"]] + [(a, b - a, text, None, "card", "center", None) for a, b, text in f["cards"]]
    for start, dur, text, st, role, position, tid in every:
        checked += 1
        words = len(text.split())
        need = max(opts["read_min_s"], words * opts["read_s_per_word"])
        if dur + 0.01 < need:
            issues.append(issue(start, "title_too_short", f"{text[:50]!r} is on screen {dur:.1f}s; {words} words need "
                                f"{need:.1f}s", f"hold it at least {need:.1f}s", event=tid))
        if role == "card":
            continue  # cards are laid out inside the safe area by titles.card
        layer = titles.overlay(text, st, w, h, position) if role == "title" else None
        if role == "caption":
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "c.png"
                titles.caption(text, st, p, w, h)
                layer = Image.open(p).convert("RGBA")
                layer.load()
        # The letters only: a caption's dark band (alpha 170) and soft shadows are allowed past the edge.
        box = layer.getchannel("A").point(lambda a: 255 if a > 200 else 0).getbbox() if layer else None
        if box and (box[0] < safe[0] or box[1] < safe[1] or box[2] > safe[2] or box[3] > safe[3]):
            issues.append(issue(start, "title_unsafe", f"{text[:50]!r} reaches outside the title-safe area",
                                "shorten the text or set it smaller", event=tid))
    return {"checked": checked}, issues


# ---------------------------------------------------------------- 6. contact sheet

def _contact_sheet(final: Path, f: dict, fps: int, out_dir: Path) -> tuple[dict, list]:
    """The frame before and after every planned cut, side by side, one row per cut."""
    sheet = out_dir / "cuts.png"
    times = [c["t"] for c in f["cuts"]]
    if not times:
        return {"sheet": None}, []
    contact_sheet(final, [(t - 1 / fps, t + 1 / fps) for t in times], sheet, [f"{t:.2f}s" for t in times])
    return {"sheet": str(sheet)}, []


def contact_sheet(video: Path, pairs: list[tuple[float, ...]], dst: Path, labels: list[str] | None = None,
                  thumb_w: int = 240) -> Path:
    """Rows of frames from a video, each row the given times, labelled; for QC's cuts and the editor's
    clips (pipeline v2)."""
    import tempfile

    from PIL import Image, ImageDraw

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for r, times in enumerate(pairs):
            frames = []
            for c, t in enumerate(times):
                p = Path(tmp) / f"{r}_{c}.jpg"
                try:
                    media._run([media.FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
                                "-frames:v", "1", "-vf", f"scale={thumb_w}:-2", str(p)])
                    frames.append(Image.open(p).convert("RGB"))
                except media.MediaError:
                    frames.append(Image.new("RGB", (thumb_w, thumb_w * 3 // 4), (40, 0, 0)))
            rows.append(frames)
    th = max(img.height for row in rows for img in row)
    label_w = 80 if labels else 0
    cols = max(len(row) for row in rows)
    out = Image.new("RGB", (label_w + cols * (thumb_w + 4), len(rows) * (th + 4)), (16, 16, 16))
    draw = ImageDraw.Draw(out)
    for r, row in enumerate(rows):
        y = r * (th + 4)
        if labels:
            draw.text((6, y + th // 2 - 6), labels[r], fill=(230, 230, 230))
        for c, img in enumerate(row):
            out.paste(img, (label_w + c * (thumb_w + 4), y))
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst)
    return dst
