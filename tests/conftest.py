import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from aitv import db
from aitv.config import Config

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_price_fetch(monkeypatch):
    """OpenRouter's price list is never fetched; a test that wants one sets prices._fetch itself."""
    from aitv import prices

    def offline(path):
        raise OSError("tests are offline")
    monkeypatch.setattr(prices, "_fetch", offline)
    monkeypatch.setattr(prices, "_cache", {})


@pytest.fixture(autouse=True)
def no_discord(tmp_path, monkeypatch):
    """Production results go to a temp outbox, never Chuppybot's real one (which DMs the user)."""
    from aitv import notify
    (tmp_path / "outbox").mkdir()
    monkeypatch.setattr(notify, "OUTBOX", tmp_path / "outbox")


@pytest.fixture
def cfg(tmp_path) -> Config:
    with open(REPO / "config" / "aitv.toml", "rb") as f:
        raw = tomllib.load(f)
    raw["worker"]["inbox_settle_seconds"] = 0
    raw["tools"].update(pocket_tts="", whisper="")  # tests never run the real models
    raw["llm"]["backend"] = "manual"  # and never call a paid API
    raw["videogen"]["enabled"] = False
    raw["edit"]["mode"], raw["review"]["enabled"] = "off", False
    raw["publish"] = {"director": "/nonexistent/chuptv", "catalog_db": "/nonexistent/chuptv.db", "places": []}  # never the real library
    shutil.copytree(REPO / "formats", tmp_path / "formats")
    for fmt in (tmp_path / "formats").glob("*/format.toml"):  # MIDIgen needs an LLM; tests use library music
        fmt.write_text(fmt.read_text().replace('source = "midigen"', 'source = "library"'))
    shutil.copytree(REPO / "config", tmp_path / "config", ignore=shutil.ignore_patterns("secrets.env"))
    toml = tmp_path / "config" / "aitv.toml"
    toml.write_text(toml.read_text().replace('backend = "openai_compat"', 'backend = "manual"')
                    .replace("enabled = true", "enabled = false"))
    c = Config(raw=raw, root=tmp_path)
    c.ensure_dirs()
    return c


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg.db_path)
    db.init(c)
    yield c
    c.close()


def make_media(kind: str, dest: Path) -> Path:
    """Generate a tiny test image, tone or clip with FFmpeg."""
    args = {
        "image": ["-f", "lavfi", "-i", "testsrc=size=640x480", "-frames:v", "1"],
        "tone": ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=1", "-f", "lavfi", "-i", "sine=f=330:d=2",
                 "-filter_complex", "[0][1]concat=n=2:v=0:a=1"],
        "silence": ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=3"],
        "clip": ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=12", "-f", "lavfi", "-i", "sine=f=440",
                 "-t", "2", "-shortest", "-pix_fmt", "yuv420p"],
    }[kind]
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(dest)], check=True)
    return dest
