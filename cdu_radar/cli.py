import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from .collect import collect
from .http import Fetcher
from .models import RadarError
from .rss import export_rss
from .sources import load_sources
from .storage import Store


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CDU M0 local collector and RSS exporter")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("sources", help="Show catalog and current SQLite state")
    collection = sub.add_parser("collect", help="Collect bounded official pages, no publishing")
    export = sub.add_parser("export-rss", help="Export successfully parsed notices to local RSS 2.0")
    for command in (listing, collection, export):
        command.add_argument("--db", type=Path, default=Path("data/radar.sqlite3"))
    collection.add_argument("--source", action="append", help="Source id; repeatable (default: implemented adapters)")
    collection.add_argument("--limit", type=positive_int, default=3, help="Article attempts per source, default 3")
    collection.add_argument("--no-env-proxy", action="store_true", help="Ignore environment proxy settings")
    collection.add_argument("--max-pages", type=positive_int, default=1)
    collection.add_argument("--resolve", action="append", default=[], metavar="HOST=IP",
                            help="Explicit public DNS answer; requires --no-env-proxy; TLS still verified")
    export.add_argument("--limit", type=positive_int, default=30)
    export.add_argument("--output", type=Path, default=Path("data/feed.xml"))
    args = parser.parse_args(argv)
    sources = load_sources()
    selected = []
    if args.command == "collect":
        if args.resolve and not args.no_env_proxy:
            parser.error("--resolve requires --no-env-proxy")
        if any("=" not in value for value in args.resolve):
            parser.error("--resolve expects HOST=IP")
        if args.source:
            unknown = set(args.source) - {s.id for s in sources}
            if unknown:
                parser.error("Unknown source: " + ", ".join(sorted(unknown)))
            selected = [s for s in sources if s.id in args.source]
        else:
            selected = [s for s in sources if s.adapter]
    # Export must not silently create a fresh empty database on a typo.
    if args.command == "export-rss" and not args.db.is_file():
        print(f"Database does not exist: {args.db}; run collect first", file=sys.stderr)
        return 2
    if args.command == "export-rss" and args.output.resolve() == args.db.resolve():
        print("RSS output must differ from the SQLite database", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with Store(args.db) as store:
            store.sync_sources(sources)
            if args.command == "sources":
                rows = [dict(row) for row in store.db.execute("SELECT * FROM sources ORDER BY id")]
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            elif args.command == "collect":
                resolve = dict(value.split("=",1) for value in args.resolve)
                with Fetcher(trust_env=not args.no_env_proxy, event_sink=store.request_event, resolve=resolve) as fetcher:
                    results = collect(store, fetcher, selected, limit=args.limit, max_pages=args.max_pages)
                print(json.dumps(results, ensure_ascii=False, indent=2))
                totals = {key:sum(r[key] for r in results) for key in ("inserted","updated","unchanged","duplicates","failed","parse_failures","pages_read","elapsed_seconds")}
                totals.update(successful_sources=sum(r["success"] for r in results),failed_sources=sum(not r["success"] for r in results))
                print(json.dumps({"collection_summary":totals},ensure_ascii=False),file=sys.stderr)
                return 1 if any(result["failed"] for result in results) else 0
            else:
                count = export_rss(store, args.output, limit=args.limit)
                print(json.dumps({"output": str(args.output), "items": count}, ensure_ascii=False))
        return 0
    except (RadarError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
