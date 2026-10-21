"""Private, zero-request multi-axis benchmark commands."""
import csv
import json
from pathlib import Path
import sqlite3
import sys

from .quality_benchmark import export_package, import_labels, evaluate_package
from .featured_benchmark import save_new


def register(sub):
    p=sub.add_parser('quality-benchmark',help='Frozen human review of importance, class and student scope; offline')
    actions=p.add_subparsers(dest='quality_action',required=True)
    for name in ['export','import','evaluate']:
        a=actions.add_parser(name)
        a.add_argument('--directory',type=Path,required=True)
        if name in {'export','evaluate'}:a.add_argument('--db',type=Path,required=True)
        if name=='export':
            a.add_argument('--as-of',required=True)
            a.add_argument('--limit',type=int,default=50)
            a.add_argument('--seed',default='m10-quality-v1')
            a.add_argument('--analysis-state',type=Path)
            a.add_argument('--analysis-config',type=Path)
        if name in {'import','evaluate'}:a.add_argument('--labels-db',type=Path,required=name=='import')
        if name=='import':
            a.add_argument('--input',type=Path,required=True)
            a.add_argument('--labeler',required=True)
            a.add_argument('--allow-synthetic',action='store_true')
        if name=='evaluate':
            a.add_argument('--split',choices=['holdout','development','all'],default='holdout')
            a.add_argument('--include-synthetic',action='store_true')
            a.add_argument('--output',type=Path,required=True,help='New JSON file, never overwrite')


def command(args):
    try:
        if args.quality_action=='export':
            analyzer=None
            if bool(args.analysis_state)!=bool(args.analysis_config):raise ValueError('Cache state and config must be paired')
            if args.analysis_state:
                from .async_cli import configuration
                analyzer,_=configuration(args.analysis_config,False)
            result=export_package(args.db,args.directory,reference_time=args.as_of,limit=args.limit,seed=args.seed,
                state_db=args.analysis_state,analyzer=analyzer)
        elif args.quality_action=='import':
            result=import_labels(args.directory,args.input,args.labels_db,labeler=args.labeler,allow_synthetic=args.allow_synthetic)
        else:
            result=evaluate_package(args.directory,raw_db=args.db,labels_db=args.labels_db,split=args.split,include_synthetic=args.include_synthetic)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            save_new(args.output,json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,csv.Error):
        print('Quality benchmark failed; check frozen versions, new human labels and independent private paths.',file=sys.stderr);return 2
