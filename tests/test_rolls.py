import random
from collections import Counter

from aitv import rolls

DEFS = {"ad_type": {"product": 5, "hotline": 3, "fake_show": 2}, "length_s": {"15": 1, "30": 3}}


def test_same_seed_same_rolls():
    assert rolls.take(DEFS, {}, 42) == rolls.take(DEFS, {}, 42)


def test_pins_win_and_leave_other_rolls_alone():
    free = rolls.take(DEFS, {}, 7)
    pinned = rolls.take(DEFS, {"ad_type": "hotline"}, 7)
    assert pinned["ad_type"] == {"value": "hotline", "pinned": True}
    assert pinned["length_s"] == free["length_s"]


def test_unknown_pins_are_kept():
    out = rolls.take(DEFS, {"voice": "mint_announcer"}, 1)
    assert out["voice"] == {"value": "mint_announcer", "pinned": True}


def test_weights_are_respected():
    counts = Counter(rolls.take(DEFS, {}, seed)["ad_type"]["value"] for seed in range(3000))
    assert counts["product"] > counts["hotline"] > counts["fake_show"] > 0


def test_least_used_favours_fresh_subjects():
    rng = random.Random(0)
    picks = Counter(rolls.pick_least_used(rng, [(1, 0), (2, 9)]) for _ in range(2000))
    assert picks[1] > picks[2] * 5
    assert picks[2] > 0  # the well-worn brand still comes back sometimes


def test_parse_pins():
    assert rolls.parse_pins("ad_type: hotline\n# comment\nlength_s: 30\nbad line") == {
        "ad_type": "hotline", "length_s": "30"}
