"""Lip-sync a still picture to a voice clip on the GTX 970: SadTalker or Wav2Lip.

Both live in tools/ with their own venv (tools/lipsync-venv) and were compared in
test_outputs/video/2026-11-11_lipsync. Run through the worker's `lipsync.render` job so the GPU has
one owner: ComfyUI is stopped first, and the job blocks while the card doesn't have room.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import comfy
from .config import Config
from .jobs import Blocked

ENGINES = ("sadtalker", "wav2lip")
NEEDS_MIB = 3300  # both peaked at ~3.1 GB in the Nov 2026 test


def free_vram_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def render(cfg: Config, engine: str, image: Path, audio: Path, out: Path, opts: dict | None = None,
           log=lambda line: None) -> dict:
    """Make out (MP4, the image's full size) of the picture saying the audio. Returns timings."""
    opts = opts or {}
    if engine not in ENGINES:
        raise ValueError(f"unknown lip-sync engine {engine!r}; use one of {ENGINES}")
    tools = cfg.path("tools")
    python = tools / "lipsync-venv/bin/python"
    if not python.is_file():
        raise Blocked(f"the lip-sync venv is missing ({python})")
    comfy.shutdown()  # ComfyUI keeps ~1 GB of the card while it idles
    if comfy.free_memory(cfg):  # one this worker didn't start: unload its models instead
        log("asked ComfyUI to unload its models")
        time.sleep(3)
    free = free_vram_mib()
    if free is not None and free < NEEDS_MIB:
        raise Blocked(f"only {free} MiB of GPU memory free, lip-sync needs ~{NEEDS_MIB}; "
                      "something else is using the card (a live stream encode?). Retry later.")
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1"}  # model files load as data only
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="lipsync_") as tmp:
        if engine == "sadtalker":
            cmd = [str(python), "inference.py", "--driven_audio", str(audio), "--source_image", str(image),
                   "--result_dir", tmp, "--preprocess", "full", "--size", "256",
                   "--batch_size", str(opts.get("batch_size", 2))]
            if opts.get("still", True):
                cmd.append("--still")  # keeps the head on the still's pose, so camera cuts stay clean
            if opts.get("enhancer"):
                cmd += ["--enhancer", opts["enhancer"]]
            cwd = tools / "sadtalker"
        else:
            result = Path(tmp) / "out.mp4"
            cmd = [str(python), "inference.py", "--checkpoint_path", "checkpoints/wav2lip.pth",
                   "--face", str(image), "--audio", str(audio), "--outfile", str(result), "--static", "True",
                   "--fps", str(opts.get("fps", 25)), "--pads", *map(str, opts.get("pads", [0, 15, 0, 0])),
                   "--face_det_batch_size", "4", "--wav2lip_batch_size", "32"]
            cwd = tools / "wav2lip"
        log(f"{engine}: {image.name} + {audio.name}")
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=int(opts.get("timeout", 3600)))
        if proc.returncode != 0:
            raise RuntimeError(f"{engine} failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-800:]}")
        made = sorted(Path(tmp).rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if not made:
            raise RuntimeError(f"{engine} finished but wrote no video: {(proc.stdout or '')[-400:]}")
        shutil.move(str(made[-1]), out)
    return {"engine": engine, "out": str(out), "took_s": round(time.monotonic() - started, 1)}
