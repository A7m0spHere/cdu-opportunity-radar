"""Rollback-lock contention with real writers; no HTTP or model mocks for locks."""
from contextlib import contextmanager
from threading import Event,Thread
import sqlite3
import time

import pytest

from cdu_radar.auto_service import AutoFeedController
from cdu_radar.recheck import collect_cycle
from cdu_radar.http import Fetcher
from test_auto_publish import auto_service,add
from test_rss_v1 import setup_featured
from test_rss_service import loopback_only,request


@contextmanager
def pending_writer(path,sql):
    entered=Event();done=Event();errors=[]
    def write():
        try:
            with sqlite3.connect(path,timeout=8) as db:
                db.execute(sql);entered.set();db.commit()
        except BaseException as exc:errors.append(exc)
        finally:done.set()
    worker=Thread(target=write);worker.start();assert entered.wait(2)
    try:
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            try:
                with sqlite3.connect(path,timeout=0) as probe:probe.execute('SELECT name FROM sqlite_master').fetchall()
            except sqlite3.OperationalError:break
            time.sleep(.005)
        else:pytest.fail('Writer did not acquire its pending commit lock')
        yield done
    finally:
        # Caller must release the held reader before joining this writer.
        worker.join(.01)
    return worker,errors


def test_original_response_reuses_read_transaction_behind_pending_writer(auto_service):
    store,c,*_=auto_service;path=store.db.execute('PRAGMA database_list').fetchone()[2]
    with c.read_gate() as snapshot:
        assert snapshot is not None
        with pending_writer(path,'UPDATE notices SET title=title') as done:
            with c.reader() as first,c.reader() as second:
                assert first is second
                assert first.db.execute('SELECT count(*) FROM notices').fetchone()[0]==1
            with c.response_gate(snapshot,'/rss/v2/all.xml?audience=undergraduate') as response:
                assert response.body and not done.is_set()
    assert done.wait(2)


def test_featured_response_reuses_queue_reader_behind_pending_writer(auto_service,tmp_path):
    _,c,*_=auto_service;_,state=setup_featured(auto_service,tmp_path)
    with c.read_gate() as snapshot:
        with c.reviewed_gate(snapshot,'/rss/v2/featured.xml?audience=undergraduate'):
            with pending_writer(state,'UPDATE jobs SET updated_at=updated_at') as done:
                with c.response_gate(snapshot,'/rss/v2/featured.xml?audience=undergraduate') as response:
                    assert response.body and not done.is_set()
    assert done.wait(2)


def test_refresh_does_not_request_a_raw_write_after_publications_reserved(auto_service,monkeypatch):
    _,c,*_=auto_service
    @contextmanager
    def forbidden():pytest.fail('Refresh requested a redundant raw write');yield
    monkeypatch.setattr(c,'writer',forbidden)
    assert c.refresh()


def test_busy_refresh_cannot_extend_snapshot_lifetime(auto_service,monkeypatch):
    _,c,_,_,mono=auto_service;old=c.snapshot
    @contextmanager
    def locked():
        exc=sqlite3.OperationalError('hidden');exc.sqlite_errorcode=sqlite3.SQLITE_BUSY;exc.sqlite_errorname='SQLITE_BUSY'
        raise exc;yield
    monkeypatch.setattr(c,'reader',locked)
    assert not c.refresh() and c.snapshot is old
    mono[0]+=c.max_age+1
    assert not c.refresh() and c.snapshot is None


def test_two_hour_inventory_capacity_grows_but_preserves_wire_budget(store,sources,monkeypatch):
    from cdu_radar import recheck as module
    from cdu_radar import collect as collector
    # Old fixed 12 checks/round could not cover 192 originals every 24h.
    for i in range(192):add(store,url=f'https://computer.cdu.edu.cn/info/1036/{880001+i}.htm')
    calls=[]
    monkeypatch.setattr(module,'recheck',lambda store,fetcher,sources,**kw:calls.append((kw['limit'],fetcher.request_limit)) or [])
    monkeypatch.setattr(collector,'collect',lambda *a,**kw:[])
    with Fetcher(interval=0,retries=0,request_limit=100) as fetcher:
        result=collect_cycle(store,fetcher,sources,recheck_limit=12,request_budget=48,cycle_interval=7200)
        assert result['recheck_target']>=24 and calls[0][1]==32
        assert fetcher.request_limit==100


def test_actual_adaptive_rechecks_leave_discovery_budget(store,sources,sample):
    import httpx
    source=next(s for s in sources if s.id=='computer')
    for i in range(42):add(store,url=f'https://computer.cdu.edu.cn/info/1036/{890001+i}.htm')
    def serve(req):
        if req.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=sample('computer_article.html' if '/info/' in req.url.path else 'computer_list.html'))
    with Fetcher(transport=httpx.MockTransport(serve),interval=0,retries=0) as fetcher:
        result=collect_cycle(store,fetcher,[source],limit=1,recheck_limit=12,request_budget=48,cycle_interval=7200)
    assert len(result['rechecks'])>12 and result['requests']<=48
    assert result['discovery'][0]['inserted']==1
