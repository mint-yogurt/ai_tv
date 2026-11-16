"""Command line: `aitv init | web | worker | scan | add | produce | status`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

from . import config, db, ingest, jobs, pipeline, rolls, system


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aitv", description="AI TV pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create folders and the database")
    sub.add_parser("web", help="run the web UI (normally started by systemd)")
    sub.add_parser("worker", help="run the job worker (normally started by systemd)")
    sub.add_parser("scan", help="register files sitting in library/inbox/ now")
    sub.add_parser("status", help="print machine and queue status")
    a = sub.add_parser("add", help="copy a file into the library (e.g. a tool's output) and queue its ingest")
    a.add_argument("file")
    a.add_argument("--category", required=True, choices=config.CATEGORIES)
    a.add_argument("--title", default="")
    a.add_argument("--tag", action="append", default=[], help="repeatable")
    a.add_argument("--source", default="generated")
    a.add_argument("--meta", default="{}", help="JSON object stored with the asset")
    p = sub.add_parser("produce", help="create a production and queue it")
    p.add_argument("format")
    p.add_argument("--title", default="")
    p.add_argument("--idea", type=int)
    p.add_argument("--seed", type=int)
    p.add_argument("--pin", action="append", default=[], help="key:value, repeatable")
    p.add_argument("--no-run", action="store_true", help="only take the rolls")
    args = parser.parse_args(argv)

    cfg = config.load()
    cfg.ensure_dirs()
    conn = db.connect(cfg.db_path)
    db.init(conn)

    if args.cmd == "init":
        print(f"ready: {cfg.db_path}")
    elif args.cmd == "web":
        import uvicorn

        web = cfg.section("web")
        uvicorn.run("aitv.web.app:app", host=web.get("host", "127.0.0.1"), port=int(web.get("port", 8421)),
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    elif args.cmd == "worker":
        from . import worker

        worker.serve(cfg)
    elif args.cmd == "scan":
        print(f"registered {len(ingest.scan_inbox(conn, cfg))} file(s)")
    elif args.cmd == "add":
        src = Path(args.file)
        dest = cfg.uploads / f"{uuid.uuid4().hex[:8]}_{ingest.safe_name(src.name)}"
        shutil.copy2(src, dest)
        asset_id = ingest.register(conn, dest, args.category, title=args.title, original_name=src.name,
                                   source=args.source, tags=args.tag, extra_meta=json.loads(args.meta))
        print(f"asset {asset_id} queued for ingest")
    elif args.cmd == "status":
        print(json.dumps(system.snapshot(conn, cfg), indent=2, default=str))
    elif args.cmd == "produce":
        pins = rolls.parse_pins("\n".join(args.pin))
        pid = pipeline.create_production(conn, cfg, args.format, title=args.title, idea_id=args.idea,
                                         pins=pins, seed=args.seed)
        row = conn.execute("SELECT seed, rolls FROM productions WHERE id = ?", (pid,)).fetchone()
        print(f"production {pid} (seed {row['seed']})")
        for name, roll in db.loads(row["rolls"]).items():
            print(f"  {name}: {roll['value']}{'  (pinned)' if roll.get('pinned') else ''}")
        if not args.no_run:
            jobs.enqueue(conn, "production.run", {"production_id": pid}, production_id=pid)
            print("queued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
