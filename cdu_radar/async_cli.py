"""Explicit local CLI. No default model requests and no public status endpoint."""
from dataclasses import fields
import json
from pathlib import Path
import sqlite3
import sys
import time

from .analysis_models import reference_datetime
from .async_contract import AsyncAnalyzer
from .async_queue import JobQueue, QueuePolicy
from .llm import LLMConfig
from .llm_json import strict_json
from .taxonomy import TYPES, TOPICS


def register(sub):
    parser=sub.add_parser('analysis-queue',help='Private async analysis; scan/status/ledger never call models')
    actions=parser.add_subparsers(dest='queue_action',required=True)
    for name in ['scan','work','status','ledger','revalidate']:
        cmd=actions.add_parser(name)
        cmd.add_argument('--db',type=Path,required=True,help='Existing raw DB, opened read-only')
        cmd.add_argument('--state-db',type=Path,required=True,help='Independent private M7 job database')
        cmd.add_argument('--config',type=Path,help='Non-secret JSON: llm, queue, prescreen')
        cmd.add_argument('--mock',action='store_true',help='Explicit synthetic offline mode; never reads keys or uses network')
        if name in {'scan','work','revalidate'}:cmd.add_argument('--notice-id',action='append',type=int,required=name=='revalidate',help='Restrict both scan and dispatch to selected notice IDs; repeatable')
        if name=='revalidate':cmd.add_argument('--from-pipeline',required=True,help='Exact prior pipeline; same provider/prompt/schema required; no model calls')
        if name=='work':
            cmd.add_argument('--send',action='store_true',help='Explicitly authorize provider requests under configured budget')
            cmd.add_argument('--watch',action='store_true',help='Poll and process bounded rounds until interrupted')
            cmd.add_argument('--interval',type=float,default=60)
            cmd.add_argument('--as-of',help='Date/time reference for deterministic offline replay')
        if name=='status':cmd.add_argument('--include-results',action='store_true',help='Private sanitized suggestions with quoted evidence')


def configuration(path, mock):
    data=strict_json(path.read_bytes()) if path else {}
    if not isinstance(data,dict) or set(data)-{'llm','queue','prescreen'}:raise ValueError('Invalid M7 configuration')
    llm=data.get('llm',{})
    if not isinstance(llm,dict) or set(llm)-{f.name for f in fields(LLMConfig)}:raise ValueError('Invalid LLM configuration')
    if mock:
        llm={**llm,'base_url':'https://offline.invalid/v1','model':'offline-mock','key_env':'CDU_OFFLINE_UNUSED'}
    elif not path:raise ValueError('Provider mode requires an explicit non-secret config file')
    llm={'retries':0,'response_format':'json_schema',**llm}
    config=LLMConfig(**llm)
    # Bound transport/payload even if a user accidentally supplies huge values.
    if config.timeout>1800 or config.max_input_chars>100000 or config.max_output_tokens>16000:
        raise ValueError('M7 request limits exceed safety bounds')
    raw_policy=data.get('queue',{})
    if not isinstance(raw_policy,dict):raise ValueError('Invalid queue configuration')
    policy=QueuePolicy(**raw_policy)
    pre=data.get('prescreen',{'types':[],'topics':[]})
    if not isinstance(pre,dict) or set(pre)!={'types','topics'}:raise ValueError('Invalid prescreen')
    for key,choices in [('types',TYPES),('topics',TOPICS)]:
        values=pre[key]
        if not isinstance(values,list) or any(not isinstance(v,str) or v not in choices for v in values) or len(values)!=len(set(values)):
            raise ValueError('Invalid prescreen values')
        pre[key]=sorted(values)
    from .review_contract import ReviewAnalyzer
    from .automatic_review import AutomaticReviewAnalyzer
    kind=AutomaticReviewAnalyzer if config.analysis_mode=='automatic_review_v3' else ReviewAnalyzer if config.analysis_mode=='classification_review_v2' else AsyncAnalyzer
    return kind(config,mock=mock,prescreen=pre),policy


def command(args):
    try:
        if args.queue_action=='work' and (args.mock==args.send):
            raise ValueError('work requires exactly one of --mock or --send')
        if args.queue_action=='work' and (not 1<=args.interval<=3600):raise ValueError('Invalid worker interval')
        # status without config is read-only inspection of all recorded versions.
        analyzer,policy=configuration(args.config,args.mock) if args.config or args.mock or args.queue_action in {'scan','work','revalidate'} else (None,QueuePolicy())
        with JobQueue(args.state_db,args.db,policy=policy,read_only=args.queue_action in {'status','ledger'}) as queue:
            if args.queue_action=='scan':result=queue.scan(analyzer,notice_ids=args.notice_id)
            elif args.queue_action=='revalidate':result=queue.revalidate(analyzer,args.notice_id,args.from_pipeline)
            elif args.queue_action=='status':result=queue.report(analyzer=analyzer,include_results=args.include_results)
            elif args.queue_action=='ledger':result=queue.ledger()
            else:
                from .async_worker import run_round
                while True:
                    point=reference_datetime(args.as_of)
                    result=run_round(queue,analyzer,reference_time=point,notice_ids=args.notice_id)
                    if not args.watch:break
                    print(json.dumps(result,ensure_ascii=False),flush=True)
                    time.sleep(args.interval)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except KeyboardInterrupt:return 0
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error):
        # Never echo values from config/provider/notice exceptions.
        print('M7 queue command failed; check configuration and private database paths.',file=sys.stderr);return 2
