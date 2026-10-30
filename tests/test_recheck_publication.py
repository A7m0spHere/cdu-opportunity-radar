"""Healthy rechecks keep HTTP available without weakening live source gates."""
from contextlib import contextmanager
from datetime import timedelta
import json
import sqlite3

import feedparser
import pytest

from cdu_radar.auto_publish import AutoRepository, MAX_SOURCE_AGE
from cdu_radar.models import Article
from test_auto_publish import auto_service, URL
from test_contention import pending_writer
from test_rss_service import loopback_only, request
from test_rss_v1 import setup_featured


def renew(store, point):
    row=store.db.execute('SELECT * FROM notices WHERE canonical_url=?',(URL,)).fetchone()
    article=Article(row['title'],row['published_at'],row['content'])
    assert store.save_notice('computer',URL,article,seen_at=point.isoformat(),discovered=False)=='unchanged'


def test_healthy_recheck_keeps_all_routes_and_subscription_identity(auto_service,tmp_path,monkeypatch):
    store,c,s,point,mono=auto_service
    analyzer,state=setup_featured(auto_service,tmp_path)
    monkeypatch.setattr(type(analyzer),'analyze',lambda *a,**kw:pytest.fail('HTTP model call'))
    monkeypatch.setattr(analyzer.provider,'complete',lambda *a,**kw:pytest.fail('HTTP provider call'))
    routes=('/feed.xml','/rss/v1/all.xml','/rss/v1/featured.xml',
            '/rss/v2/all.xml?audience=undergraduate','/rss/v2/featured.xml?audience=undergraduate')
    before={path:request(s,path) for path in routes}
    assert all(status==200 for status,_,_ in before.values())
    old=c.snapshot
    publications=list(store.db.execute('SELECT * FROM auto_publications ORDER BY notice_id'))
    with sqlite3.connect(c.subscription_path) as db:
        first=db.execute('SELECT * FROM first_featured ORDER BY guid').fetchall()
        token=db.execute('SELECT token FROM collection').fetchone()[0]
    point[0]+=timedelta(minutes=1);mono[0]=7.0
    renew(store,point[0])
    files=(c.db,state,c.subscription_path)
    bytes_before={path:path.read_bytes() for path in files}
    assert AutoRepository(store.db).feed_plan(point[0])['plan_hash']==old.plan_hash
    assert request(s,'/health')[0]==200
    for path in routes:
        status,_,body=request(s,path)
        assert status==200
        previous=feedparser.parse(before[path][2]).entries
        current=feedparser.parse(body).entries
        assert [(x.id,x.published) for x in current]==[(x.id,x.published) for x in previous]
        assert request(s,path,headers={'If-None-Match':before[path][1]['ETag']})[0] in {200,304}
    for path in ('/api/notices','/api/notices/v2','/api/notices/v3','/api/notices/v4?audience=all'):
        status,_,body=request(s,path)
        assert status==200
        payload=json.loads(body)
        item=next(i for i in payload['items'] if i['source_url']==URL)
        assert item['source_checked_at']==point[0].isoformat()
        assert payload['valid_for_ms']==23000
        assert payload['refreshed_at']==old.data['refreshed_at']
    assert c.snapshot is old and not c.gate_failures
    assert all(path.read_bytes()==data for path,data in bytes_before.items()),'HTTP must remain read-only'
    assert list(store.db.execute('SELECT * FROM auto_publications ORDER BY notice_id'))==publications
    with sqlite3.connect(c.subscription_path) as db:
        assert db.execute('SELECT * FROM first_featured ORDER BY guid').fetchall()==first
        assert db.execute('SELECT token FROM collection').fetchone()[0]==token


def test_metadata_projection_keeps_withdrawal_writer_out_until_response_finishes(auto_service):
    store,c,s,point,_=auto_service
    point[0]+=timedelta(minutes=1);renew(store,point[0])
    with c.read_gate() as snapshot:
        assert snapshot and snapshot.data['items'][0]['source_checked_at']==point[0].isoformat()
        with pending_writer(c.db,"UPDATE notice_health SET status='withdrawn' WHERE notice_id=1") as done:
            with c.response_gate(snapshot,'/rss/v2/all.xml?audience=all') as response:
                assert response.body and not done.is_set()
    assert done.wait(2)
    assert request(s,'/rss/v2/all.xml?audience=all',headers={'If-None-Match':'*'})[0]==503


@pytest.mark.parametrize('fault',('body','withdrawn','unavailable','expired','future','validation_age'))
def test_recheck_never_bypasses_real_invalidation(auto_service,fault):
    store,c,s,point,mono=auto_service
    point[0]+=timedelta(minutes=1);renew(store,point[0])
    assert request(s,'/health')[0]==200
    if fault=='validation_age':mono[0]=c.max_age+1
    else:
        with store.db:
            if fault=='body':store.db.execute("UPDATE notices SET content=content||' changed' WHERE id=1")
            elif fault in {'withdrawn','unavailable'}:store.db.execute('UPDATE notice_health SET status=? WHERE notice_id=1',(fault,))
            else:
                checked=point[0]-MAX_SOURCE_AGE-timedelta(seconds=1) if fault=='expired' else point[0]+timedelta(seconds=1)
                store.db.execute('UPDATE notice_health SET checked_at=? WHERE notice_id=1',(checked.isoformat(),))
    for route in ('/health','/rss/v2/all.xml?audience=all','/api/notices/v4?audience=all'):
        assert request(s,route,headers={'If-None-Match':'*'})[0]==503
    if fault=='validation_age':
        assert c.gate_failures['validation_expired']==1
        assert c.gate_failures['snapshot_missing']==2


def test_renewal_between_snapshot_build_and_featured_persistence_is_safe(auto_service,tmp_path,monkeypatch):
    store,c,s,point,_=auto_service
    setup_featured(auto_service,tmp_path)
    original=c.selection_gate
    @contextmanager
    def renewed(snapshot):
        point[0]+=timedelta(minutes=1);renew(store,point[0])
        with original(snapshot) as value:yield value
    with monkeypatch.context() as patch:
        patch.setattr(c,'selection_gate',renewed)
        assert c.refresh() and c.subscription_ready
    assert request(s,'/rss/v2/featured.xml?audience=all')[0]==200
    assert c.failures==0 and not c.gate_failures
