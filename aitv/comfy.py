"""Image generation through a local ComfyUI, started on demand by the worker.

ComfyUI holds ~2 GB of RAM and the GPU while it runs, so it isn't a permanent service:
the first image job starts it, and the worker stops it after a few idle minutes.
Settings (checkpoint, presets) live in [images] in config/aitv.toml; the GTX 970 numbers
behind the presets are in test_outputs/image/.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config


class ComfyError(RuntimeError):
    pass


_server: subprocess.Popen | None = None
_last_used = 0.0


def _url(cfg: Config) -> str:
    return cfg.tool("comfyui_url").rstrip("/")


def _call(cfg: Config, path: str, body: dict | None = None, timeout: int = 30) -> bytes:
    req = urllib.request.Request(_url(cfg) + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def is_up(cfg: Config) -> bool:
    try:
        _call(cfg, "/system_stats", timeout=3)
        return True
    except (OSError, urllib.error.URLError):
        return False


def ensure_running(cfg: Config, log=lambda line: None) -> None:
    global _server, _last_used
    _last_used = time.monotonic()
    if is_up(cfg):
        return
    home = (cfg.root / cfg.tool("comfyui")).resolve()
    python = home / ".venv" / "bin" / "python"
    if not python.is_file():
        raise ComfyError(f"ComfyUI isn't installed at {home}")
    port = _url(cfg).rsplit(":", 1)[-1]
    log("starting ComfyUI")
    logfile = open(home / "server.log", "a")
    _server = subprocess.Popen(
        [str(python), "main.py", "--listen", "127.0.0.1", "--port", port, *cfg.section("images").get("comfyui_args", [])],
        cwd=home, stdout=logfile, stderr=subprocess.STDOUT,
    )
    for _ in range(180):
        if _server.poll() is not None:
            raise ComfyError(f"ComfyUI exited at startup ({_server.returncode}); see {home / 'server.log'}")
        if is_up(cfg):
            log("ComfyUI is up")
            return
        time.sleep(1)
    raise ComfyError("ComfyUI didn't come up within 3 minutes")


def free_memory(cfg: Config) -> bool:
    """Ask a running ComfyUI to unload its models and give back GPU memory. Works on one this process
    didn't start (e.g. left over from before an in-place worker restart), without stopping it."""
    if not cfg.tool("comfyui_url") or not is_up(cfg):
        return False
    _call(cfg, "/free", {"unload_models": True, "free_memory": True})
    return True


def stop_if_idle(cfg: Config) -> bool:
    """Called from the worker loop. Only stops a ComfyUI this process started."""
    global _server
    idle = float(cfg.section("images").get("idle_shutdown_seconds", 600))
    if _server is not None and _server.poll() is None and time.monotonic() - _last_used > idle:
        _server.terminate()
        try:
            _server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _server.kill()
        _server = None
        return True
    return False


def shutdown() -> None:
    global _server
    if _server is not None and _server.poll() is None:
        _server.terminate()
        _server.wait(timeout=30)
    _server = None


def workflow(images_cfg: dict, preset: str, prompt: str, negative: str, width: int, height: int,
             seed: int, batch: int, prefix: str) -> dict:
    p = images_cfg["presets"][preset]
    g = {
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": images_cfg["checkpoint"]}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": images_cfg["vae"]}},
        "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch}},
    }
    model, clip = ["ckpt", 0], ["ckpt", 1]
    if p.get("lcm"):
        g["lora"] = {"class_type": "LoraLoader", "inputs": {
            "lora_name": images_cfg["lcm_lora"], "strength_model": 1.0, "strength_clip": 1.0, "model": model, "clip": clip}}
        model, clip = ["lora", 0], ["lora", 1]
    g["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": clip}}
    g["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip}}
    g["sample"] = {"class_type": "KSampler", "inputs": {
        "seed": seed, "steps": p["steps"], "cfg": p["cfg"], "sampler_name": p["sampler"], "scheduler": p["scheduler"],
        "denoise": 1.0, "model": model, "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0]}}
    g["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}}
    g["save"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["decode", 0]}}
    return g


def img2img_workflow(images_cfg: dict, preset: str, image_name: str, prompt: str, negative: str, seed: int,
                     denoise: float, prefix: str) -> dict:
    """Redraw an image already in ComfyUI's input folder (see put_input) at a low denoise."""
    g = workflow(images_cfg, preset, prompt, negative, 8, 8, seed, 1, prefix)
    del g["latent"]
    g["img"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    g["enc"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["img", 0], "vae": ["vae", 0]}}
    g["sample"]["inputs"].update(latent_image=["enc", 0], denoise=denoise)
    return g


def put_input(cfg: Config, src: Path, name: str) -> str:
    """Copy an image into ComfyUI's input folder for LoadImage; returns the name to use."""
    import shutil

    folder = (cfg.root / cfg.tool("comfyui")).resolve() / "input"
    folder.mkdir(exist_ok=True)
    shutil.copy(src, folder / name)
    return name


def render(cfg: Config, graph: dict, out_dir: Path, timeout: int = 1800) -> list[Path]:
    """Queue a workflow, wait for it, download every image it saved into out_dir."""
    global _last_used
    pid = json.loads(_call(cfg, "/prompt", {"prompt": graph}))["prompt_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hist = json.loads(_call(cfg, f"/history/{pid}"))
        if pid in hist:
            entry = hist[pid]
            if entry.get("status", {}).get("status_str") == "error":
                msgs = [m for m in entry["status"].get("messages", []) if m[0] == "execution_error"]
                raise ComfyError(json.dumps(msgs[-1][1] if msgs else entry["status"])[:600])
            paths = []
            for img in entry["outputs"]["save"]["images"]:
                data = _call(cfg, f"/view?filename={img['filename']}&subfolder={img['subfolder']}&type={img['type']}")
                path = out_dir / img["filename"]
                path.write_bytes(data)
                paths.append(path)
            _last_used = time.monotonic()
            return paths
        time.sleep(0.5)
    raise ComfyError("timed out waiting for ComfyUI")
