"""Publish: a finished production goes on air.

The final is copied into the media library where the director's catalog expects its kind
([publish] places: commercials under their brand, trailers with the promos, shorts and shows with
ai_tv's shows), then catalogued by the director's own code (`chuptv catalog publish`), with what the
pipeline knows about it: title, era, brand, a one-line blurb and summary, and tags from the director's
vocabulary. ai_tv never writes chuptv.db itself.

Runs as the last stage, and only for productions marked to publish (the make form's checkbox, the
production page's Publish button). The published copy is the keeper: nothing goes in keepers/ any more.
A piece the reviewer or QC found a blocking problem in is held back: the production waits on you (and
Chuppybot DMs you) until you watch it and press Publish anyway.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

from .config import Config
from .jobs import Blocked

TAGS_SQL = "SELECT facet, name FROM tags WHERE status = 'active' ORDER BY facet, name"


def settings(cfg: Config) -> dict:
    return cfg.section("publish")


def file_name(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "untitled"


def brand_folder(name: str) -> str:
    """'1-800-COPE-BALDY' -> '1-800-cope-baldy': lowercased, like the brand folders already there."""
    return re.sub(r"\s+", " ", re.sub(r"[/\\:*?\"<>|]+", " ", name)).strip().lower() or "unknown brand"


def place(cfg: Config, fmt_id: str, rolls: dict, brand: str | None) -> dict:
    """The first [publish] places entry that fits: {dir, kind}."""
    ad_type = str(rolls.get("ad_type", {}).get("value", ""))
    for p in settings(cfg).get("places", []):
        if p.get("format", "*") not in ("*", fmt_id) or p.get("ad_type", ad_type) != ad_type:
            continue
        if "{brand}" in p["dir"] and not brand:
            raise Blocked(f"publish: a {fmt_id} is filed under its brand, and this one has no subject")
        return {"dir": Path(p["dir"].replace("{brand}", brand_folder(brand or ""))).expanduser(), "kind": p.get("kind", "generated")}
    raise Blocked(f"no [publish] place fits format {fmt_id} ({ad_type or 'no ad_type'})")


def target(folder: Path, name: str, previous: str | None) -> Path:
    """Where the file goes: this production's earlier path if it's in the same folder, else a free name
    (a different production's file is never overwritten)."""
    if previous and Path(previous).parent == folder:
        return Path(previous)
    dst, n = folder / f"{name}.mp4", 2
    while dst.exists():
        dst, n = folder / f"{name}_{n}.mp4", n + 1
    return dst


def copy_in(src: Path, dst: Path) -> None:
    """Copy next to the target and rename, so the library's scanners never see half a file."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, part)
    part.replace(dst)


def vocabulary(cfg: Config) -> dict[str, list[str]]:
    """The director's active tags by facet, read only."""
    db = Path(settings(cfg).get("catalog_db", "~/chuptv_director/data/chuptv.db")).expanduser()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out: dict[str, list[str]] = {}
        for facet, name in conn.execute(TAGS_SQL):
            out.setdefault(facet, []).append(name)
        return out
    finally:
        conn.close()


def _clip(text: str, most: int) -> str:
    """At most `most` characters, cut at a word."""
    text = " ".join(text.split())
    return text if len(text) <= most else text[:most - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"


def era_tag(year) -> str | None:
    m = re.match(r"(19|20)(\d)\d", str(year or ""))
    return f"{m.group(2)}0s" if m else None


def describe_schema(vocab: dict[str, list[str]]) -> dict:
    tag_list = lambda facet: {"type": "array", "items": {"enum": vocab.get(facet, [])}}  # noqa: E731
    return {
        "title": "describe", "type": "object", "additionalProperties": False,
        "required": ["blurb", "summary", "product", "vibe", "genre", "season"],
        "properties": {
            "blurb": {"type": "string"},
            "summary": {"type": "string"},
            "product": {"type": ["string", "null"]},
            "vibe": tag_list("vibe"), "genre": tag_list("genre"), "season": tag_list("season"),
        },
    }


DESCRIBE_RULES = """You write the TV station catalog's entry for a piece the station made itself. The
station's programming director reads it to decide where it airs, so describe what the piece IS, never
when it should air or whether it's good.

- blurb: one line, at most 100 characters, what a viewer would see. Like: "Fake 1991 mail-order ad for a
  memory-foam pillow shaped and scented like a glazed ham".
- summary: 2-4 sentences: its length, that it's AI-made, what happens, the key lines or on-screen text.
  Describe the finished cut (the transcript is what is actually said), not the plan.
- product: what's advertised (a pillow, a hotline, a film), or null for a non-ad.
- vibe, genre, season: only from the listed tags; vibes as many as fit, one or two genres if any fit,
  a season only if it's clearly seasonal."""


def holds(ctx) -> list[str]:
    """Why this piece shouldn't go on air unseen: the last review round's blocking notes and QC's
    blocking issues."""
    at = lambda t: f" at {t:.1f}s" if t is not None else ""  # noqa: E731  a whole-piece problem has no time
    rounds = (ctx.latest("review") or {}).get("rounds") or []
    out = [f"review{at(n.get('t'))}: {n['problem']}" for n in (rounds[-1]["notes"] if rounds else [])
           if n.get("severity") == "blocking"]
    out += [f"QC{at(i.get('t'))}: {i['problem']}" for i in (ctx.latest("qc") or {}).get("issues", [])
            if i.get("severity") == "blocking"]
    return out


def run(ctx) -> None:
    """The publish stage."""
    from .pipeline import WaitingOnYou

    if not ctx.options.get("publish"):
        ctx.log("publish: not marked to publish; press Publish on the production page to put it on air")
        return
    held = holds(ctx)
    if held and not ctx.options.get("publish_anyway"):
        for h in held:
            ctx.log(f"publish: held back: {h}")
        raise WaitingOnYou(f"held back from ChupTV ({len(held)} blocking problem(s)): " + "; ".join(held)
                           + ". Watch it, then press Publish anyway on the production page")
    look = ctx.latest("look")
    if not look:
        raise Blocked("publish needs a finished piece")
    src = ctx.cfg.path("productions") / look["file"]
    rolls, prod = ctx.rolls, ctx.production
    subject = rolls.get("subject", {})
    brand = None
    if subject.get("entity_id"):
        row = ctx.conn.execute("SELECT name FROM entities WHERE id = ?", (subject["entity_id"],)).fetchone()
        brand = row["name"] if row else None
    where = place(ctx.cfg, prod["format"], rolls, brand)
    previous = (ctx.latest("publish") or {}).get("path")
    dst = target(where["dir"], file_name(prod["title"]), previous)

    vocab = vocabulary(ctx.cfg)
    script, outline, qc = ctx.latest("screenplay") or {}, ctx.latest("outline") or {}, ctx.latest("qc") or {}
    era = era_tag(rolls.get("era", {}).get("value"))
    prompt = "\n\n".join(x for x in [
        f"Title: {prod['title']}  ({where['kind']}, {look.get('seconds', 0):.0f} s, era {rolls.get('era', {}).get('value', '?')})",
        f"Brand (spell it exactly like this): {brand}" if brand else "",
        f"Logline: {outline.get('logline', '')}",
        f"Screenplay:\n{json.dumps(script, ensure_ascii=False)}",
        f"What is actually said in the finished cut (speech recognition: it misspells names, so spell them as the "
        f"screenplay does):\n{qc['transcript']}" if qc.get("transcript") else "",
        "Allowed tags:\n" + "\n".join(f"- {f}: {', '.join(n)}" for f, n in vocab.items() if f in ("vibe", "genre", "season")),
        "Write the catalog entry.",
    ] if x)
    entry = ctx.write("describe", DESCRIBE_RULES, prompt, describe_schema(vocab), tier="small")

    fmt_tag = "ad" if where["kind"] in ("commercial", "promo") else "short"
    tags = [["format", fmt_tag]] + ([["era", era]] if era else []) + [
        [facet, name] for facet in ("vibe", "genre", "season") for name in dict.fromkeys(entry[facet])]
    meta = {"title": prod["title"], "year": int(prod["created_at"][:4]), "origin": "ai_tv",
            "origin_ref": f"ai_tv:productions/{prod['id']:04d}",
            "profile": {"blurb": _clip(entry["blurb"], 100), "summary": entry["summary"], "era": era, "brand": brand,
                        "product": entry["product"]},
            "profile_source": "ai_tv", "tags": tags, "tag_source": "llm"}

    if not (dst.exists() and dst.stat().st_size == src.stat().st_size and dst.read_bytes() == src.read_bytes()):
        copy_in(src, dst)
        ctx.log(f"publish: copied to {dst}")
    meta_file = ctx.cfg.path("productions") / f"{prod['id']:04d}" / "publish.json"
    meta_file.write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    director = Path(settings(ctx.cfg).get("director", "~/chuptv_director/.venv/bin/chuptv")).expanduser()
    done = subprocess.run([str(director), "catalog", "publish", str(dst), "--meta", str(meta_file)],
                          capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError(f"the director couldn't catalog it: {(done.stderr or done.stdout)[-800:]}")
    result = json.loads(done.stdout.strip().splitlines()[-1])
    ctx.conn.execute("UPDATE productions SET final_path = ? WHERE id = ?", (str(dst), prod["id"]))
    ctx.save("publish", {"path": str(dst), "code": result["code"], "kind": where["kind"], "tags": tags,
                         "profile": meta["profile"], "skipped_tags": result.get("skipped_tags", [])})
    ctx.log(f"publish: on air as {result['code']} ({where['kind']}), {dst}")
    if ctx.options.get("publish_anyway"):  # it covered the cut you watched, not the next one
        ctx.conn.execute("UPDATE productions SET options = ? WHERE id = ?",
                         (json.dumps({k: v for k, v in ctx.options.items() if k != "publish_anyway"}), prod["id"]))
