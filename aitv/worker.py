"""The background worker: runs queued jobs one at a time and watches the inbox.

Run by systemd at low CPU and I/O priority so the website on this box stays responsive.
"""

from __future__ import annotations

import contextlib
import os
import random
import shutil
import sys
import signal
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

from . import comfy, db, ingest, jobs, lipsync, media, online, pipeline, tts
from .config import Config
from .jobs import Blocked, Stopped

Handler = Callable[[object, Config, dict, Callable[[str], None]], dict | None]


def handle_ingest(conn, cfg, payload, log):
    return ingest.process(conn, cfg, int(payload["asset_id"]), log)


def handle_voice_transcribe(conn, cfg, payload, log):
    if not cfg.tool("whisper"):
        raise Blocked("Whisper isn't installed yet. Set [tools] whisper in config/aitv.toml, then retry.")
    raise Blocked("transcription isn't wired up yet (planned right after the Whisper install test)")


PREVIEW_LINE = ("Hi, this is {name}. This is how I sound when the station makes me read something out loud, "
                "so, you know, no pressure.")


def handle_voice_preview(conn, cfg, payload, log):
    """Speak a test line in the cloned voice so it can be judged on the voice's page."""
    tts.binary(cfg)  # blocks the job if Pocket TTS isn't configured
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (payload["asset_id"],)).fetchone()
    if asset is None or asset["status"] != "ready":
        raise Blocked("the voice recording isn't ingested yet")
    voice = conn.execute("SELECT name FROM entities WHERE id = ?", (payload.get("voice_id"),)).fetchone()
    reference = tts.reference_for_asset(conn, cfg, asset, log)
    out = cfg.store / "derived" / asset["sha256"] / "tts_preview.wav"
    log("generating preview with Pocket TTS")
    took = tts.speak(cfg, PREVIEW_LINE.format(name=voice["name"] if voice else "a new voice"), reference, out)
    asset = conn.execute("SELECT meta FROM assets WHERE id = ?", (asset["id"],)).fetchone()  # may have been rebuilt
    meta = db.loads(asset["meta"])
    meta["derived"]["tts_preview"] = str(out.relative_to(cfg.library))
    conn.execute("UPDATE assets SET meta = ? WHERE id = ?", (db.dumps(meta), payload["asset_id"]))
    seconds = media.probe(out)["duration_s"]
    log(f"made {seconds:.1f}s of speech in {took:.1f}s")
    return {"preview": meta["derived"]["tts_preview"], "audio_s": seconds, "took_s": round(took, 1)}


MAX_BATCH = 4


def handle_image_generate(conn, cfg, payload, log):
    """Render images with ComfyUI and file them in the library like any upload."""
    if not cfg.tool("comfyui_url"):
        raise Blocked("ComfyUI isn't configured. Set [tools] comfyui_url in config/aitv.toml, then retry.")
    images = cfg.section("images")
    preset = payload.get("preset", "draft")
    if preset not in images.get("presets", {}):
        raise ValueError(f"unknown image preset {preset!r}")
    count = max(1, min(MAX_BATCH, int(payload.get("count", 1))))
    seed = int(payload["seed"]) if str(payload.get("seed", "")).strip().isdigit() else random.randrange(2**31)
    width, height = int(payload.get("width") or images.get("width", 640)), int(payload.get("height") or images.get("height", 480))
    negative = payload.get("negative") or images.get("negative", "")

    comfy.ensure_running(cfg, log)
    out_dir = cfg.uploads / f"gen_{uuid.uuid4().hex[:10]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"rendering {count} x {width}x{height}, preset {preset}, seed {seed}")
    started = time.monotonic()
    graph = comfy.workflow(images, preset, payload["prompt"], negative, width, height, seed, count, "aitv")
    paths = comfy.render(cfg, graph, out_dir)
    took = time.monotonic() - started
    log(f"rendered {len(paths)} image(s) in {took:.1f}s")

    entity_id = payload.get("entity_id")
    created = []
    for i, path in enumerate(paths):
        generation = {"prompt": payload["prompt"], "negative": negative, "preset": preset, "seed": seed,
                      "batch_index": i, "checkpoint": images.get("checkpoint"), "seconds": round(took / len(paths), 1)}
        created.append(ingest.register(
            conn, path, "image", title=payload.get("title") or f"generated {seed}-{i + 1}",
            original_name=path.name, source="generated", entity_id=entity_id, role=payload.get("role", "reference"),
            tags=["generated", preset], extra_meta={"generation": generation},
        ))
    return {"assets": created, "seed": seed, "seconds": round(took, 1)}


def handle_online_fetch(conn, cfg, payload, log):
    """Download a search result picked on the Find page (or a culled asset again)."""
    asset_id = online.fetch(conn, cfg, payload["result"], payload.get("category"), payload.get("tags"),
                            payload.get("entity_id"), log)
    if payload.get("replaces"):
        conn.execute("DELETE FROM assets WHERE id = ? AND status = 'culled'", (payload["replaces"],))
    return {"asset_id": asset_id}


def handle_cull(conn, cfg, payload, log):
    result = online.cull(conn, cfg, log)
    # Tests folders holding a .temporary marker are for a quick look only: gone at the next cleanup.
    removed = []
    for marker in sorted(cfg.path("test_outputs").rglob(".temporary")):
        shutil.rmtree(marker.parent)
        removed.append(str(marker.parent.relative_to(cfg.path("test_outputs"))))
        log(f"removed temporary test folder {removed[-1]}")
    return {**result, "temporary_tests_removed": removed}


def handle_production(conn, cfg, payload, log):
    return pipeline.run_production(conn, cfg, int(payload["production_id"]), log)


def handle_lipsync(conn, cfg, payload, log):
    """Lip-sync a still to an audio clip. Used by newsgen's news segments (paths are absolute)."""
    result = lipsync.render(cfg, payload.get("engine", "sadtalker"), Path(payload["image"]), Path(payload["audio"]),
                            Path(payload["out"]), payload.get("opts"), log)
    log(f"done in {result['took_s']}s -> {result['out']}")
    return result


def handle_footage_index(conn, cfg, payload, log):
    """Index footage files (pipeline v2): scenes, thumbnails and a Whisper transcript, one file at a time.
    Stops between files while someone is watching ChupTV; the next sweep picks up the rest."""
    from . import footage, system

    ids = payload.get("ids") or footage.sweep(conn, cfg, int(payload.get("limit", 20)))
    done = []
    for fid in ids:
        if system.someone_watching():
            raise Blocked(f"someone is watching ChupTV; {len(ids) - len(done)} file(s) left for the next sweep")
        try:
            footage.index(conn, cfg, fid, log)
            done.append(fid)
        except Exception as exc:  # noqa: BLE001  one bad file doesn't stop the rest
            log(f"footage {fid}: {type(exc).__name__}: {exc}")
    return {"indexed": done}


def handle_rescan(conn, cfg, payload, log):
    created = ingest.scan_inbox(conn, cfg)
    log(f"registered {len(created)} new inbox file(s)")
    return {"registered": created}


HANDLERS: dict[str, Handler] = {
    "ingest": handle_ingest,
    "voice.transcribe": handle_voice_transcribe,
    "voice.preview": handle_voice_preview,
    "image.generate": handle_image_generate,
    "online.fetch": handle_online_fetch,
    "library.cull": handle_cull,
    "production.run": handle_production,
    "library.rescan": handle_rescan,
    "lipsync.render": handle_lipsync,
    "footage.index": handle_footage_index,
}


def run_one(conn, cfg: Config, job) -> None:
    job_id = job["id"]

    def log(line: str) -> None:
        jobs.log(conn, job_id, line)

    handler = HANDLERS.get(job["kind"])
    try:
        if handler is None:
            raise Blocked(f"no handler for job kind {job['kind']!r}")
        result = handler(conn, cfg, db.loads(job["payload"]), log)
        jobs.finish(conn, job_id, result)
    except Stopped as exc:
        log(f"stopped: {exc}")
        jobs.fail(conn, job_id, str(exc), status="cancelled")
    except Blocked as exc:
        log(f"blocked: {exc}")
        jobs.fail(conn, job_id, str(exc), status="blocked")
    except Exception as exc:  # noqa: BLE001 - a failed job must never take the worker down
        log(traceback.format_exc())
        jobs.fail(conn, job_id, f"{type(exc).__name__}: {exc}")
        if job["kind"] == "ingest" and job["asset_id"]:
            conn.execute("UPDATE assets SET status = 'failed' WHERE id = ? AND status != 'ready'", (job["asset_id"],))


@contextlib.contextmanager
def _heartbeat(cfg: Config, every: float):
    """Keep the heartbeat going while one job runs (a production run can take many minutes), from a
    thread with its own connection."""
    done = threading.Event()

    def beat():
        c = db.connect(cfg.db_path)
        while not done.wait(every):
            try:
                db.set_meta(c, "worker_heartbeat", db.now())
            except Exception:  # noqa: BLE001  (a locked database just skips a beat)
                pass
        c.close()

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()


def code_fingerprint(cfg: Config) -> int:
    """A hash of every package .py file's and config/aitv.toml's path and mtime (format files are read
    fresh by every run, so they don't count)."""
    package = Path(__file__).resolve().parent
    files = sorted([*package.rglob("*.py"), cfg.root / "config" / "aitv.toml"])
    return hash(tuple((str(f), f.stat().st_mtime_ns) for f in files if f.is_file()))


def _imports_cleanly() -> str | None:
    """None if a fresh interpreter can load the worker and the config from disk, else the error."""
    try:
        proc = subprocess.run([sys.executable, "-c", "import aitv.worker; from aitv import config; config.load()"],
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "the import check timed out"
    return None if proc.returncode == 0 else (proc.stderr.strip().splitlines() or ["(no output)"])[-1]


def serve(cfg: Config) -> None:
    media.configure(cfg.tool("ffmpeg"), cfg.tool("ffprobe"))
    conn = db.connect(cfg.db_path)
    db.init(conn)
    requeued = jobs.requeue_stale(conn)
    if requeued:
        print(f"requeued {requeued} job(s) left running by a previous worker", flush=True)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    wcfg = cfg.section("worker")
    poll = float(wcfg.get("poll_seconds", 2))
    scan_every = float(wcfg.get("inbox_scan_seconds", 60))
    last_scan = 0.0
    cull_every = float(cfg.section("online").get("cull_every_hours", 6)) * 3600
    last_cull = time.monotonic()  # the first cleanup runs one interval after start
    fcfg = cfg.section("footage")
    sweep_every = float(fcfg.get("sweep_hours", 6)) * 3600 if fcfg.get("enabled") else 0
    last_sweep = time.monotonic() - sweep_every + 300 if sweep_every else 0.0  # first sweep 5 min after start
    # Between jobs, a change to the code or config restarts the worker in place (same PID, so systemd
    # doesn't notice), once the files have stopped changing (an edit across several files) and the new
    # code is known to import. Broken code leaves the old one running. The web app isn't reloaded.
    settle = float(wcfg.get("inbox_settle_seconds", 10))
    loaded, broken, seen, since = code_fingerprint(cfg), None, None, 0.0
    print("worker ready", flush=True)
    while not stopping:
        current = code_fingerprint(cfg)
        if current != seen:
            seen, since = current, time.monotonic()
        if current != loaded and current != broken and time.monotonic() - since >= settle:
            error = _imports_cleanly()
            if error is None:
                print("code changed, restarting the worker", flush=True)
                comfy.shutdown()
                conn.close()
                os.execv(sys.executable, [sys.executable, *sys.argv])
            print(f"code changed but doesn't import ({error}); still running the old code", flush=True)
            broken = current
        db.set_meta(conn, "worker_heartbeat", db.now())
        if time.monotonic() - last_scan > scan_every:
            last_scan = time.monotonic()
            try:
                ingest.scan_inbox(conn, cfg)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        if cull_every and time.monotonic() - last_cull > cull_every:
            last_cull = time.monotonic()
            if not conn.execute("SELECT 1 FROM jobs WHERE kind = 'library.cull' AND status IN ('queued', 'running')").fetchone():
                jobs.enqueue(conn, "library.cull", {}, priority=-5)
        if sweep_every and time.monotonic() - last_sweep > sweep_every:
            last_sweep = time.monotonic()
            if not conn.execute("SELECT 1 FROM jobs WHERE kind = 'footage.index' AND status IN ('queued', 'running')").fetchone():
                jobs.enqueue(conn, "footage.index", {}, priority=-6)
        job = jobs.claim(conn)
        if job is None:
            if comfy.stop_if_idle(cfg):
                print("stopped idle ComfyUI", flush=True)
            time.sleep(poll)
            continue
        print(f"job {job['id']} {job['kind']}", flush=True)
        db.set_meta(conn, "worker_current_job", str(job["id"]))
        with _heartbeat(cfg, poll):
            run_one(conn, cfg, job)
        db.set_meta(conn, "worker_current_job", "")
    comfy.shutdown()
    print("worker stopped", flush=True)
