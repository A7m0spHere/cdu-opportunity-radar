"""Offline featured benchmark CLI; never calls a model or edits old labels."""
import csv
import json
from pathlib import Path
import sqlite3
import sys

from .featured import FeaturedPolicy
from .featured_benchmark import export_dataset, import_labels, evaluate_dataset, save_new


def register(sub):
    parser=sub.add_parser('featured-benchmark',help='Frozen new-guideline labels and offline featured evaluation')
    actions=parser.add_subparsers(dest='featured_action',required=True)
    export=actions.add_parser('export')
    export.add_argument('--db',type=Path,required=True)
    export.add_argument('--directory',type=Path,required=True,help='New private directory, never overwrite')
    export.add_argument('--limit',type=int,default=36)
    export.add_argument('--seed',default='featured-v1')
    export.add_argument('--as-of',required=True)
    export.add_argument('--model-threshold',choices=['low','normal','high'],default='normal')
    export.add_argument('--rule-threshold',type=int,choices=range(1,5),default=3)
    ingest=actions.add_parser('import')
    ingest.add_argument('--dataset',type=Path,required=True)
    ingest.add_argument('--input',type=Path,required=True)
    ingest.add_argument('--labels-db',type=Path,required=True)
    ingest.add_argument('--labeler',required=True)
    ingest.add_argument('--allow-synthetic',action='store_true')
    evaluate=actions.add_parser('evaluate')
    evaluate.add_argument('--dataset',type=Path,required=True)
    evaluate.add_argument('--db',type=Path,required=True)
    evaluate.add_argument('--labels-db',type=Path)
    evaluate.add_argument('--analysis-state',type=Path)
    evaluate.add_argument('--analysis-config',type=Path)
    evaluate.add_argument('--analysis-mock',action='store_true',help='Explicit offline testing only, recorded in report')
    evaluate.add_argument('--include-synthetic',action='store_true')
    evaluate.add_argument('--split',choices=['holdout','development','all'],default='holdout')
    evaluate.add_argument('--output',type=Path,help='New JSON report; refuses overwrite')


def command(args):
    try:
        if args.featured_action=='export':
            result=export_dataset(args.db,args.directory,limit=args.limit,seed=args.seed,reference_time=args.as_of,
                policy=FeaturedPolicy(model_threshold=args.model_threshold,rule_threshold=args.rule_threshold))
        elif args.featured_action=='import':
            result=import_labels(args.dataset,args.input,args.labels_db,labeler=args.labeler,allow_synthetic=args.allow_synthetic)
        else:
            analyzer=None
            if args.analysis_state:
                from .async_cli import configuration
                analyzer,_=configuration(args.analysis_config,args.analysis_mock)
            elif args.analysis_config or args.analysis_mock:raise ValueError('Analysis state required')
            result=evaluate_dataset(args.dataset,args.db,labels_db=args.labels_db,state_db=args.analysis_state,
                analyzer=analyzer,split=args.split,include_synthetic=args.include_synthetic)
            result['analysis_mode']='offline_mock' if analyzer and analyzer.mock else 'provider_cache_only' if analyzer else 'not_configured'
            if args.output:
                args.output.parent.mkdir(parents=True,exist_ok=True)
                save_new(args.output,json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,csv.Error):
        print('Featured benchmark failed; check frozen dataset, label confirmation and private paths.',file=sys.stderr);return 2
