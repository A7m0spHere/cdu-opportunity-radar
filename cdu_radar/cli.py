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
    for stream in (sys.stdout, sys.stderr):
        if (getattr(stream,'encoding',None) or '').lower().replace('-','')!='utf8' and hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="CDU automatic official notice aggregation, safe RSS and offline evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("sources", help="Show catalog and current SQLite state")
    collection = sub.add_parser("collect", help="Collect bounded official pages, no publishing")
    export = sub.add_parser("export-rss", help="Legacy technical diagnostic export; use serve-rss for automatic aggregation")
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
    analysis=sub.add_parser("analyze",help="Offline rule baseline; no LLM and no approval")
    analysis.add_argument("--output",type=Path,default=Path("data/m2a-analysis.json"))
    label=sub.add_parser("label",help="Export/import explicitly human-labelled CSV")
    label_actions=label.add_subparsers(dest="label_action",required=True)
    label_export=label_actions.add_parser("export")
    label_export.add_argument("--output",type=Path,default=Path("data/m2a-labels.csv"))
    label_export.add_argument("--overwrite",action="store_true",help="Replace existing worksheet; export imported labels only")
    label_import=label_actions.add_parser("import")
    label_import.add_argument("--input",type=Path,required=True)
    label_import.add_argument("--labeler",required=True)
    label_import.add_argument("--allow-synthetic",action="store_true")
    evaluation=sub.add_parser("evaluate",help="Evaluate only explicitly imported labels")
    evaluation.add_argument("--output",type=Path,default=Path("data/m2a-evaluation.json"))
    evaluation.add_argument("--include-synthetic",action="store_true")
    review=sub.add_parser("review",help="Read-only historical review inspection; confirmation writes are retired")
    review_actions=review.add_subparsers(dest="review_action",required=True)
    review_list=review_actions.add_parser("list")
    review_list.add_argument('--all',action='store_true',help='Include approved, rejected and revoked publication decisions')
    from .publication_cli import register_review
    register_review(review_actions)
    review_set=review_actions.add_parser("set")
    review_set.add_argument("--notice-id",type=positive_int,required=True)
    review_set.add_argument("--status",choices=["pending","approved","rejected"],required=True)
    review_set.add_argument("--reason",required=True)
    review_set.add_argument("--reviewer",required=True)
    review_set.add_argument('--confirm',action='store_true',help='Retired; confirmation writes are rejected in M4-B')
    for command in (analysis,label_export,label_import,evaluation,review_list,review_set):
        command.add_argument("--db",type=Path,default=Path("data/m2a.sqlite3"))
    for command in (analysis,label_export,evaluation,review_list):
        command.add_argument("--rules",type=Path)
        command.add_argument("--as-of",help="YYYY-MM-DD or timestamp with timezone; interpreted in Asia/Shanghai")
    approved=sub.add_parser('export-approved-rss',help='Historical approval preview only; confirmed export is retired')
    approved.add_argument('--db',type=Path,default=Path('data/m2a.sqlite3'))
    approved.add_argument('--output',type=Path,default=Path('data/approved.xml'))
    approved.add_argument('--confirm')
    approved.add_argument('--dry-run',action='store_true',help='Read-only preview; incompatible with --confirm')
    rss_server=sub.add_parser('serve-approved-rss',help='Retired; use serve-rss for automatic /feed.xml')
    rss_server.add_argument('--db',type=Path,required=True)
    rss_server.add_argument('--state-dir',type=Path,required=True,help='Exclusive private service cache; never served as a directory')
    rss_server.add_argument('--bind',default='127.0.0.1')
    rss_server.add_argument('--port',type=int,default=8765)
    rss_server.add_argument('--interval',type=float,default=10)
    rss_server.add_argument('--max-age',type=float,default=30)
    auto=sub.add_parser('aggregate',help='Inspect automatic quality, category and time state; no human approval or LLM')
    auto.add_argument('--db',type=Path,required=True)
    auto.add_argument('--as-of')
    auto_server=sub.add_parser('serve-rss',help='Automatic aggregation RSS with live 503 gate; no approvals or models')
    auto_server.add_argument('--db',type=Path,required=True)
    auto_server.add_argument('--state-dir',type=Path,required=True)
    auto_server.add_argument('--bind',default='127.0.0.1')
    auto_server.add_argument('--port',type=int,default=8765)
    auto_server.add_argument('--interval',type=float,default=10)
    auto_server.add_argument('--max-age',type=float,default=30)
    auto_server.add_argument('--collect-interval',type=float,default=0,help='Optional bounded collection every N seconds (>=60); 0 serves DB only')
    auto_server.add_argument('--source',action='append')
    auto_server.add_argument('--limit',type=positive_int,default=3)
    auto_server.add_argument('--no-env-proxy',action='store_true')
    lan=sub.add_parser('serve-lan-preview',help='Explicit device-allowlisted read proxy; never changes the loopback RSS listener')
    lan.add_argument('--bind',required=True,help='Specific RFC1918 device IPv4, never 0.0.0.0')
    lan.add_argument('--allow-client',action='append',required=True,help='Specific allowed device IPv4; repeatable, at most 8')
    lan.add_argument('--port',type=int,default=8780)
    lan.add_argument('--upstream-port',type=int,default=8765)
    lan_mode=lan.add_mutually_exclusive_group()
    lan_mode.add_argument('--check-only',action='store_true',help='Validate configuration and local public upstream; open no listener')
    lan_mode.add_argument('--enable-lan',action='store_true',help='Explicitly enable the separate private-address listener')
    synthetic=sub.add_parser('rss-synthetic',help='Separate synthetic-only lab; never uses existing user data')
    synthetic_actions=synthetic.add_subparsers(dest='synthetic_action',required=True)
    for name in ['init','add','revoke','update','approve']:
        action=synthetic_actions.add_parser(name)
        action.add_argument('--directory',type=Path,required=True)
        if name in {'revoke','update','approve'}:action.add_argument('notice_id',type=positive_int)
        if name in {'init','add','approve'}:action.add_argument('--ttl',type=positive_int,default=3600)
    llm = sub.add_parser("llm", help="Bounded model analysis and cache-only comparison")
    llm_actions = llm.add_subparsers(dest="llm_action", required=True)
    llm_analyze = llm_actions.add_parser("analyze", help="Default: preview only; --send explicitly spends API requests")
    llm_analyze.add_argument("--limit", type=positive_int)
    llm_analyze.add_argument("--retry-failed", action="store_true", help="Include previously failed results; explicit ids also allow retry")
    mode = llm_analyze.add_mutually_exclusive_group()
    mode.add_argument("--send", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    llm_analyze.add_argument("--output", type=Path, default=Path("data/m2b-llm-analysis.json"))
    llm_compare = llm_actions.add_parser("compare", help="Read predictions; never call a model")
    llm_compare.add_argument("--output", type=Path, default=Path("data/m2b-comparison.json"))
    llm_compare.add_argument("--errors-output", type=Path)
    llm_compare.add_argument("--analysis-version", help="Explicit historical cache version; revalidate, never send")
    llm_history = llm_actions.add_parser("history", help="Read original cached version without changing or revalidating it")
    llm_history.add_argument("--analysis-version", required=True)
    llm_history.add_argument("--output", type=Path, default=Path("data/m2b1-history.json"))
    llm_diagnose = llm_actions.add_parser("diagnose", help="Offline diagnosis from local historical reports and cache")
    llm_diagnose.add_argument("--comparison", type=Path, default=Path("data/m2b-comparison-revalidated.json"))
    llm_diagnose.add_argument("--errors-report", type=Path, default=Path("data/m2b-errors-revalidated.json"))
    llm_diagnose.add_argument("--output", type=Path, default=Path("data/m2b1-diagnosis.json"))
    for command in (llm_analyze, llm_compare, llm_history, llm_diagnose):
        command.add_argument("--db", type=Path, default=Path("data/m2b.sqlite3"))
        command.add_argument("--notice-id", type=positive_int, action="append")
        command.add_argument("--as-of")
        command.add_argument("--overwrite", action="store_true", help="Explicitly replace JSON report")
    args = parser.parse_args(argv)
    if args.command=='serve-lan-preview':
        from .lan_preview import serve_command
        return serve_command(args)
    if ((args.command=='review' and args.review_action in {'approve','reject','defer','revoke','set'} and getattr(args,'confirm',None))
            or args.command=='export-approved-rss' and args.confirm
            or args.command=='serve-approved-rss'):
        print('M4-B: human approval publishing is retired and history is read-only; use serve-rss for /feed.xml.',file=sys.stderr)
        return 2
    if args.command=='serve-rss':
        from .auto_service import serve_command
        return serve_command(args)
    if args.command=='aggregate':
        from .auto_publish import AutoRepository
        try:
            if not args.db.is_file():raise ValueError('Database does not exist')
            from .analysis_models import reference_datetime
            with Store(args.db) as store:
                plan=AutoRepository(store.db).feed_plan(reference_datetime(args.as_of))
            print(json.dumps(plan,ensure_ascii=False,indent=2));return 0
        except (OSError,sqlite3.Error,ValueError) as exc:
            print(f'{type(exc).__name__}: {exc}',file=sys.stderr);return 2
    if args.command=='rss-synthetic':
        from .rss_synthetic import synthetic_command
        return synthetic_command(args)
    if args.command=='export-approved-rss' or args.command=='review' and args.review_action!='set':
        if getattr(args,'dry_run',False) and getattr(args,'confirm',None):parser.error('--dry-run cannot be combined with --confirm')
        from .publication_cli import publication_command
        return publication_command(args)
    if args.command=='review' and args.review_action=='set' and not args.confirm:
        print(json.dumps({'preview':True,'scope':'legacy_analysis_only','notice_id':args.notice_id,'status':args.status,
            'reason':args.reason,'reviewer':args.reviewer,'confirmation_required':'--confirm','publishes_rss':False},ensure_ascii=False));return 0
    if args.command == "llm":
        from .llm_cli import llm_command
        return llm_command(args)
    if args.command in {"analyze","label","evaluate","review"}:
        return analysis_command(args)
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


def analysis_command(args):
    from .analysis_store import AnalysisRepository
    from .analysis_service import analyze_all
    from .analysis_models import reference_datetime
    from .rule_analysis import RuleAnalyzer
    from .labels import atomic_text,export_labels,import_labels
    from .evaluation import evaluate
    if not args.db.is_file():
        print(f"Database does not exist: {args.db}",file=sys.stderr);return 2
    if getattr(args,"output",None) and args.output.resolve()==args.db.resolve():
        print("Output must differ from database",file=sys.stderr);return 2
    if args.command=="label" and args.label_action=="export" and args.output.exists() and not args.overwrite:
        print("Worksheet exists; choose another output or explicitly use --overwrite",file=sys.stderr);return 2
    try:
        now=reference_datetime(getattr(args,"as_of",None))
        analyzer=RuleAnalyzer(getattr(args,"rules",None))
        with Store(args.db) as store:
            repository=AnalysisRepository(store)
            if args.command=="analyze":
                rows,summary=analyze_all(repository,analyzer=analyzer,reference_time=now)
                atomic_text(args.output,json.dumps({"summary":summary,"analyses":[r.to_dict() for r in rows]},ensure_ascii=False,indent=2),encoding="utf-8")
                print(json.dumps(summary,ensure_ascii=False))
                return 1 if summary["failed"] else 0
            if args.command=="label":
                if args.label_action=="export":
                    count=export_labels(repository,args.output,analyzer=analyzer,reference_time=now)
                    print(json.dumps({"output":str(args.output),"rows":count,"new_rows_status":"unlabeled"},ensure_ascii=False))
                else:print(json.dumps(import_labels(repository,args.input,labeler=args.labeler,allow_synthetic=args.allow_synthetic),ensure_ascii=False))
            elif args.command=="evaluate":
                report=evaluate(repository,analyzer=analyzer,reference_time=now if args.as_of else None,include_synthetic=args.include_synthetic)
                atomic_text(args.output,json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
                print(json.dumps(report,ensure_ascii=False,indent=2))
            elif args.review_action=="set":
                print(json.dumps(repository.review(args.notice_id,args.status,args.reason,args.reviewer),ensure_ascii=False))
            else:
                rows,_=analyze_all(repository,analyzer=analyzer,reference_time=now)
                print(json.dumps([{ "notice_id":r.notice_id,"category":r.category,"decision":r.candidate_decision,
                    "review_status":r.review_status,"needs_manual_review":r.needs_manual_review} for r in rows],ensure_ascii=False,indent=2))
        return 0
    except (OSError,sqlite3.Error,ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}",file=sys.stderr);return 2
