"""The control room: a small private web UI over the database and job queue.

Server-rendered pages, plain forms, a little JS for uploads, autosave and live status.
It listens on localhost only; Caddy publishes it on the Tailscale port.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, ingest, jobs, online, pipeline, prices, rolls, spend, system
from ..config import CATEGORIES, load

HERE = Path(__file__).parent
cfg = load()
cfg.ensure_dirs()
with db.connect(cfg.db_path) as _conn:
    db.init(_conn)

ENTITY_TYPES = ["character", "voice", "set", "brand", "product", "fake_show", "hotline", "presenter", "prop"]
ROLES = ["reference", "headshot", "plate", "logo", "packshot", "voice_reference", "theme", "sting"]
NOTEPAD = cfg.path("ideas") / "NOTEPAD.md"

app = FastAPI(title="AI TV", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


# ---------------------------------------------------------------- template helpers

def _filesize(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _duration(s):
    if not s:
        return ""
    s = float(s)
    return f"{int(s // 60)}:{s % 60:04.1f}" if s >= 60 else f"{s:.1f}s"


def _ago(ts):
    if not ts:
        return ""
    then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - then).total_seconds())
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return f"{secs}s ago"


templates.env.filters.update(fromjson=db.loads, filesize=_filesize, duration=_duration, ago=_ago,
                             slug=lambda t: re.sub(r"[^A-Za-z0-9-]+", "_", t or "").strip("_") or "final",
                             tojson_pretty=lambda v: json.dumps(v, indent=2, ensure_ascii=False))


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"nav": name.split(".")[0], **ctx})


def back(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def get_db() -> Iterator[sqlite3.Connection]:
    conn = db.connect(cfg.db_path)
    try:
        yield conn
    finally:
        conn.close()


DB = Depends(get_db)


def counts(conn) -> dict:
    out = {"jobs": {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}}
    out["assets"] = {r["category"]: r["n"] for r in conn.execute(
        "SELECT category, COUNT(*) n FROM assets WHERE status = 'ready' GROUP BY category")}
    out["entities"] = {r["type"]: r["n"] for r in conn.execute("SELECT type, COUNT(*) n FROM entities GROUP BY type")}
    return out


# ---------------------------------------------------------------- make (the home page)

MAKE_FORMAT = "freeform"  # the make form's backing format: no description, house prompts or rolls


def format_label(row) -> str:
    """What a production or idea calls its format: the producer's typed label, else the format's name."""
    label = (db.loads(row["options"]) if "options" in row.keys() and row["options"] else {}).get("format_label")
    if label is not None or row["format"] == MAKE_FORMAT:
        return label or ""
    try:
        return pipeline.load_format(cfg, row["format"])["format"].get("name", row["format"])
    except KeyError:
        return str(row["format"] or "").replace("_", " ")


templates.env.globals["format_label"] = format_label


def _format_history(conn: sqlite3.Connection) -> list[str]:
    """Every format label used so far, most recent first, once each (ignoring case)."""
    rows = conn.execute("SELECT format, options, created_at AS at FROM productions UNION ALL "
                        "SELECT format, options, updated_at AS at FROM ideas ORDER BY at DESC").fetchall()
    seen, out = set(), []
    for r in rows:
        label = format_label(r).strip() if r["format"] else ""
        if label and label.lower() not in seen:
            seen.add(label.lower())
            out.append(label)
    return out


def _parse_tags(text: str) -> dict[str, str]:
    """One tag per line: 'name: value', or just a word or phrase (kept as written, with no value)."""
    tags: dict[str, str] = {}
    for line in text.splitlines():
        name, _, value = line.partition(":")
        if name.strip():
            tags[name.strip()] = value.strip()
    return tags


def _model_stages(options: dict | None = None) -> list[dict]:
    """The make form's model boxes: each stage's current model (the draft's pick, else what config runs)."""
    off = {"edit": cfg.section("edit").get("mode", "off") != "llm", "review": not cfg.section("review").get("enabled")}
    return [{"stage": st, "label": label, "needs": needs or "", "off": off.get(st, False),
             "model": pipeline.effective_model(cfg, st, options) or ""} for st, label, _, needs in pipeline.MODEL_STAGES]


def _picked_models(form) -> dict[str, str]:
    """The model boxes that differ from what config would run, checked against the live catalog when it's
    reachable (an unknown id, or one that can't keep to the schema or see the stage's input, is refused)."""
    picked = {}
    for st in _model_stages():
        value = str(form.get(f"model_{st['stage']}") or "").strip()
        if value and value != st["model"]:
            picked[st["stage"]] = value
    try:
        catalog = prices.chat_models()
    except prices.PricesUnavailable:
        return picked
    for st in _model_stages():
        value = picked.get(st["stage"])
        if value and value not in catalog:
            raise HTTPException(400, f"{st['label']}: OpenRouter has no model called {value!r}")
        if value and not prices.fits(catalog[value], st["needs"] or None):
            need = f"can't look at {st['needs']}" if catalog[value]["structured"] else "can't keep to a JSON schema"
            raise HTTPException(400, f"{st['label']}: {value} {need}")
    return picked


@app.get("/api/prices")
def api_prices():
    """Live prices for the make form: chat models that can do each stage, and the video models."""
    try:
        chat, video = prices.chat_models(), prices.video_models()
    except prices.PricesUnavailable as exc:
        return JSONResponse({"error": str(exc)})
    vg = cfg.section("videogen")
    in_use = {vg.get("model"): vg.get("resolution", ""), vg.get("talkie_model"): vg.get("talkie_resolution", "")}
    return JSONResponse({
        "chat": [m for m in chat.values() if m["structured"]],
        "video": [{**m, "in_use": m["id"] in in_use, "resolution": in_use.get(m["id"], "")} for m in video],
    })


def _show_tags(tags: dict) -> str:
    return "\n".join(f"{k}: {v}" if v else k for k, v in tags.items())


def _seconds(text: str) -> float | None:
    """'1:30' or '90' -> 90.0; anything else -> None."""
    m = re.fullmatch(r"(?:(\d+):)?(\d+(?:\.\d+)?)", text.strip())
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def _clock(seconds) -> str:
    """90 -> '1:30' (the make form's length boxes)."""
    if seconds is None:
        return ""
    whole = round(float(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


@app.get("/", response_class=HTMLResponse)
def make(request: Request, idea: int | None = None, conn=DB):
    draft = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea,)).fetchone() if idea else None
    pictured = ("(SELECT a.id FROM asset_entities ae JOIN assets a ON a.id = ae.asset_id WHERE ae.entity_id = e.id "
                "AND a.media = 'image' AND a.status = 'ready' ORDER BY ae.role = 'headshot' DESC, a.id LIMIT 1)")
    characters = conn.execute(f"SELECT e.id, e.name, e.description, {pictured} AS thumb_id FROM entities e "
                              "WHERE e.type IN ('character', 'presenter') ORDER BY thumb_id IS NULL, e.name").fetchall()
    voices = conn.execute(
        "SELECT e.id, e.name, e.description, (SELECT ae.asset_id FROM asset_entities ae JOIN assets a ON a.id = ae.asset_id "
        "WHERE ae.entity_id = e.id AND ae.role = 'voice_reference' AND a.status = 'ready' LIMIT 1) AS asset_id "
        "FROM entities e WHERE e.type = 'voice' ORDER BY e.name").fetchall()
    media_rows = conn.execute("SELECT * FROM assets WHERE status = 'ready' AND (media IN ('image', 'video') OR category = 'music') "
                              "AND id NOT IN (SELECT asset_id FROM asset_entities WHERE role IN ('headshot', 'voice_reference')) "
                              "ORDER BY id DESC LIMIT 400").fetchall()
    cost = conn.execute("SELECT COUNT(*) n, AVG(spent_usd) avg, MAX(spent_usd) most FROM productions "
                        "WHERE status = 'done' AND spent_usd > 0").fetchone()
    drafts = conn.execute("SELECT id, title, format, options, updated_at FROM ideas WHERE status = 'queued' "
                          "ORDER BY updated_at DESC LIMIT 15").fetchall()
    recent = conn.execute("SELECT * FROM productions ORDER BY id DESC LIMIT 8").fetchall()
    return page(request, "make.html", draft=draft, clock=_clock,
                history=_format_history(conn), model_stages=_model_stages(db.loads(draft["options"]) if draft else None),
                tags=_show_tags(db.loads(draft["pins"])) if draft else "",
                opts=db.loads(draft["options"]) if draft else {}, characters=characters, voices=voices, media=media_rows,
                cost=cost, drafts=drafts, recent=recent, saved=request.query_params.get("saved"))


@app.post("/make")
async def make_submit(request: Request, conn=DB):
    form = await request.form()
    get = lambda k: str(form.get(k) or "").strip()  # noqa: E731
    num = lambda k: float(get(k)) if get(k).replace(".", "", 1).isdigit() else None  # noqa: E731
    hi = _seconds(get("len_max"))  # the second box: the length, or the longest of a range
    lo = _seconds(get("len_min")) if get("len_mode") == "range" else hi
    if lo is None:
        lo = hi
    if hi is None and get("action") != "draft":
        raise HTTPException(400, "the length needs a time, like 0:30")
    if lo is not None and hi is not None and hi < lo:
        lo, hi = hi, lo
    options = {"length": {"min": lo, "max": hi} if lo is not None else None,
               "characters": [int(v) for v in form.getlist("characters")], "voices": [int(v) for v in form.getlist("voices")],
               "assets": [int(v) for v in form.getlist("assets")], "publish": bool(get("publish")),
               "format_label": get("format"), "models": _picked_models(form)}
    tags = json.dumps(_parse_tags(get("tags")), ensure_ascii=False)  # unsorted: the writer gets them in the order typed
    budget = num("budget") or 0.0
    title = get("title") or get("prompt")[:60] or "Untitled"
    draft = {**options, "budget": budget}
    idea_id = int(get("idea_id")) if get("idea_id").isdigit() else None
    if idea_id and conn.execute("SELECT 1 FROM ideas WHERE id = ? AND status = 'queued'", (idea_id,)).fetchone():
        conn.execute("UPDATE ideas SET title = ?, body = ?, format = ?, pins = ?, options = ?, updated_at = ? WHERE id = ?",
                     (title, get("prompt"), MAKE_FORMAT, tags, db.dumps(draft), db.now(), idea_id))
    else:
        idea_id = conn.execute("INSERT INTO ideas (title, body, format, pins, options) VALUES (?, ?, ?, ?, ?)",
                               (title, get("prompt"), MAKE_FORMAT, tags, db.dumps(draft))).lastrowid
    if get("action") == "draft":
        return back(f"/?idea={idea_id}&saved=1")
    pid = pipeline.create_production(conn, cfg, MAKE_FORMAT, title=title, idea_id=idea_id, budget_usd=budget,
                                     options=options, roll=False)
    jobs.enqueue(conn, "production.run", {"production_id": pid}, production_id=pid)
    return back(f"/productions/{pid}")


# ---------------------------------------------------------------- system status

@app.get("/system", response_class=HTMLResponse)
def dashboard(request: Request, conn=DB):
    active = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('running', 'queued') ORDER BY status = 'running' DESC, id LIMIT 12"
    ).fetchall()
    recent = conn.execute(
        "SELECT * FROM jobs WHERE status NOT IN ('running', 'queued') ORDER BY COALESCE(finished_at, created_at) DESC LIMIT 8"
    ).fetchall()
    productions = conn.execute("SELECT * FROM productions ORDER BY updated_at DESC LIMIT 6").fetchall()
    fresh = conn.execute("SELECT * FROM assets ORDER BY id DESC LIMIT 8").fetchall()
    return page(request, "dashboard.html", sys=system.snapshot(conn, cfg), counts=counts(conn), active=active,
                recent=recent, productions=productions, fresh=fresh)


@app.get("/api/status")
def api_status(conn=DB):
    snap = system.snapshot(conn, cfg)
    snap["counts"] = counts(conn)
    return JSONResponse(json.loads(json.dumps(snap, default=str)))


# ---------------------------------------------------------------- library

@app.get("/library", response_class=HTMLResponse)
def library(request: Request, category: str = "", q: str = "", status: str = "", conn=DB):
    sql, args = "SELECT * FROM assets WHERE 1=1", []
    if category:
        sql += " AND category = ?"
        args.append(category)
    if status:
        sql += " AND status = ?"
        args.append(status)
    if q:
        sql += " AND (title LIKE ? OR original_name LIKE ? OR tags LIKE ?)"
        args += [f"%{q}%"] * 3
    assets = conn.execute(sql + " ORDER BY id DESC LIMIT 300", args).fetchall()
    return page(request, "library.html", assets=assets, categories=CATEGORIES, category=category, q=q, status=status,
                presets=list(cfg.section("images").get("presets", {})))


@app.get("/library/{asset_id}", response_class=HTMLResponse)
def asset_detail(request: Request, asset_id: int, conn=DB):
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if asset is None:
        raise HTTPException(404)
    links = conn.execute(
        "SELECT ae.role, e.* FROM asset_entities ae JOIN entities e ON e.id = ae.entity_id WHERE ae.asset_id = ?",
        (asset_id,),
    ).fetchall()
    asset_jobs = conn.execute("SELECT * FROM jobs WHERE asset_id = ? ORDER BY id DESC", (asset_id,)).fetchall()
    entities = conn.execute("SELECT id, type, name FROM entities ORDER BY type, name").fetchall()
    return page(request, "asset.html", asset=asset, links=links, jobs=asset_jobs, entities=entities, roles=ROLES)


@app.post("/library/{asset_id}")
def asset_update(asset_id: int, title: str = Form(""), tags: str = Form(""), conn=DB):
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    conn.execute("UPDATE assets SET title = ?, tags = ? WHERE id = ?", (title, db.dumps(tag_list), asset_id))
    return back(f"/library/{asset_id}")


@app.post("/library/{asset_id}/link")
def asset_link(asset_id: int, entity_id: int = Form(...), role: str = Form("reference"), conn=DB):
    conn.execute("INSERT OR IGNORE INTO asset_entities (asset_id, entity_id, role) VALUES (?, ?, ?)",
                 (asset_id, entity_id, role))
    return back(f"/library/{asset_id}")


@app.post("/library/{asset_id}/unlink")
def asset_unlink(asset_id: int, entity_id: int = Form(...), role: str = Form(...), conn=DB):
    conn.execute("DELETE FROM asset_entities WHERE asset_id = ? AND entity_id = ? AND role = ?",
                 (asset_id, entity_id, role))
    return back(f"/library/{asset_id}")


@app.post("/library/{asset_id}/delete")
def asset_delete(asset_id: int, conn=DB):
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if asset:
        meta = db.loads(asset["meta"])
        if asset["path"]:
            (cfg.library / asset["path"]).unlink(missing_ok=True)
        if asset["sha256"]:
            shutil.rmtree(cfg.store / "derived" / asset["sha256"], ignore_errors=True)
        if meta.get("inbox_path"):
            Path(meta["inbox_path"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return back("/library")


# ---------------------------------------------------------------- find online

@app.get("/find", response_class=HTMLResponse)
def find(request: Request, source: str = "archive", kind: str = "video", q: str = "", conn=DB):
    if kind not in online.SOURCES.get(source, {}).get("kinds", []):
        kind = online.SOURCES.get(source, online.SOURCES["archive"])["kinds"][0]
    results, error = [], ""
    if q.strip():
        try:
            results = online.search(cfg, source, kind, q.strip())
        except (online.OnlineError, ValueError) as exc:
            error = f"Search failed: {exc}. The site may be down; try again or use another source."
    recent = conn.execute(
        f"SELECT * FROM assets WHERE source IN ({','.join('?' * len(online.ONLINE_SOURCES))}) ORDER BY id DESC LIMIT 12",
        online.ONLINE_SOURCES).fetchall()
    return page(request, "find.html", sources=online.SOURCES, source=source, kind=kind, q=q, results=results,
                error=error, categories=CATEGORIES, default_category=online.DEFAULT_CATEGORY[kind],
                storage=online.storage(conn, cfg), recent=recent, added=request.query_params.get("added", ""))


@app.post("/find/add")
def find_add(result: str = Form(...), category: str = Form(...), tags: str = Form(""), q: str = Form(""), conn=DB):
    data = json.loads(result)
    if category not in CATEGORIES:
        raise HTTPException(400, "unknown category")
    jobs.enqueue(conn, "online.fetch", {"result": data, "category": category,
                                        "tags": [t.strip() for t in tags.split(",") if t.strip()]}, priority=2)
    from urllib.parse import urlencode
    return back("/find?" + urlencode({"source": data["source"], "kind": data["kind"], "q": q, "added": data["title"]}))


@app.post("/library/{asset_id}/refetch")
def asset_refetch(asset_id: int, conn=DB):
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    src = db.loads(asset["meta"]).get("online") if asset else None
    if not src:
        raise HTTPException(400, "this asset didn't come from an online source")
    result = {"source": src["source"], "kind": src["kind"], "id": src["id"], "title": src["title"], "url": src["url"],
              "landing": src.get("landing"), "seconds": asset["duration_s"]}
    tags = db.loads(asset["tags"])
    jobs.enqueue(conn, "online.fetch", {"result": result, "category": asset["category"], "tags": tags,
                                        "replaces": asset_id}, priority=2)
    return back("/jobs")


@app.post("/library/cull")
def library_cull(conn=DB):
    jobs.enqueue(conn, "library.cull", {}, priority=-5)
    return back("/jobs")


VARIANTS = {"thumb", "proxy", "waveform", "preview", "reference", "tts_preview"}


@app.get("/media/{asset_id}/{variant}")
def media_file(asset_id: int, variant: str, conn=DB):
    asset = conn.execute("SELECT path, meta, original_name FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if asset is None:
        raise HTTPException(404)
    if variant == "original":
        rel = asset["path"]
    elif variant in VARIANTS:
        rel = db.loads(asset["meta"]).get("derived", {}).get(variant)
    else:
        raise HTTPException(404)
    if not rel:
        raise HTTPException(404)
    path = (cfg.library / rel).resolve()
    if not path.is_relative_to(cfg.library) or not path.is_file():
        raise HTTPException(404)
    headers = {"Cache-Control": "private, max-age=86400"}
    if variant == "original":
        return FileResponse(path, filename=asset["original_name"] or path.name, headers=headers)
    return FileResponse(path, headers=headers)


# ---------------------------------------------------------------- upload

@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, entity_id: int | None = None, category: str = "", conn=DB):
    entities = conn.execute("SELECT id, type, name FROM entities ORDER BY type, name").fetchall()
    return page(request, "upload.html", entities=entities, categories=CATEGORIES, entity_types=ENTITY_TYPES,
                roles=ROLES, preselect=entity_id, category=category)


@app.post("/upload")
def upload(
    request: Request,
    files: list[UploadFile],
    category: str = Form(...),
    entity_id: str = Form(""),
    role: str = Form("reference"),
    new_type: str = Form(""),
    new_name: str = Form(""),
    new_description: str = Form(""),
    title: str = Form(""),
    tags: str = Form(""),
    conn=DB,
):
    if category not in CATEGORIES:
        raise HTTPException(400, f"unknown category {category}")
    if category == "voice" and role == "reference":
        role = "voice_reference"  # what casting and the voice stage look for
    target = int(entity_id) if entity_id.strip().isdigit() else None
    if new_name.strip():
        etype = new_type or ("voice" if category == "voice" else "character")
        row = conn.execute("SELECT id FROM entities WHERE type = ? AND name = ?", (etype, new_name.strip())).fetchone()
        target = row["id"] if row else conn.execute(
            "INSERT INTO entities (type, name, description) VALUES (?, ?, ?)",
            (etype, new_name.strip(), new_description.strip()),
        ).lastrowid
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    created = []
    for up in files:
        if not up.filename:
            continue
        dest = cfg.uploads / f"{uuid.uuid4().hex[:8]}_{ingest.safe_name(up.filename)}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(up.file, out, length=1 << 20)
        created.append(ingest.register(
            conn, dest, category, title=title if len(files) == 1 else "", original_name=up.filename,
            source="upload", entity_id=target, role=role, tags=tag_list,
            voice_name=new_name.strip() if category == "voice" else "",
            voice_description=new_description.strip() if category == "voice" else "",
        ))
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"assets": created, "entity_id": target})
    return back(f"/world/{target}" if target else "/library")


# ---------------------------------------------------------------- world (entities)

@app.get("/world", response_class=HTMLResponse)
def world(request: Request, type: str = "", conn=DB):
    sql = (
        "SELECT e.*, (SELECT a.id FROM asset_entities ae JOIN assets a ON a.id = ae.asset_id "
        "  WHERE ae.entity_id = e.id AND a.media = 'image' AND a.status = 'ready' ORDER BY ae.role = 'headshot' DESC, a.id LIMIT 1) AS thumb_id, "
        "(SELECT COUNT(*) FROM asset_entities ae WHERE ae.entity_id = e.id) AS n_assets, "
        "(SELECT COUNT(*) FROM usage u WHERE u.entity_id = e.id) AS n_uses FROM entities e"
    )
    args = []
    if type:
        sql += " WHERE e.type = ?"
        args.append(type)
    entities = conn.execute(sql + " ORDER BY e.type, e.name", args).fetchall()
    return page(request, "world.html", entities=entities, entity_types=ENTITY_TYPES, type=type)


@app.post("/world")
def world_create(type: str = Form(...), name: str = Form(...), description: str = Form(""), conn=DB):
    try:
        eid = conn.execute("INSERT INTO entities (type, name, description) VALUES (?, ?, ?)",
                           (type.strip(), name.strip(), description.strip())).lastrowid
    except sqlite3.IntegrityError:
        eid = conn.execute("SELECT id FROM entities WHERE type = ? AND name = ?", (type.strip(), name.strip())).fetchone()["id"]
    return back(f"/world/{eid}")


@app.get("/world/{entity_id}", response_class=HTMLResponse)
def entity_detail(request: Request, entity_id: int, conn=DB):
    entity = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if entity is None:
        raise HTTPException(404)
    assets = conn.execute(
        "SELECT ae.role, a.* FROM asset_entities ae JOIN assets a ON a.id = ae.asset_id WHERE ae.entity_id = ? ORDER BY a.id DESC",
        (entity_id,),
    ).fetchall()
    links = conn.execute(
        "SELECT l.relation, 'out' AS dir, e.id, e.type, e.name FROM entity_links l JOIN entities e ON e.id = l.to_id WHERE l.from_id = ? "
        "UNION ALL SELECT l.relation, 'in', e.id, e.type, e.name FROM entity_links l JOIN entities e ON e.id = l.from_id WHERE l.to_id = ?",
        (entity_id, entity_id),
    ).fetchall()
    others = conn.execute("SELECT id, type, name FROM entities WHERE id != ? ORDER BY type, name", (entity_id,)).fetchall()
    used = conn.execute(
        "SELECT u.used_at, p.id, p.title, p.format FROM usage u JOIN productions p ON p.id = u.production_id "
        "WHERE u.entity_id = ? ORDER BY u.id DESC LIMIT 20", (entity_id,)).fetchall()
    gen_jobs = conn.execute(
        "SELECT * FROM jobs WHERE kind = 'image.generate' AND json_extract(payload, '$.entity_id') = ? "
        "ORDER BY id DESC LIMIT 5", (entity_id,)).fetchall()
    return page(request, "entity.html", entity=entity, assets=assets, links=links, others=others, used=used,
                entity_types=ENTITY_TYPES, categories=CATEGORIES, roles=ROLES, gen_jobs=gen_jobs,
                prompt=starting_prompt(entity), presets=list(cfg.section("images").get("presets", {})))


def starting_prompt(entity) -> str:
    template = cfg.section("images").get("prompts", {}).get(entity["type"], "{description}")
    return template.format(name=entity["name"], description=entity["description"] or entity["name"])


@app.post("/generate")
def generate(prompt: str = Form(...), preset: str = Form("draft"), count: int = Form(1), seed: str = Form(""),
             entity_id: str = Form(""), role: str = Form("reference"), conn=DB):
    eid = int(entity_id) if entity_id.isdigit() else None
    payload = {"prompt": prompt.strip(), "preset": preset, "count": count, "seed": seed.strip(), "role": role}
    if eid:
        payload["entity_id"] = eid
        etype = conn.execute("SELECT type FROM entities WHERE id = ?", (eid,)).fetchone()["type"]
        extra = cfg.section("images").get("negative_extra", {}).get(etype)
        if extra:
            payload["negative"] = f"{cfg.section('images').get('negative', '')}, {extra}"
    jobs.enqueue(conn, "image.generate", payload, priority=3)
    return back(f"/world/{eid}" if eid else "/jobs")


@app.post("/world/{entity_id}")
def entity_update(entity_id: int, type: str = Form(...), name: str = Form(...), description: str = Form(""),
                  attributes: str = Form("{}"), conn=DB):
    try:
        attrs = json.loads(attributes or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"attributes must be JSON: {exc}")
    conn.execute(
        "UPDATE entities SET type = ?, name = ?, description = ?, attributes = ?, updated_at = ? WHERE id = ?",
        (type.strip(), name.strip(), description.strip(), db.dumps(attrs), db.now(), entity_id),
    )
    return back(f"/world/{entity_id}")


@app.post("/world/{entity_id}/link")
def entity_link(entity_id: int, to_id: int = Form(...), relation: str = Form(...), conn=DB):
    conn.execute("INSERT OR IGNORE INTO entity_links (from_id, to_id, relation) VALUES (?, ?, ?)",
                 (entity_id, to_id, relation.strip() or "related"))
    return back(f"/world/{entity_id}")


@app.post("/world/{entity_id}/unlink")
def entity_unlink(entity_id: int, other_id: int = Form(...), relation: str = Form(...), conn=DB):
    conn.execute("DELETE FROM entity_links WHERE relation = ? AND ((from_id = ? AND to_id = ?) OR (from_id = ? AND to_id = ?))",
                 (relation, entity_id, other_id, other_id, entity_id))
    return back(f"/world/{entity_id}")


@app.post("/world/{entity_id}/delete")
def entity_delete(entity_id: int, conn=DB):
    # Assets stay in the library; only the entity and its links go.
    conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    return back("/world")


# ---------------------------------------------------------------- ideas and notepad

@app.get("/ideas", response_class=HTMLResponse)
def ideas(request: Request, conn=DB):
    rows = conn.execute(
        "SELECT * FROM ideas ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'in_production' THEN 1 "
        "WHEN 'done' THEN 2 ELSE 3 END, id DESC").fetchall()
    return page(request, "ideas.html", ideas=rows, formats=pipeline.list_formats(cfg), format_pins=rolls.format_pins)


@app.post("/ideas")
def idea_create(title: str = Form(...), body: str = Form(""), format: str = Form(""), pins: str = Form(""), conn=DB):
    conn.execute("INSERT INTO ideas (title, body, format, pins) VALUES (?, ?, ?, ?)",
                 (title.strip(), body.strip(), format or None, db.dumps(rolls.parse_pins(pins))))
    return back("/ideas")


@app.post("/ideas/{idea_id}")
def idea_update(idea_id: int, title: str = Form(...), body: str = Form(""), format: str = Form(""),
                pins: str = Form(""), status: str = Form("queued"), conn=DB):
    conn.execute("UPDATE ideas SET title = ?, body = ?, format = ?, pins = ?, status = ?, updated_at = ? WHERE id = ?",
                 (title.strip(), body.strip(), format or None, db.dumps(rolls.parse_pins(pins)), status, db.now(), idea_id))
    return back("/ideas")


@app.post("/ideas/{idea_id}/delete")
def idea_delete(idea_id: int, conn=DB):
    conn.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
    return back("/ideas")


@app.get("/notepad", response_class=HTMLResponse)
def notepad(request: Request):
    text = NOTEPAD.read_text() if NOTEPAD.is_file() else ""
    return page(request, "notepad.html", text=text, path=NOTEPAD)


@app.post("/notepad")
async def notepad_save(request: Request):
    data = await request.json()
    tmp = NOTEPAD.with_suffix(".tmp")
    tmp.write_text(str(data.get("text", "")))
    tmp.replace(NOTEPAD)
    return {"saved": db.now()}


# ---------------------------------------------------------------- productions

@app.get("/productions", response_class=HTMLResponse)
def productions(request: Request, idea: int | None = None, conn=DB):
    rows = conn.execute("SELECT * FROM productions ORDER BY id DESC LIMIT 100").fetchall()
    open_ideas = conn.execute("SELECT id, title, format FROM ideas WHERE status = 'queued' ORDER BY id DESC").fetchall()
    return page(request, "productions.html", productions=rows, formats=pipeline.list_formats(cfg), ideas=open_ideas,
                preselect_idea=idea)


@app.post("/productions")
def production_create(format: str = Form(...), title: str = Form(""), idea_id: str = Form(""), pins: str = Form(""),
                      seed: str = Form(""), budget: str = Form("0"), run: str = Form(""), conn=DB):
    pid = pipeline.create_production(
        conn, cfg, format, title=title.strip(), idea_id=int(idea_id) if idea_id.isdigit() else None,
        pins=rolls.parse_pins(pins), seed=int(seed) if seed.strip().lstrip("-").isdigit() else None,
        budget_usd=float(budget or 0),
    )
    if run:
        jobs.enqueue(conn, "production.run", {"production_id": pid}, production_id=pid)
    return back(f"/productions/{pid}")


@app.get("/productions/{production_id}", response_class=HTMLResponse)
def production_detail(request: Request, production_id: int, conn=DB):
    prod = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    if prod is None:
        raise HTTPException(404)
    try:
        fmt = pipeline.load_format(cfg, prod["format"])
    except KeyError:
        fmt = {"format": {"id": prod["format"], "name": prod["format"]}, "pipeline": {"stages": []}}
    docs = conn.execute("SELECT * FROM documents WHERE production_id = ? ORDER BY kind, version DESC",
                        (production_id,)).fetchall()
    prod_jobs = conn.execute("SELECT * FROM jobs WHERE production_id = ? ORDER BY id DESC", (production_id,)).fetchall()
    shots = conn.execute("SELECT * FROM shots WHERE production_id = ? ORDER BY ord", (production_id,)).fetchall()
    entity_names = {r["id"]: f"{r['type']}: {r['name']}" for r in conn.execute("SELECT id, type, name FROM entities")}
    latest = {}
    for d in docs:  # ordered by kind, version DESC: the first of each kind is the newest
        latest.setdefault(d["kind"], db.loads(d["body"]))
    return page(request, "production.html", prod=prod, fmt=fmt, docs=docs, jobs=prod_jobs, shots=shots,
                built=set(pipeline.STAGES), entity_names=entity_names, latest=latest,
                pending=pipeline.pending_requests(cfg, production_id), writing_stages=set(pipeline.SCHEMAS),
                answer_error=request.query_params.get("error", ""), cast_form=_cast_form(conn, prod, latest),
                spend=spend.by_stage(conn, production_id), pdir=f"{production_id:04d}", **_picks(conn, prod),
                voices=conn.execute("SELECT id, name, description FROM entities WHERE type = 'voice' ORDER BY name").fetchall())


def _picks(conn, prod) -> dict:
    """The make form's choices for the production page: the prompt, the asked length and what was attached."""
    opts = db.loads(prod["options"])
    idea = conn.execute("SELECT title, body FROM ideas WHERE id = ?", (prod["idea_id"],)).fetchone() if prod["idea_id"] else None
    named = lambda table, ids: conn.execute(  # noqa: E731
        f"SELECT * FROM {table} WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
    return {"opts": opts, "idea": idea, "picked_entities": named("entities", opts.get("characters", []) + opts.get("voices", [])),
            "picked_assets": named("assets", opts.get("assets", []))}


def _cast_form(conn, prod, latest) -> dict | None:
    """Roles to show in the casting form while the production waits at the cast stage."""
    if prod["stage"] != "cast" or "screenplay" not in latest:
        return None
    paths = pipeline.casting_paths(cfg, prod["id"])
    if paths["answer"].exists():
        return None
    proposal = (json.loads(paths["proposal"].read_text()) if paths["proposal"].exists()
                else pipeline.propose_casting(conn, latest["screenplay"]))
    speaking = set(pipeline.speaking_characters(latest["screenplay"]))
    desc = {m["character"]: m["description"] for m in latest["screenplay"]["cast"]}
    return {"roles": [{**r, "speaks": r["character"] in speaking, "description": desc.get(r["character"], "")}
                      for r in proposal["roles"]]}


@app.post("/productions/{production_id}/cast")
async def production_cast(production_id: int, request: Request, conn=DB):
    form = await request.form()
    roles, i = [], 0
    while f"character_{i}" in form:
        roles.append({"character": str(form[f"character_{i}"]), "voice": str(form.get(f"voice_{i}", "")) or None})
        i += 1
    pipeline.save_answer(cfg, production_id, "casting", {"roles": roles}, pipeline.CASTING_SCHEMA)
    production_run(production_id, conn)
    return back(f"/productions/{production_id}")


@app.get("/productions/{production_id}/file/{rel:path}")
def production_file(production_id: int, rel: str, name: str = ""):
    base = pipeline.production_dir(cfg, production_id).resolve()
    path = (cfg.path("productions") / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404)
    # ?name= downloads it under the production's title.
    return FileResponse(path, filename=name or None, headers={"Cache-Control": "no-cache"})


@app.post("/productions/{production_id}/answer/{stage}")
def production_answer(production_id: int, stage: str, answer: str = Form(...), conn=DB):
    if stage not in pipeline.SCHEMAS:
        raise HTTPException(404)
    try:
        data = json.loads(answer)
        pipeline.save_answer(cfg, production_id, stage, data, pipeline.SCHEMAS[stage])
    except (json.JSONDecodeError, ValueError) as exc:
        from urllib.parse import quote
        return back(f"/productions/{production_id}?error={quote(str(exc)[:1500])}#write-{stage}")
    production_run(production_id, conn)
    return back(f"/productions/{production_id}")


@app.post("/productions/{production_id}/redo/{stage}")
def production_redo(production_id: int, stage: str, conn=DB):
    """Run a production again from a non-writing stage (e.g. sound after swapping a sound)."""
    if stage not in pipeline.STAGES or stage in pipeline.ANSWER_STAGE.values():
        raise HTTPException(404)
    conn.execute("UPDATE productions SET stage = ?, status = 'planned', note = '' WHERE id = ?", (stage, production_id))
    production_run(production_id, conn)
    return back(f"/productions/{production_id}")


@app.post("/productions/{production_id}/rewind/{stage}")
def production_rewind(production_id: int, stage: str, conn=DB):
    if stage not in pipeline.SCHEMAS:
        raise HTTPException(404)
    pipeline.rewind(conn, cfg, production_id, stage)
    production_run(production_id, conn)
    return back(f"/productions/{production_id}")


@app.post("/productions/{production_id}/run")
def production_run(production_id: int, conn=DB):
    busy = conn.execute("SELECT 1 FROM jobs WHERE production_id = ? AND kind = 'production.run' AND status IN ('queued', 'running')",
                        (production_id,)).fetchone()
    if not busy:
        jobs.enqueue(conn, "production.run", {"production_id": production_id}, production_id=production_id)
    return back(f"/productions/{production_id}")


@app.post("/productions/{production_id}/publish")
def production_publish(production_id: int, conn=DB):
    """Put a finished production on air (again): mark it, and run the publish stage. Pressing it is
    watching it: a piece held back for blocking problems goes on air anyway."""
    prod = conn.execute("SELECT options FROM productions WHERE id = ?", (production_id,)).fetchone()
    if prod is None:
        raise HTTPException(404)
    conn.execute("UPDATE productions SET options = ?, stage = 'publish', status = 'planned', note = '' WHERE id = ?",
                 (db.dumps({**db.loads(prod["options"]), "publish": True, "publish_anyway": True}), production_id))
    return production_run(production_id, conn)


@app.post("/productions/{production_id}/cancel")
def production_cancel(production_id: int, conn=DB):
    conn.execute("UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE production_id = ? AND status IN ('queued', 'blocked')",
                 (db.now(), production_id))
    conn.execute("UPDATE productions SET status = 'cancelled', updated_at = ? WHERE id = ? AND status != 'done'",
                 (db.now(), production_id))
    return back(f"/productions/{production_id}")


# ---------------------------------------------------------------- jobs

@app.get("/jobs", response_class=HTMLResponse)
def job_list(request: Request, status: str = "", conn=DB):
    sql, args = "SELECT * FROM jobs", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT 200", args).fetchall()
    return page(request, "jobs.html", jobs=rows, status=status, counts=counts(conn)["jobs"])


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, conn=DB):
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise HTTPException(404)
    return page(request, "job.html", job=job)


@app.post("/jobs/{job_id}/retry")
def job_retry(job_id: int, conn=DB):
    jobs.retry(conn, job_id)
    return back(f"/jobs/{job_id}")


@app.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int, conn=DB):
    jobs.cancel(conn, job_id)
    return back(f"/jobs/{job_id}")


@app.post("/jobs/clear")
def jobs_clear(conn=DB):
    jobs.clear_dead(conn)
    return back("/system")


@app.post("/jobs/retry-blocked")
def jobs_retry_blocked(conn=DB):
    conn.execute("UPDATE jobs SET status = 'queued', error = NULL, finished_at = NULL WHERE status = 'blocked'")
    return back("/jobs")


@app.post("/scan")
def scan(conn=DB):
    jobs.enqueue(conn, "library.rescan", priority=10)
    return back("/jobs")


# ---------------------------------------------------------------- soundfonts (music tool: MIDIgen)

SOUNDFONTS = cfg.path("soundfonts")
SOUNDFONTS.mkdir(parents=True, exist_ok=True)


def _midigen(*args: str, timeout: int = 600):
    """Run the MIDIgen CLI ([tools] midigen) and return its JSON output."""
    exe = cfg.tool("midigen")
    if not exe:
        raise HTTPException(503, "MIDIgen isn't configured. Set [tools] midigen in config/aitv.toml.")
    try:
        proc = subprocess.run([exe, *args, "--json"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise HTTPException(503, f"MIDIgen not found at {exe}")
    if proc.returncode not in (0, 2):
        raise HTTPException(500, f"midigen {args[0]} failed: {proc.stderr.strip()[-600:]}")
    return json.loads(proc.stdout)


def _soundfont_catalog() -> dict:
    summary = _midigen("catalog", "--cached")
    try:
        return json.loads(Path(summary["catalog"]).read_text())
    except (OSError, ValueError):
        return {"soundfonts": [], "errors": []}


@app.get("/soundfonts", response_class=HTMLResponse)
def soundfonts_page(request: Request):
    error, cat = "", {"soundfonts": [], "errors": []}
    try:
        cat = _soundfont_catalog()
    except HTTPException as exc:
        error = exc.detail
    return page(request, "soundfonts.html", cat=cat, error=error, folder=SOUNDFONTS)


@app.post("/soundfonts/upload")
def soundfonts_upload(request: Request, files: list[UploadFile]):
    saved, problems = [], []
    for up in files:
        if not up.filename:
            continue
        name = ingest.safe_name(up.filename)
        if not name.lower().endswith(".sf2"):
            problems.append(f"{up.filename}: only .sf2 SoundFonts are supported")
            continue
        head = up.file.read(12)
        up.file.seek(0)
        if head[:4] != b"RIFF" or head[8:12] != b"sfbk":
            problems.append(f"{up.filename}: not a SoundFont 2 file")
            continue
        dest = SOUNDFONTS / name
        if dest.exists():
            problems.append(f"{up.filename}: {name} is already uploaded (delete it first to replace it)")
            continue
        part = SOUNDFONTS / f".{name}.part"
        with open(part, "wb") as out:
            shutil.copyfileobj(up.file, out, length=1 << 20)
        part.rename(dest)
        saved.append(name)
    summary = _midigen("catalog") if saved else None
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"saved": saved, "problems": problems,
                             "catalog_errors": summary["errors"] if summary else []},
                            status_code=200 if saved or not problems else 400)
    return back("/soundfonts")


@app.post("/soundfonts/scan")
def soundfonts_scan():
    _midigen("catalog")
    return back("/soundfonts")


@app.post("/soundfonts/delete")
def soundfonts_delete(file: str = Form(...)):
    path = (SOUNDFONTS / file).resolve()
    if path.parent != SOUNDFONTS or not path.is_file():
        raise HTTPException(404)
    path.unlink()
    _midigen("catalog")
    return back("/soundfonts")


@app.get("/soundfonts/{sf_id}", response_class=HTMLResponse)
def soundfont_page(request: Request, sf_id: str, family: str = ""):
    font = next((f for f in _soundfont_catalog()["soundfonts"] if f["id"] == sf_id), None)
    if font is None:
        raise HTTPException(404)
    families = sorted({p["gm_family"] for p in font["presets"] if p["kind"] == "melodic"})
    presets = [p for p in font["presets"] if not family or p.get("gm_family") == family
               or (family == "drums" and p["kind"] == "drum kit")]
    return page(request, "soundfont.html", font=font, presets=presets, families=families, family=family)


@app.get("/soundfonts/{sf_id}/audition/{bank}/{program}.wav")
def soundfont_audition(sf_id: str, bank: int, program: int, phrase: str = "auto"):
    """Render (or reuse) MIDIgen's test phrase for one preset."""
    r = _midigen("audition", sf_id, f"{bank}:{program}", "--phrase", phrase)
    if not r.get("files"):
        raise HTTPException(404, "; ".join(r.get("errors", [])))
    return FileResponse(r["files"]["wav"], media_type="audio/wav")


# ---------------------------------------------------------------- test outputs

TESTS = cfg.path("test_outputs")
FILE_KINDS = {
    "audio": {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".opus"},
    "video": {".mp4", ".webm", ".mov", ".mkv"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "text": {".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".log", ".csv"},
    "midi": {".mid", ".midi"},
}
NOTE_NAMES = {"notes.md", "README.md", "notes.txt"}
MAX_INLINE_TEXT = 20_000


def _kind(path: Path) -> str:
    return next((k for k, exts in FILE_KINDS.items() if path.suffix.lower() in exts), "other")


def _inside_tests(rel: str) -> Path:
    path = (TESTS / rel).resolve()
    if path == TESTS or not path.is_relative_to(TESTS) or not path.exists():
        raise HTTPException(404)
    return path


def _visible(folder: Path) -> list[Path]:
    """A folder's entries, hidden ones skipped."""
    return [e for e in folder.iterdir() if not e.name.startswith(".")]


@app.get("/tests", response_class=HTMLResponse)
def tests_page(request: Request, area: str = ""):
    """One row per test (an area's subfolder), newest first. Only top-level entries are looked at, so
    the page stays instant however much a test holds; its files load when it's opened."""
    tests = []
    areas = sorted(d.name for d in _visible(TESTS) if d.is_dir())
    for a in areas if not area else [area]:
        for d in _visible(TESTS / a) if (TESTS / a).is_dir() else []:
            if not d.is_dir():
                continue
            entries = _visible(d)
            newest = max([e.stat().st_mtime for e in entries] or [d.stat().st_mtime])
            tests.append({"folder": f"{a}/{d.name}", "newest": newest, "count": len(entries),
                          "when": datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    tests.sort(key=lambda t: t["newest"], reverse=True)
    return page(request, "tests.html", tests=tests, areas=areas, area=area, root=TESTS)


@app.get("/tests/folder", response_class=HTMLResponse)
def tests_folder(request: Request, rel: str, area: str = ""):
    """One folder's files (not its subfolders' files): names and sizes only, nothing probed or read."""
    folder = _inside_tests(rel)
    if not folder.is_dir():
        raise HTTPException(404)
    note, files, subs = "", [], []
    for e in sorted(_visible(folder), key=lambda e: e.name):
        r = str(e.relative_to(TESTS))
        if e.is_dir():
            subs.append({"name": e.name, "rel": r, "count": len(_visible(e))})
        elif e.name in NOTE_NAMES:
            note = e.read_text(errors="replace")[:MAX_INLINE_TEXT]
        else:
            kind = _kind(e)
            render = e.with_suffix(".wav") if kind == "midi" else None
            files.append({"name": e.name, "rel": r, "kind": kind, "size": e.stat().st_size,
                          "render": str(render.relative_to(TESTS)) if render and render.exists() else ""})
    # Things to look at first; scripts, data and logs after.
    files.sort(key=lambda f: f["kind"] in ("text", "other"))
    return templates.TemplateResponse(request, "_tests_folder.html", {"rel": rel, "note": note, "files": files,
                                                                      "subs": subs, "area": area})


@app.get("/tests/file/{rel:path}")
def tests_file(rel: str, download: bool = False):
    path = _inside_tests(rel)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name if download else None, headers={"Cache-Control": "no-cache"})


@app.post("/tests/delete")
def tests_delete(rel: str = Form(...), area: str = Form("")):
    path = _inside_tests(rel)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return back(f"/tests?area={area}" if area else "/tests")


# ---------------------------------------------------------------- formats

@app.get("/formats", response_class=HTMLResponse)
def formats(request: Request):
    items = []
    for fmt in pipeline.list_formats(cfg):
        fdir = Path(fmt["_dir"])
        prompts = sorted(p.name for p in (fdir / "prompts").glob("*.md")) if (fdir / "prompts").is_dir() else []
        items.append({"fmt": fmt, "toml": (fdir / "format.toml").read_text(), "prompts": prompts})
    return page(request, "formats.html", items=items, built=set(pipeline.STAGES))
