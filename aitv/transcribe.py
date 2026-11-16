"""Speech to text with local Whisper (pipeline v2), cached by file content in the `transcripts` table.

The cache key is the ChupTV director's file fingerprint (sha1 of the size plus the first and last
1 MB), so a moved or renamed file is never transcribed twice, and either project can read the other's
transcripts. Whisper runs in its own venv (tools/whisper) as a subprocess: [tools] whisper is that
venv's python, [whisper] holds the model, threads and chunk length.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

from .config import Config
from .db import dumps, loads
from .jobs import Blocked

SCRIPT = "tools/whisper/transcribe.py"


def fingerprint(path: Path) -> str:
    """Identity of the exact bytes (the same function as chuptv_director's media.fingerprint)."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read())
    return h.hexdigest()


def available(cfg: Config) -> bool:
    tool = cfg.tool("whisper")
    return bool(tool) and _resolve(cfg, tool).exists()


def _resolve(cfg: Config, tool: str) -> Path:
    p = Path(tool).expanduser()
    return p if p.is_absolute() else cfg.root / p


def cached(conn: sqlite3.Connection, path: Path) -> dict | None:
    row = conn.execute("SELECT * FROM transcripts WHERE fingerprint = ?", (fingerprint(path),)).fetchone()
    return _row(row) if row else None


def _row(row) -> dict:
    return {"model": row["model"], "language": row["language"], "duration": row["duration_s"],
            "segments": loads(row["segments"], []), "words": loads(row["words"], [])}


def transcribe(conn: sqlite3.Connection, cfg: Config, path: Path, log=lambda line: None) -> dict:
    """{"segments": [[start, end, text]], "words": [[start, end, word]], ...} for a file, from the cache
    or a fresh Whisper run. Raises Blocked if Whisper isn't installed."""
    path = Path(path)
    fp = fingerprint(path)
    row = conn.execute("SELECT * FROM transcripts WHERE fingerprint = ?", (fp,)).fetchone()
    if row:
        return _row(row)
    if not available(cfg):
        raise Blocked("Whisper isn't installed ([tools] whisper in config/aitv.toml)")
    opts = cfg.section("whisper")
    args = [str(_resolve(cfg, cfg.tool("whisper"))), str(cfg.root / SCRIPT), str(path),
            "--model", str(opts.get("model", "small")), "--threads", str(opts.get("threads", 2)),
            "--chunk", str(opts.get("chunk_seconds", 120))]
    if opts.get("language"):
        args += ["--language", str(opts["language"])]
    log(f"transcribing {path.name} with Whisper")
    proc = subprocess.run(args, capture_output=True, text=True, timeout=int(opts.get("timeout_seconds", 7200)))
    if proc.returncode != 0:
        raise RuntimeError(f"Whisper failed on {path.name}: {proc.stderr.strip().splitlines()[-1:]}")
    data = json.loads(proc.stdout)
    conn.execute("INSERT OR REPLACE INTO transcripts (fingerprint, path, duration_s, model, language, segments, words) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (fp, str(path), data.get("duration"), data["model"], data.get("language"),
                  dumps(data["segments"]), dumps(data["words"])))
    return data


# ---------------------------------------------------------------- matching what was said

def norm_words(text: str) -> list[str]:
    """Lower-case words without punctuation: "Prime Cheese." and "prime cheese" match."""
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def words_between(words: list, start: float, end: float) -> list[str]:
    return [w for s, e, text in words if e > start and s < end for w in norm_words(text)]


NUMBER_WORDS = set("""zero oh one two three four five six seven eight nine ten eleven twelve thirteen fourteen
fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand
million dollars dollar cents cent""".split())


def _wordy(words: list[str]) -> list[str]:
    """Without numbers: Whisper writes "nineteen ninety-five" as "1995" or "$19.95", so they're not compared."""
    return [w for w in words if w not in NUMBER_WORDS and not any(ch.isdigit() for ch in w)]


def match_score(expected: str, heard: list[str]) -> float:
    """Share of the expected words heard, in order (a longest common subsequence), 0..1. Numbers are left out."""
    want, heard = _wordy(norm_words(expected)), _wordy(heard)
    if not want:
        return 1.0
    prev = [0] * (len(heard) + 1)
    for w in want:
        row = [0]
        for j, h in enumerate(heard, 1):
            row.append(prev[j - 1] + 1 if _close(w, h) else max(prev[j], row[j - 1]))
        prev = row
    return prev[-1] / len(want)


def _close(a: str, b: str) -> bool:
    """Same word, or near enough for Whisper's spelling of a made-up name ("chuptv" / "chuptv's")."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def find_phrase(words: list, phrase: str, min_score: float = 0.8) -> list[tuple[float, float, float]]:
    """Every place a phrase is said: [(start, end, score)], best first. Word-timed in-points for footage."""
    want = norm_words(phrase)
    if not want:
        return []
    flat = [(s, e, w) for s, e, text in words for w in norm_words(text)]
    out = []
    n = len(want)
    for i in range(len(flat)):
        window = flat[i:i + n + 2]
        if not window or not _close(window[0][2], want[0]):
            continue
        score = match_score(phrase, [w for _, _, w in window])
        if score >= min_score:
            hit = window[:n] if len(window) >= n else window
            out.append((hit[0][0], hit[-1][1], round(score, 2)))
    return sorted(out, key=lambda t: -t[2])
