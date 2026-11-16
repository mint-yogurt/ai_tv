"""Machine status for the dashboard: GPU, memory, disk, load, worker liveness."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone

from . import db
from .config import Config

WORKER_STALE_SECONDS = 20


def gpu() -> dict | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    name, used, total, util, temp = [p.strip() for p in out.splitlines()[0].split(",")]
    return {"name": name, "mem_used_mb": int(used), "mem_total_mb": int(total), "util_pct": int(util), "temp_c": int(temp)}


def memory() -> dict:
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) // 1024
    return {"total_mb": info.get("MemTotal", 0), "available_mb": info.get("MemAvailable", 0)}


def disk(cfg: Config) -> dict:
    usage = shutil.disk_usage(cfg.library)
    return {"free_gb": usage.free / 1e9, "total_gb": usage.total / 1e9}


def worker(conn) -> dict:
    beat = db.get_meta(conn, "worker_heartbeat")
    alive = False
    age = None
    if beat:
        then = datetime.strptime(beat, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - then).total_seconds()
        alive = age < WORKER_STALE_SECONDS
    current = db.get_meta(conn, "worker_current_job") or ""
    return {"alive": alive, "heartbeat_age_s": age, "current_job": int(current) if current.isdigit() else None}


def snapshot(conn, cfg: Config) -> dict:
    return {
        "gpu": gpu(),
        "memory": memory(),
        "disk": disk(cfg),
        "load": os.getloadavg(),
        "worker": worker(conn),
        "llm_backend": cfg.section("llm").get("backend", "none"),
        "tools": {name: bool(cfg.tool(name)) for name in ("whisper", "pocket_tts", "comfyui_url")},
    }


def someone_watching() -> bool:
    """True while ChupTV is streaming to someone: ErsatzTV only starts its encoder (ffmpeg or
    ersatztv-channel) when a client connects, so a child encoder of the ErsatzTV process means a viewer.
    Background CPU work (the footage index) waits while this is true."""
    procs = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                stat = f.read()
        except OSError:
            continue
        name = stat[stat.index("(") + 1:stat.rindex(")")]
        ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        procs[int(entry)] = (name, ppid)
    ersatz = {pid for pid, (name, _) in procs.items() if name.startswith("ErsatzTV")}
    return any(ppid in ersatz and (name.startswith("ffmpeg") or name.startswith("ersatztv-chann"))
               for name, ppid in procs.values())
