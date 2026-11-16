"""Thin FFmpeg/ffprobe helpers. Everything media-related shells out to these two binaries."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

IMAGE_CODECS = {"png", "mjpeg", "webp", "bmp", "tiff", "gif", "jpegls", "jpeg2000"}


class MediaError(RuntimeError):
    pass


def configure(ffmpeg: str, ffprobe: str) -> None:
    global FFMPEG, FFPROBE
    FFMPEG, FFPROBE = ffmpeg or "ffmpeg", ffprobe or "ffprobe"


def _run(args: list[str], timeout: int = 1800) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise MediaError(f"{Path(args[0]).name} failed ({proc.returncode}): {tail}")
    return proc.stdout


def probe(path: Path) -> dict:
    """Return a compact summary: media kind, duration, size and codecs."""
    data = json.loads(
        _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    )
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0) or None

    if video and (video.get("codec_name") in IMAGE_CODECS) and not audio and (duration or 0) < 0.2:
        media = "image"
    elif video and video.get("disposition", {}).get("attached_pic") and audio:
        media = "audio"  # music file with cover art
    elif video:
        media = "video"
    elif audio:
        media = "audio"
    else:
        raise MediaError("no audio or video streams found")

    return {
        "media": media,
        "duration_s": None if media == "image" else duration,
        "width": int(video["width"]) if video and media != "audio" else None,
        "height": int(video["height"]) if video and media != "audio" else None,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "format_name": fmt.get("format_name"),
        "has_audio": audio is not None,
    }


def thumbnail(src: Path, dst: Path, width: int = 360, seek: float | None = None) -> Path:
    args = [FFMPEG, "-y", "-v", "error"]
    if seek:
        args += ["-ss", f"{seek:.2f}"]
    args += ["-i", str(src), "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "4", str(dst)]
    _run(args)
    return dst


def proxy_video(src: Path, dst: Path) -> Path:
    """A small H.264 copy the browser can always play, whatever the original codec."""
    _run([
        FFMPEG, "-y", "-v", "error", "-i", str(src),
        "-vf", "scale=-2:'min(480,ih)'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst),
    ])
    return dst


def waveform(src: Path, dst: Path) -> Path:
    _run([
        FFMPEG, "-y", "-v", "error", "-i", str(src),
        "-filter_complex", "aformat=channel_layouts=mono,showwavespic=s=720x120:colors=0x8fd18f",
        "-frames:v", "1", str(dst),
    ])
    return dst


def preview_audio(src: Path, dst: Path) -> Path:
    _run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "5", str(dst)])
    return dst


def concat_audio(files: list[Path], dst: Path, gap_s: float = 0.35, rate: int = 24000) -> Path:
    """Join audio files end to end with a short silence between each, as mono WAV."""
    args = [FFMPEG, "-y", "-v", "error"]
    for f in files:
        args += ["-i", str(f)]
    parts, labels = [], []
    for i in range(len(files)):
        pad = f",apad=pad_dur={gap_s}" if i < len(files) - 1 else ""
        parts.append(f"[{i}:a]aresample={rate},aformat=channel_layouts=mono{pad}[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(files)}:v=0:a=1[out]"
    _run(args + ["-filter_complex", graph, "-map", "[out]", str(dst)])
    return dst


def silences(path: Path, threshold_db: int = -38, min_s: float = 0.3) -> list[tuple[float, float]]:
    """(start, end) of every pause in the audio. An unterminated final pause ends at infinity."""
    proc = subprocess.run(
        [FFMPEG, "-v", "info", "-i", str(path), "-af", f"silencedetect=n={threshold_db}dB:d={min_s}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    out, start = [], None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            out.append((start, float(line.split("silence_end:")[1].split()[0])))
            start = None
    if start is not None:
        out.append((start, float("inf")))
    return out


# Voice cloning tests (Nov 2026) settled these: references cut straight from the original
# recording (no resampling, no loudness normalisation, which smoothed away a scratchy voice's
# texture) and about 15 s long (Pocket TTS stopped mid-line when given 43 s).
REFERENCE_TARGET_S = 15.0
REFERENCE_MIN_S = 8.0


def voice_reference(src: Path, dst: Path, target_s: float = REFERENCE_TARGET_S) -> tuple[Path, float]:
    """Cut up to target_s of speech from the start, ending in a natural pause where possible."""
    pauses = silences(src)
    begin = pauses[0][1] if pauses and pauses[0][0] <= 0.05 else 0.0
    if begin == float("inf"):
        begin = 0.0  # all silence; the caller rejects the result as too short
    end = begin + target_s
    breaks = [s for s, _ in pauses if begin + REFERENCE_MIN_S <= s <= begin + target_s]
    if breaks:
        end = breaks[-1] + 0.15
    _run([FFMPEG, "-y", "-v", "error", "-ss", f"{begin:.3f}", "-to", f"{end:.3f}", "-i", str(src),
          "-vn", "-ac", "1", str(dst)])
    speech = end - begin
    if pauses and pauses[0][1] == float("inf") and pauses[0][0] <= 0.05:
        speech = 0.0
    return dst, min(speech, probe(dst)["duration_s"] or 0.0)


# ---------------------------------------------------------------- shots, mixing and looks

# zoompan expressions per camera move. N is the clip's frame count, filled in per clip.
MOTIONS = {
    "still": ("1", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "push_in": ("1+0.15*on/N", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pull_out": ("1.15-0.15*on/N", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    "pan_left": ("1.15", "(iw-iw/zoom)*(1-on/N)", "ih/2-(ih/zoom/2)"),
    "pan_right": ("1.15", "(iw-iw/zoom)*on/N", "ih/2-(ih/zoom/2)"),
}


def motion_clip(still: Path, dst: Path, seconds: float, motion: str = "still", fps: int = 30,
                w: int = 640, h: int = 480, grade: str = "colour", caption: Path | None = None,
                caption_fade: tuple = (0, 0), fade: tuple = (0, 0)) -> Path:
    """A silent clip of a still image with a slow camera move, optional black and white, a caption
    layer (titles.caption) faded in and out over it, and the whole picture faded in and out (cards)."""
    frames = max(1, round(seconds * fps))
    z, x, y = (e.replace("N", str(frames)) for e in MOTIONS[motion])
    # Scale up first so the move is smooth instead of stepping a pixel at a time.
    vf = (f"scale={w * 3}:{h * 3}:force_original_aspect_ratio=increase,crop={w * 3}:{h * 3},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps}")
    _run([FFMPEG, "-y", "-v", "error", "-i", str(still), *_shot_graph(vf, seconds, grade, caption, caption_fade, fade),
          "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(dst)])
    return dst


def _fades(seconds: float, fade_in: float, fade_out: float, alpha: bool = False) -> str:
    a = ":alpha=1" if alpha else ""
    out = f",fade=t=in:st=0:d={fade_in}{a}" if fade_in else ""
    return out + (f",fade=t=out:st={max(0.0, seconds - fade_out):.3f}:d={fade_out}{a}" if fade_out else "")


def _shot_graph(vf: str, seconds: float, grade: str, caption: Path | None, caption_fade: tuple, fade: tuple) -> list[str]:
    """The arguments that finish every shot: its picture filters, grade, the caption layer (a second,
    looped input) faded in and out on top, whole-frame fades, pixel format."""
    graph = f"[0:v]{vf}" + (",hue=s=0" if grade == "bw" else "")
    inputs = []
    if caption:
        inputs = ["-loop", "1", "-t", f"{seconds + 1:.3f}", "-i", str(caption)]
        graph += (f"[pic];[1:v]format=rgba{_fades(seconds, *caption_fade, alpha=True)}[text];"
                  "[pic][text]overlay=0:0:eof_action=pass")
    graph += _fades(seconds, *fade) + ",format=yuv420p,setsar=1[out]"
    return [*inputs, "-filter_complex", graph, "-map", "[out]"]


def cover_image(src: Path, dst: Path, w: int, h: int) -> Path:
    """src scaled and cropped to fill w x h exactly."""
    _run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vf",
          f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", "-frames:v", "1", str(dst)])
    return dst


def frame_at(src: Path, seconds: float, dst: Path) -> Path:
    """One frame of a video, at the given time, as an image."""
    _run([FFMPEG, "-y", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(src), "-frames:v", "1", str(dst)])
    return dst


def last_frame(src: Path, dst: Path) -> Path:
    """The final frame of a video as an image."""
    _run([FFMPEG, "-y", "-v", "error", "-sseof", "-0.1", "-i", str(src), "-update", "1", "-frames:v", "1", str(dst)])
    return dst


def video_clip(src: Path, dst: Path, seconds: float, start: float = 0.0, fps: int = 30, w: int = 640, h: int = 480,
               grade: str = "colour", caption: Path | None = None, caption_fade: tuple = (0, 0)) -> Path:
    """A silent clip cut from footage, cropped to fill the frame, looped if the footage is too short."""
    frames = max(1, round(seconds * fps))
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps}"
    _run([FFMPEG, "-y", "-v", "error", "-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(src), "-an",
          *_shot_graph(vf, seconds, grade, caption, caption_fade, (0, 0)), "-frames:v", str(frames),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(dst)])
    return dst


def shrink_video(src: Path, dst: Path, height: int = 480) -> Path:
    """A small H.264 copy no taller than height, for downloaded footage."""
    _run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vf", f"scale=-2:'min({height},ih)'",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-b:a", "96k",
          "-movflags", "+faststart", str(dst)], timeout=3600)
    return dst


def concat_video(clips: list[Path], dst: Path) -> Path:
    """Join clips that share codec settings without re-encoding."""
    listing = dst.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    _run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dst)])
    return dst


def mix_at(placements: list[tuple], seconds: float, dst: Path, rate: int = 48000) -> Path:
    """Lay audio files on a silent track of the given length. Each placement is
    (file, start time) or (file, start time, gain in dB, longest it may play), faded out if cut short."""
    args = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono:d={seconds:.3f}"]
    parts, labels = [], ["[0:a]"]
    for i, (path, at, *rest) in enumerate(placements, 1):
        gain, limit = (rest + [0.0, None])[:2]
        trim = f",atrim=0:{limit:.3f},afade=t=out:st={max(0.0, limit - 0.4):.3f}:d=0.4" if limit else ""
        args += ["-i", str(path)]
        parts.append(f"[{i}:a]aresample={rate},aformat=channel_layouts=mono{trim},volume={gain}dB,"
                     f"adelay={round(at * 1000)}:all=1[a{i}]")
        labels.append(f"[a{i}]")
    graph = ";".join(parts + [f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[out]"])
    _run(args + ["-filter_complex", graph, "-map", "[out]", str(dst)])
    return dst


def loudness(path: Path) -> dict:
    """EBU R128 measurements of a file (integrated LUFS "input_i", true peak "input_tp", ...), as loudnorm
    reports them; integrated is None for silence."""
    proc = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=print_format=json",
                           "-f", "null", "-"], capture_output=True, text=True, timeout=600)
    text = proc.stderr
    data = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
    out = {k: float(v) if v not in ("-inf", "inf") else None for k, v in data.items() if k.startswith(("input", "target"))}
    return out


def mix_buses(buses: dict[str, list[tuple]], seconds: float, dst: Path, duck: dict | None = None) -> Path:
    """Voice, music and sfx buses (each a mix_at placement list) summed, with the music bus ducked by a
    sidechain compressor keyed on the voices."""
    work = dst.parent
    files = {}
    for name in ("voice", "music", "sfx"):
        if buses.get(name):
            files[name] = mix_at(buses[name], seconds, work / f"bus_{name}.wav")
    if "voice" not in files:
        files["voice"] = mix_at([], seconds, work / "bus_voice.wav")
    args = [FFMPEG, "-y", "-v", "error"] + [a for f in files.values() for a in ("-i", str(f))]
    idx = {name: i for i, name in enumerate(files)}
    graph, inputs = [], [f"[{idx['voice']}:a]"]
    if "music" in files:
        d = duck or {}
        graph.append(f"[{idx['voice']}:a]asplit=2[v][key]")
        graph.append(f"[{idx['music']}:a][key]sidechaincompress=threshold={d.get('threshold', 0.03)}:ratio={d.get('ratio', 4)}"
                     f":attack={d.get('attack_ms', 20)}:release={d.get('release_ms', 400)}:detection=rms[m]")
        inputs = ["[v]", "[m]"]
    if "sfx" in files:
        inputs.append(f"[{idx['sfx']}:a]")
    # A gentle limiter so one loud transient (a downloaded effect) can't stop the finishing pass
    # from bringing the whole programme up to its loudness.
    graph.append(f"{''.join(inputs)}amix=inputs={len(inputs)}:duration=first:normalize=0,"
                 f"alimiter=limit={(duck or {}).get('limit', 0.35)}:attack=5:release=80:level=false[out]")
    _run(args + ["-filter_complex", ";".join(graph), "-map", "[out]", str(dst)])
    return dst


def mux(video: Path, audio: Path, dst: Path, chapters: list[tuple[float, float, str]] | None = None) -> Path:
    """Put picture and sound in one MP4, with optional chapter markers (start, end, title)."""
    args = [FFMPEG, "-y", "-v", "error", "-i", str(video), "-i", str(audio)]
    if chapters:
        meta = dst.with_suffix(".chapters.txt")
        meta.write_text(";FFMETADATA1\n" + "".join(
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(s * 1000)}\nEND={round(e * 1000)}\ntitle={t}\n"
            for s, e, t in chapters))
        args += ["-i", str(meta), "-map_chapters", "2"]
    _run(args + ["-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                 "-shortest", "-movflags", "+faststart", str(dst)])
    return dst


# Finishing looks: a video filter chain and an audio chain. Only "clean" exists; the broadcast look
# (an NTSC signal filter, real interlacing) is still to be picked, see the README.
LOOKS = {
    # No effects: square pixels, 4:3, levelled loudness. Broadcast filters (NTSC, interlacing) come later.
    "clean": {
        "video": "setsar=1,setdar=4/3",
        "loudness": -16.0,  # integrated LUFS of the finished programme
        "true_peak": -1.5,
    },
}


def look(src: Path, dst: Path, preset: str, limit_tp: float | None = None) -> Path:
    """The finishing pass. Loudness is set in two passes (measure, then one fixed gain), so the mix's
    balance and ducking are kept exactly; a single loudnorm pass would ride the levels like an AGC.
    limit_tp: a peak limiter (dBFS) after the gain, for QC's loudness fix when a peak would clip."""
    spec = LOOKS[preset]
    m = loudness(src)
    if m.get("input_i") is None:
        audio = "aresample=48000"
    else:
        audio = (f"loudnorm=I={spec['loudness']}:TP={spec['true_peak']}:LRA=20:measured_I={m['input_i']}"
                 f":measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
                 f":linear=true,aresample=48000")
        if limit_tp is not None:
            audio += f",alimiter=limit={10 ** (limit_tp / 20):.4f}:attack=2:release=60:level=false"
    _run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-vf", f"{spec['video']},format=yuv420p", "-af", audio,
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
          "-movflags", "+faststart", str(dst)])
    return dst
