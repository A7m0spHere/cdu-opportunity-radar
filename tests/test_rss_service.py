"""Real loopback HTTP only; synthetic DB and a source-aligned subscriber Mock."""
from datetime import timedelta
import _socket
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import threading
import time

import feedparser
import pytest

from cdu_radar.analysis_models import reference_datetime
from cdu_radar.cli import main
from cdu_radar.llm_workflow import readonly_repository
from cdu_radar.publication import snapshot
from cdu_radar.models import Article
from cdu_radar.rss_service import FeedController,make_server
from cdu_radar.rss_synthetic import init_lab,lab,add_fixture,approve_fixture

NOW=reference_datetime('2026-10-14T12:00:00+08:00')


class Clock:
    def __init__(self):self.now=NOW;self.monotonic=0
    def __call__(self):return self.now
    def advance(self,seconds):self.now+=timedelta(seconds=seconds);self.monotonic+=seconds


@pytest.fixture
def loopback_only(monkeypatch):
    # Keep the global no-network policy for every non-loopback destination.
    def checked(method):
        def connect(sock,address):
            assert isinstance(address,tuple) and ipaddress.ip_address(address[0]).is_loopback,'Only literal loopback connections are allowed'
            return method(sock,address)
        return connect
    monkeypatch.setattr(socket.socket,'connect',checked(_socket.socket.connect))
    monkeypatch.setattr(socket.socket,'connect_ex',checked(_socket.socket.connect_ex))


@pytest.fixture
def service(tmp_path,loopback_only):
    root=tmp_path/'lab';clock=Clock();init_lab(root,ttl=3600,now=clock())
    c=FeedController(root/'synthetic.sqlite3',root/'http-state',max_age=30,clock=clock,monotonic=lambda:clock.monotonic)
    assert c.refresh()
    server=make_server(c,port=0);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.05},daemon=True);thread.start()
    try:yield root,c,server,clock
    finally:server.shutdown();server.server_close();thread.join();c.close()


def request(server,path='/approved.xml',method='GET',headers=None):
    conn=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=3)
    try:
        conn.request(method,path,headers=headers or {});r=conn.getresponse()
        return r.status,dict(r.getheaders()),r.read()
    finally:conn.close()


def revoke(root, ident=1, now=NOW):
    with lab(root) as (_,pub):
        p=pub.preview(ident,'revoked','synthetic-fixture','Offline revoke')
        with pub.db:
            pub.db.execute('BEGIN IMMEDIATE')
            pub.decide(ident,'revoked','synthetic-fixture','Offline revoke',confirm=p['confirmation_token'],now=now)


def test_http_rss_head_etag_and_readonly_health(service):
    root,c,s,clock=service
    status,h,body=request(s)
    assert status==200 and h['Content-Type']=='application/rss+xml; charset=utf-8'
    assert h['Cache-Control']=='no-store, max-age=0' and h['X-Content-Type-Options']=='nosniff'
    feed=feedparser.parse(body);assert not feed.bozo and len(feed.entries)==1 and '合成初始化' in feed.entries[0].title
    assert request(s,method='HEAD')[2]==b''
    assert request(s,headers={'If-None-Match':h['ETag']})[0]==304
    assert request(s,headers={'If-None-Match':'W/'+h['ETag']})[0]==304
    assert json.loads(request(s,'/health')[2])=={'ready':True,'status':'ready'}


@pytest.mark.parametrize('path',['/','/synthetic.sqlite3','/owner.json','/publisher.lock','/.env','/logs','/../../synthetic.sqlite3','/%2e%2e/synthetic.sqlite3','/approved.xml?file=synthetic.sqlite3','/approved.xml/','http://example.invalid/approved.xml'])
def test_no_directory_private_files_or_path_aliases(service,path):
    _,_,s,_=service
    status,_,body=request(s,path)
    assert status==404 and b'SQLite' not in body and b'database_identity' not in body


def test_malformed_absolute_target_is_rejected_without_url_parsing(service):
    _,_,s,_=service
    with socket.create_connection(('127.0.0.1',s.server_address[1]),timeout=3) as conn:
        conn.sendall(b'GET http://[ HTTP/1.0\r\n\r\n')
        assert b'404' in conn.recv(4096).split(b'\r\n',1)[0]


@pytest.mark.parametrize('method',['POST','PUT','PATCH','DELETE','OPTIONS'])
def test_no_http_mutation_endpoints(service,method):
    root,_,s,_=service
    with lab(root) as (_,p):before=[tuple(r) for r in p.db.execute('SELECT * FROM publication_reviews')]
    assert request(s,method=method)[0]==405
    with lab(root) as (_,p):assert [tuple(r) for r in p.db.execute('SELECT * FROM publication_reviews')]==before


def test_revoke_blocks_old_response_and_old_304_before_refresh(service):
    root,c,s,_=service;_,headers,old=request(s)
    revoke(root)
    assert request(s,headers={'If-None-Match':headers['ETag']})[0]==503
    assert c.snapshot is None  # The private disk cache is never the HTTP response source.
    assert c.refresh()
    status,h,body=request(s)
    assert status==200 and h['ETag']!=headers['ETag'] and not feedparser.parse(body).entries
    assert old!=body


def test_source_update_and_model_cache_corruption_remain_isolated(service):
    root,c,s,clock=service
    with lab(root) as (store,p):
        n,_=snapshot(p.db,1)
        store.save_notice('computer',n['canonical_url'],Article(n['title'],n['published_at'],n['content']+' Updated synthetic body.'))
    assert request(s)[0]==503 and c.refresh()
    assert not feedparser.parse(request(s)[2]).entries
    with lab(root) as (_,p):
        approve_fixture(p,1,3600,clock())
        n=p.repository.input(1)
        with p.db:p.db.execute('INSERT INTO notice_analyses (notice_id,content_hash,input_hash,analyzer_version,analyzed_at,payload) VALUES (?,?,?,?,?,?)',(1,n.content_hash,n.input_hash,'fixture-corrupt',clock().isoformat(),'{invalid'))
    assert c.refresh() and len(feedparser.parse(request(s)[2]).entries)==1


def test_expiry_and_watchdog_never_serve_last_good_content(service):
    _,c,s,clock=service;old=request(s)[1]['ETag']
    clock.advance(3601)
    assert request(s,headers={'If-None-Match':old})[0]==503
    assert c.refresh() and not feedparser.parse(request(s)[2]).entries


def test_validation_age_watchdog_blocks_if_worker_stalls(service):
    _,c,s,clock=service
    clock.monotonic=31
    assert request(s)[0]==503 and c.snapshot is None
    assert c.refresh() and request(s)[0]==200


def test_legacy_long_window_is_still_blocked_by_expired_original_deadline(service):
    root,c,s,clock=service
    with lab(root) as (store,p):
        n,_=snapshot(p.db,1)
        store.save_notice('computer',n['canonical_url'],Article(n['title'],None,n['content']+'\n报名截止：2026年11月1日。'))
        approve_fixture(p,1,86400,clock())
        record=p.current(1);payload=json.loads(record['approved_payload'])
        payload['valid_until']='2030-11-01T23:59:59+08:00'
        with p.db:p.db.execute('UPDATE publication_reviews SET approved_payload=? WHERE id=?',(json.dumps(payload),record['id']))
    assert c.refresh() and request(s)[0]==200
    clock.now=reference_datetime('2026-11-02')
    assert request(s)[0]==503 and c.refresh()
    assert not feedparser.parse(request(s)[2]).entries


def test_response_read_lock_orders_a_concurrent_revoke(service):
    root,c,s,clock=service;started=threading.Event();done=threading.Event();errors=[]
    def writer():
        started.set()
        try:revoke(root,now=clock())
        except Exception as e:errors.append(type(e).__name__)
        finally:done.set()
    with c.read_gate() as snapshot:
        assert snapshot
        thread=threading.Thread(target=writer);thread.start();assert started.wait(1)
        assert not done.wait(0.1)
    thread.join(3)
    assert done.is_set() and not errors and request(s)[0]==503


def test_wal_is_rejected_instead_of_serving_an_old_read_snapshot(service):
    root,c,s,_=service
    with lab(root) as (_,p):p.db.execute('PRAGMA journal_mode=WAL')
    assert request(s)[0]==503 and not c.refresh() and c.snapshot is None


def test_export_failure_database_failure_and_recovery_fail_closed(service,monkeypatch):
    root,c,s,clock=service;tag=request(s)[1]['ETag']
    from cdu_radar import rss_service
    original=rss_service.export_approved
    def failed(*args,**kwargs):raise OSError('private-fixture-do-not-expose')
    monkeypatch.setattr(rss_service,'export_approved',failed)
    assert not c.refresh() and request(s,headers={'If-None-Match':tag})[0]==503
    assert b'private-fixture' not in request(s)[2] and not c.output.exists()
    monkeypatch.setattr(rss_service,'export_approved',original)
    assert c.refresh() and request(s)[1]['ETag']==tag
    db=root/'synthetic.sqlite3';saved=root/'saved.sqlite3';db.rename(saved)
    try:
        assert request(s,headers={'If-None-Match':tag})[0]==503
        assert request(s,'/health')[0]==503 and not db.exists()
    finally:saved.rename(db)
    assert c.refresh() and request(s)[0]==200


def test_disk_tampering_and_response_socket_error_do_not_expose_or_break_gate(service):
    _,c,s,_=service;original=request(s)[2]
    c.output.write_bytes(b'private-fake-SQLite-data')
    assert request(s)[2]==original
    with pytest.raises(BrokenPipeError):
        with c.read_gate() as snap:
            assert snap;raise BrokenPipeError('synthetic disconnected client')
    assert request(s)[0]==200


def test_busy_or_stuck_publisher_returns_503_promptly(service):
    _,c,s,_=service;etag=request(s)[1]['ETag']
    started=time.monotonic()
    with c.lock:
        status,_,body=request(s,headers={'If-None-Match':etag})
    assert status==503 and b'RSS temporarily unavailable' in body
    assert time.monotonic()-started<1.5


def wait_for(predicate,seconds=3):
    limit=time.monotonic()+seconds
    while time.monotonic()<limit:
        if predicate():return
        time.sleep(0.02)
    raise AssertionError('Background refresh did not finish')


def test_periodic_fallback_refreshes_without_requests(tmp_path):
    root=tmp_path/'lab';clock=Clock();init_lab(root,now=clock())
    c=FeedController(root/'synthetic.sqlite3',root/'state',clock=clock)
    try:
        c.start(0.1);initial=c.refreshes
        revoke(root,now=clock())
        wait_for(lambda:c.refreshes>initial and c.snapshot and not feedparser.parse(c.snapshot.body).entries)
        with lab(root) as (store,p):add_fixture(store,p,now=clock())
        wait_for(lambda:c.snapshot and len(feedparser.parse(c.snapshot.body).entries)==1)
        clock.advance(3601)
        wait_for(lambda:c.snapshot and not feedparser.parse(c.snapshot.body).entries)
    finally:c.close()


def test_read_gate_wakes_background_before_long_poll_interval(service):
    root,c,s,clock=service;c.start(10)
    revoke(root,now=clock())
    assert request(s)[0]==503
    wait_for(lambda:c.snapshot and not feedparser.parse(c.snapshot.body).entries)
    assert request(s)[0]==200


def test_restart_does_not_trust_disk_and_preserves_guid_pubdate(service):
    root,c,s,clock=service;old=feedparser.parse(request(s)[2]).entries[0]
    c.close();c.output.write_bytes(b'private-stale-cache')
    new=FeedController(root/'synthetic.sqlite3',root/'http-state',clock=clock)
    try:
        assert new.snapshot is None and new.refresh()
        with new.read_gate() as snap:entry=feedparser.parse(snap.body).entries[0]
        assert (entry.id,entry.published)==(old.id,old.published)
    finally:new.close()


def test_lease_nonempty_directory_schema_and_nonloopback_rejections(tmp_path):
    root=tmp_path/'lab';init_lab(root,now=NOW)
    c=FeedController(root/'synthetic.sqlite3',root/'state',clock=lambda:NOW)
    try:
        with pytest.raises((OSError,ValueError)):FeedController(root/'synthetic.sqlite3',root/'state')
        with pytest.raises(ValueError):make_server(c,'0.0.0.0',0)
        with pytest.raises(ValueError):FeedController(root/'synthetic.sqlite3',root)
        bad=tmp_path/'not-owned';bad.mkdir();(bad/'private.txt').write_text('fixture')
        with pytest.raises(ValueError):FeedController(root/'synthetic.sqlite3',bad)
        assert [p.name for p in bad.iterdir()]==['private.txt']
        with lab(root) as (_,p):p.db.execute('PRAGMA user_version=4')
        assert not c.refresh()
    finally:c.close()


def test_synthetic_commands_refuse_existing_user_directory_or_forged_marker(tmp_path,capsys):
    root=tmp_path/'existing';root.mkdir();(root/'private.sqlite3').write_bytes(b'private-fixture')
    assert main(['rss-synthetic','init','--directory',str(root)])==2
    assert (root/'private.sqlite3').read_bytes()==b'private-fixture'
    fresh=tmp_path/'lab';assert main(['rss-synthetic','init','--directory',str(fresh)])==0
    assert main(['rss-synthetic','add','--directory',str(fresh)])==0
    with lab(fresh) as (_,p):assert len(p.repository.ids())==2 and not p.db.execute('SELECT count(*) FROM manual_labels').fetchone()[0]
    (fresh/'synthetic-owner.json').write_text('{}')
    assert main(['rss-synthetic','revoke','1','--directory',str(fresh)])==2


def test_separate_synthetic_labs_do_not_share_source_identity(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';init_lab(a,now=NOW);init_lab(b,now=NOW)
    with lab(a) as (_,p):first=p.current(1)['guid']
    with lab(b) as (_,p):second=p.current(1)['guid']
    assert first!=second


class RSSHubPolicyMock:
    """Protocol expectation Mock, not the installed plugin or its implementation."""
    def __init__(self,path):
        self.path=path;self.seen=set(json.loads(path.read_text()) if path.exists() else [])
    def poll(self,server):
        status,_,body=request(server)
        if status!=200:return {'status':'fetch_error','deliveries':[]}
        parsed=feedparser.parse(body)
        if parsed.bozo or not parsed.entries:return {'status':'empty_or_invalid','deliveries':[]}
        identities=[e.get('id') or e.link for e in parsed.entries]
        bootstrap=not self.seen
        deliveries=[] if bootstrap else [i for i in identities if i not in self.seen]
        self.seen.update(identities);self.path.write_text(json.dumps(sorted(self.seen)))
        return {'status':'bootstrapped' if bootstrap else 'updated' if deliveries else 'unchanged','deliveries':deliveries}


def test_nonempty_bootstrap_new_item_dedup_restart_and_failures(service,tmp_path):
    root,c,s,clock=service;state=tmp_path/'mock-history.json';mock=RSSHubPolicyMock(state)
    assert mock.poll(s)=={'status':'bootstrapped','deliveries':[]}
    with lab(root) as (store,p):add_fixture(store,p,now=clock())
    assert request(s)[0]==503 and c.refresh()
    assert len({e.published for e in feedparser.parse(request(s)[2]).entries})==1  # Same-second entries still deduplicate by stable identity.
    fresh=mock.poll(s);assert len(fresh['deliveries'])==1
    assert not mock.poll(s)['deliveries']
    restarted=RSSHubPolicyMock(state);assert not restarted.poll(s)['deliveries']
    with lab(root) as (store,p):
        n,_=snapshot(p.db,2);store.save_notice('computer',n['canonical_url'],Article(n['title'],None,n['content']+' Synthetic correction.'))
        approve_fixture(p,2,3600,clock())
    assert c.refresh() and not restarted.poll(s)['deliveries']
    before=state.read_bytes();c.invalidate('synthetic_failure')
    assert restarted.poll(s)['status']=='fetch_error' and state.read_bytes()==before


def test_empty_feed_does_not_establish_history_and_first_item_is_bootstrap(service,tmp_path):
    root,c,s,clock=service;revoke(root);assert c.refresh()
    state=tmp_path/'empty-history.json';mock=RSSHubPolicyMock(state)
    assert mock.poll(s)['status']=='empty_or_invalid' and not state.exists()
    with lab(root) as (store,p):add_fixture(store,p,now=clock())
    assert c.refresh() and mock.poll(s)=={'status':'bootstrapped','deliveries':[]}


def test_cli_rejects_public_binding_without_claiming_state(tmp_path):
    root=tmp_path/'lab';init_lab(root,now=NOW);state=root/'never-created'
    assert main(['serve-approved-rss','--db',str(root/'synthetic.sqlite3'),'--state-dir',str(state),'--bind','0.0.0.0'])==2
    assert not state.exists()
