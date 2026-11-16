"""Loads config/aitv.toml and resolves project paths."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

ROOT = Path(os.environ.get("AITV_ROOT", Path(__file__).resolve().parent.parent))

# Inbox subfolder -> asset category.
INBOX_FOLDERS = {
    "images": "image",
    "voices": "voice",
    "sfx": "sfx",
    "music": "music",
    "footage": "footage",
}
CATEGORIES = tuple(INBOX_FOLDERS.values())


@dataclass
class Config:
    raw: dict
    root: Path

    def section(self, name: str) -> dict:
        node = self.raw
        for part in name.split("."):
            node = node.get(part, {})
        return node

    def path(self, key: str) -> Path:
        return (self.root / self.raw["paths"][key]).resolve()

    @cached_property
    def db_path(self) -> Path:
        return self.path("data") / "aitv.sqlite3"

    @property
    def library(self) -> Path:
        return self.path("library")

    @property
    def inbox(self) -> Path:
        return self.library / "inbox"

    @property
    def uploads(self) -> Path:
        # Hidden, so the inbox scanner never picks up a half-written upload.
        return self.inbox / ".uploads"

    @property
    def store(self) -> Path:
        return self.library / "store"

    def tool(self, name: str) -> str:
        return self.raw.get("tools", {}).get(name, "") or ""

    def ensure_dirs(self) -> None:
        for key in ("data", "ideas", "productions", "formats", "tools", "test_outputs"):
            self.path(key).mkdir(parents=True, exist_ok=True)
        for folder in INBOX_FOLDERS:
            (self.inbox / folder).mkdir(parents=True, exist_ok=True)
        for sub in (self.uploads, self.store, self.store / "derived", self.library / "manual"):
            sub.mkdir(parents=True, exist_ok=True)


def load_secrets(path: Path) -> None:
    """KEY=value lines (API keys) into the environment; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("AITV_CONFIG", ROOT / "config" / "aitv.toml"))
    # config/secrets.env (chmod 600, never in git) holds the API keys.
    load_secrets(path.parent / "secrets.env")
    with open(path, "rb") as f:
        return Config(raw=tomllib.load(f), root=ROOT)
