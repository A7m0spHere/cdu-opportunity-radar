"""Explicit finite model batch on the existing private ledger; no collection."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.async_cli import configuration
from cdu_radar.async_queue import JobQueue, source_snapshot
from cdu_radar.async_worker import run_round


def save(path, value):
    with path.open('x',encoding='utf-8') as handle:json.dump(value,handle,ensure_ascii=False,indent=2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--state-db',required=True,type=Path)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--as-of')
    parser.add_argument('--send',action='store_true',required=True)
    selected=parser.add_mutually_exclusive_group(required=True)
    selected.add_argument('--notice-id',type=int,action='append')
    selected.add_argument('--all-notices',action='store_true')
    args=parser.parse_args(); point=reference_datetime(args.as_of)
    analyzer,policy=configuration(args.config,False)
    if analyzer.config.analysis_mode!='automatic_review_v3':raise ValueError('Use the explicit automatic v3 profile')
    with source_snapshot(args.db) as repo:
        all_ids=repo.ids()
        if args.all_notices:
            from cdu_radar.auto_publish import AutoRepository
            eligible=[item['notice_id'] for item in AutoRepository(repo.db).feed_plan(point)['items']]
        else:eligible=args.notice_id
    ids=sorted(set(eligible))
    if not ids or not set(ids)<=set(all_ids):raise ValueError('Invalid notice selection')
    args.directory.mkdir(parents=True,exist_ok=False)
    raw_hash=hashlib.sha256(args.db.read_bytes()).hexdigest();start=time.monotonic()
    with JobQueue(args.state_db,args.db,policy=policy) as queue:
        before=queue.ledger();save(args.directory/'ledger-before.json',before)
        handled=[]
        while True:
            result=run_round(queue,analyzer,reference_time=point,notice_ids=ids)
            handled.extend(result['handled'])
            save(args.directory/('batch-'+str(len(list(args.directory.glob('batch-*.json')))+1)+'.json'),result)
            print(json.dumps(dict(handled=len(handled),last=result['handled']),ensure_ascii=True),flush=True)
            if result.get('stop_reason')=='consecutive_format_failures':break
            if any(item.get('error') in {'authentication','forbidden','missing_api_key','http_error'} for item in result['handled']):break
            pending=queue.db.execute("SELECT state,next_at,lease_until FROM jobs WHERE pipeline_version=? AND notice_id IN ("+
                ','.join('?' for _ in ids)+") AND state IN ('queued','retry_wait','running','budget_blocked')",
                (analyzer.version,*ids)).fetchall()
            if not pending:break
            if not result['handled']:
                # Never sleep through a daily budget gate or spin on an owner.
                if any(row['state'] in {'budget_blocked','running'} for row in pending):break
                delay=max(0,min(row['next_at'] for row in pending)-time.time())
                time.sleep(min(delay,1))
        after=queue.ledger();save(args.directory/'ledger-after.json',after)
        current={row['id']:row for row in after['attempts']}
        assert all(current.get(row['id'])==row for row in before['attempts']), 'Previous usage ledger changed'
        states=dict(Counter(row[0] for row in queue.db.execute(
            'SELECT state FROM jobs WHERE pipeline_version=? AND notice_id IN ('+','.join('?' for _ in ids)+')',
            (analyzer.version,*ids))))
    assert raw_hash==hashlib.sha256(args.db.read_bytes()).hexdigest(),'Raw source changed'
    report=dict(pipeline_version=analyzer.version,model=analyzer.config.model,reference_time=point.isoformat(),
                selected_ids=ids,states=states,elapsed_seconds=round(time.monotonic()-start,3),
                new_request_reservations=after['request_reservations']-before['request_reservations'],
                new_dispatch_attempts=after['dispatch_attempts']-before['dispatch_attempts'],
                new_known_tokens=after['total_tokens_known']-before['total_tokens_known'],
                usage_complete=after['usage_complete'],estimated_cost=after['estimated_cost'],
                currency=after['currency'],raw_hash_preserved=True,old_ledger_preserved=True,
                official_site_requests=0,qq_deliveries=0)
    save(args.directory/'result.json',report);print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
