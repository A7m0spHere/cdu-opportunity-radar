"""Replay M4-A's stored parsed notices offline into a NEW isolated directory.

No source requests, model calls, approvals or production connections. This does
not certify that stored original links are currently reachable on the Internet.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import feedparser

from cdu_radar.analysis_models import reference_datetime
from cdu_radar.auto_publish import AutoRepository
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.models import Article, ArticleLink
from cdu_radar.rss_service import make_server
from cdu_radar.storage import Store


def snapshot(db):
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {name:[tuple(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
        for name in ('notices','notice_links','notice_sources','manual_labels','analysis_reviews',
                     'publication_reviews','publication_state','publication_events','notice_analyses') if name in names}


def run(source, directory, as_of):
    source=Path(source).resolve();directory=Path(directory).resolve()
    if not source.is_file():raise ValueError('M4-A source DB not found')
    if directory.exists():raise ValueError('Choose a NEW isolated output directory')
    directory.mkdir(parents=True)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    original=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    target=directory/'replay.sqlite3'
    try:
        before=snapshot(original)
        with sqlite3.connect(target) as copy:original.backup(copy)
    finally:original.close()
    point=reference_datetime(as_of)
    with Store(target) as store:
        # These are real stored parser outputs with their original collection
        # times. No dates, contents, successful network checks or approvals are invented.
        rows=store.db.execute('SELECT * FROM notices ORDER BY id').fetchall()
        for row in rows:
            links={kind:tuple(ArticleLink(r['name'],r['url']) for r in store.links(row['id'],kind)) for kind in ('body','attachment')}
            article=Article(row['title'],row['published_at'],row['content'],links['body'],links['attachment'],row['content_source_id'])
            origin=store.db.execute('SELECT published_at FROM notice_sources WHERE notice_id=? AND source_id=?',(row['id'],row['source_id'])).fetchone()
            result=store.save_notice(row['source_id'],row['canonical_url'],article,seen_at=row['last_seen_at'],
                discovery_published_at=origin[0] if origin else None)
            if result!='unchanged':raise AssertionError('Replay must not change stored real notice data')
        if snapshot(store.db)!=before:raise AssertionError('Raw data or historical labels/reviews changed')
        plan=AutoRepository(store.db).feed_plan(point)
        tables={r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts={t:store.db.execute('SELECT count(*) FROM '+t).fetchone()[0]
            for t in ('publication_reviews','publication_state','publication_events','manual_labels','analysis_reviews') if t in tables}
    c=AutoFeedController(target,directory/'http-state',clock=lambda:point)
    s=None;t=None;responses=[]
    try:
        if not c.refresh():raise AssertionError('Replay refresh failed')
        s=make_server(c,port=0)
        t=threading.Thread(target=s.serve_forever,kwargs={'poll_interval':0.02},daemon=True);t.start()
        for path in ('/feed.xml','/current.xml','/history.xml','/approved.xml'):
            conn=http.client.HTTPConnection('127.0.0.1',s.server_address[1],timeout=5)
            try:
                conn.request('GET',path);r=conn.getresponse();body=r.read()
                if r.status==200:
                    parsed=feedparser.parse(body)
                    if parsed.bozo:raise AssertionError('Invalid RSS')
                    (directory/path.lstrip('/')).write_bytes(body)
                responses.append({'path':path,'status':r.status,'items':len(parsed.entries) if r.status==200 else None})
            finally:conn.close()
        identities=[(e.id,e.published) for e in feedparser.parse(c.snapshot.body).entries]
        if not c.refresh():raise AssertionError('Repeat refresh failed')
        if identities!=[(e.id,e.published) for e in feedparser.parse(c.snapshot.body).entries]:raise AssertionError('GUID/pubDate changed')
        with sqlite3.connect(target) as copy:
            if snapshot(copy)!=before:raise AssertionError('Publication modified historical evidence')
    finally:
        if s:s.shutdown();s.server_close()
        if t:t.join(5)
        c.close()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=original_hash:raise AssertionError('M4-A source DB changed')
    report={'mode':'offline_stored_parser_output_replay','source_db':str(source),'source_sha256':original_hash,
        'source_db_unchanged':True,'raw_and_historical_rows_unchanged':True,'reference_time':point.isoformat(),
        'input_notices':len(rows),'published':len(plan['items']),'statuses':plan['statuses'],'listings':plan['listings'],
        'excluded':plan['excluded'],'legacy_table_counts':counts,'responses':responses,
        'entries':[{'notice_id':i['notice_id'],'url':i['source_url'],'source':i['source_name'],
            'source_date':i['source_published_date'],'category':i['category'],'status':i['registration_status'],
            'listing':i['listing'],'deadline':i['facts']['registration_deadline']} for i in plan['items']],
        'external_requests':0,'paid_llm_calls':0,'qq_calls':0,'new_approvals':0,
        'live_link_reachability_verified':False,'http_stopped':True}
    (directory/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-db',type=Path,required=True)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--as-of',required=True,help='Explicit offline reference time; no system clock change')
    args=p.parse_args()
    result=run(args.source_db,args.directory,args.as_of)
    print(json.dumps({k:result[k] for k in ('mode','input_notices','published','statuses','listings','excluded','responses','source_db_unchanged')},ensure_ascii=True))
