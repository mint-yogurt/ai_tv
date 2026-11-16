"""Tell the user on Discord: drop a message in Chuppybot's outbox (/code/discordbot, see its README).

Never raises: a missing bot must not fail a production.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

OUTBOX = Path("/code/discordbot/outbox")


def send(title: str, body: str = "", url: str = "") -> None:
    try:
        tmp = OUTBOX / f"{time.time_ns()}-ai_tv.tmp"
        tmp.write_text(json.dumps({"source": "ai_tv", "title": title, "body": body, "url": url}))
        tmp.rename(tmp.with_suffix(".json"))
    except OSError:
        pass
