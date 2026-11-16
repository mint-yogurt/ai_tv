"""Image-to-video through OpenRouter's /videos API: tbc/mischief-1 for silent shots, HeyGen Video 1
(the "talkie" model) for shots with recurring characters or their own sound.

A shot's wide (16:9) frame becomes the first frame of a short generated clip; the render stage makes
the frames wide to begin with and crops the 4:3 middle out of the result (join). Jobs are
asynchronous: submit() every shot first, then wait() for all of them, so they generate in parallel.
Each finished job's usage.cost (dollars) is returned for the production log.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config
from .media import FFMPEG, MediaError, _run


class VideoGenError(RuntimeError):
    pass


def settings(cfg: Config) -> dict:
    return cfg.section("videogen")


def enabled(cfg: Config) -> bool:
    return bool(settings(cfg).get("enabled"))


def _request(cfg: Config, method: str, url: str, body: dict | None = None, raw: bool = False):
    opts = settings(cfg)
    key = os.environ.get(opts.get("api_key_env", "OPENROUTER_API_KEY"), "")
    if not key:
        raise VideoGenError(f"set {opts.get('api_key_env', 'OPENROUTER_API_KEY')} to generate video")
    if url.startswith("/"):
        url = "https://openrouter.ai" + url
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise VideoGenError(f"{method} {url} said {exc.code}: {exc.read()[:500].decode(errors='replace')}") from exc
    return data if raw else json.loads(data)


def submit(cfg: Config, frame: Path, prompt: str, duration: int) -> dict:
    """Start one job from a wide first frame. Returns {id, duration}."""
    opts = settings(cfg)
    size = opts.get("size", "854x480")
    sized = frame.with_name(frame.stem + f"_{size}.png")
    if not sized.is_file():
        _run([FFMPEG, "-y", "-v", "error", "-i", str(frame), "-vf", f"scale={size.replace('x', ':')}", str(sized)])
    image = "data:image/png;base64," + base64.b64encode(sized.read_bytes()).decode()
    body = {
        "model": opts.get("model", "tbc/mischief-1"),
        "prompt": prompt,
        "duration": int(duration),
        "resolution": opts.get("resolution", "480p"),
        "aspect_ratio": "16:9",
        "frame_images": [{"type": "image_url", "image_url": {"url": image}, "frame_type": "first_frame"}],
    }
    job = _request(cfg, "POST", opts.get("base_url", "https://openrouter.ai/api/v1") + "/videos", body)
    return {"id": job["id"], "duration": duration}


def wait(cfg: Config, job_id: str, dst: Path, log=lambda line: None) -> float | None:
    """Poll one job until it finishes, download the clip to dst, and return its cost in dollars."""
    opts = settings(cfg)
    base = opts.get("base_url", "https://openrouter.ai/api/v1")
    deadline = time.monotonic() + float(opts.get("timeout_seconds", 900))
    while True:
        status = _request(cfg, "GET", f"{base}/videos/{job_id}")
        if status["status"] == "completed":
            break
        if status["status"] in ("failed", "cancelled", "expired"):
            raise VideoGenError(f"video job {job_id} {status['status']}: {status.get('error', '')}")
        if time.monotonic() > deadline:
            raise VideoGenError(f"video job {job_id} still {status['status']} after {opts.get('timeout_seconds', 900)}s")
        time.sleep(float(opts.get("poll_seconds", 10)))
    dst.write_bytes(_request(cfg, "GET", f"{base}/videos/{job_id}/content?index=0", raw=True))
    return (status.get("usage") or {}).get("cost")


def join(segments: list[Path], dst: Path, seconds: float, w: int = 640, h: int = 480,
         cuts: list[float | None] | None = None) -> Path:
    """Generated pieces end to end, cropped to the 4:3 middle, held on the last frame if they run short
    of the shot. Never slowed down. cuts[i] ends piece i early (where the next piece was started from)."""
    cuts = cuts or [None] * len(segments)
    ins = [a for seg in segments for a in ("-i", str(seg))]
    chain = "".join(f"[{i}:v]{f'trim=end={cuts[i]},setpts=PTS-STARTPTS,' if cuts[i] else ''}"
                    f"scale=-2:{h},crop={w}:{h},setsar=1,fps=30[v{i}];" for i in range(len(segments)))
    chain += "".join(f"[v{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[c];"
    chain += f"[c]tpad=stop_mode=clone:stop_duration={seconds + 1:.2f}[out]"
    _run([FFMPEG, "-y", "-v", "error", *ins, "-filter_complex", chain, "-map", "[out]", "-t", f"{seconds:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", str(dst)])
    return dst


def place(cfg: Config, refs: list[Path], prompt: str, dst: Path) -> float | None:
    """A 4:3 first frame with the reference pictures' people put on a set by the image-to-image model
    (tencent/hy-image-v3.5-preview held their likeness in the Nov 2026 tests). Returns its cost."""
    opts = settings(cfg)

    def uri(p: Path) -> str:
        return f"data:image/{'jpeg' if p.suffix.lower() in ('.jpg', '.jpeg') else 'png'};base64," + \
            base64.b64encode(p.read_bytes()).decode()

    body = {"model": opts.get("image_model", "tencent/hy-image-v3.5-preview"), "prompt": prompt,
            "aspect_ratio": "4:3", "resolution": "1K",
            "input_references": [{"type": "image_url", "image_url": {"url": uri(r)}} for r in refs]}
    data = _request(cfg, "POST", opts.get("base_url", "https://openrouter.ai/api/v1") + "/images", body)
    big = dst.with_name(dst.stem + "_1k.png")
    big.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
    _run([FFMPEG, "-y", "-v", "error", "-i", str(big), "-vf", "scale=640:480:force_original_aspect_ratio=increase,crop=640:480",
          str(dst)])
    return (data.get("usage") or {}).get("cost")


def submit_talkie(cfg: Config, frame: Path, prompt: str, duration: int) -> dict:
    """Start one HeyGen Video 1 job from a 4:3 first frame: it makes the clip's own sound (effects, an
    on-camera voice). First frame only, never input_references: $0.01/s at 480p against $0.05/s."""
    opts = settings(cfg)
    image = "data:image/png;base64," + base64.b64encode(frame.read_bytes()).decode()
    body = {
        "model": opts.get("talkie_model", "heygen/heygen-video-1"),
        "prompt": prompt,
        "duration": int(duration),
        "resolution": opts.get("talkie_resolution", "480p"),
        "aspect_ratio": "4:3",
        "frame_images": [{"type": "image_url", "image_url": {"url": image}, "frame_type": "first_frame"}],
    }
    job = _request(cfg, "POST", opts.get("base_url", "https://openrouter.ai/api/v1") + "/videos", body)
    return {"id": job["id"], "duration": duration}


def clip_audio(src: Path, dst: Path, seconds: float) -> Path | None:
    """The clip's own soundtrack as a WAV, or None if it has none."""
    try:
        _run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000",
              "-t", f"{seconds:.3f}", str(dst)])
    except MediaError:  # no audio stream
        return None
    return dst
