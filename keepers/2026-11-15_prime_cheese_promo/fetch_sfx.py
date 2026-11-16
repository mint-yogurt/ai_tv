"""Downloads this promo's sound effects and field recordings from Openverse into the ai_tv library (free, no key).
build.py reads copies in src/sfx/ (sfx.json "path"): unused online downloads get culled from the library."""
import json, shutil
from pathlib import Path
from aitv import config, db, online

HERE = Path(__file__).parent
WANT = {  # name: (search, id prefix)
    "laugh_1": ("sitcom laugh track", "beaadf1e"),
    "laugh_2": ("sitcom laugh track", "274d34c2"),
    "laugh_5": ("sitcom laugh track", "af2861b6"),
    "crowd_laugh": ("sitcom laugh track", "8130c747"),
    "laugh_claps": ("audience applause cheering", "813b5820"),
    "cheer_clap": ("audience applause cheering", "7d0cb30b"),
    "aww": ("audience aww", "b8be4844"),
    "wine_pour": ("wine pouring glass", "373c5ae9"),
    "cork": ("cork pop champagne", "6cf0b7a6"),
    "fireplace": ("fireplace crackle", "b9759891"),
    "harp": ("harp glissando", "9636ab8f"),
    "cheese_cut": ("cheese knife cut", "ae9fa19b"),
    "stamp": ("rubber stamp", "e5f3310d"),
    "clink": ("glasses clink toast", "9941d77b"),
    "restaurant": ("restaurant ambience", "bd4175ce"),
    "record_scratch": ("record scratch", "4a313d5d"),
    "tape_screech": ("tape rewind", "f65084fa"),
}
cfg = config.load()
conn = db.connect(cfg.db_path)
(HERE / "src" / "sfx").mkdir(parents=True, exist_ok=True)
out = {}
for name, (q, rid) in WANT.items():
    r = next(r for r in online.search(cfg, "openverse", "audio", q, 8) if r["id"].startswith(rid))
    aid = online.fetch_now(conn, cfg, r, "sfx", tags=["prime_cheese_promo"], log=print)
    row = conn.execute("SELECT path, title FROM assets WHERE id = ?", (aid,)).fetchone()
    lib = cfg.path("library") / row["path"]
    copy = HERE / "src" / "sfx" / f"{name}{lib.suffix}"
    shutil.copy2(lib, copy)
    out[name] = {"asset_id": aid, "path": str(copy), "library_path": str(lib),
                 "title": r["title"], "landing": r["landing"], "seconds": r["seconds"]}
    print(name, aid, copy.name)
(HERE / "src" / "sfx.json").write_text(json.dumps(out, indent=1))
