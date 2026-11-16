"""Downloads this promo's sound effects and field recordings from Openverse into the ai_tv library (free, no key).
build.py reads copies in src/sfx/ (sfx.json "path"): unused online downloads get culled from the library."""
import json
from pathlib import Path
from aitv import config, db, online

HERE = Path(__file__).parent
WANT = {  # name: (search, id prefix)
    "pine_wind": ("wind pine trees", "555064f4"),
    "crickets_owl": ("owl hoot night", "7379a181"),
    "powerlines": ("power line hum", "1cdab992"),
    "street_lamp_hum": ("electrical hum", "f77084ee"),
    "hotel_room_tone": ("electrical hum", "0738ea41"),
    "fluorescent": ("fluorescent light buzz", "4fa774d7"),
    "bulb_flicker": ("light bulb flicker", "dc0de37b"),
    "jacobs_ladder": ("transformer buzz", "7d4253aa"),
    "tv_static": ("tv static", "2155b721"),
    "crt_off": ("crt tv turn off", "df1859d8"),
    "distant_train": ("train passing distant", "ce10828f"),
    "reverse_glass": ("reverse cymbal", "1349736d"),
}
cfg = config.load()
conn = db.connect(cfg.db_path)
out = {}
for name, (q, rid) in WANT.items():
    r = next(r for r in online.search(cfg, "openverse", "audio", q, 8) if r["id"].startswith(rid))
    aid = online.fetch_now(conn, cfg, r, "sfx", tags=["tv_afterlife_promo"], log=print)
    row = conn.execute("SELECT path, title FROM assets WHERE id = ?", (aid,)).fetchone()
    out[name] = {"asset_id": aid, "path": str(cfg.path("library") / row["path"]),
                 "title": r["title"], "landing": r["landing"], "seconds": r["seconds"]}
    print(name, aid, out[name]["path"])
(HERE / "src" / "sfx.json").write_text(json.dumps(out, indent=1))
