"""Publishing a finished production: where it goes, how it's copied, and what the director is told.
The director is a fake script and the media library a temp folder; nothing real is touched."""

import json
import sqlite3

import pytest

from aitv import publish
from aitv.jobs import Blocked

from .test_v2 import Paid, _ctx, _latest, _production

PLACES = [
    {"format": "parody_commercial", "ad_type": "fake_show", "dir": "{root}/promos/trailers", "kind": "promo"},
    {"format": "parody_commercial", "dir": "{root}/commercials/{brand}", "kind": "commercial"},
    {"format": "*", "dir": "{root}/shows/ai_tv", "kind": "generated"},
]


@pytest.fixture
def library(cfg, tmp_path):
    root = tmp_path / "media"
    cfg.raw["publish"] = {"places": [{**p, "dir": p["dir"].replace("{root}", str(root))} for p in PLACES]}
    return root


def test_each_kind_of_piece_has_its_place(cfg, library):
    ad = lambda t: {"ad_type": {"value": t}}  # noqa: E731
    assert publish.place(cfg, "parody_commercial", ad("hotline"), "1-800-COPE-BALDY") == {
        "dir": library / "commercials" / "1-800-cope-baldy", "kind": "commercial"}
    assert publish.place(cfg, "parody_commercial", ad("fake_show"), "Night Court 2")["kind"] == "promo"
    assert publish.place(cfg, "weird_short", {}, None) == {"dir": library / "shows" / "ai_tv", "kind": "generated"}
    with pytest.raises(Blocked):  # an ad with no brand to file it under
        publish.place(cfg, "parody_commercial", ad("product"), None)
    assert publish.era_tag("1994") == "90s" and publish.era_tag("2003") == "00s" and publish.era_tag(None) is None


def test_make_form_pieces_are_filed_by_kind(cfg, tmp_path):
    import tomllib

    from aitv import config
    with open(config.ROOT / "config" / "aitv.toml", "rb") as f:
        places = tomllib.load(f)["publish"]["places"]
    cfg.raw["publish"] = {"places": places}
    ad = lambda t: {"ad_type": {"value": t}}  # noqa: E731
    assert publish.place(cfg, "freeform", ad("hotline"), "Cope")["kind"] == "commercial"
    assert publish.place(cfg, "freeform", ad("product"), "Forget-O")["kind"] == "commercial"
    assert publish.place(cfg, "freeform", ad("fake_show"), "Night Court 2")["kind"] == "promo"
    assert publish.place(cfg, "freeform", {}, None)["kind"] == "generated"


def test_a_republish_reuses_its_path_and_never_overwrites_another(tmp_path):
    folder = tmp_path / "commercials" / "hamrest"
    src = tmp_path / "final.mp4"
    src.write_bytes(b"ham" * 1000)
    first = publish.target(folder, "hamrest", None)
    publish.copy_in(src, first)
    assert first.read_bytes() == src.read_bytes() and not list(folder.glob("*.part"))
    assert publish.target(folder, "hamrest", str(first)) == first           # this production again
    assert publish.target(folder, "hamrest", None) == folder / "hamrest_2.mp4"  # another one with the same title


def test_publish_copies_the_final_and_hands_the_director_its_facts(cfg, conn, tmp_path, monkeypatch, library):
    pid = _production(cfg, conn, tmp_path, monkeypatch, "auto")
    vocab = tmp_path / "chuptv.db"
    with sqlite3.connect(vocab) as v:
        v.execute("CREATE TABLE tags (facet TEXT, name TEXT, status TEXT)")
        v.executemany("INSERT INTO tags VALUES (?, ?, 'active')", [("vibe", "cursed"), ("vibe", "creepy"),
                                                                  ("genre", "horror"), ("season", "halloween")])
    seen = tmp_path / "director_args.json"
    fake = tmp_path / "chuptv"
    fake.write_text(f'#!/bin/sh\necho "$@" > {seen}\necho "scanning" >&2\necho \'{{"code": "abc123", "skipped_tags": []}}\'\n')
    fake.chmod(0o755)
    cfg.raw["publish"].update(director=str(fake), catalog_db=str(vocab))

    ctx = _ctx(cfg, conn, pid)
    ctx.stage = "publish"
    publish.run(ctx)  # not marked to publish: nothing happens
    assert _latest(conn, pid, "publish") is None and not library.exists()

    conn.execute("UPDATE productions SET options = ? WHERE id = ?", (json.dumps({"publish": True}), pid))
    entry = {"blurb": "word " * 30, "summary": "A fake ad.", "product": "detergent", "vibe": ["cursed", "cursed"],
             "genre": [], "season": []}
    backend = Paid({"describe": entry})
    ctx = _ctx(cfg, conn, pid, backend)
    ctx.stage = "publish"
    publish.run(ctx)
    out = _latest(conn, pid, "publish")
    assert out["code"] == "abc123" and out["kind"] == "commercial"
    dst = library / "commercials" / out["path"].split("/")[-2] / out["path"].split("/")[-1]
    assert dst.is_file() and conn.execute("SELECT final_path FROM productions WHERE id = ?", (pid,)).fetchone()[0] == str(dst)
    meta = json.loads((cfg.path("productions") / f"{pid:04d}" / "publish.json").read_text())
    assert meta["origin_ref"] == f"ai_tv:productions/{pid:04d}" and len(meta["profile"]["blurb"]) <= 100
    assert ["format", "ad"] in meta["tags"] and meta["tags"].count(["vibe", "cursed"]) == 1
    assert "catalog publish" in seen.read_text()
    assert backend.calls[0]["tier"] == "small" and "halloween" in backend.calls[0]["prompt"]
    # Again: the saved description, the same file, no second paid call.
    publish.run(ctx)
    assert len(backend.calls) == 1 and _latest(conn, pid, "publish")["path"] == out["path"]
    assert len(list(dst.parent.iterdir())) == 1


def test_blocking_problems_hold_it_back_until_you_say_publish_anyway():
    from types import SimpleNamespace

    from aitv.pipeline import WaitingOnYou

    docs = {"review": {"rounds": [{"notes": [{"t": 28.4, "problem": "end card shows raw script text", "severity": "blocking"},
                                             {"t": 24.0, "problem": "card a bit fast", "severity": "minor"}]}]},
            "qc": {"issues": [{"t": 17.6, "problem": "line a7: the voice is only -4.1 dB above the sound under it",
                               "severity": "blocking"}, {"t": 10.6, "problem": "unplanned cut", "severity": "minor"},
                              {"t": None, "problem": "the piece is silent", "severity": "blocking"}]}}
    ctx = SimpleNamespace(options={"publish": True}, latest=docs.get, log=lambda line: None)
    assert publish.holds(ctx) == ["review at 28.4s: end card shows raw script text",
                                  "QC at 17.6s: line a7: the voice is only -4.1 dB above the sound under it",
                                  "QC: the piece is silent"]
    with pytest.raises(WaitingOnYou, match="held back from ChupTV"):
        publish.run(ctx)
    ctx.options["publish_anyway"] = True
    with pytest.raises(Blocked, match="finished piece"):  # past the hold (this fake has no final to publish)
        publish.run(SimpleNamespace(options=ctx.options, latest={**docs, "look": None}.get, log=lambda line: None))
