"""Productions and the stage runner.

A production is created by taking its format's rolls (pins win), resolving its subject
from the world database, and recording the seed. Running it walks the format's stage
list from wherever it last stopped. Stages that don't exist yet stop the run as
'blocked' with a clear message instead of pretending.

Writing stages (outline, screenplay) go through StageContext.write(): the answer is saved
as productions/<id>/writing/<stage>.json, so it's never asked for twice. With the
'manual' LLM backend, write() saves the request next to it and the production waits
until someone supplies the answer (pasted on the production page, or written to that file).
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import jsonschema

from . import llm as llm_mod
from . import rolls as rolls_mod
from . import notify, spend, titles
from .config import Config
from .db import dumps, loads, now
from .jobs import Blocked, Stopped

RECENT_PRODUCTIONS = 20  # how far back "recently used" looks for round-robin weights


def _stop_requested(ctx: "StageContext") -> bool:
    row = ctx.conn.execute("SELECT status FROM productions WHERE id = ?", (ctx.production["id"],)).fetchone()
    return bool(row and row["status"] == "cancelled")


def check_stop(ctx: "StageContext") -> None:
    """Raise Stopped if the production's Stop button was pressed. Checked between stages and before
    paid calls are submitted, never while a submitted job is still out (it would be paid for twice)."""
    if _stop_requested(ctx):
        raise Stopped("stopped from the production page")


class WaitingOnYou(Blocked):
    """A stage needs something a person (or an outside model) has to provide."""


# ---------------------------------------------------------------- formats

def list_formats(cfg: Config) -> list[dict]:
    return [load_format(cfg, path.parent.name) for path in sorted(cfg.path("formats").glob("*/format.toml"))]


def load_format(cfg: Config, format_id: str) -> dict:
    path = cfg.path("formats") / format_id / "format.toml"
    if not path.is_file():
        raise KeyError(f"no such format: {format_id}")
    with open(path, "rb") as f:
        fmt = tomllib.load(f)
    fmt.setdefault("format", {})["id"] = format_id
    fmt["_dir"] = str(path.parent)
    return fmt


def format_prompt(fmt: dict, name: str) -> str:
    path = Path(fmt["_dir"]) / "prompts" / f"{name}.md"
    return path.read_text() if path.is_file() else ""


def production_dir(cfg: Config, production_id: int) -> Path:
    return cfg.path("productions") / f"{production_id:04d}"


# ---------------------------------------------------------------- creating productions

def _subject_candidates(conn: sqlite3.Connection, entity_type: str) -> list[tuple[int, int]]:
    rows = conn.execute(
        """
        SELECT e.id,
               (SELECT COUNT(*) FROM usage u
                 WHERE u.entity_id = e.id
                   AND u.production_id IN (SELECT id FROM productions ORDER BY id DESC LIMIT ?)) AS uses
          FROM entities e WHERE e.type = ? ORDER BY e.id
        """,
        (RECENT_PRODUCTIONS, entity_type),
    ).fetchall()
    return [(r["id"], r["uses"]) for r in rows]


# The stages whose model the make form can pick: (stage, label, tier, what the model has to look at).
# Music is left out on purpose (MIDIgen's writing keeps its own [llm.stage_models] music entry).
MODEL_STAGES = [
    ("outline", "Outline", "strong", None),
    ("screenplay", "Screenplay", "strong", None),
    ("plan", "Shot plan", "strong", None),
    ("edit", "Editor (looks at stills)", "strong", "image"),
    ("review", "Reviewer (watches the video)", "strong", "video"),
    ("describe", "Publish description", "small", None),
]


def stage_model(cfg: Config, stage: str, options: dict | None = None) -> str | None:
    """A production's pick for a stage, else [llm.stage_models]; review_2 counts as review, music_0_-1 as music."""
    base = stage.split("_")[0]
    picked = (options or {}).get("models") or {}
    if base != "music" and (picked.get(stage) or picked.get(base)):
        return picked.get(stage) or picked.get(base)
    models = cfg.section("llm").get("stage_models", {}) or {}
    return models.get(stage) or models.get(base) or None


def effective_model(cfg: Config, stage: str, options: dict | None = None) -> str | None:
    """The model that would actually run a stage, the backend's tier model included (for showing it)."""
    tier = next((t for s, _, t, _ in MODEL_STAGES if s == stage.split("_")[0]), "strong")
    backend = cfg.section("llm").get("backend", "")
    return stage_model(cfg, stage, options) or cfg.section(f"llm.{backend}").get(f"model_{tier}")


def create_production(
    conn: sqlite3.Connection,
    cfg: Config,
    format_id: str,
    *,
    title: str = "",
    idea_id: int | None = None,
    pins: dict | None = None,
    seed: int | None = None,
    budget_usd: float = 0.0,
    options: dict | None = None,
    roll: bool = True,
) -> int:
    """roll=False (the make form): the producer's prompt is the whole brief. Nothing is rolled; the
    pins are the producer's own tags, given to the writer as written. A format with a subject gets a
    new one, typed by a "type" tag or else named and typed by the outline (only if the piece sells or
    promotes something)."""
    fmt = load_format(cfg, format_id)
    if idea_id:
        idea = conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
        pins = {**loads(idea["pins"]), **(pins or {})}
        title = title or idea["title"]
    seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
    roll_defs = fmt.get("rolls", {})
    if not roll:
        roll_defs = {}
        options = {**(options or {}), "rolls": False}
    taken = rolls_mod.take(roll_defs, pins, seed)
    if not roll and fmt.get("subject"):
        kind = _tagged_kind(fmt, taken)
        taken["subject"] = {"value": "new", "pinned": False}
        if kind:  # filed by this kind when published; the writer sees only the producer's own tag
            taken["subject"]["entity_type"] = kind
            taken[fmt["subject"].get("from_roll", "ad_type")] = {"value": kind, "pinned": False, "from": "tags"}

    subject_cfg = fmt.get("subject")
    chosen_entity = None
    if subject_cfg and "subject" in taken and roll:
        entity_type = subject_cfg.get("entity_type", "")
        if "from_roll" in subject_cfg:
            entity_type = str(taken[subject_cfg["from_roll"]]["value"])
        taken["subject"]["entity_type"] = entity_type
        value = taken["subject"]["value"]
        if value == "existing":
            candidates = _subject_candidates(conn, entity_type)
            if candidates:
                chosen_entity = rolls_mod.pick_least_used(rolls_mod.roll_rng(seed, "subject_pick"), candidates)
                taken["subject"]["entity_id"] = chosen_entity
            else:
                taken["subject"].update(value="new", reason=f"no existing {entity_type} in the database yet")
        elif value not in ("new",):
            # A pinned subject by name: use it if it exists, otherwise it becomes a new one.
            row = conn.execute(
                "SELECT id FROM entities WHERE type = ? AND name = ? COLLATE NOCASE", (entity_type, value)
            ).fetchone()
            if row:
                chosen_entity = row["id"]
                taken["subject"]["entity_id"] = chosen_entity

    stages = fmt.get("pipeline", {}).get("stages", [])
    cur = conn.execute(
        "INSERT INTO productions (format, title, idea_id, seed, rolls, budget_usd, options, stage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (format_id, title or fmt["format"].get("name", format_id), idea_id, seed, dumps(taken), budget_usd,
         dumps(options or {}), stages[0] if stages else None),
    )
    production_id = cur.lastrowid
    if chosen_entity:
        conn.execute("INSERT INTO usage (production_id, entity_id) VALUES (?, ?)", (production_id, chosen_entity))
    if idea_id:
        conn.execute("UPDATE ideas SET status = 'in_production', updated_at = ? WHERE id = ?", (now(), idea_id))
    return production_id


# ---------------------------------------------------------------- writing (LLM or manual)

def writing_paths(cfg: Config, production_id: int, stage: str) -> dict[str, Path]:
    base = production_dir(cfg, production_id) / "writing"
    return {"request": base / f"{stage}.request.md", "schema": base / f"{stage}.schema.json",
            "answer": base / f"{stage}.json"}


def validate(schema: dict, data) -> None:
    """Raise ValueError listing every way data doesn't match schema."""
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        lines = [f"- {'/'.join(map(str, e.path)) or '(top)'}: {e.message}" for e in errors[:15]]
        raise ValueError("the answer doesn't match the expected shape:\n" + "\n".join(lines))


def save_answer(cfg: Config, production_id: int, stage: str, data: dict, schema: dict) -> Path:
    validate(schema, data)
    path = writing_paths(cfg, production_id, stage)["answer"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def pending_requests(cfg: Config, production_id: int) -> list[dict]:
    base = production_dir(cfg, production_id) / "writing"
    out = []
    for req in sorted(base.glob("*.request.md")) if base.is_dir() else []:
        stage = req.name.removesuffix(".request.md")
        paths = writing_paths(cfg, production_id, stage)
        if not paths["answer"].exists():
            out.append({"stage": stage, "request": req.read_text(), "schema": paths["schema"].read_text()})
    return out


@dataclass
class StageContext:
    conn: sqlite3.Connection
    cfg: Config
    fmt: dict
    production: sqlite3.Row
    log: Callable[[str], None]
    llm: llm_mod.Backend | None = None
    stage: str | None = None  # the stage running now: what spend rows and per-stage models are keyed on

    def paid(self, what: str, cost: float | None, model: str | None = None, **kw) -> None:
        """Record a paid call against this production and the running stage."""
        spend.record(self.conn, self.production["id"], self.stage, what, cost, model, **kw)

    def check_budget(self, doing: str = "continue") -> None:
        spend.check_budget(self.conn, self.production["id"], doing)

    def stage_model(self, stage: str | None = None) -> str | None:
        """The model for a stage: the make form's pick for this production, else the one [llm.stage_models]
        names (writing stages, music_* by prefix, edit, review), else None for the backend's tier model."""
        return stage_model(self.cfg, stage or self.stage or "", self.options)

    @property
    def rolls(self) -> dict:
        return loads(self.production["rolls"])

    @property
    def options(self) -> dict:
        """The make form's choices (length range, attached characters, voices and assets)."""
        return loads(self.production["options"]) if "options" in self.production.keys() else {}

    def set_rolls(self, rolls: dict) -> None:
        self.conn.execute("UPDATE productions SET rolls = ? WHERE id = ?", (dumps(rolls), self.production["id"]))
        self.production = self.conn.execute("SELECT * FROM productions WHERE id = ?", (self.production["id"],)).fetchone()

    def write(self, stage: str, system: str, prompt: str, schema: dict, tier: str = "strong",
              reasoning: str | None = None, images: list[Path] | None = None, video: Path | None = None) -> dict:
        """Get a stage's writing: a saved answer, a fresh one from the LLM, or wait for a person.
        images / video are shown to the model with the prompt (listed by path in a manual request)."""
        paths = writing_paths(self.cfg, self.production["id"], stage)
        if paths["answer"].exists():
            data = json.loads(paths["answer"].read_text())
            try:
                validate(schema, data)
            except ValueError as exc:
                raise WaitingOnYou(f"the saved {stage} needs fixing: {exc}") from exc
            self.log(f"using the saved {stage} ({paths['answer'].name})")
            return data
        backend = self.cfg.section("llm").get("backend", "manual")
        if self.llm is None and backend in ("manual", "none", ""):
            paths["request"].parent.mkdir(parents=True, exist_ok=True)
            seen = "".join(f"\n- {p}" for p in [*(images or []), *([video] if video else [])])
            paths["request"].write_text(f"# {stage}\n\n## Instructions\n\n{system}\n\n## Request\n\n{prompt}\n"
                                        + (f"\n## Files to look at\n{seen}\n" if seen else ""))
            paths["schema"].write_text(json.dumps(schema, indent=2) + "\n")
            raise WaitingOnYou(f"waiting for the {stage} to be written (see the production page)")
        if self.llm is None:
            try:
                self.llm = llm_mod.get(self.cfg)
            except llm_mod.LLMUnavailable as exc:
                raise Blocked(str(exc)) from exc
        self.log(f"asking the {self.llm.name if self.llm else backend} backend for the {stage}")
        data = self._ask(system, prompt, schema, tier, reasoning, stage, images, video)
        try:
            validate(schema, data)
        except ValueError as exc:
            # One repair round: show the model its answer and what's wrong with it.
            self.log(f"the {stage} didn't validate, asking for a fix: {exc}")
            data = self._ask(system, f"{prompt}\n\nYour previous answer:\n{json.dumps(data)}\n\n{exc}\n"
                             "Reply with the corrected JSON object.", schema, tier, reasoning, stage, images, video)
        save_answer(self.cfg, self.production["id"], stage, data, schema)
        return data

    def _ask(self, system: str, prompt: str, schema: dict, tier: str, reasoning: str | None = None,
             stage: str | None = None, images: list[Path] | None = None, video: Path | None = None) -> dict:
        kwargs = {"reasoning": reasoning} if reasoning else {}
        if model := self.stage_model(stage):
            kwargs["model"] = model
        if images:
            kwargs["images"] = images
        if video:
            kwargs["video"] = video
        self.check_budget(f"ask for the {stage or self.stage}")
        if hasattr(self.llm, "last_usage"):
            self.llm.last_usage = {}
        data = self.llm.complete_json(system, prompt, schema, tier=tier, **kwargs)
        if usage := getattr(self.llm, "last_usage", None):
            cost = usage.get("cost_usd")
            self.paid("llm", cost, usage.get("model"), tokens_in=usage.get("prompt_tokens"),
                      tokens_out=usage.get("completion_tokens"), note=stage or "")
            self.log(f"  {usage['model']} ({usage.get('provider') or '?'}): {usage['prompt_tokens']} in, "
                     f"{usage['completion_tokens']} out"
                     + (f" ({usage['reasoning_tokens']} thinking)" if usage.get("reasoning_tokens") else "")
                     + (f", ${cost:.4f}" if cost is not None else ""))
        return data

    def latest(self, kind: str) -> dict | None:
        row = self.conn.execute(
            "SELECT body FROM documents WHERE production_id = ? AND kind = ? ORDER BY version DESC LIMIT 1",
            (self.production["id"], kind),
        ).fetchone()
        return loads(row["body"]) if row else None

    def save(self, kind: str, body: dict) -> int:
        if self.latest(kind) == body:
            return self.conn.execute(
                "SELECT MAX(version) FROM documents WHERE production_id = ? AND kind = ?",
                (self.production["id"], kind)).fetchone()[0]
        version = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM documents WHERE production_id = ? AND kind = ?",
            (self.production["id"], kind),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO documents (production_id, kind, version, body) VALUES (?, ?, ?, ?)",
            (self.production["id"], kind, version, dumps(body)),
        )
        return version


# ---------------------------------------------------------------- shared prompt pieces

def _rolls_block(ctx: StageContext) -> str:
    """The "Tags:" section of a writing prompt. Make-form productions get only the producer's own tags."""
    if ctx.options.get("rolls") is False:
        rolls, order = ctx.rolls, []
        if ctx.production["idea_id"]:  # the order they were typed in (rolls are stored sorted)
            idea = ctx.conn.execute("SELECT pins FROM ideas WHERE id = ?", (ctx.production["idea_id"],)).fetchone()
            order = list(json.loads(idea["pins"] or "{}")) if idea else []
        names = [k for k in order if k in rolls] + sorted(k for k in rolls if k not in order)
        own = [f"- {k}: {rolls[k]['value']}" if rolls[k]["value"] else f"- {k}" for k in names if rolls[k].get("pinned")]
        return "Tags the producer set:\n" + "\n".join(own) + "\n\n" if own else ""
    return f"Tags:\n{_describe_rolls(ctx)}\n\n"


def _format_name(ctx: StageContext) -> str:
    """What the producer typed as the format on the make form, else the format's own name."""
    if ctx.options.get("rolls") is False:
        return str(ctx.options.get("format_label") or "").strip()
    return ctx.fmt["format"].get("name", "")


def _format_line(ctx: StageContext) -> str:
    name = _format_name(ctx)
    return f"Format: {name}\n" if name else ""


def _describe_rolls(ctx: StageContext) -> str:
    lines = []
    for name, roll in ctx.rolls.items():
        line = f"- {name}: {roll['value']}"
        if roll.get("entity_id"):
            ent = ctx.conn.execute("SELECT name, description, attributes FROM entities WHERE id = ?",
                                   (roll["entity_id"],)).fetchone()
            if ent:
                line += f" -> {ent['name']}: {ent['description']} {ent['attributes'] if ent['attributes'] != '{}' else ''}"
        lines.append(line)
    return "\n".join(lines)


def _describe_voices(ctx: StageContext) -> str:
    rows = ctx.conn.execute("SELECT name, description FROM entities WHERE type = 'voice' ORDER BY name").fetchall()
    return "\n".join(f"- {r['name']}: {r['description'] or '(no description)'}" for r in rows) or "(none yet)"


def _pictured_characters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """World characters that have a reference picture: the only ones a shot can show as themselves."""
    from . import composite

    rows = conn.execute("SELECT id, name, description FROM entities WHERE type = 'character' ORDER BY id").fetchall()
    return [r for r in rows if composite.reference_image(conn, r["id"])]


def _system(ctx: StageContext, stage: str) -> str:
    return ("You write for a homemade AI television station. "
            + ctx.fmt["format"].get("description", "") + "\n\n" + format_prompt(ctx.fmt, stage)).strip()


WORDS_PER_S = 2.5


def length_range(ctx: StageContext) -> tuple[float, float] | None:
    """The finished length the producer asked for, (min, max) seconds; exact is min == max. Productions
    made before the make form have only the length_s roll, and no range."""
    want = ctx.options.get("length") or {}
    if want.get("min") is None:
        return None
    lo = float(want["min"])
    return lo, float(want.get("max") if want.get("max") is not None else lo)


def _length_note(ctx: StageContext) -> str:
    rng = length_range(ctx)
    if not rng:
        return ""
    lo, hi = rng
    span = f"exactly {lo:g} seconds" if lo == hi else f"between {lo:g} and {hi:g} seconds"
    return (f"Length: the finished piece must run {span}. Spoken words (lines and voiceover) take about "
            f"{WORDS_PER_S:g} per second, so write at most {int(hi * WORDS_PER_S * 0.85)} spoken words in all, "
            "leaving room for title cards and the ending.\n\n")


def _attached(ctx: StageContext, kind: str) -> list[sqlite3.Row]:
    ids = [int(i) for i in ctx.options.get(kind, [])]
    if not ids:
        return []
    table = "assets" if kind == "assets" else "entities"
    rows = {r["id"]: r for r in ctx.conn.execute(
        f"SELECT * FROM {table} WHERE id IN ({','.join('?' * len(ids))})", ids)}
    return [rows[i] for i in ids if i in rows]


def _producer_picks(ctx: StageContext) -> str:
    """What the producer attached on the make form: their explicit picks, not the World's roster."""
    out = _length_note(ctx)
    chars = _attached(ctx, "characters")
    if chars:
        out += ("Characters the producer chose for this piece (use them, by these names):\n"
                + "\n".join(f"- {c['name']}: {c['description'] or '(no description)'}" for c in chars) + "\n\n")
    voices = _attached(ctx, "voices")
    if voices:
        out += ("Voices the producer chose for this piece (cast the speaking roles from these):\n"
                + "\n".join(f"- {v['name']}: {v['description'] or '(no description)'}" for v in voices) + "\n\n")
    return out


def _idea(ctx: StageContext) -> str:
    if ctx.production["idea_id"]:
        idea = ctx.conn.execute("SELECT * FROM ideas WHERE id = ?", (ctx.production["idea_id"],)).fetchone()
        return f"{idea['title']} - {idea['body']}"
    return "(none: invent one that fits the tags)"


# ---------------------------------------------------------------- outline

SUBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "tagline": {"type": "string"},
    },
    "required": ["name", "description", "tagline"],
    "additionalProperties": False,
}

OUTLINE_SCHEMA = {
    "title": "outline",
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "logline": {"type": "string"},
        "new_subject": {"anyOf": [SUBJECT_SCHEMA, {"type": "null"}]},
        "beats": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "act_break_after": {"type": "boolean"},
                },
                "required": ["summary", "act_break_after"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "logline", "new_subject", "beats"],
    "additionalProperties": False,
}


def _kinds(fmt: dict) -> list[str]:
    """The entity types a format's subject can be: its own list, else the options of the roll it's typed by."""
    cfg = fmt.get("subject") or {}
    if cfg.get("kinds"):
        return list(cfg["kinds"])
    if cfg.get("entity_type"):
        return [cfg["entity_type"]]
    spec = fmt.get("rolls", {}).get(cfg.get("from_roll", ""), {})
    return list(spec.get("choices", spec)) if isinstance(spec, dict) else []


def _subject_kinds(ctx: StageContext) -> list[str]:
    return _kinds(ctx.fmt)


def _tagged_kind(fmt: dict, tags: dict) -> str | None:
    """A producer's "type: hotline" tag ("fake show" works too), if it names one of the format's kinds."""
    for name, tag in tags.items():
        value = re.sub(r"[\s-]+", "_", str(tag["value"]).strip().lower())
        if name.strip().lower() == "type" and value in _kinds(fmt):
            return value
    return None


def stage_outline(ctx: StageContext) -> None:
    subject = ctx.rolls.get("subject", {})
    needs_subject = subject.get("value") == "new" and not subject.get("entity_id")
    # Make-form productions roll no subject type: the outline names what the prompt is about and its kind.
    kinds = [] if subject.get("entity_type") else list(_subject_kinds(ctx))
    schema = OUTLINE_SCHEMA
    if needs_subject and kinds:
        typed = {**SUBJECT_SCHEMA, "properties": {**SUBJECT_SCHEMA["properties"], "kind": {"enum": kinds}},
                 "required": [*SUBJECT_SCHEMA["required"], "kind"]}
        schema = {**OUTLINE_SCHEMA, "properties": {**OUTLINE_SCHEMA["properties"],
                                                    "new_subject": {"anyOf": [typed, {"type": "null"}]}}}
    if needs_subject and kinds:
        ask = (f"Only if this piece advertises or promotes something ({', '.join(kinds)}), name it in "
               "new_subject (name, a one-line description, its tagline, and kind); it will be saved to the "
               "World. Otherwise set new_subject to null.\n")
    elif needs_subject and ctx.options.get("rolls") is False:
        ask = (f"Name the {subject.get('entity_type')} this piece is for in new_subject (name, a one-line "
               "description, its tagline). It will be saved to the World.\n")
    elif needs_subject:
        ask = (f"The subject is new: invent a {subject.get('entity_type')} and describe it in new_subject "
               "(name, a one-line description, its tagline). It will be saved to the World and reused in "
               "later productions.\n")
    else:
        ask = "Set new_subject to null.\n"
    prompt = (
        f"{_format_line(ctx)}"
        f"{_rolls_block(ctx)}"
        f"Pitch: {_idea(ctx)}\n\n"
        + _producer_picks(ctx)
        + ask
        + "Write the outline."
    )
    outline = ctx.write("outline", _system(ctx, "outline"), prompt, schema)

    if needs_subject and outline.get("new_subject"):
        s = outline["new_subject"]
        etype = subject.get("entity_type") or s.get("kind")
        row = ctx.conn.execute("SELECT id FROM entities WHERE type = ? AND name = ?", (etype, s["name"])).fetchone()
        eid = row["id"] if row else ctx.conn.execute(
            "INSERT INTO entities (type, name, description, attributes) VALUES (?, ?, ?, ?)",
            (etype, s["name"], s["description"], dumps({"tagline": s["tagline"]})),
        ).lastrowid
        ctx.conn.execute("INSERT INTO usage (production_id, entity_id) VALUES (?, ?)", (ctx.production["id"], eid))
        rolls = ctx.rolls
        rolls["subject"].update(entity_id=eid, entity_type=etype)
        if kinds:  # recorded for publishing (where the piece is filed), after the writing
            rolls[ctx.fmt.get("subject", {}).get("from_roll", "ad_type")] = {"value": etype, "pinned": False,
                                                                           "from": "outline"}
        ctx.set_rolls(rolls)
        ctx.log(f"new {etype} saved to the World: {s['name']}")
    if outline.get("title"):
        ctx.conn.execute("UPDATE productions SET title = ? WHERE id = ?", (outline["title"], ctx.production["id"]))
    version = ctx.save("outline", outline)
    ctx.log(f"outline v{version}: {outline.get('title', '')!r}")


# ---------------------------------------------------------------- screenplay

BEAT_TYPES = ["line", "action", "sfx", "music", "laugh", "title_card", "voiceover"]

SCREENPLAY_SCHEMA = {
    "title": "screenplay",
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "cast": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "character": {"type": "string"},
                    "description": {"type": "string"},
                    "voice": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["character", "description", "voice"],
                "additionalProperties": False,
            },
        },
        "scenes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "setting": {"type": "string"},
                    "description": {"type": "string"},
                    "act_break_after": {"type": "boolean"},
                    "beats": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"enum": BEAT_TYPES},
                                "character": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                "text": {"type": "string"},
                                "delivery": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            },
                            "required": ["type", "character", "text", "delivery"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["setting", "description", "act_break_after", "beats"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "cast", "scenes"],
    "additionalProperties": False,
}

SCREENPLAY_RULES = """Shape of the screenplay:
- cast: everyone who speaks, including an announcer or voiceover. voice = the name of an existing
  voice from the list below if one fits, otherwise null (casting picks or makes one later).
- scenes: in order. setting = a short place name ("Kitchen, night"); description = what we see.
  act_break_after = true where a real broadcast would put a commercial break (chapter marker).
- beats: in order. type is one of: line (a character speaks), voiceover (unseen narrator),
  action (what we see happen), sfx (a sound), music (a cue), laugh (studio audience), title_card
  (text on screen). character is set for line and voiceover, null otherwise. delivery is a short
  acting note for lines ("whispering", "too loud"), or null."""


def stage_screenplay(ctx: StageContext) -> None:
    outline = ctx.latest("outline")
    if not outline:
        raise Blocked("the screenplay needs an outline first")
    prompt = (
        f"{_format_line(ctx)}"
        f"{_rolls_block(ctx)}"
        f"Pitch: {_idea(ctx)}\n\n"
        f"{_producer_picks(ctx)}"
        f"Outline:\n{json.dumps(outline, indent=2, ensure_ascii=False)}\n\n"
        f"Voices already in the World:\n{_describe_voices(ctx)}\n\n"
        f"{SCREENPLAY_RULES}\n\nWrite the screenplay."
    )
    script = ctx.write("screenplay", _system(ctx, "screenplay"), prompt, SCREENPLAY_SCHEMA)
    for scene in script["scenes"]:
        for beat in scene["beats"]:
            if beat["type"] == "title_card":
                beat["text"] = titles.clean_text(beat["text"]) or beat["text"]
    lines = sum(1 for sc in script["scenes"] for b in sc["beats"] if b["type"] in ("line", "voiceover"))
    version = ctx.save("screenplay", script)
    ctx.log(f"screenplay v{version}: {len(script['scenes'])} scene(s), {lines} spoken line(s)")


# ---------------------------------------------------------------- casting

CASTING_SCHEMA = {
    "title": "casting",
    "type": "object",
    "properties": {
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "character": {"type": "string"},
                    "voice": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["character", "voice"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["roles"],
    "additionalProperties": False,
}

SPOKEN = ("line", "voiceover")


def speaking_characters(script: dict) -> list[str]:
    seen: list[str] = []
    for scene in script["scenes"]:
        for beat in scene["beats"]:
            if beat["type"] in SPOKEN and beat["character"] and beat["character"] not in seen:
                seen.append(beat["character"])
    return seen


def _voice_id(conn: sqlite3.Connection, name: str | None) -> int | None:
    if not name:
        return None
    row = conn.execute("SELECT id FROM entities WHERE type = 'voice' AND name = ? COLLATE NOCASE", (name,)).fetchone()
    return row["id"] if row else None


def _world_character(conn: sqlite3.Connection, name: str) -> int | None:
    """A World character by name; a bracketed note in the World name is optional, so the script's
    "Tarman" is the World's "Tarman (Salesman)"."""
    row = conn.execute("SELECT id FROM entities WHERE type = 'character' AND name = ? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    for r in conn.execute("SELECT id, name FROM entities WHERE type = 'character' AND name LIKE '%(%' ORDER BY id"):
        if re.sub(r"\s*\(.*?\)", "", r["name"]).strip().lower() == name.strip().lower():
            return r["id"]
    return None


def propose_casting(conn: sqlite3.Connection, script: dict, pool: list[str] | None = None) -> dict:
    """Fill each role from the script's suggestion, else the World character's usual voice. A role still
    without a voice gets the producer's chosen voice (pool) when exactly one is left for it."""
    roles = []
    for member in script["cast"]:
        voice = member["voice"] if _voice_id(conn, member["voice"]) else None
        if voice is None:
            row = conn.execute(
                "SELECT v.name FROM entity_links l JOIN entities v ON v.id = l.to_id "
                "WHERE l.from_id = ? AND l.relation = 'voiced_by'", (_world_character(conn, member["character"]),),
            ).fetchone()
            voice = row["name"] if row else None
        roles.append({"character": member["character"], "voice": voice})
    speaking = set(speaking_characters(script))
    left = [v for v in pool or [] if v not in {r["voice"] for r in roles}]
    empty = [r for r in roles if r["character"] in speaking and not r["voice"]]
    if len(empty) == 1 and len(left) == 1:
        empty[0]["voice"] = left[0]
    return {"roles": roles}


def casting_paths(cfg: Config, production_id: int) -> dict[str, Path]:
    paths = writing_paths(cfg, production_id, "casting")
    paths["proposal"] = paths["answer"].with_name("casting.proposal.json")
    return paths


def stage_cast(ctx: StageContext) -> None:
    script = ctx.latest("screenplay")
    if not script:
        raise Blocked("casting needs a screenplay first")
    speaking = speaking_characters(script)
    paths = casting_paths(ctx.cfg, ctx.production["id"])

    if paths["answer"].exists():
        casting = json.loads(paths["answer"].read_text())
    else:
        casting = propose_casting(ctx.conn, script, [v["name"] for v in _attached(ctx, "voices")])
        missing = [r["character"] for r in casting["roles"] if r["character"] in speaking and not r["voice"]]
        if missing:
            paths["proposal"].parent.mkdir(parents=True, exist_ok=True)
            paths["proposal"].write_text(json.dumps(casting, indent=2) + "\n")
            raise WaitingOnYou(f"pick voices for: {', '.join(missing)} (see the production page)")
        save_answer(ctx.cfg, ctx.production["id"], "casting", casting, CASTING_SCHEMA)
        ctx.log("every speaking role already had a voice; cast automatically")

    validate(CASTING_SCHEMA, casting)
    by_name = {r["character"]: r["voice"] for r in casting["roles"]}
    problems = [c for c in speaking if not _voice_id(ctx.conn, by_name.get(c))]
    if problems:
        raise WaitingOnYou(f"these speaking roles need an existing voice: {', '.join(problems)}")

    # Everyone in the cast becomes (or is matched to) a World character, so they can recur.
    resolved = []
    for member in script["cast"]:
        name = member["character"]
        cid = _world_character(ctx.conn, name) or ctx.conn.execute(
            "INSERT INTO entities (type, name, description) VALUES ('character', ?, ?)",
            (name, member["description"])).lastrowid
        vid = _voice_id(ctx.conn, by_name.get(name))
        if vid:
            ctx.conn.execute("DELETE FROM entity_links WHERE from_id = ? AND relation = 'voiced_by'", (cid,))
            ctx.conn.execute("INSERT INTO entity_links (from_id, to_id, relation) VALUES (?, ?, 'voiced_by')", (cid, vid))
        ctx.conn.execute("INSERT INTO usage (production_id, entity_id) VALUES (?, ?)", (ctx.production["id"], cid))
        resolved.append({"character": name, "character_id": cid, "voice": by_name.get(name), "voice_id": vid,
                         "speaks": name in speaking})
    version = ctx.save("casting", {"roles": resolved})
    ctx.log(f"casting v{version}: " + ", ".join(f"{r['character']} = {r['voice'] or '(silent)'}" for r in resolved))


# ---------------------------------------------------------------- voice

LINE_GAP_S = 0.35  # pause between lines in the dialogue draft


def _card_reader(ctx: StageContext, script: dict, voices: dict) -> tuple[str, int]:
    """Who reads title cards aloud: the script's voiceover, else the World's Announcer."""
    for scene in script["scenes"]:
        for beat in scene["beats"]:
            if beat["type"] == "voiceover" and voices.get(beat["character"]):
                return beat["character"], voices[beat["character"]]
    row = ctx.conn.execute(
        "SELECT c.name, l.to_id FROM entities c JOIN entity_links l ON l.from_id = c.id AND l.relation = 'voiced_by' "
        "WHERE c.type = 'character' AND c.name = 'Announcer' COLLATE NOCASE").fetchone()
    if row:
        return row["name"], row["to_id"]
    raise WaitingOnYou("a title card should be read aloud but there's no voiceover or Announcer voice; "
                       "link a voice to the World's Announcer")


def card_speech(text: str) -> str:
    """A card's lines as one spoken sentence or two ("The Shart in Mart. Everything must go.")."""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return " ".join(p if p[-1] in ".!?" else p + "." for p in parts)


def stage_voice(ctx: StageContext) -> None:
    """Speak every line with its cast voice, then lay them end to end as a rough dialogue track."""
    from . import media, tts

    script, casting = ctx.latest("screenplay"), ctx.latest("casting")
    if not script or not casting:
        raise Blocked("voicing needs a screenplay and a cast")
    voices = {r["character"]: r["voice_id"] for r in casting["roles"]}
    out_dir = production_dir(ctx.cfg, ctx.production["id"]) / "audio" / "lines"
    previous = {(l["character"], l["text"], l["voice_id"]): l for l in (ctx.latest("voice") or {}).get("lines", [])}

    read_cards = bool(ctx.fmt.get("voice", {}).get("read_cards"))
    lines, n = [], 0
    for si, scene in enumerate(script["scenes"]):
        talks = any(b["type"] in SPOKEN for b in scene["beats"])
        for bi, beat in enumerate(scene["beats"]):
            # A title card in a scene nobody speaks in is read aloud, as announcers did.
            card = read_cards and beat["type"] == "title_card" and not talks
            if beat["type"] not in SPOKEN and not card:
                continue
            n += 1
            if card:
                who, vid = _card_reader(ctx, script, voices)
                beat = {**beat, "text": card_speech(beat["text"])}
            else:
                who, vid = beat["character"], voices.get(beat["character"])
            if not vid:
                raise WaitingOnYou(f"{who} has no voice; recast")
            slug = "".join(ch if ch.isalnum() else "_" for ch in who.lower())[:20]
            path = out_dir / f"{n:02d}_{slug}.wav"
            cached = previous.get((who, beat["text"], vid))
            if cached and (ctx.cfg.path("productions") / cached["file"]).is_file() and cached["file"].endswith(path.name):
                ctx.log(f"line {n} ({who}): unchanged, reusing")
                lines.append(cached)
                continue
            ref = tts.reference_for_voice(ctx.conn, ctx.cfg, vid, ctx.log)
            took = tts.speak(ctx.cfg, beat["text"], ref, path)
            seconds = media.probe(path)["duration_s"]
            ctx.log(f"line {n} ({who}): {seconds:.1f}s of speech in {took:.1f}s")
            lines.append({"n": n, "scene": si, "beat": bi, "character": who, "voice_id": vid, "text": beat["text"],
                          "file": str(path.relative_to(ctx.cfg.path("productions"))), "duration_s": round(seconds, 2),
                          **({"card": True} if card else {})})

    draft = out_dir.parent / "dialogue_draft.wav"
    if lines:
        files = [ctx.cfg.path("productions") / l["file"] for l in lines]
        media.concat_audio(files, draft, gap_s=LINE_GAP_S)
    total = sum(l["duration_s"] for l in lines) + LINE_GAP_S * max(0, len(lines) - 1)
    body = {"lines": lines, "total_s": round(total, 2),
            "draft": str(draft.relative_to(ctx.cfg.path("productions"))) if lines else None}
    target = ctx.rolls.get("length_s", {}).get("value")
    most = length_range(ctx)[1] if length_range(ctx) else (int(target) if target and str(target).isdigit() else None)
    if most and total > most:
        body["warning"] = f"spoken lines alone run {total:.1f}s, longer than the {most:g}s allowed"
        ctx.log(body["warning"])
    version = ctx.save("voice", body)
    ctx.log(f"voice v{version}: {len(lines)} line(s), {total:.1f}s of dialogue")


# ---------------------------------------------------------------- shot plan

MOTION_NAMES = ["still", "push_in", "pull_out", "pan_left", "pan_right"]
LEAD_S = 0.25  # quiet before the first line of a shot

PLAN_SCHEMA = {
    "title": "plan",
    "type": "object",
    "additionalProperties": False,
    "required": ["title_style", "shots"],
    "properties": {"title_style": {"enum": list(titles.STYLES)}, "shots": {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["scene", "lines", "hold_s", "source", "prompt", "characters", "action", "clip_sound", "asset_id",
                     "card_text", "caption", "motion", "grade"],
        "properties": {
            "scene": {"type": "integer", "minimum": 0},
            "characters": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["name", "position", "size"],
                "properties": {"name": {"type": "string"},
                               "position": {"enum": ["left", "center", "right"]},
                               "size": {"enum": ["closeup", "waist", "full"]}}}},
            "action": {"type": ["string", "null"]},
            "clip_sound": {"type": ["string", "null"]},
            "lines": {"type": "array", "items": {"type": "integer", "minimum": 1}},
            "hold_s": {"type": "number", "minimum": 0, "maximum": 30},
            "source": {"enum": ["generate", "stock", "asset", "card", "footage"]},
            "prompt": {"type": ["string", "null"]},
            "asset_id": {"type": ["integer", "null"]},
            "card_text": {"type": ["string", "null"]},
            "caption": {"type": ["string", "null"]},
            "motion": {"enum": MOTION_NAMES},
            "grade": {"enum": ["colour", "bw"]},
        },
    }}},
}

PLAN_RULES = """Shape of the shot plan. Every shot is one picture with a slow camera move.
- title_style: the lettering for every card and caption in this piece; pick the one whose feel
  matches the piece's tone, not just its genre:
""" + "\n".join(f"    {name}: {st['hint']}" for name, st in titles.STYLES.items()) + """
- A card's text fades in and out over the picture of the shot next to it, darkened; card_text's first
  line is the title, any further lines are set smaller beneath it, and it is exactly the words on
  screen (never "logo:", "tagline:"). Only make a card where the screenplay has a title_card beat (one
  card per beat at most); most pieces need none. A card is on screen at most 4 s and carries no
  spoken lines except the card being read aloud: put everything else said on a picture shot (with
  the card's words as its caption, if they belong there).
- scene: the screenplay scene index (0-based) the shot belongs to.
- lines: the numbers of the spoken lines heard over this shot, in order. Every line is used exactly
  once, in order. A shot with no lines is fine (a product reveal, a card).
- hold_s: seconds the shot runs on after its last line (or its whole length if it has no lines).
  A shot lasts 0.25 s + its lines (each followed by a 0.35 s pause) + hold_s.
- source: generate (an image model renders the scene, then it's animated), stock (real old footage
  found by searching for prompt: the library first, then the Internet Archive), asset (reuse a World
  product, set or footage asset by asset_id; never a character's portrait) or card (a full-screen
  title card showing card_text).
- prompt: for generate, a plain photographic description of the scene for Stable Diffusion 1.5:
  setting, framing, lighting, era, and every person in it EXCEPT the ones listed in characters. It
  can't spell, so never ask for text in the picture; use caption or a card. For stock, two to four
  search words ("1950s kitchen", "city traffic night"). Motion is ignored for footage.
- characters: recurring characters with a reference picture (listed below) who appear in a generate
  shot. They're put into the scene from their real picture at position (left, center, right) and size (closeup: head and
  shoulders filling the frame; waist: waist up in the foreground; full: whole figure). Don't describe
  them in prompt. Anyone else (an extra, a one-off wife or customer) goes in prompt as a description,
  never in characters. Empty for shots without them.
- action: for generate shots, one short sentence of what visibly moves in the shot, for the video
  model: plain physical description of people and things ("the salesman grins and holds up a jar,
  the woman behind him laughs"). Never use words like zombie, monster, blood, corpse, dead or gore
  here, even for a monster character: the video service refuses them. Null for other sources.
- clip_sound: for generate shots, what the clip itself should sound like: effects ("two ray gun zaps,
  an alarm klaxon"), crowd noise, or words someone on screen says out loud, in quotes, when they
  aren't one of the recorded lines. Never repeat a recorded line here (they're laid in separately),
  and never music (scored separately). Null for a shot that needs no sound of its own. Shots with
  characters or clip_sound go to a video model that makes sound (twice the price of a silent one),
  so leave it null unless the shot needs it.
- Pictures are made wide and cropped to 4:3, so keep the people and action in the middle.
- A generated shot's clip has a hard maximum length (given below); a longer shot freezes on its last
  frame. Fill the rolled length with more shots, never by stretching hold_s.
- caption: text over the bottom of the picture (a name super, a price, fine print), or null.
- motion: still, push_in, pull_out, pan_left or pan_right. grade: colour or bw.
Unused fields are null. Aim for the rolled length."""


CARD_MIN_S = 2.0  # a title card on screen for less than this can't be read
CARD_MAX_S = 4.0  # ...and more than this is dead air (unless a line is read over it)


def time_plan(plan: dict, voice: dict, portraits: set[int] | None = None, cast: set[str] | None = None,
              max_shot_s: float | None = None, max_cards: int | None = None) -> list[dict]:
    """Give each shot its length and the start time of each of its lines. Raises ValueError on a bad plan.
    portraits: asset ids of character reference images (never a shot by themselves); cast: the names
    allowed in a shot's characters; max_shot_s: the longest clip the video models make; max_cards: the
    screenplay's title_card beats."""
    by_n = {l["n"]: l for l in voice["lines"]}
    used, out, t = [], [], 0.0
    for i, shot in enumerate(plan["shots"], 1):
        for n in shot["lines"]:
            if n not in by_n:
                raise ValueError(f"shot {i} uses line {n}, which doesn't exist")
        if shot["source"] in ("generate", "stock", "footage") and not shot["prompt"]:
            raise ValueError(f"shot {i} is {shot['source']} but has no prompt")
        if shot["source"] == "asset" and not shot["asset_id"]:
            raise ValueError(f"shot {i} reuses an asset but has no asset_id")
        if shot["source"] == "asset" and shot["asset_id"] in (portraits or set()):
            raise ValueError(f"shot {i} uses asset {shot['asset_id']}, a character's reference portrait: make it a "
                             "generate shot with that character in characters instead")
        unknown = [c["name"] for c in shot.get("characters") or [] if cast is not None and c["name"] not in cast]
        if unknown:
            raise ValueError(f"shot {i} lists {', '.join(unknown)} in characters, but only characters with a reference "
                             "picture can go there; describe them in prompt instead")
        if shot["source"] == "card" and not shot["card_text"]:
            raise ValueError(f"shot {i} is a card but has no card_text")
        spoken = [n for n in shot["lines"] if not by_n[n].get("card")]
        if shot["source"] == "card" and spoken:
            raise ValueError(f"shot {i} is a card, but line(s) {', '.join(map(str, spoken))} are spoken over it: a card "
                             "only carries its own reading; put those lines on a picture shot")
        used += shot["lines"]
        at, placed = LEAD_S, []
        for n in shot["lines"]:
            placed.append({"n": n, "file": by_n[n]["file"], "at": round(t + at, 3)})
            at += by_n[n]["duration_s"] + LINE_GAP_S
        seconds = round(at + shot["hold_s"], 3)
        if shot["source"] == "card":
            seconds = max(at, min(max(seconds, CARD_MIN_S), CARD_MAX_S))  # long enough to read, never longer
        elif shot["source"] == "generate" and max_shot_s and seconds > max_shot_s + 0.5 and shot["hold_s"] > 0.5:
            raise ValueError(f"shot {i} runs {seconds:.1f}s but a generated clip is at most {max_shot_s:.0f}s: "
                             "split it into more shots instead of a long hold_s")
        out.append({**shot, "n": i, "start": round(t, 3), "seconds": seconds, "placed": placed})
        t += seconds
    cards = sum(1 for s in plan["shots"] if s["source"] == "card")
    if max_cards is not None and cards > max_cards:
        raise ValueError(f"the plan has {cards} card(s) but the screenplay has {max_cards} title_card beat(s): "
                         "only make a card for a title_card beat")
    if used != sorted(by_n):
        raise ValueError(f"the shots must use every line once, in order: got {used}, expected {sorted(by_n)}")
    return out


def _era(ctx: StageContext) -> str:
    """The production's own look words for the video models ("1950s sci-fi B-movie, ..."), from its rolls."""
    return ", ".join(str(ctx.rolls[k]["value"]) for k in ("style", "era") if k in ctx.rolls)


def _max_shot_seconds(cfg: Config) -> float:
    """The longest clip either video model makes for one shot (mischief in continued pieces, HeyGen in one)."""
    vg = cfg.section("videogen")
    mischief = max(int(d) for d in vg.get("durations", [3, 5])) * int(vg.get("max_segments", 3))
    return float(min(mischief, int(vg.get("talkie_max_seconds", 15))))


def _portrait_ids(ctx: StageContext) -> set[int]:
    return {r[0] for r in ctx.conn.execute(
        "SELECT ae.asset_id FROM asset_entities ae JOIN entities e ON e.id = ae.entity_id WHERE e.type = 'character'")}


def _describe_images(ctx: StageContext) -> str:
    """Reusable World images and footage; character portraits aren't shots, so they're left out."""
    portraits = _portrait_ids(ctx)
    rows = ctx.conn.execute(
        "SELECT a.id, a.title, a.media, group_concat(e.type || ' ' || e.name, ', ') AS ents FROM assets a "
        "LEFT JOIN asset_entities ae ON ae.asset_id = a.id LEFT JOIN entities e ON e.id = ae.entity_id "
        "WHERE a.media IN ('image', 'video') AND a.status = 'ready' GROUP BY a.id ORDER BY a.id").fetchall()
    return "\n".join(f"- asset {r['id']} ({r['media']}): {r['ents'] or r['title']}" for r in rows
                     if r["id"] not in portraits) or "(none yet)"


def _attached_media(ctx: StageContext) -> str:
    rows = [a for a in _attached(ctx, "assets") if a["media"] in ("image", "video")]
    if not rows:
        return ""
    return ("Images and footage the producer attached to this piece (use each at least once, as an asset shot):\n"
            + "\n".join(f"- asset {a['id']} ({a['media']}): {a['title'] or a['original_name']}" for a in rows) + "\n\n")


def _pasteable_cast(ctx: StageContext) -> dict[str, int]:
    """Script names of this production's characters that have a reference picture -> entity id: the
    cast, and World characters the pitch itself names who never speak (a monster, a gang of pirates)."""
    from . import composite

    out = {}
    for role in (ctx.latest("casting") or {}).get("roles", []):
        if role.get("character_id") and composite.reference_image(ctx.conn, role["character_id"]):
            out[role["character"]] = role["character_id"]
    for c in _attached(ctx, "characters"):
        if c["id"] not in out.values() and composite.reference_image(ctx.conn, c["id"]):
            out[c["name"]] = c["id"]
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())  # noqa: E731  "Kaiju 1" finds the World's "Kaiju_1"
    text = squash(_idea(ctx)) if ctx.production["idea_id"] else ""
    for r in _pictured_characters(ctx.conn):
        name = re.sub(r"\s*\(.*?\)", "", r["name"]).strip()
        if r["id"] not in out.values() and squash(name) in text:
            out[name] = r["id"]
    return out


def _plan_schema_without_footage() -> dict:
    import copy

    schema = copy.deepcopy(PLAN_SCHEMA)
    source = schema["properties"]["shots"]["items"]["properties"]["source"]
    source["enum"] = [s for s in source["enum"] if s != "footage"]
    return schema


FOOTAGE_RULE = """Real footage (source footage): the station has an indexed library of real TV and film footage,
searched by what's said in it and by show name. For a footage shot, prompt is either a line someone says in
it, in double quotes ("more power", "it's a custom of my people"), or a few words naming a show or what's
shown ("sitcom living room", "Home Improvement"). The clip keeps its own sound. Prefer it to generate for
anything a real show, ad or film would do better.

"""


def _clean_plan_text(plan: dict) -> None:
    """Direction labels off the plan's on-screen text ("logo with tagline: ..."), in place."""
    for shot in plan["shots"]:
        for field in ("card_text", "caption"):
            if shot.get(field):
                shot[field] = titles.clean_text(shot[field]) or None


def stage_plan(ctx: StageContext) -> None:
    from . import footage

    script, voice = ctx.latest("screenplay"), ctx.latest("voice")
    if not script or not voice:
        raise Blocked("the shot plan needs a screenplay and a voice track")
    lines = "\n".join(f"{l['n']}. {l['character']} ({l['duration_s']:.1f}s, scene {l['scene']}"
                      f"{', reading the title card: put it over that card shot' if l.get('card') else ''}): {l['text']}"
                      for l in voice["lines"])
    cast = _pasteable_cast(ctx)
    max_shot = _max_shot_seconds(ctx.cfg)
    max_cards = sum(1 for sc in script["scenes"] for b in sc["beats"] if b["type"] == "title_card")
    recent = [loads(r["body"]).get("title_style") for r in ctx.conn.execute(
        "SELECT body FROM documents WHERE kind = 'plan' AND production_id != ? ORDER BY id DESC LIMIT 4",
        (ctx.production["id"],))]
    recent = [r for r in recent if r]
    prompt = (
        f"{_format_line(ctx)}{_rolls_block(ctx)}"
        f"Screenplay:\n{json.dumps(script, indent=2, ensure_ascii=False)}\n\n"
        f"Spoken lines, already recorded ({voice['total_s']:.1f}s in all):\n{lines}\n\n"
        + _length_note(ctx)
        + _attached_media(ctx)
        + f"Images and footage already in the World:\n{_describe_images(ctx)}\n\n"
        f"Characters with a reference picture (the only names allowed in characters):\n"
        + ("\n".join(f"- {name}" for name in cast) or "(none)") + f"\n\n{PLAN_RULES}\n"
        f"A generated shot is at most {max_shot:.0f} s long. The screenplay has {max_cards} title_card beat(s).\n\n"
        + (FOOTAGE_RULE if footage.enabled(ctx.cfg) else "")
        + (f"The station's last pieces used these title styles: {', '.join(recent)}. Prefer a different one "
           "unless it clearly fits worse.\n\n" if recent else "") + "Write the shot plan."
    )
    # The planner is only offered footage shots while the footage index is on (saved plans validate either way).
    schema = PLAN_SCHEMA if footage.enabled(ctx.cfg) else _plan_schema_without_footage()
    plan = ctx.write("plan", _system(ctx, "plan"), prompt, schema)
    _clean_plan_text(plan)
    portraits = _portrait_ids(ctx)
    try:
        shots = time_plan(plan, voice, portraits, set(cast), max_shot, max_cards)
    except ValueError as exc:
        if ctx.llm is None:
            raise WaitingOnYou(f"the shot plan needs fixing: {exc}. Edit writing/plan.json or press Rewrite.") from exc
        ctx.log(f"the plan doesn't work yet, asking for a fix: {exc}")
        plan = ctx._ask(_system(ctx, "plan"), f"{prompt}\n\nYour previous answer:\n{json.dumps(plan)}\n\n"
                        f"It can't be used: {exc}\nReply with the whole corrected plan.", schema, "strong")
        save_answer(ctx.cfg, ctx.production["id"], "plan", plan, PLAN_SCHEMA)
        _clean_plan_text(plan)
        try:
            shots = time_plan(plan, voice, portraits, set(cast), max_shot, max_cards)
        except ValueError as exc2:
            raise WaitingOnYou(f"the shot plan needs fixing: {exc2}. Edit writing/plan.json or press Rewrite.") from exc2
    total = round(sum(s["seconds"] for s in shots), 2)
    body = {"title_style": plan["title_style"], "shots": shots, "total_s": total}
    target = ctx.rolls.get("length_s", {}).get("value")
    rng = length_range(ctx)
    if rng and not rng[0] - 1 <= total <= rng[1] + 1:
        body["warning"] = f"the shots add up to {total:.1f}s; asked for {rng[0]:g}-{rng[1]:g}s"
    elif not rng and target and str(target).isdigit() and abs(total - int(target)) > 2:
        body["warning"] = f"the shots add up to {total:.1f}s for a {target}s slot"
        ctx.log(body["warning"])
    version = ctx.save("plan", body)
    ctx.log(f"plan v{version}: {len(shots)} shot(s), {total:.1f}s")


# ---------------------------------------------------------------- render

def _key(*parts) -> str:
    import hashlib

    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _fit_fades(fades, seconds: float) -> tuple:
    """Fade in and out times shrunk to at most half the shot together, so the text holds at full strength."""
    fade_in, fade_out = fades
    scale = min(1.0, seconds * 0.5 / max(0.01, fade_in + fade_out))
    return round(fade_in * scale, 2), round(fade_out * scale, 2)


def _stock_footage(ctx: StageContext, shot: dict, required: bool = True) -> int | None:
    """Footage for a stock shot: the library first, then the Internet Archive. None when nothing
    usable turns up and the shot isn't required to be footage (it can be generated instead)."""
    from . import online

    query = shot["prompt"]
    found = online.library_match(ctx.conn, "footage", query, "video")
    if found:
        ctx.log(f"shot {shot['n']}: footage for '{query}' from the library (asset {found})")
        return found
    source = ctx.cfg.section("online").get("stock_video", "archive")
    try:
        results = online.search(ctx.cfg, source, "video", query, 10)
    except online.OnlineError as exc:
        if not required:
            ctx.log(f"shot {shot['n']}: couldn't search for '{query}': {exc}")
            return None
        raise Blocked(f"shot {shot['n']}: couldn't search for '{query}': {exc}") from exc
    for r in results:
        try:
            asset_id = online.fetch_now(ctx.conn, ctx.cfg, r, "footage", ["stock"] + query.lower().split(), ctx.log)
        except (online.OnlineError, ValueError) as exc:
            ctx.log(f"shot {shot['n']}: skipped '{r['title']}': {exc}")
            continue
        ctx.log(f"shot {shot['n']}: downloaded '{r['title']}' for '{query}' (asset {asset_id})")
        return asset_id
    if not required:
        return None
    raise WaitingOnYou(f"shot {shot['n']}: no usable footage found for '{query}'. Change the prompt or use another source.")


def _footage_shot(ctx: StageContext, shot: dict, base: Path, fps: int, w: int, h: int, cap: str | None) -> dict | None:
    """A footage shot (pipeline v2): the best hit in the footage index for its prompt, cut to start just
    before the words (or the scene), with its own sound kept next to the clip (<clip>.wav)."""
    from . import footage, media

    if not footage.enabled(ctx.cfg):
        return None
    hits = footage.search(ctx.conn, shot["prompt"], 1)
    if not hits:
        return None
    hit = hits[0]
    lead = float(ctx.cfg.section("footage").get("lead_s", 0.3))
    start = round(max(0.0, hit["start"] - lead), 2)
    clip = base / "clips" / f"{shot['n']:02d}_{_key(hit['path'], start, shot['seconds'], cap, shot['grade'], fps, w, h)}.mp4"
    if not clip.is_file():
        media.video_clip(Path(hit["path"]), clip, shot["seconds"], start, fps, w, h, shot["grade"],
                         shot.get("caption_layer"), shot.get("caption_fade", (0, 0)))
        media._run([media.FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{shot['seconds']:.3f}", "-i", hit["path"],
                    "-vn", "-ac", "1", "-ar", "48000", str(clip.with_suffix(".wav"))])
        ctx.log(f"shot {shot['n']}: {shot['seconds']:.1f}s of {hit['title']} from {start:.1f}s ({hit['kind']}"
                + (f": {hit['text']!r}" if hit["text"] else "") + ")")
    return {"clip": clip, "hit": {k: hit[k] for k in ("path", "title", "kind", "start", "end", "text")}}


# ---------------------------------------------------------------- sound cues

SOUND_CUES = {"sfx": "sfx", "music": "music", "laugh": "sfx"}


def stage_sound(ctx: StageContext) -> None:
    """A sound for every music, sfx and laugh cue in the script: the library first, then online."""
    from . import online

    script = ctx.latest("screenplay")
    if not script:
        raise Blocked("sound needs a screenplay")
    settings = ctx.cfg.section("online")
    music, plan = ctx.fmt.get("music", {}), ctx.latest("plan") or {"shots": []}
    music_scenes = sorted({si for si, sc in enumerate(script["scenes"]) for b in sc["beats"] if b["type"] == "music"})
    previous = {(c["scene"], c["beat"], c["text"]): c for c in (ctx.latest("sound") or {}).get("cues", [])}
    beats = [(si, bi, beat) for si, scene in enumerate(script["scenes"]) for bi, beat in enumerate(scene["beats"])]
    if music.get("always") and not music_scenes and beats:
        # The script forgot the music: score the whole thing from the top (beat -1 = before the first beat).
        title = (ctx.latest("outline") or {}).get("title") or ctx.production["title"]
        beats.insert(0, (0, -1, {"type": "music", "cue": music.get("always_cue", music.get("cue", "bed")),
                                 "text": f"background music for {title}, under the whole piece"}))
        music_scenes = [0]
    own_music = [a["id"] for a in _attached(ctx, "assets") if a["category"] == "music"]  # the producer's, in order
    # One composed piece per scene: a later music beat in the same scene ("fades out with a sting") is
    # written into the first one's piece instead of becoming a second full-length song.
    scene_music: dict[int, list[str]] = {}
    for si, bi, beat in beats:
        if beat["type"] == "music":
            scene_music.setdefault(si, []).append(beat["text"])
    cues = []
    for si, bi, beat in beats:
        if beat["type"] not in SOUND_CUES:
            continue
        category = SOUND_CUES[beat["type"]]
        text = "audience laughing" if beat["type"] == "laugh" else beat["text"]
        query = online.cue_query(text) or beat["type"]
        cue = {"scene": si, "beat": bi, "type": beat["type"], "text": beat["text"], "query": query, "asset_id": None}
        old = previous.get((si, bi, beat["text"]))
        ready = old and old["asset_id"] and ctx.conn.execute(
            "SELECT duration_s FROM assets WHERE id = ? AND status = 'ready'", (old["asset_id"],)).fetchone()
        composed = category == "music" and music.get("source") == "midigen"
        slot = _cue_seconds(plan, si, music_scenes) if composed else 0.0
        if ready and composed and old.get("found") == "midigen" and (ready["duration_s"] or 0) < slot - 0.5:
            # The shots were retimed since this piece was written and it no longer reaches its end.
            ctx.log(f"music for '{beat['text']}' is {ready['duration_s']:.1f}s but now needs {slot:.1f}s: rewriting it")
            writing_paths(ctx.cfg, ctx.production["id"], f"music_{si}_{bi}")["answer"].unlink(missing_ok=True)
            ready = None
        if ready:
            cue = old
        elif category == "music" and own_music:
            cue.update(asset_id=own_music.pop(0), found="attached")
        elif composed and scene_music.get(si, [beat["text"]])[0] != beat["text"]:
            cue["found"] = "part of the scene's first music cue"
        elif composed:
            # A little longer than the slot: the mix fades the music out at the cut, never runs short.
            seconds = slot + float(music.get("tail_s", 2.0))
            card = beat.get("cue", music.get("cue", "bed"))
            later = scene_music.get(si, [])[1:]
            text = beat["text"] + "".join(f" Later in the same piece: {x}" for x in later)
            for attempt in (1, 2):
                try:
                    if found := _compose(ctx, si, bi, text, seconds, card):
                        cue.update(asset_id=found, found="midigen")
                    break
                except (llm_mod.LLMError, ValueError, RuntimeError) as exc:  # a bad answer, a cut-off reply, MIDIgen itself
                    ctx.log(f"music for '{beat['text']}' failed (try {attempt} of 2): {exc}")
                    writing_paths(ctx.cfg, ctx.production["id"], f"music_{si}_{bi}")["answer"].unlink(missing_ok=True)
        elif found := online.library_match(ctx.conn, category, query, "audio"):
            cue.update(asset_id=found, found="library")
        else:
            limit = float(settings.get("music_max_seconds" if category == "music" else "sfx_max_seconds", 12))
            result = online.find_sound(ctx.cfg, "audio", text, limit)
            if result:
                try:
                    cue.update(asset_id=online.fetch_now(ctx.conn, ctx.cfg, result, category,
                                                         ["cue"] + result["query"].split(), ctx.log),
                               found=f"openverse: {result['title']}", query=result["query"])
                except (online.OnlineError, ValueError) as exc:
                    ctx.log(f"cue '{beat['text']}': download failed: {exc}")
        if cue["asset_id"]:
            online.mark_used(ctx.conn, cue["asset_id"], ctx.production["id"])
        ctx.log(f"cue '{beat['text']}' -> " + (f"asset {cue['asset_id']} ({cue.get('found')})" if cue["asset_id"]
                                                 else cue.get("found", "nothing found")))
        cues.append(cue)
    version = ctx.save("sound", {"cues": cues})
    ctx.log(f"sound v{version}: {sum(1 for c in cues if c['asset_id'])} of {len(cues)} cue(s) have a sound")


# ---------------------------------------------------------------- music (MIDIgen)

def _cue_seconds(plan: dict, scene: int, music_scenes: list[int]) -> float:
    """How long a music cue runs: from its scene's first shot to the next music cue's scene, or the end."""
    starts = {}
    for shot in plan["shots"]:
        starts.setdefault(shot["scene"], shot["start"])
    total = sum(s["seconds"] for s in plan["shots"])
    later = [starts[s] for s in music_scenes if s > scene and s in starts]
    return max(3.0, (later[0] if later else total) - starts.get(scene, 0.0))


def _midigen(ctx: StageContext, *args: str) -> dict:
    import subprocess

    tool = ctx.cfg.tool("midigen")
    if not tool or not Path(tool).exists():
        raise Blocked("MIDIgen isn't installed ([tools] midigen in config/aitv.toml)")
    proc = subprocess.run([tool, *args, "--json"], capture_output=True, text=True, timeout=900)
    if proc.returncode not in (0, 2):  # 2 = an invalid composition, reported in the JSON
        raise RuntimeError(f"midigen {args[0]} failed: {proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout)


def _music_style(ctx: StageContext) -> str | None:
    """The music_style roll; productions made before the format had one roll it now."""
    rolls = ctx.rolls
    if "music_style" not in rolls and "music_style" in ctx.fmt.get("rolls", {}):
        rolls.update(rolls_mod.take({"music_style": ctx.fmt["rolls"]["music_style"]}, {}, ctx.production["seed"]))
        ctx.set_rolls(rolls)
    return rolls.get("music_style", {}).get("value")


def _compose(ctx: StageContext, scene: int, beat: int, text: str, seconds: float, card: str) -> int | None:
    """A fresh MIDIgen piece for one music cue: the LLM writes it, MIDIgen checks and renders it, and
    the WAV goes into the library. Returns the asset id, or None if it couldn't be made valid."""
    import shutil
    import uuid

    from . import ingest

    music, style = ctx.fmt.get("music", {}), _music_style(ctx)
    outline = ctx.latest("outline") or {}
    rolls = ctx.rolls
    era, tone = rolls.get("era", {}).get("value"), rolls.get("tone", {}).get("value")
    context = (", ".join(x for x in [_format_name(ctx), era, tone and f"tone: {tone}"] if x)
               + f". \"{outline.get('title', '')}\": "
               f"{outline.get('logline', '')}\nThis cue, from the script: {text}")
    args = ["prompt", f"Music for this cue: {text}", "--seconds", str(round(seconds)),
            "--cue", card, "--context", context]
    if style:
        args += ["--style", style]
    request = _midigen(ctx, *args)
    stage = f"music_{scene}_{beat}"
    path = writing_paths(ctx.cfg, ctx.production["id"], stage)["answer"]
    comp = ctx.write(stage, request["system"], request["prompt"], request["schema"], reasoning=music.get("reasoning"))
    ctx.log(f"music for '{text}': {comp.get('title')} ({style or 'no style'}, {card}, ~{seconds:.0f}s)")
    for attempt in range(int(music.get("repairs", 2)) + 1):
        check = _midigen(ctx, "validate", str(path))
        if check["ok"]:
            break
        if attempt == int(music.get("repairs", 2)):
            ctx.log(f"music for '{text}' still invalid after {attempt} fix(es), skipping: {check['errors'][:3]}")
            return None
        if ctx.llm is None:
            ctx.llm = llm_mod.get(ctx.cfg)
        ctx.log(f"music isn't valid yet ({len(check['errors'])} error(s)), asking for a fix")
        repair = "\n".join([request["prompt"], "", "# Your previous answer", "", "```json", json.dumps(comp), "```", "",
                             "# It can't be rendered yet. Fix these problems and answer with the whole corrected JSON:",
                             ""] + [f"- {e}" for e in check["errors"]])
        comp = ctx._ask(request["system"], repair, request["schema"], "strong", music.get("reasoning"))
        save_answer(ctx.cfg, ctx.production["id"], stage, comp, request["schema"])
    result = _midigen(ctx, "render", str(path), "--out", str(production_dir(ctx.cfg, ctx.production["id"]) / "music" / stage))
    if not result.get("ok", True) or "files" not in result:  # e.g. it rendered to silence
        raise RuntimeError(f"MIDIgen couldn't render it: {'; '.join(result.get('errors', []))[:500]}")
    wav = Path(result["files"]["wav"])
    dest = ctx.cfg.uploads / f"{uuid.uuid4().hex[:8]}_{ctx.production['id']:04d}_{stage}.wav"
    shutil.copy2(wav, dest)
    asset_id = ingest.register(ctx.conn, dest, "music", title=comp.get("title") or stage, original_name=wav.name,
                               source="generated", tags=["midigen", card] + (style or "").split(),
                               extra_meta={"production": ctx.production["id"], "composition": str(path),
                                           "style": style, "seconds": result.get("wav_seconds")})
    ctx.conn.execute("DELETE FROM jobs WHERE kind = 'ingest' AND asset_id = ? AND status = 'queued'", (asset_id,))
    out = ingest.process(ctx.conn, ctx.cfg, asset_id, ctx.log)
    ctx.log(f"music rendered: {result.get('wav_seconds')}s -> asset {out.get('duplicate_of', asset_id)}")
    return out.get("duplicate_of", asset_id)


def stage_render(ctx: StageContext) -> None:
    """A picture for every shot (generated, reused or a title card), then a moving clip of it.

    With [videogen] on, a generated shot goes one of two ways:
    - recurring characters or a sound of its own (clip_sound): the image-to-image model puts the
      characters on the set from their reference pictures (4:3), and the talkie model (HeyGen) animates
      it with its own soundtrack, which the mix lays under the shot;
    - otherwise Stable Diffusion renders the scene wide (16:9), the silent video model (mischief)
      animates it and the clip is cropped to the 4:3 middle.
    If the image-to-image call fails, the characters are pasted in from their cut-out pictures and
    blended with a light img2img pass instead. Without videogen (or for cards and stills that fail to
    animate) the picture gets a slow camera move. A card's lettering fades in over the nearest other
    shot's picture, darkened (a gradient if there's none), never over a flat colour."""
    import shutil
    import tempfile

    from . import comfy, composite, media, online, titles, videogen

    plan = ctx.latest("plan")
    if not plan:
        raise Blocked("rendering needs a shot plan")
    style_name = plan.get("title_style") or titles.DEFAULT_STYLE
    style = titles.style(style_name)
    images, settings, vg = ctx.cfg.section("images"), ctx.cfg.section("render"), videogen.settings(ctx.cfg)
    preset, fps = settings.get("still_preset", "final"), int(settings.get("fps", 30))
    w, h = int(images.get("width", 640)), int(images.get("height", 480))
    animate = videogen.enabled(ctx.cfg)
    vw, vh = int(vg.get("frame_width", 856)), int(vg.get("frame_height", 480))
    base = production_dir(ctx.cfg, ctx.production["id"]) / "render"
    for sub in ("stills", "clips", "animated"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    rel = lambda p: str(p.relative_to(ctx.cfg.path("productions")))  # noqa: E731
    cast = _pasteable_cast(ctx)
    looks = {m["character"]: m["description"] for m in (ctx.latest("screenplay") or {}).get("cast", [])}
    negative = images.get("negative", "")
    era = _era(ctx)
    spent = 0.0

    started_comfy = False

    def sd(graph: dict, dst: Path) -> None:
        nonlocal started_comfy
        comfy.ensure_running(ctx.cfg, ctx.log)
        started_comfy = True
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            [made] = comfy.render(ctx.cfg, graph, Path(tmp))
            shutil.move(made, dst)

    def placed(shot: dict, figures: list[dict]) -> Path | None:
        """The shot's first frame from the image-to-image model, or None if it failed."""
        nonlocal spent
        refs = [ctx.cfg.library / composite.reference_image(ctx.conn, cast[c["name"]])["path"]
                for c in figures][:int(vg.get("image_max_references", 3))]
        sizes = {"closeup": "head and shoulders close-up", "waist": "waist up", "full": "full figure"}
        who = "; ".join(f"{c['name']} is the person in reference image {i} ({looks.get(c['name'], 'exactly as pictured')}), "
                        f"keeping their face, costume and build exactly as in the picture, {c['position']}, {sizes[c['size']]}"
                        for i, c in enumerate(figures[:len(refs)], 1))
        prompt = f"{shot['prompt']}. {who}. A film still" + (f" in the style of {era}" if era else "") + ". No text."
        frame = base / "stills" / f"{shot['n']:02d}_placed_{_key(prompt, [r.name for r in refs], vg.get('image_model'))}.png"
        if not frame.is_file():
            check_stop(ctx)
            ctx.check_budget("place characters on a set")
            try:
                cost = videogen.place(ctx.cfg, refs, prompt, frame)
            except (videogen.VideoGenError, KeyError, IndexError, media.MediaError) as exc:
                ctx.log(f"shot {shot['n']}: image-to-image failed, pasting the characters in instead: {exc}")
                return None
            spent += cost or 0.0
            ctx.paid("image", cost, vg.get("image_model"), note=f"shot {shot['n']}")
            ctx.log(f"shot {shot['n']}: put {', '.join(c['name'] for c in figures)} on the set"
                    + (f", ${cost:.3f}" if cost is not None else ""))
        return frame

    def paste_blend(shot: dict, still: Path, figures: list[dict], seed: int) -> Path:
        n = shot["n"]
        cutouts = []
        for c in figures:
            ref = composite.reference_image(ctx.conn, cast[c["name"]])
            cutouts.append((composite.cutout(ctx.cfg, ref), c["position"], c["size"]))
        denoise = float(vg.get("blend_denoise", 0.25))
        frame = base / "stills" / f"{n:02d}_with_{_key(still.name, [(str(p.name), po, si) for p, po, si in cutouts], denoise)}.png"
        if not frame.is_file():
            pasted = composite.paste(still, cutouts, frame.with_name(frame.stem + "_pasted.png"), w)
            prompt = ", ".join([shot["prompt"]] + [looks.get(c["name"], c["name"]) for c in figures])
            name = comfy.put_input(ctx.cfg, pasted, f"aitv_{ctx.production['id']:04d}_{n:02d}.png")
            sd(comfy.img2img_workflow(images, preset, name, prompt, negative, seed, denoise, "aitv_blend"), frame)
            ctx.log(f"shot {n}: pasted in {', '.join(c['name'] for c in figures)} and blended")
        return frame

    def caption_layer(shot: dict) -> Path | None:
        if not shot["caption"]:
            return None
        layer = base / "stills" / f"{shot['n']:02d}_caption_{_key(shot['caption'], style_name, titles.VERSION, w, h)}.png"
        if not layer.is_file():
            titles.caption(shot["caption"], style_name, layer, w, h)
        return layer

    out, pending, talkies = [], [], []
    pictures: dict[int, Path] = {}  # shot -> its picture (or clip) for the cards next to it
    cards: list[tuple[int, dict]] = []
    try:
        for shot in plan["shots"]:
            n = shot["n"]
            shot = {**shot, "caption_layer": caption_layer(shot), "caption_fade": _fit_fades(style["caption_fade"], shot["seconds"]),
                    "fade": _fit_fades(style["fade"], shot["seconds"]) if shot["source"] == "card" else (0, 0)}
            cap = shot["caption_layer"].name if shot["caption_layer"] else None
            asset_id = shot["asset_id"]
            wide = animate and shot["source"] in ("generate", "asset")
            if shot["source"] == "footage":
                made = _footage_shot(ctx, shot, base, fps, w, h, cap)
                if made:
                    pictures[n] = made["clip"]
                    out.append({"n": n, "still": None, "asset_id": None, "clip": rel(made["clip"]), "seconds": shot["seconds"],
                                "own_sound": True, "footage": made["hit"]})
                    continue
                ctx.log(f"shot {n}: nothing in the footage index for '{shot['prompt']}', searching stock instead")
                shot = {**shot, "source": "stock"}
            if shot["source"] == "stock":
                asset_id = _stock_footage(ctx, shot, required=not animate)
                if not asset_id:  # no footage anywhere: generate the scene and animate it instead
                    ctx.log(f"shot {n}: no footage for '{shot['prompt']}', generating it instead")
                    shot, wide = {**shot, "source": "generate"}, True
            if asset_id:
                asset = ctx.conn.execute("SELECT * FROM assets WHERE id = ? AND media IN ('image', 'video') "
                                         "AND status = 'ready'", (asset_id,)).fetchone()
                if asset is None:
                    raise WaitingOnYou(f"shot {n} uses asset {asset_id}, which isn't a ready image or video")
                online.mark_used(ctx.conn, asset_id, ctx.production["id"])
                still = ctx.cfg.library / asset["path"]
                if asset["media"] == "video":
                    length = asset["duration_s"] or 0
                    rng = random.Random(f"{ctx.production['seed']}:{n}")
                    start = round(rng.uniform(length * 0.1, max(length * 0.1, length * 0.9 - shot["seconds"])), 2)
                    clip = base / "clips" / f"{n:02d}_{_key(asset['sha256'], start, shot['seconds'], cap, shot['grade'], fps, w, h)}.mp4"
                    if not clip.is_file():
                        media.video_clip(still, clip, shot["seconds"], start, fps, w, h, shot["grade"], shot["caption_layer"],
                                         shot["caption_fade"])
                        ctx.log(f"shot {n}: {shot['seconds']:.1f}s of footage from {start:.0f}s in ({asset['title']})")
                    pictures[n] = clip
                    out.append({"n": n, "still": None, "asset_id": asset_id, "clip": rel(clip), "seconds": shot["seconds"]})
                    continue
                if wide:  # a World image fills the wide frame (cropped, never padded)
                    frame = base / "stills" / f"{n:02d}_asset_{_key(asset['sha256'], vw, vh)}.png"
                    if not frame.is_file():
                        media.cover_image(still, frame, vw, vh)
                    still = frame
                pictures[n] = still
            elif shot["source"] == "card":
                cards.append((len(out), shot))  # made once every other shot has its picture
                out.append({})
                continue
            else:
                seed = (int(ctx.production["seed"]) * 100 + n) % 2**31
                figures = [c for c in shot.get("characters") or [] if c["name"] in cast] if wide else []
                talkie = wide and bool(figures or shot.get("clip_sound"))
                sw, sh = (vw, vh) if wide and not talkie else (w, h)
                frame = placed(shot, figures) if talkie and figures else None
                if frame:
                    still, figures = frame, []
                else:
                    still = base / "stills" / f"{n:02d}_{_key(shot['prompt'], negative, preset, seed, images.get('checkpoint'), sw, sh)}.png"
                    if not still.is_file():
                        sd(comfy.workflow(images, preset, shot["prompt"], negative, sw, sh, seed, 1, "aitv_shot"), still)
                        ctx.log(f"shot {n}: rendered the scene (seed {seed})")
                pictures[n] = still
                if talkie:
                    if figures:  # the image-to-image model failed: paste them in
                        still = paste_blend(shot, still, figures, seed)
                    clip = base / "clips" / f"{n:02d}_{_key(still.name, shot['action'], shot.get('clip_sound'), shot['seconds'], cap, shot['caption_fade'], shot['grade'], vg.get('talkie_model'), era, fps, w, h)}.mp4"
                    if not clip.is_file():
                        talkies.append((shot, still, clip))
                    out.append({"n": n, "still": rel(still), "asset_id": None, "clip": rel(clip),
                                "seconds": shot["seconds"], "animated": True, "talkie": True})
                    continue
                if wide and figures:
                    still = paste_blend(shot, still, figures, seed)
            if wide:
                clip = base / "clips" / f"{n:02d}_{_key(still.name, shot['action'], shot['seconds'], cap, shot['caption_fade'], shot['grade'], vg.get('model'), vg.get('prompt_suffix'), era, fps, w, h)}.mp4"
                if not clip.is_file():
                    pending.append((shot, still, clip))
                out.append({"n": n, "still": rel(still) if still.is_relative_to(ctx.cfg.path("productions")) else None,
                            "asset_id": asset_id, "clip": rel(clip), "seconds": shot["seconds"], "animated": True})
                continue
            clip = base / "clips" / f"{n:02d}_{_key(still.name, shot['motion'], shot['seconds'], cap, shot['caption_fade'], shot['fade'], shot['grade'], fps, w, h)}.mp4"
            if not clip.is_file():
                media.motion_clip(still, clip, shot["seconds"], shot["motion"], fps, w, h, shot["grade"],
                                  shot["caption_layer"], shot["caption_fade"], shot["fade"])
                ctx.log(f"shot {n}: {shot['seconds']:.1f}s {shot['motion']} clip")
            out.append({"n": n, "still": None if asset_id else rel(still), "asset_id": asset_id,
                        "clip": rel(clip), "seconds": shot["seconds"]})
        for i, shot in cards:
            out[i] = _card_shot(shot, pictures, style_name, base, fps, w, h, rel, ctx.log)
    finally:
        if started_comfy:
            comfy.shutdown()  # give the RAM back to the website; the next image job starts it again
    cost = spent + _animate(ctx, pending, base / "animated", era, fps, w, h) + _talkie(ctx, talkies, base / "animated", era, fps, w, h)
    keep = {Path(s["clip"]).stem for s in out}
    for old in (base / "clips").iterdir():
        if old.suffix in (".mp4", ".wav") and old.stem not in keep:
            old.unlink()
    version = ctx.save("render", {"shots": out, "video_cost_usd": round(cost, 4)})
    ctx.log(f"render v{version}: {len(out)} clip(s)" + (f", video generation ${cost:.3f}" if pending or talkies or spent else ""))


def _card_shot(shot: dict, pictures: dict[int, Path], style_name: str, base: Path, fps: int, w: int, h: int,
               rel, log) -> dict:
    """A title card's clip: its lettering faded in and out over the nearest shot's picture (the one before,
    else the one after), darkened; a gradient when the piece has no other picture."""
    from . import media, titles

    n = shot["n"]
    k = next((k for k in sorted(pictures, key=lambda k: (abs(k - n), k > n)) if pictures[k].is_file()), None)
    near = pictures[k] if k is not None else None
    picture = None
    if near is not None:
        picture = near
        if near.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
            picture = base / "stills" / f"{n:02d}_cardbg_{_key(near.name)}.png"
            if not picture.is_file():
                media.frame_at(near, 0.5, picture)
    text = titles.clean_text(shot["card_text"]) or shot["card_text"]
    key = _key(text, style_name, titles.VERSION, picture.name if picture else "gradient", w, h)
    bg = base / "stills" / f"{n:02d}_cardbg_{key}.png"
    layer = base / "stills" / f"{n:02d}_card_{key}.png"
    if not bg.is_file():
        titles.card_background(bg, w, h, picture, style_name)
    if not layer.is_file():
        titles.card_layer(text, style_name, w, h).save(layer)
    fade = _fit_fades(titles.style(style_name)["fade"], shot["seconds"])
    clip = base / "clips" / f"{n:02d}_{_key(bg.name, layer.name, shot['motion'], shot['seconds'], fade, shot['grade'], fps, w, h)}.mp4"
    if not clip.is_file():
        media.motion_clip(bg, clip, shot["seconds"], shot["motion"], fps, w, h, shot["grade"], layer, fade)
        log(f"shot {n}: {shot['seconds']:.1f}s card over " + (f"shot {k}'s picture" if near else "a gradient"))
    return {"n": n, "still": rel(bg), "asset_id": None, "clip": rel(clip), "seconds": shot["seconds"]}


def _safe_prompt(vg: dict, era: str) -> str:
    """The plain prompt used once when a video model refuses a shot's own."""
    return vg.get("safe_prompt", "a quiet scene, subtle natural movement, static camera") + (f", {era}" if era else "")


def joined_length(segments: list[Path], cuts: list[float | None]) -> float:
    from . import media

    return sum(c if c is not None else media.probe(s)["duration_s"] for s, c in zip(segments, cuts))


def _warn_if_held(ctx: StageContext, shot: dict, footage_s: float) -> None:
    """Log a shot whose footage runs out before the shot ends (it holds the last frame)."""
    held = shot["seconds"] - footage_s
    if held > 0.5:
        ctx.log(f"shot {shot['n']}: footage is {footage_s:.1f}s for a {shot['seconds']:.1f}s shot, "
                f"the last frame is held {held:.1f}s")


def _talkie(ctx: StageContext, pending: list, folder: Path, era: str, fps: int, w: int, h: int) -> float:
    """Make every pending shot's clip with the talkie model (HeyGen) in one piece from its 4:3 first
    frame, all submitted together. The clip's own soundtrack is kept next to it (<clip>.wav) for the
    mix. A shot with no clip gets a camera move on its frame instead."""
    import math

    from . import media, videogen

    vg = videogen.settings(ctx.cfg)
    jobs, cost = [], 0.0
    if pending:
        check_stop(ctx)  # before any submit only: a submitted job is always waited for
        ctx.check_budget("buy more video")
    for shot, frame, clip in pending:
        seconds = min(int(vg.get("talkie_max_seconds", 15)), max(int(vg.get("talkie_min_seconds", 5)), math.ceil(shot["seconds"])))
        sound = shot.get("clip_sound")
        prompt = (f"{shot['action'] or shot['prompt']}." + (f" Sound: {sound}." if sound else "")
                  + ("" if sound and '"' in sound else " Nobody speaks.") + (f" A {era} film." if era else ""))
        segment = folder / f"{clip.stem}_talkie.mp4"
        try:
            if not segment.is_file():
                jobs.append((shot, frame, clip, segment, videogen.submit_talkie(ctx.cfg, frame, prompt, seconds)))
                ctx.log(f"shot {shot['n']}: {seconds}s talkie clip submitted")
            else:
                jobs.append((shot, frame, clip, segment, None))
        except videogen.VideoGenError as exc:
            ctx.log(f"shot {shot['n']}: talkie clip not started: {exc}")
            jobs.append((shot, frame, clip, segment, None))
    for shot, frame, clip, segment, job in jobs:
        for attempt in range(2 if job else 0):
            try:
                spent = videogen.wait(ctx.cfg, job["id"], segment)
                cost += spent or 0.0
                ctx.paid("video", spent, vg.get("talkie_model"), note=f"shot {shot['n']} talkie")
                ctx.log(f"shot {shot['n']}: talkie clip ready" + (f", ${spent:.3f}" if spent is not None else ""))
                break
            except videogen.VideoGenError as exc:
                if attempt == 0 and "content policy" in str(exc) and vg.get("talkie_retry_refused") and not _stop_requested(ctx):
                    # One retry with a plain prompt; a made retry is billed like any clip.
                    ctx.log(f"shot {shot['n']}: the talkie model refused the prompt, retrying with a plain one")
                    try:
                        job = videogen.submit_talkie(ctx.cfg, frame, _safe_prompt(vg, era) + " Nobody speaks.", job["duration"])
                        continue
                    except videogen.VideoGenError as exc2:
                        exc = exc2
                ctx.log(f"shot {shot['n']}: talkie clip failed: {exc}")
                break
        if segment.is_file():
            joined = clip.with_name(clip.stem + "_joined.mp4")
            videogen.join([segment], joined, shot["seconds"], w, h)
            _warn_if_held(ctx, shot, media.probe(segment)["duration_s"])
            media.video_clip(joined, clip, shot["seconds"], 0.0, fps, w, h, shot["grade"],
                             shot.get("caption_layer"), shot.get("caption_fade", (0, 0)))
            joined.unlink()
            videogen.clip_audio(segment, clip.with_suffix(".wav"), shot["seconds"])
        else:
            media.motion_clip(frame, clip, shot["seconds"], shot["motion"], fps, w, h, shot["grade"],
                              shot.get("caption_layer"), shot.get("caption_fade", (0, 0)), shot.get("fade", (0, 0)))
            ctx.log(f"shot {shot['n']}: fell back to a {shot['motion']} move on the still")
    return cost


def _animate(ctx: StageContext, pending: list, folder: Path, era: str, fps: int, w: int, h: int) -> float:
    """Animate every pending shot's wide frame. A shot longer than one clip is continued from the last
    frame of the clip before (up to max_segments). Every shot's next piece is submitted together, so
    they generate in parallel. Refusals by the service's content filter are retried (a plain prompt,
    an earlier starting frame); a shot with no clip at all gets a camera move on its frame instead."""
    import math

    from . import media, videogen

    vg = videogen.settings(ctx.cfg)
    # The production's own look, never a fixed era: "..., subtle natural movement, 1950s sci-fi B-movie".
    suffix = vg.get("prompt_suffix", "") + (f", {era}" if era else "")
    longest = max(int(d) for d in vg.get("durations", [3, 5]))
    plans = []
    for shot, frame, clip in pending:
        count = min(int(vg.get("max_segments", 3)), max(1, math.ceil((shot["seconds"] - 0.6) / longest)))
        key = _key(frame.name, shot["action"], vg.get("model"), suffix)
        segments = [folder / f"{shot['n']:02d}_{key}_{k}.mp4" for k in range(count)]
        left = shot["seconds"] - longest * (count - 1)
        durations = [longest] * (count - 1) + [min(int(d) for d in vg.get("durations", [3, 5]) if int(d) >= min(left, longest))]
        plans.append({"shot": shot, "frame": frame, "clip": clip, "segments": segments, "durations": durations,
                      "prompt": f"{shot['action'] or shot['prompt']}{suffix}",
                      "ok": True})
    cost = 0.0
    for k in range(max((len(p["segments"]) for p in plans), default=0)):
        check_stop(ctx)  # between rounds only: a submitted job is always waited for, or it's paid for twice
        ctx.check_budget("buy more video")
        jobs = {}
        for p in plans:
            if not p["ok"] or k >= len(p["segments"]) or p["segments"][k].is_file():
                continue
            start = p["frame"] if k == 0 else media.last_frame(p["segments"][k - 1], p["segments"][k].with_suffix(".start.png"))
            try:
                jobs[id(p)] = (p, start, videogen.submit(ctx.cfg, start, p["prompt"], p["durations"][k]))
                ctx.log(f"shot {p['shot']['n']}: video piece {k + 1}/{len(p['segments'])} submitted")
            except videogen.VideoGenError as exc:
                ctx.log(f"shot {p['shot']['n']}: video piece {k + 1} not started: {exc}")
                p["ok"] = False
        for p, start, job in jobs.values():
            n, prompt, cut = p["shot"]["n"], p["prompt"], None
            safe = _safe_prompt(vg, era)
            for attempt in range(3):
                try:
                    spent = videogen.wait(ctx.cfg, job["id"], p["segments"][k])
                    cost += spent or 0.0
                    ctx.paid("video", spent, vg.get("model"), note=f"shot {n} piece {k + 1}")
                    if cut is not None:  # the piece before ends where this one starts
                        p["segments"][k - 1].with_suffix(".cut").write_text(f"{cut:.3f}")
                    ctx.log(f"shot {n}: video piece {k + 1} ready" + (f", ${spent:.3f}" if spent is not None else ""))
                    break
                except videogen.VideoGenError as exc:
                    refused = "content policy" in str(exc)
                    if refused and "image" in str(exc) and k > 0 and attempt < 2:
                        # The continuation's starting frame was refused. Start from an earlier frame of the
                        # piece before (cut there, so the motion still joins up); failing that, from the
                        # shot's own frame (a visible restart, but no freeze).
                        if attempt == 0:
                            cut = round(p["durations"][k - 1] * 0.5, 2)
                            start = media.frame_at(p["segments"][k - 1], cut, p["segments"][k].with_suffix(".start.png"))
                            ctx.log(f"shot {n}: the video service refused piece {k + 1}'s starting frame, "
                                    f"continuing from {cut}s into the piece before")
                        else:
                            cut, start = None, p["frame"]
                            ctx.log(f"shot {n}: refused again, restarting piece {k + 1} from the shot's frame")
                    elif refused and prompt != safe and attempt < 2:
                        prompt = safe
                        ctx.log(f"shot {n}: the video service refused the prompt, retrying with a plain one")
                    else:
                        ctx.log(f"shot {n}: video piece {k + 1} failed: {exc}")
                        p["ok"] = False  # no more pieces; any earlier ones still make a (held) clip
                        break
                    job = videogen.submit(ctx.cfg, start, prompt, p["durations"][k])
    for p in plans:
        shot, made = p["shot"], [s for s in p["segments"] if s.is_file()]
        if made:
            joined = p["clip"].with_name(p["clip"].stem + "_joined.mp4")
            cuts = [float(c.read_text()) if (c := seg.with_suffix(".cut")).is_file() else None for seg in made]
            videogen.join(made, joined, shot["seconds"], w, h, cuts)
            _warn_if_held(ctx, shot, joined_length(made, cuts))
            media.video_clip(joined, p["clip"], shot["seconds"], 0.0, fps, w, h, shot["grade"],
                             shot.get("caption_layer"), shot.get("caption_fade", (0, 0)))
            joined.unlink()
        else:
            media.motion_clip(p["frame"], p["clip"], shot["seconds"], shot["motion"], fps, w, h, shot["grade"],
                              shot.get("caption_layer"), shot.get("caption_fade", (0, 0)), shot.get("fade", (0, 0)))
            ctx.log(f"shot {shot['n']}: fell back to a {shot['motion']} move on the still")
    return cost


# ---------------------------------------------------------------- assemble and look

def stage_assemble(ctx: StageContext) -> None:
    """Today's assembly ([edit] mode = "off"), or the production's EDL rendered (pipeline v2)."""
    from . import editing

    if editing.mode(ctx.cfg) == "off":
        return _assemble_template(ctx)
    return editing.assemble_edl(ctx)


def _assemble_template(ctx: StageContext) -> None:
    """Clips end to end, every line at its planned time, chapter markers at act breaks."""
    from . import media

    plan, render, script = ctx.latest("plan"), ctx.latest("render"), ctx.latest("screenplay")
    if not plan or not render:
        raise Blocked("assembly needs a shot plan and rendered clips")
    root = ctx.cfg.path("productions")
    pdir = production_dir(ctx.cfg, ctx.production["id"])
    total = sum(s["seconds"] for s in plan["shots"])
    picture = media.concat_video([root / s["clip"] for s in render["shots"]], pdir / "render" / "picture.mp4")
    levels = ctx.cfg.section("mix")
    target = float(levels.get("voice_lufs", -20.0))
    gains: dict[Path, float] = {}

    def level(path: Path, offset_db: float = 0.0) -> float:
        """Gain that brings a file to the voice loudness, plus offset_db (every file is measured first:
        TTS lines and downloaded effects come at any level)."""
        if path not in gains:
            measured = media.loudness(path).get("input_i")
            gains[path] = 0.0 if measured is None else max(-30.0, min(30.0, target - measured))
        return round(gains[path] + offset_db, 2)

    sound = ctx.latest("sound") or {"cues": []}
    buses = {"voice": [(root / p["file"], p["at"], level(root / p["file"])) for s in plan["shots"] for p in s["placed"]],
             "music": [], "sfx": []}
    for path, at, kind, limit in _cue_placements(ctx, script, plan, ctx.latest("voice"), sound, total):
        bus = "music" if kind == "music" else "sfx"
        buses[bus].append((path, at, level(path, float(levels.get(f"{bus}_db", -8.0 if bus == "music" else -4.0))), limit))
    # A talkie clip's own soundtrack (effects, an on-camera voice) under its shot, at the level it came
    # at: measuring it would pump a mostly quiet clip's hiss up to voice loudness.
    starts = {s["n"]: s["start"] for s in plan["shots"]}
    for s in render["shots"]:
        track = (root / s["clip"]).with_suffix(".wav")
        if (s.get("talkie") or s.get("own_sound")) and track.is_file():
            buses["sfx"].append((track, starts[s["n"]], float(levels.get("clip_db", -3.0)), s["seconds"]))
    (pdir / "audio").mkdir(parents=True, exist_ok=True)
    mix = media.mix_buses(buses, total, pdir / "audio" / "mix.wav", levels.get("duck"))

    # A chapter ends after the last shot of every scene marked act_break_after: that's where the
    # station cuts to commercials at playout time. Nothing is ever cut into the show itself.
    chapters, start = [], 0.0
    breaks = {i for i, sc in enumerate(script["scenes"]) if sc.get("act_break_after")}
    for i, shot in enumerate(plan["shots"]):
        last_of_scene = i + 1 == len(plan["shots"]) or plan["shots"][i + 1]["scene"] != shot["scene"]
        if shot["scene"] in breaks and last_of_scene:
            end = shot["start"] + shot["seconds"]
            chapters.append((start, end, f"Part {len(chapters) + 1}"))
            start = end
    if chapters and start < total:
        chapters.append((start, total, f"Part {len(chapters) + 1}"))

    out = media.mux(picture, mix, pdir / "assembled.mp4", chapters or None)
    placed_cues = {(c["scene"], c["beat"]) for c in sound["cues"] if c["asset_id"]}
    cues = [f"{b['type']}: {b['text']}" for si, sc in enumerate(script["scenes"]) for bi, b in enumerate(sc["beats"])
            if b["type"] in SOUND_CUES and (si, bi) not in placed_cues]
    body = {"file": str(out.relative_to(root)), "seconds": round(total, 2),
            "chapters": [{"start": s, "end": e, "title": t} for s, e, t in chapters],
            "missing_sound": cues}
    version = ctx.save("assemble", body)
    ctx.log(f"assemble v{version}: {total:.1f}s, {len(chapters)} chapter(s), {len(cues)} sound cue(s) without a sound")


def _cue_placements(ctx: StageContext, script: dict, plan: dict, voice: dict, sound: dict, total: float) -> list:
    """(file, start, cue type, longest it may play) for each sound cue: right after the line before it in
    its scene, or at the start of the scene's first shot. Music runs until the next music cue or the
    end; effects play once."""
    by_n = {p["n"]: p["at"] for s in plan["shots"] for p in s["placed"]}
    line_end = {(l["scene"], l["beat"]): by_n[l["n"]] + l["duration_s"] for l in voice["lines"] if l["n"] in by_n}
    scene_start = {}
    for s in plan["shots"]:
        scene_start.setdefault(s["scene"], s["start"])
    timed = []
    for c in sound["cues"]:
        before = [end for (si, bi), end in line_end.items() if si == c["scene"] and bi < c["beat"]]
        at = max(before) + 0.1 if before else scene_start.get(c["scene"], 0.0)
        timed.append((min(at, total), c))
    out = []
    for i, (at, c) in enumerate(timed):
        if not c["asset_id"]:
            continue
        asset = ctx.conn.execute("SELECT path FROM assets WHERE id = ? AND status = 'ready'", (c["asset_id"],)).fetchone()
        if asset is None:
            continue
        if c["type"] == "music":
            nxt = [t for t, d in timed[i + 1:] if d["type"] == "music" and d["asset_id"]]
            out.append((ctx.cfg.library / asset["path"], at, "music", max(0.5, (nxt[0] if nxt else total) - at)))
        else:
            out.append((ctx.cfg.library / asset["path"], at, c["type"], max(0.5, total - at)))
    return out


def stage_look(ctx: StageContext) -> None:
    from . import media

    assembled = ctx.latest("assemble")
    if not assembled:
        raise Blocked("the look needs an assembled cut")
    preset = ctx.fmt.get("look", {}).get("preset", "clean")
    if preset not in media.LOOKS:
        ctx.log(f"look '{preset}' isn't built yet (have: {', '.join(media.LOOKS)}), finishing with 'clean'")
        preset = "clean"
    root = ctx.cfg.path("productions")
    out = media.look(root / assembled["file"], production_dir(ctx.cfg, ctx.production["id"]) / "final.mp4", preset)
    version = ctx.save("look", {"file": str(out.relative_to(root)), "preset": preset, "seconds": assembled["seconds"]})
    ctx.log(f"look v{version}: {preset} -> {out.name}")


def _stage_edit(ctx: StageContext) -> None:
    from . import editing

    editing.stage_edit(ctx)


def _stage_qc(ctx: StageContext) -> None:
    from . import editing

    editing.stage_qc(ctx)


def _stage_review(ctx: StageContext) -> None:
    from . import editing

    editing.stage_review(ctx)


def _stage_publish(ctx: StageContext) -> None:
    from . import publish

    publish.run(ctx)


# Stage name -> handler. Anything in a format's stage list that isn't here blocks the run.
STAGES: dict[str, Callable[[StageContext], None]] = {
    "outline": stage_outline,
    "screenplay": stage_screenplay,
    "cast": stage_cast,
    "voice": stage_voice,
    "plan": stage_plan,
    "sound": stage_sound,
    "render": stage_render,
    "assemble": stage_assemble,
    "look": stage_look,
    # pipeline v2: each one is off (or harmless) until switched on in config/aitv.toml
    "edit": _stage_edit,
    "qc": _stage_qc,
    "review": _stage_review,
    "publish": _stage_publish,
}
# Stages whose input is a saved answer that can be pasted, edited or rewritten.
SCHEMAS = {"outline": OUTLINE_SCHEMA, "screenplay": SCREENPLAY_SCHEMA, "casting": CASTING_SCHEMA, "plan": PLAN_SCHEMA}
# A rewind of these answers restarts the run from the stage that reads them.
ANSWER_STAGE = {"outline": "outline", "screenplay": "screenplay", "casting": "cast", "plan": "plan"}


def preflight(cfg: Config, fmt: dict, stages: list[str]) -> list[str]:
    """Problems that would stop a run partway (after money is spent), found before it starts. A look
    that isn't built only warns: the look stage finishes with 'clean' instead."""
    import os

    from . import videogen

    problems = [f"stage '{s}' isn't built yet" for s in stages if s not in STAGES]
    if set(stages) & {"outline", "screenplay", "plan"} and cfg.section("llm").get("backend", "manual") != "manual":
        opts = cfg.section("llm").get(cfg.section("llm")["backend"], {})
        if opts.get("api_key_env") and not os.environ.get(opts["api_key_env"]):
            problems.append(f"the writing backend needs {opts['api_key_env']} (config/secrets.env)")
    if "render" in stages and videogen.enabled(cfg):
        env = videogen.settings(cfg).get("api_key_env", "OPENROUTER_API_KEY")
        if not os.environ.get(env):
            problems.append(f"video generation needs {env} (config/secrets.env)")
    music = fmt.get("music", {})
    if "sound" in stages and music.get("source") == "midigen":
        tool = cfg.tool("midigen")
        if not tool or not Path(tool).exists():
            problems.append("the format scores with MIDIgen, which isn't installed ([tools] midigen)")
    return problems


def _tell(conn: sqlite3.Connection, production: sqlite3.Row, status: str, note: str = "") -> None:
    """DM the user how a run ended (Chuppybot outbox). Never raises: a message must not change how a run ends."""
    try:
        spent = conn.execute("SELECT spent_usd FROM productions WHERE id = ?", (production["id"],)).fetchone()[0]
        icon = {"done": "✅", "failed": "❌", "blocked": "⛔", "waiting_on_you": "✋"}.get(status, "•")
        notify.send(f"{icon} ai_tv #{production['id']} {production['title'] or production['format']}: "
                    f"{status.replace('_', ' ')}", (note[:1500] + "\n" if note else "") + f"spent ${spent:.2f}",
                    f"http://mintyogurt:8420/productions/{production['id']}")
    except Exception:
        pass


def run_production(
    conn: sqlite3.Connection,
    cfg: Config,
    production_id: int,
    log: Callable[[str], None] = lambda line: None,
    llm: llm_mod.Backend | None = None,
) -> dict:
    production = conn.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
    fmt = load_format(cfg, production["format"])
    stages = fmt.get("pipeline", {}).get("stages", [])
    start = stages.index(production["stage"]) if production["stage"] in stages else 0
    ctx = StageContext(conn, cfg, fmt, production, log, llm)

    # Everything a run will need is checked before the first (paid) stage, not discovered at the end.
    problems = preflight(cfg, fmt, stages[start:])
    if problems:
        note = "fix before running: " + "; ".join(problems)
        conn.execute("UPDATE productions SET status = 'blocked', note = ?, updated_at = ? WHERE id = ?",
                     (note, now(), production_id))
        _tell(conn, production, "blocked", note)
        raise Blocked(note)
    conn.execute("UPDATE productions SET status = 'running', note = '', updated_at = ? WHERE id = ?", (now(), production_id))
    for stage in stages[start:]:
        conn.execute("UPDATE productions SET stage = ?, updated_at = ? WHERE id = ?", (stage, now(), production_id))
        ctx.stage = stage
        handler = STAGES.get(stage)
        try:
            check_stop(ctx)
            if handler is None:
                raise Blocked(f"stage '{stage}' isn't built yet")
            log(f"stage {stage}: start")
            handler(ctx)
            log(f"stage {stage}: done")
        except Stopped:
            log(f"stopped before finishing {stage}")
            raise
        except Blocked as exc:
            status = "waiting_on_you" if isinstance(exc, WaitingOnYou) else "blocked"
            conn.execute("UPDATE productions SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                         (status, str(exc), now(), production_id))
            _tell(conn, production, status, str(exc))
            raise
        except Exception as exc:
            conn.execute(
                "UPDATE productions SET status = 'failed', note = ?, updated_at = ? WHERE id = ?",
                (f"{stage}: {exc}", now(), production_id),
            )
            _tell(conn, production, "failed", f"{stage}: {exc}")
            raise
    conn.execute(
        "UPDATE productions SET status = 'done', stage = NULL, note = '', updated_at = ? WHERE id = ?",
        (now(), production_id),
    )
    if production["idea_id"]:
        conn.execute("UPDATE ideas SET status = 'done', updated_at = ? WHERE id = ?", (now(), production["idea_id"]))
    _tell(conn, production, "done")
    return {"completed": stages[start:]}


def rewind(conn: sqlite3.Connection, cfg: Config, production_id: int, stage: str) -> None:
    """Throw away a stage's saved writing and restart the production from that stage."""
    paths = writing_paths(cfg, production_id, stage)
    paths["answer"].unlink(missing_ok=True)
    conn.execute("UPDATE productions SET stage = ?, status = 'planned', note = '', updated_at = ? WHERE id = ?",
                 (ANSWER_STAGE.get(stage, stage), now(), production_id))
