import importlib
import json

import pytest
from fastapi.testclient import TestClient

from aitv import config, db, jobs, worker

from .conftest import make_media


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setattr(config, "ROOT", cfg.root)
    from aitv.web import app as app_mod

    app_mod = importlib.reload(app_mod)
    return TestClient(app_mod.app), app_mod.cfg


def test_every_page_renders_empty(client):
    c, _ = client
    for path in ["/", "/library", "/upload", "/find", "/world", "/ideas", "/notepad", "/productions", "/jobs", "/formats", "/api/status"]:
        assert c.get(path).status_code == 200, path


def test_headshot_upload_for_new_character(client, tmp_path):
    c, cfg = client
    img = make_media("image", tmp_path / "headshot.png")
    with open(img, "rb") as f:
        r = c.post("/upload", headers={"Accept": "application/json"},
                   data={"category": "image", "role": "headshot", "new_type": "character", "new_name": "Dave"},
                   files={"files": ("headshot.png", f, "image/png")})
    assert r.status_code == 200
    body = r.json()
    (asset_id,), entity_id = body["assets"], body["entity_id"]

    conn = db.connect(cfg.db_path)
    job = jobs.claim(conn)
    worker.run_one(conn, cfg, job)

    assert c.get(f"/media/{asset_id}/thumb").status_code == 200
    page = c.get(f"/world/{entity_id}").text
    assert "Dave" in page and f"/library/{asset_id}" in page
    assert c.get("/media/999/thumb").status_code == 404
    assert c.get(f"/media/{asset_id}/../../aitv.sqlite3").status_code == 404


def test_voice_upload_creates_one_voice_entity(client, tmp_path):
    c, cfg = client
    wav = make_media("tone", tmp_path / "me.wav")
    with open(wav, "rb") as f:
        r = c.post("/upload", headers={"Accept": "application/json"},
                   data={"category": "voice", "new_type": "voice", "new_name": "mint_announcer"},
                   files={"files": ("me.wav", f, "audio/wav")})
    conn = db.connect(cfg.db_path)
    worker.run_one(conn, cfg, jobs.claim(conn))
    rows = conn.execute("SELECT type, name FROM entities").fetchall()
    assert [tuple(r) for r in rows] == [("voice", "mint_announcer")]
    assert r.json()["entity_id"] == conn.execute("SELECT id FROM entities").fetchone()["id"]


def test_notepad_and_production_flow(client):
    c, _ = client
    assert c.post("/notepad", json={"text": "a ufo hits the moon"}).status_code == 200
    assert "a ufo hits the moon" in c.get("/notepad").text
    r = c.post("/productions", data={"format": "parody_commercial", "pins": "ad_type: hotline", "seed": "5", "run": "1"},
               follow_redirects=False)
    assert r.status_code == 303
    page = c.get(r.headers["location"]).text
    assert "hotline" in page and "seed 5" in page


def test_tests_page_lists_plays_and_deletes(client):
    c, cfg = client
    run = cfg.path("test_outputs") / "voice" / "run1"
    run.mkdir(parents=True)
    (run / "notes.md").write_text("first try")
    (run / "out.json").write_text('{"ok": true}')
    page = c.get("/tests").text
    assert "voice/run1" in page and "first try" in page and "&#34;ok&#34;" in page
    assert c.get("/tests/file/voice/run1/out.json").status_code == 200
    assert c.get("/tests/file/../config/aitv.toml").status_code == 404
    assert c.post("/tests/delete", data={"rel": "voice/run1"}, follow_redirects=False).status_code == 303
    assert not run.exists()


def test_pasting_an_answer_validates_and_resumes(client):
    from aitv import pipeline

    c, cfg = client
    conn = db.connect(cfg.db_path)
    pid = pipeline.create_production(conn, cfg, "parody_commercial", seed=1)
    with __import__("pytest").raises(pipeline.WaitingOnYou):
        pipeline.run_production(conn, cfg, pid)
    assert "Waiting for the outline" in c.get(f"/productions/{pid}").text

    bad = c.post(f"/productions/{pid}/answer/outline", data={"answer": '{"title": "x"}'}, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    good = {"title": "Dial-A-Dad", "logline": "A hotline", "new_subject": None,
            "beats": [{"summary": "Phone rings", "act_break_after": False}]}
    ok = c.post(f"/productions/{pid}/answer/outline", data={"answer": json.dumps(good)}, follow_redirects=False)
    assert ok.status_code == 303 and "error" not in ok.headers["location"]
    assert pipeline.writing_paths(cfg, pid, "outline")["answer"].is_file()
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'production.run' AND status = 'queued'").fetchone()[0] == 1


def test_production_page_shows_qc_review_spend_and_edit(client):
    from aitv import pipeline, spend

    c, cfg = client
    conn = db.connect(cfg.db_path)
    pid = pipeline.create_production(conn, cfg, "parody_commercial", pins={"ad_type": "product"}, seed=3)
    docs = {
        "qc": {"ok": False, "checks": {"loudness": {"ok": True, "lufs": -16.0, "true_peak": -1.8},
                                       "contact_sheet": {"ok": True, "sheet": "x"}},
               "issues": [{"t": 3.2, "check": "lines", "category": "masked_line", "severity": "blocking",
                           "problem": "line a3 is buried", "hint": "raise a3"}]},
        "review": {"rounds": [{"round": 1, "video": "0001/review/round_0.mp4", "summary": "Quiet line.",
                               "scores": {"pacing": 4}, "notes": [{"t": 3.0, "problem": "can't hear it", "severity": "blocking",
                                                                    "fix": []}], "regenerate": [], "applied": [{}], "rejected": []}]},
        "edl": {"made_by": "llm", "picture": [], "titles": [], "audio": [], "editor_notes": "Tighter open."},
    }
    for kind, body in docs.items():
        conn.execute("INSERT INTO documents (production_id, kind, version, body) VALUES (?, ?, 1, ?)",
                     (pid, kind, db.dumps(body)))
    spend.record(conn, pid, "review", "llm", 0.0081, "google/gemini-3.5-flash-lite")
    page = c.get(f"/productions/{pid}").text
    for text in ("line a3 is buried", "Quiet line.", "$0.0081", "Tighter open.", "1 fix(es) applied"):
        assert text in page, text


def test_make_form_takes_minutes_seconds_and_rolls_nothing_for_the_writer(client):
    c, cfg = client
    form = {"title": "Cope", "format": "Late-night hotline ad", "prompt": "A hotline for bald men.",
            "tags": "type: hotline\nsad", "len_mode": "range", "len_min": "0:20", "len_max": "1:05", "budget": "1"}
    r = c.post("/make", data={**form, "action": "draft"}, follow_redirects=False)
    idea = int(r.headers["location"].split("idea=")[1].split("&")[0])
    page = c.get(f"/?idea={idea}").text
    assert 'value="0:20"' in page and 'value="1:05"' in page and "roll" not in page.lower()
    assert 'value="Late-night hotline ad"' in page and "type: hotline\nsad</textarea>" in page
    assert "<li>Late-night hotline ad</li>" in page  # in the list of formats used before

    # A range draft with no longest yet still saves.
    assert c.post("/make", data={**form, "len_max": "", "action": "draft"}, follow_redirects=False).status_code == 303

    # Exact ignores the first box.
    c.post("/make", data={**form, "len_mode": "exact", "action": "start"}, follow_redirects=False)
    conn = db.connect(cfg.db_path)
    prod = conn.execute("SELECT * FROM productions ORDER BY id DESC LIMIT 1").fetchone()
    assert json.loads(prod["options"])["length"] == {"min": 65.0, "max": 65.0}
    assert json.loads(prod["options"])["rolls"] is False and prod["format"] == "freeform"
    rolls = json.loads(prod["rolls"])
    assert rolls["type"] == {"value": "hotline", "pinned": True} and rolls["sad"]["pinned"]
    assert rolls["ad_type"]["value"] == "hotline" and rolls["subject"]["entity_type"] == "hotline"
    assert "music_style" not in rolls
    page = c.get(f"/productions/{prod['id']}").text
    assert "Late-night hotline ad" in page and "set by you" in page and "picked at random" not in page


CATALOG = {
    "/models": [
        {"id": "deepseek/deepseek-v4-pro", "name": "DeepSeek V4 Pro", "pricing": {"prompt": "0.0000009", "completion": "0.0000015"},
         "architecture": {"input_modalities": ["text"]}, "supported_parameters": ["structured_outputs"],
         "top_provider": {"max_completion_tokens": 32000}},
        {"id": "google/gemini-3.8-flash", "name": "Gemini", "pricing": {"prompt": "0.000000825", "completion": "0.000004125"},
         "architecture": {"input_modalities": ["text", "image", "video"]}, "supported_parameters": ["structured_outputs"]},
        {"id": "old/no-json", "name": "Old", "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"]}, "supported_parameters": []},
    ],
    "/videos/models": [
        {"id": "tbc/mischief-1", "name": "Mischief", "pricing_skus": {"cents_per_video_output_second_480p": "0.5"}},
        {"id": "heygen/heygen-video-1", "name": "HeyGen", "pricing_skus": {"duration_seconds_480p": "0.02",
                                                                           "reference_input_images": "0.04"}},
        {"id": "bytedance/seedance-2.0", "name": "Seedance", "pricing_skus": {"video_tokens": "0.000007"}},
    ],
}


def test_model_picks_are_checked_against_the_live_list_and_saved(client, monkeypatch):
    from aitv import prices
    monkeypatch.setattr(prices, "_fetch", lambda path: CATALOG[path])
    c, cfg = client
    form = {"title": "Cope", "format": "", "prompt": "x", "len_mode": "exact", "len_max": "0:30", "action": "start"}
    r = c.post("/make", data={**form, "model_review": "deepseek/deepseek-v4-pro"}, follow_redirects=False)
    assert r.status_code == 400 and "can't look at video" in r.text
    r = c.post("/make", data={**form, "model_outline": "old/no-json"}, follow_redirects=False)
    assert r.status_code == 400 and "JSON schema" in r.text
    r = c.post("/make", data={**form, "model_outline": "nobody/nothing"}, follow_redirects=False)
    assert r.status_code == 400 and "no model called" in r.text

    r = c.post("/make", data={**form, "model_outline": "google/gemini-3.8-flash", "action": "draft"}, follow_redirects=False)
    page = c.get(r.headers["location"]).text
    assert 'name="model_outline" value="google/gemini-3.8-flash"' in page

    body = c.get("/api/prices").json()
    assert {m["id"] for m in body["chat"]} == {"deepseek/deepseek-v4-pro", "google/gemini-3.8-flash"}
    mischief = next(v for v in body["video"] if v["id"] == "tbc/mischief-1")
    assert mischief["in_use"] and mischief["rates"] == [{"label": "480p", "usd": 0.005, "unit": "per second"}]
    heygen = next(v for v in body["video"] if v["id"] == "heygen/heygen-video-1")
    assert {"label": "reference input images", "usd": 0.04, "unit": "per image"} in heygen["rates"]
    seedance = next(v for v in body["video"] if v["id"] == "bytedance/seedance-2.0")
    assert seedance["rates"][0]["unit"] == "per video token" and seedance["cheapest_per_s"] is None


def test_make_page_works_without_prices(client):
    c, _ = client
    assert c.get("/").status_code == 200
    assert "error" in c.get("/api/prices").json()
    r = c.post("/make", data={"title": "x", "format": "", "prompt": "x", "len_mode": "exact", "len_max": "0:30",
                              "model_outline": "anything/at-all", "action": "draft"}, follow_redirects=False)
    assert r.status_code == 303  # no list to check against: the pick is taken as typed
