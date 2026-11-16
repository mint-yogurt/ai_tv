"""Speech in a cloned voice, via Pocket TTS on the CPU. Used by voice previews and the voice stage."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from pathlib import Path

from . import db, media
from .config import Config
from .ingest import REFERENCE_METHOD
from .jobs import Blocked


def binary(cfg: Config) -> str:
    path = cfg.tool("pocket_tts")
    if not path:
        raise Blocked("Pocket TTS isn't installed yet. Set [tools] pocket_tts in config/aitv.toml, then retry.")
    return path if path.startswith("/") else str(cfg.root / path)


def reference_for_asset(conn: sqlite3.Connection, cfg: Config, asset: sqlite3.Row, log=lambda line: None) -> Path:
    """The ~15 s reference for one voice recording, rebuilt if it was cut with an older method."""
    meta = db.loads(asset["meta"])
    if meta.get("reference_method") != REFERENCE_METHOD or not meta.get("derived", {}).get("reference"):
        log(f"rebuilding the voice reference for asset {asset['id']} with the current method")
        derived_dir = cfg.store / "derived" / asset["sha256"]
        derived_dir.mkdir(parents=True, exist_ok=True)
        ref, seconds = media.voice_reference(cfg.library / asset["path"], derived_dir / "reference.wav")
        meta.setdefault("derived", {})["reference"] = str(ref.relative_to(cfg.library))
        meta.update(reference_duration_s=seconds, reference_method=REFERENCE_METHOD)
        conn.execute("UPDATE assets SET meta = ? WHERE id = ?", (db.dumps(meta), asset["id"]))
    return cfg.library / meta["derived"]["reference"]


def reference_for_voice(conn: sqlite3.Connection, cfg: Config, voice_id: int, log=lambda line: None) -> Path:
    """The reference of a voice's newest ready recording."""
    asset = conn.execute(
        "SELECT a.* FROM assets a JOIN asset_entities ae ON ae.asset_id = a.id "
        "WHERE ae.entity_id = ? AND ae.role = 'voice_reference' AND a.status = 'ready' ORDER BY a.id DESC LIMIT 1",
        (voice_id,),
    ).fetchone()
    if asset is None:
        name = conn.execute("SELECT name FROM entities WHERE id = ?", (voice_id,)).fetchone()
        raise Blocked(f"voice {name['name'] if name else voice_id} has no ready recording")
    return reference_for_asset(conn, cfg, asset, log)


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_DIGIT = ["oh"] + _ONES[1:10]  # phone numbers say 0 as "oh"
_TOLL_FREE = {"800", "888", "877", "866", "855", "844", "833", "900"}
_PRICE = re.compile(r"\$(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?(?!\d)")
_PHONE = re.compile(r"(?<![\w-])(?:1-)?\d{3}-[0-9A-Za-z]{2,}(?:-[0-9A-Za-z]+)*(?![\w-])")


def _words(n: int) -> str:
    """A whole number below a million in words ("forty-nine", "one thousand two hundred")."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        return f"{_ONES[n // 100]} hundred" + (f" {_words(n % 100)}" if n % 100 else "")
    return f"{_words(n // 1000)} thousand" + (f" {_words(n % 1000)}" if n % 1000 else "")


def _price(m: re.Match) -> str:
    dollars, cents = int(m.group(1).replace(",", "")), m.group(2)
    if dollars >= 1_000_000:
        return m.group(0)
    if cents is None or cents == "00":
        return f"{_words(dollars)} dollar{'' if dollars == 1 else 's'}"
    if dollars == 0:
        return f"{_words(int(cents))} cents"
    # As announcers read prices: $3.99 "three ninety-nine", $19.05 "nineteen oh five".
    return f"{_words(dollars)} {_words(int(cents)) if cents[0] != '0' else 'oh ' + _ONES[int(cents[1])]}"


def _phone(m: re.Match) -> str:
    groups = m.group(0).split("-")
    digits = sum(c.isdigit() for c in m.group(0))
    if not (groups[0] == "1" or groups[0] in _TOLL_FREE or digits >= 7):
        return m.group(0)  # "100-percent", "360-degree": not a number to dial
    said = []
    for i, g in enumerate(groups):
        if g.isdigit() and g in ("800", "900"):
            said.append(f"{_ONES[int(g[0])]} hundred")
        elif g.isdigit():
            said.append(" ".join(_DIGIT[int(c)] for c in g))
        elif i and not groups[i - 1].isdigit():
            said[-1] += f" {g.lower()}"  # 1-800-COPE-BALDY: "cope baldy", one phrase
        else:
            said.append(g.lower())  # a vanity word is said as the word: 1-800-DROOL
    return ", ".join(said)


def spoken(text: str) -> str:
    """Prices and phone numbers written out the way an announcer says them, so the TTS doesn't guess
    ("$3.99" came out as "three dollars", "1-800-555-DROOL" as "1-855-DRAWL"). The rest is left alone."""
    return _PRICE.sub(_price, _PHONE.sub(_phone, text))


def speak(cfg: Config, text: str, reference: Path, out: Path, timeout: int = 900) -> float:
    """Say text in the reference's voice into out (WAV). Returns seconds taken."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [binary(cfg), "generate", "-q", "--text", spoken(text), "--voice", str(reference), "--output-path", str(out)]
    if cfg.tool("pocket_tts_language"):
        cmd += ["--language", cfg.tool("pocket_tts_language")]
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"pocket-tts failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[-500:]}")
    return time.monotonic() - started
