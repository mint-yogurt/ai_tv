"""Live model prices and abilities from OpenRouter's public catalog (no key, nothing billed).

Two lists: chat models (/models: price per token, what they can look at, whether they keep to a JSON
schema) and video models (/videos/models: price SKUs, mostly per second). Both are cached for a few
minutes so page loads stay quick; a fetch that fails raises PricesUnavailable and callers carry on
without prices.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

BASE = "https://openrouter.ai/api/v1"
CACHE_SECONDS = 600
TIMEOUT_SECONDS = 6

_lock = threading.Lock()
_cache: dict[str, tuple[float, list]] = {}


class PricesUnavailable(RuntimeError):
    pass


def _fetch(path: str) -> list[dict]:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "aitv-hobby/0.1"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read())["data"]


def _cached(path: str) -> list[dict]:
    with _lock:
        hit = _cache.get(path)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    try:
        data = _fetch(path)
    except Exception as exc:  # network, HTTP or JSON trouble: no prices this time
        raise PricesUnavailable(f"OpenRouter's price list couldn't be fetched ({exc})") from exc
    with _lock:
        _cache[path] = (time.time(), data)
    return data


def chat_models() -> dict[str, dict]:
    """{id: {id, name, in_per_m, out_per_m, inputs, structured, max_out}}; prices in dollars per 1M tokens."""
    out = {}
    for m in _cached("/models"):
        price = m.get("pricing") or {}
        per_m = lambda k: round(float(price[k]) * 1e6, 4) if price.get(k) not in (None, "") else None  # noqa: E731
        out[m["id"]] = {
            "id": m["id"], "name": m.get("name", m["id"]),
            "in_per_m": per_m("prompt"), "out_per_m": per_m("completion"),
            "inputs": (m.get("architecture") or {}).get("input_modalities") or ["text"],
            "structured": "structured_outputs" in (m.get("supported_parameters") or []),
            "max_out": (m.get("top_provider") or {}).get("max_completion_tokens"),
        }
    return out


def max_output(model: str) -> int | None:
    """The most a model can write in one reply, if the catalog says (None when it doesn't or can't be fetched)."""
    try:
        return chat_models().get(model, {}).get("max_out")
    except PricesUnavailable:
        return None


def fits(model: dict, needs: str | None) -> bool:
    """Can this model do a stage: keep to the JSON schema, and look at the stage's images or video."""
    return model["structured"] and (not needs or needs in model["inputs"])


def sku(key: str, value) -> dict:
    """One video price SKU, readable: {"label": "480p", "usd": 0.005, "unit": "per second"}."""
    usd = float(value) / 100 if key.startswith(("cents_", "minimum_cents")) else float(value)
    named = key.replace("text_to_video", "text-to-video").replace("image_to_video", "image-to-video")
    words = [w for w in named.split("_") if w not in
             ("cents", "per", "duration", "seconds", "second", "output", "video", "minimum")]
    if "token" in key:
        unit = "per video token"
        words = [w for w in words if w != "tokens"]
    elif "megapixel" in key:
        unit = "per megapixel-second"
        words = [w for w in words if w != "megapixel"]
    elif key.startswith("minimum"):
        unit = "minimum per clip"
        words = [w for w in words if w != "generation"]
    elif "image" in key and "second" not in key:
        unit = "per image"
    else:
        unit = "per second"
    label = re.sub(r"\s+", " ", " ".join(words)).strip()
    return {"label": label or "standard", "usd": usd, "unit": unit}


def video_models() -> list[dict]:
    """[{id, name, rates: [sku...], cheapest_per_s}] for every video model OpenRouter lists."""
    out = []
    for m in _cached("/videos/models"):
        rates = [sku(k, v) for k, v in (m.get("pricing_skus") or {}).items()]
        per_s = [r["usd"] for r in rates if r["unit"] == "per second"]
        out.append({"id": m["id"], "name": m.get("name", m["id"]), "rates": rates,
                    "cheapest_per_s": min(per_s) if per_s else None})
    return out
