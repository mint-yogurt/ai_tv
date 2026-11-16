-- AI TV database schema (SQLite). Applied idempotently by `aitv init`.
-- Loose, per-format details live in JSON columns so a new format needs no migration.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Anything in the fake world: characters, voices, sets, brands, products,
-- fictional shows and films, hotlines, presenters, props.
CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    attributes  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (type, name)
);

-- Brand -> product, character -> voice, show -> cast, voice variant -> parent voice.
CREATE TABLE IF NOT EXISTS entity_links (
    from_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, relation)
);

-- One row per stored file. Files live under library/store/, named by content hash.
CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY,
    category      TEXT NOT NULL,              -- image | voice | sfx | music | footage
    media         TEXT,                       -- image | audio | video (after ingest)
    title         TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL DEFAULT '',
    sha256        TEXT UNIQUE,
    path          TEXT,                       -- relative to library/
    mime          TEXT,
    duration_s    REAL,
    width         INTEGER,
    height        INTEGER,
    size_bytes    INTEGER,
    tags          TEXT NOT NULL DEFAULT '[]',
    source        TEXT NOT NULL DEFAULT 'upload',  -- upload | inbox | generated | manual | archive | commons | openverse
    status        TEXT NOT NULL DEFAULT 'pending', -- pending | processing | ready | failed | culled
    meta          TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS assets_category ON assets (category, status);

CREATE TABLE IF NOT EXISTS asset_entities (
    asset_id  INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'reference',  -- headshot | plate | logo | voice_reference | ...
    PRIMARY KEY (asset_id, entity_id, role)
);

CREATE TABLE IF NOT EXISTS ideas (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    format     TEXT,
    pins       TEXT NOT NULL DEFAULT '{}',
    options    TEXT NOT NULL DEFAULT '{}',   -- a saved make form's length range and attachments
    status     TEXT NOT NULL DEFAULT 'queued',   -- queued | in_production | done | shelved
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS productions (
    id         INTEGER PRIMARY KEY,
    format     TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    idea_id    INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    seed       INTEGER NOT NULL,
    rolls      TEXT NOT NULL DEFAULT '{}',
    budget_usd REAL NOT NULL DEFAULT 0,
    spent_usd  REAL NOT NULL DEFAULT 0,
    options    TEXT NOT NULL DEFAULT '{}',   -- the make form's choices: length range, attached cast/voices/assets
    status     TEXT NOT NULL DEFAULT 'planned',  -- planned | running | blocked | done | failed | cancelled
    stage      TEXT,
    note       TEXT NOT NULL DEFAULT '',
    final_path TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Every stage's output (outline, screenplay, plan, timeline), versioned.
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    version       INTEGER NOT NULL,
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (production_id, kind, version)
);

CREATE TABLE IF NOT EXISTS shots (
    id            INTEGER PRIMARY KEY,
    production_id INTEGER NOT NULL REFERENCES productions(id) ON DELETE CASCADE,
    shot_key      TEXT NOT NULL,
    ord           INTEGER NOT NULL,
    source        TEXT NOT NULL DEFAULT 'still_motion',  -- library | still_motion | stock | manual | paid_clip
    status        TEXT NOT NULL DEFAULT 'planned',
    asset_id      INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    spec          TEXT NOT NULL DEFAULT '{}',
    UNIQUE (production_id, shot_key)
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed | blocked | cancelled
    priority      INTEGER NOT NULL DEFAULT 0,
    payload       TEXT NOT NULL DEFAULT '{}',
    result        TEXT,
    error         TEXT,
    log           TEXT NOT NULL DEFAULT '',
    production_id INTEGER REFERENCES productions(id) ON DELETE SET NULL,
    asset_id      INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    started_at    TEXT,
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, priority, id);

-- Which entity or asset a production used, and when. Feeds the round-robin weights.
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY,
    production_id INTEGER REFERENCES productions(id) ON DELETE CASCADE,
    entity_id     INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    asset_id      INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    used_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Every paid call (pipeline v2, part A). Feeds productions.spent_usd and the per-production budget.
CREATE TABLE IF NOT EXISTS spend (
    id            INTEGER PRIMARY KEY,
    production_id INTEGER REFERENCES productions(id) ON DELETE CASCADE,
    stage         TEXT,                        -- the stage that paid (outline, render, edit, review...)
    what          TEXT NOT NULL,               -- llm | video | image
    model         TEXT,
    tokens_in     INTEGER,
    tokens_out    INTEGER,
    seconds       REAL,                        -- video seconds bought
    cost_usd      REAL NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS spend_production ON spend (production_id, stage);

-- Speech to text, one row per file content (pipeline v2, parts B and G). Keyed by the same fingerprint
-- the ChupTV director's catalog uses (sha1 of the size plus the first and last 1 MB), so either project
-- can read the other's transcripts. words: JSON [[start, end, word], ...]; segments: [[start, end, text], ...].
CREATE TABLE IF NOT EXISTS transcripts (
    fingerprint   TEXT PRIMARY KEY,
    path          TEXT NOT NULL,               -- where it was last seen (informational)
    duration_s    REAL,
    model         TEXT NOT NULL,
    language      TEXT,
    segments      TEXT NOT NULL,
    words         TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- The footage index (pipeline v2, part G): files the planner and editor can pull real footage from.
CREATE TABLE IF NOT EXISTS footage (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,        -- absolute
    asset_id      INTEGER REFERENCES assets(id) ON DELETE CASCADE,  -- set for library footage
    fingerprint   TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',    -- show / file name, searchable
    tags          TEXT NOT NULL DEFAULT '',    -- space separated, searchable
    duration_s    REAL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | indexed | failed
    error         TEXT,
    indexed_at    TEXT
);
-- Scenes between the file's own cuts, each with a keyframe (relative to library/).
CREATE TABLE IF NOT EXISTS footage_scenes (
    footage_id    INTEGER NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
    start_s       REAL NOT NULL,
    end_s         REAL NOT NULL,
    thumb         TEXT,
    PRIMARY KEY (footage_id, start_s)
);
-- Full-text search over what's said: one row per transcript segment.
CREATE VIRTUAL TABLE IF NOT EXISTS footage_text USING fts5(
    text, footage_id UNINDEXED, start_s UNINDEXED, end_s UNINDEXED, tokenize = 'porter unicode61'
);
