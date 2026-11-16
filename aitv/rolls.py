"""Dice rolls with pins.

Each format lists named rolls with weights. Anything pinned by the user is used as-is;
everything else is rolled. Every roll gets its own RNG derived from (seed, roll name), so
pinning one roll never changes the outcome of the others for the same seed.
"""

from __future__ import annotations

import random
from typing import Any, Sequence


def roll_rng(seed: int, name: str) -> random.Random:
    return random.Random(f"{seed}:{name}")


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    items = [(k, float(w)) for k, w in weights.items() if float(w) > 0]
    if not items:
        raise ValueError("a roll needs at least one option with a positive weight")
    keys, ws = zip(*items)
    return rng.choices(keys, weights=ws, k=1)[0]


def take(roll_defs: dict[str, Any], pins: dict[str, Any] | None, seed: int) -> dict[str, dict]:
    """Resolve every roll. Returns {name: {"value": ..., "pinned": bool}}.

    A roll definition is either a {option: weight} table, or {"choices": {...}}.
    Pins for names the format doesn't define are kept too, so a pitch can pin free-form values.
    """
    pins = dict(pins or {})
    out: dict[str, dict] = {}
    for name, spec in roll_defs.items():
        weights = spec.get("choices", spec) if isinstance(spec, dict) else None
        if name in pins:
            out[name] = {"value": pins.pop(name), "pinned": True}
        elif isinstance(weights, dict):
            out[name] = {"value": weighted_choice(roll_rng(seed, name), weights), "pinned": False}
    for name, value in pins.items():
        out[name] = {"value": value, "pinned": True}
    return out


def pick_least_used(rng: random.Random, candidates: Sequence[tuple[int, int]]) -> int:
    """Pick an id from (id, recent_use_count) pairs, weighting by 1 / (1 + uses).

    Fresh subjects are favoured, but well-used ones still come back now and then, which is
    what makes a fake brand feel like it has a long-running ad campaign.
    """
    if not candidates:
        raise ValueError("no candidates")
    ids = [c[0] for c in candidates]
    weights = [1.0 / (1 + max(0, c[1])) for c in candidates]
    return rng.choices(ids, weights=weights, k=1)[0]


def parse_pins(text: str) -> dict[str, Any]:
    """Parse 'key: value' lines (the web form and pitch files use this)."""
    pins: dict[str, Any] = {}
    for line in text.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            pins[key] = value
    return pins


def format_pins(pins: dict[str, Any]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in pins.items())
