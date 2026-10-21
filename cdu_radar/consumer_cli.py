"""One-shot local polling and durable draft replay."""
import json
from pathlib import Path
import sqlite3
import sys

from .rss_consumer import config, poll, export_run


def register(sub):
    p=sub.add_parser('rss-consumer',help='Local RSS v2 to reviewable drafts; zero bot/model calls')
    actions=p.add_subparsers(dest='consumer_action',required=True)
    for action in ['poll','export']:
        a=actions.add_parser(action)
        a.add_argument('--state-db',type=Path,required=True)
        a.add_argument('--directory',type=Path,required=True,help='New private output directory')
        if action=='poll':a.add_argument('--config',type=Path,required=True)
        else:a.add_argument('--run-id',type=int)


def command(args):
    try:
        if args.directory.exists():raise ValueError('Output directory exists')
        if args.consumer_action=='poll':
            from .llm_json import strict_json
            subscriptions=config(strict_json(args.config.read_bytes()))
            if args.state_db.resolve()==args.config.resolve():raise ValueError('State/config collision')
            report=poll(subscriptions,args.state_db)
            export_run(args.state_db,args.directory,report['run_id'])
        else:report=export_run(args.state_db,args.directory,args.run_id)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if all(s['status'] in {'ready','empty','initialized'} for s in report['subscriptions']) else 1
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError):
        print('Consumer failed; check local config, independent state and output paths. Stored runs can be exported again.',file=sys.stderr)
        return 2
