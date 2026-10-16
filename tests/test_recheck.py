"""Offline scheduler regressions, sharing the real publication and HTTP gates."""
from dataclasses import replace
from datetime import timedelta
from threading import Event

import httpx
import pytest

from cdu_radar.auto_publish import AutoRepository
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher, RequestBudgetExceeded
from cdu_radar.recheck import collect_cycle, initialize, recheck, stamp, CLAIM_TTL
from cdu_radar.storage import Store
from test_auto_publish import NOW, URL, add, auto_service, entries
from test_rss_service import loopback_only, request


def mock_fetcher(sample, *, response=None, handler=None, **kwargs):
    def serve(req):
        if req.url.path == '/robots.txt':
            return httpx.Response(404)
        if handler:
            return handler(req)
        return response if response is not None else httpx.Response(200, content=sample('computer_article.html'))
    return Fetcher(transport=httpx.MockTransport(serve), interval=0, retries=0, **kwargs)


def queue(store):
    return dict(store.db.execute('SELECT * FROM notice_rechecks ORDER BY notice_id LIMIT 1').fetchone())


def test_off_homepage_recheck_renews_health_after_restart(store, sources, sample):
    add(store)
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    at = NOW + timedelta(hours=71)
    # Reopen the DB: no list request or in-memory cursor is needed.
    with Store(path) as reopened, mock_fetcher(sample, event_sink=reopened.request_event) as fetcher:
        result = recheck(reopened, fetcher, sources, clock=lambda:at)
        assert result[0]['result'] == 'updated'
        assert fetcher.request_count == 2
        assert all('/info/' in r[0] or r[0].endswith('/robots.txt') for r in reopened.db.execute('SELECT url FROM request_events'))
    assert store.db.execute('SELECT checked_at FROM notice_health').fetchone()[0] == at.isoformat()
    assert AutoRepository(store.db).feed_plan(at+timedelta(hours=2))['items']
    assert queue(store)['failures'] == 0


@pytest.mark.parametrize('mode', ['maintenance','gone','timeout'])
def test_failure_invalidates_without_renewing_success_and_backs_off(store, sources, sample, mode):
    add(store)
    at = NOW + timedelta(hours=25)
    def handler(req):
        if mode=='timeout':
            raise httpx.ReadTimeout('synthetic timeout', request=req)
        return httpx.Response(410) if mode=='gone' else httpx.Response(200,text='<html>维护中</html>',headers={'content-type':'text/html'})
    with mock_fetcher(sample, handler=handler) as fetcher:
        result=recheck(store,fetcher,sources,clock=lambda:at)[0]
        assert result['result']=='failed'
        if mode=='maintenance':
            assert result['error_category'] in {'html_parse','source_maintenance'}
        count=fetcher.request_count
        assert recheck(store,fetcher,sources,clock=lambda:at+timedelta(minutes=30))==[]
        assert fetcher.request_count==count
        assert recheck(store,fetcher,sources,clock=lambda:at+timedelta(hours=1))[0]['result']=='failed'
    q=queue(store)
    assert q['last_success_at']==stamp(NOW) and q['failures']==2
    assert q['next_check_at']==stamp(at+timedelta(hours=3))
    assert not AutoRepository(store.db).feed_plan(at)['items']


def test_update_invalidates_json_and_rss_until_refresh_preserving_identity(auto_service, sources, sample):
    store,c,server,point,_=auto_service
    old=entries(server)[0]
    etag=request(server,'/feed.xml')[1]['ETag']
    point[0]+=timedelta(hours=25)
    with mock_fetcher(sample) as fetcher:
        assert recheck(store,fetcher,sources,clock=lambda:point[0])[0]['result']=='updated'
    for path in ['/feed.xml','/api/notices']:
        assert request(server,path,headers={'If-None-Match':etag})[0]==503
    assert c.refresh()
    new=entries(server)[0]
    assert (old.id,old.published)==(new.id,new.published)
    assert '中国数学会' in new.summary


def test_explicit_withdrawal_is_never_restored(store, sources, sample):
    add(store)
    at=NOW+timedelta(hours=25)
    html=sample('computer_article.html').replace(b'<p class="vsbcontent_start"', '<p>本通知已撤回。</p><p class="vsbcontent_start"'.encode())
    with mock_fetcher(sample,response=httpx.Response(200,content=html)) as fetcher:
        recheck(store,fetcher,sources,clock=lambda:at)
        assert recheck(store,fetcher,sources,clock=lambda:at+timedelta(days=2))==[]
    assert store.db.execute('SELECT status FROM notice_health').fetchone()[0]=='withdrawn'
    assert not AutoRepository(store.db).feed_plan(at)['items']


def test_claim_survives_crash_and_is_retried_after_lease(store, sources, sample):
    add(store)
    at=NOW+timedelta(hours=25)
    with store.db:
        store.db.execute("UPDATE notice_rechecks SET state='running',next_check_at=?", (stamp(at+CLAIM_TTL),))
    with mock_fetcher(sample) as fetcher:
        assert recheck(store,fetcher,sources,clock=lambda:at)==[]
        assert recheck(store,fetcher,sources,clock=lambda:at+CLAIM_TTL)


def test_budget_does_not_invalidate_notice_or_cache_robots_failure(store, sources, sample):
    add(store)
    at=NOW+timedelta(hours=25)
    with mock_fetcher(sample,request_limit=1) as fetcher:
        assert recheck(store,fetcher,sources,clock=lambda:at)==[]
        assert fetcher.request_count==1
        assert queue(store)['state']=='pending'
        assert queue(store)['last_success_at']==stamp(NOW)
        assert store.db.execute('SELECT status FROM notice_health').fetchone()[0]=='valid'
        fetcher.request_limit=3
        assert recheck(store,fetcher,sources,clock=lambda:at+timedelta(minutes=5))


def test_wire_budget_counts_robots_redirects_and_retries(sample):
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        if req.url.path=='/robots.txt':return httpx.Response(404)
        if req.url.path== '/start':return httpx.Response(302,headers={'Location':'/next'})
        return httpx.Response(503)
    with Fetcher(transport=httpx.MockTransport(handler),interval=0,retries=3,sleep=lambda _:None,request_limit=4) as fetcher:
        with pytest.raises(RequestBudgetExceeded):fetcher.fetch('https://computer.cdu.edu.cn/start')
        assert fetcher.request_count==len(calls)==4


def test_budget_before_robots_can_resume_in_same_cycle(sample):
    with mock_fetcher(sample,request_limit=1) as fetcher:
        fetcher.request_count=1
        with pytest.raises(RequestBudgetExceeded):fetcher.fetch(URL)
        assert fetcher.robots=={}
        fetcher.request_limit=3
        assert fetcher.fetch(URL)


def test_discovery_skips_known_and_advances_within_bounded_list(store, sources, sample):
    selected=[s for s in sources if s.id=='computer']
    def handler(req):
        return httpx.Response(200, content=sample('computer_article.html' if '/info/' in req.url.path else 'computer_list.html'))
    with mock_fetcher(sample,handler=handler) as fetcher:
        for _ in range(3):
            result=collect_cycle(store,fetcher,selected,limit=1,request_budget=8)
            assert result['requests']<=8
        assert store.db.execute('SELECT count(*) FROM notices').fetchone()[0]==3
    assert store.db.execute('SELECT count(*) FROM notice_rechecks').fetchone()[0]==3


def test_cycle_reserves_requests_for_due_originals(store, sources, sample, monkeypatch):
    from cdu_radar import recheck as module
    add(store)
    # Explicitly pass the clock because the function default is bound at import.
    original=module.recheck
    monkeypatch.setattr(module,'recheck',lambda *a,**kw:original(*a,**kw,clock=lambda:NOW+timedelta(hours=25)))
    selected=[s for s in sources if s.id=='computer']
    def handler(req):
        return httpx.Response(200,content=sample('computer_article.html' if '/info/' in req.url.path else 'computer_list.html'))
    with mock_fetcher(sample,handler=handler) as fetcher:
        result=collect_cycle(store,fetcher,selected,request_budget=6)
    assert result['requests']<=6 and result['rechecks']
    assert queue(store)['last_success_at']==stamp(NOW+timedelta(hours=25))


def test_stop_and_revoked_origin_do_not_fetch(store, sources, sample):
    add(store)
    stop=Event();stop.set()
    with mock_fetcher(sample) as fetcher:
        assert recheck(store,fetcher,sources,stop_event=stop,clock=lambda:NOW+timedelta(days=2))==[]
        assert recheck(store,fetcher,[replace(s,status='pending') for s in sources],clock=lambda:NOW+timedelta(days=2))==[]
        assert fetcher.request_count==0


def test_migration_preserves_notices_publications_and_history(store):
    add(store)
    with store.db:
        AutoRepository(store.db).reserve(NOW)
        store.db.execute('DROP TABLE notice_rechecks')
    before=[tuple(r) for r in store.db.execute('SELECT * FROM notices')]
    publications=[tuple(r) for r in store.db.execute('SELECT * FROM auto_publications')]
    initialize(store.db)
    assert [tuple(r) for r in store.db.execute('SELECT * FROM notices')]==before
    assert [tuple(r) for r in store.db.execute('SELECT * FROM auto_publications')]==publications
    assert queue(store)['next_check_at']==stamp(NOW+timedelta(hours=24))


def test_competing_workers_do_not_fetch_claimed_notice(store, sources, sample):
    add(store)
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    at=NOW+timedelta(hours=25)
    def handler(req):
        with Store(path) as other, mock_fetcher(sample) as second:
            assert recheck(other,second,sources,clock=lambda:at)==[]
            assert second.request_count==0
        return httpx.Response(200,content=sample('computer_article.html'))
    with mock_fetcher(sample,handler=handler) as fetcher:
        assert recheck(store,fetcher,sources,clock=lambda:at)


def test_original_redirect_and_publisher_mismatch_block_refresh(store, sources, sample):
    add(store)
    at=NOW+timedelta(hours=25)
    with mock_fetcher(sample,response=httpx.Response(302,headers={'Location':'/info/1036/777.htm'}),request_limit=3) as fetcher:
        # A redirect loop exhausts the local budget without renewing success.
        assert recheck(store,fetcher,sources,clock=lambda:at)==[]
    assert queue(store)['last_success_at']==stamp(NOW)
    with store.db:
        store.db.execute("UPDATE notices SET content_source_id='business'")
    with mock_fetcher(sample) as fetcher:
        assert recheck(store,fetcher,sources,clock=lambda:at+timedelta(minutes=5))[0]['result']=='failed'
        assert fetcher.request_count==0


def test_budget_still_allows_later_sources_next_round(store, sources, sample):
    def handler(req):
        kind='computer' if req.url.host=='computer.cdu.edu.cn' else 'business'
        return httpx.Response(200,content=sample(kind+('_article.html' if '/info/' in req.url.path else '_list.html')))
    for _ in range(2):
        with mock_fetcher(sample,handler=handler) as fetcher:
            assert collect_cycle(store,fetcher,sources,request_budget=6)['requests']<=6
    assert {r[0] for r in store.db.execute('SELECT DISTINCT source_id FROM notices')}=={'business','computer'}


def test_budget_cutoff_before_first_article_resumes_that_source(store, sources, sample):
    def handler(req):
        kind='computer' if req.url.host=='computer.cdu.edu.cn' else 'business'
        return httpx.Response(200,content=sample(kind+('_article.html' if '/info/' in req.url.path else '_list.html')))
    with mock_fetcher(sample,handler=handler) as fetcher:
        first=collect_cycle(store,fetcher,sources,limit=1,request_budget=6)
    assert first['discovery'][-1]['source']=='computer' and first['discovery'][-1]['error_category']=='budget'
    assert store.db.execute('SELECT next_source FROM collection_cursor').fetchone()[0]=='computer'
    with mock_fetcher(sample,handler=handler) as fetcher:
        second=collect_cycle(store,fetcher,sources,limit=1,request_budget=6)
    assert second['discovery'][0]['source']=='computer' and second['discovery'][0]['inserted']==1


def test_known_notice_keeps_new_discovery_without_renewing_health(store, sources, sample):
    add(store)
    health=tuple(store.db.execute('SELECT * FROM notice_health').fetchone())
    schedule=queue(store)
    listing=f'<ul class="jtfylb"><li><a href="{URL}"><p>合成转载</p><span>2026-10-16</span></a></li></ul>'
    def handler(req):
        assert '/info/' not in req.url.path, 'Known articles must not be downloaded by discovery'
        return httpx.Response(200,text=listing,headers={'content-type':'text/html'})
    with mock_fetcher(sample,handler=handler) as fetcher:
        result=collect(store,fetcher,[s for s in sources if s.id=='business'],skip_known=True)
        assert fetcher.request_count==2
    assert result[0]['duplicates']==1
    assert {r['source_id'] for r in store.db.execute('SELECT source_id FROM notice_sources')}=={'computer','business'}
    assert store.db.execute("SELECT published_at FROM notice_sources WHERE source_id='business'").fetchone()[0]=='2026-10-16'
    assert tuple(store.db.execute('SELECT * FROM notice_health').fetchone())==health
    assert queue(store)==schedule


def test_original_recheck_preserves_discovery_date_and_seen_times(store, sources, sample):
    add(store)
    with store.db:
        store.db.execute("UPDATE notice_sources SET published_at='2026-10-16'")
    discovery=tuple(store.db.execute('SELECT * FROM notice_sources').fetchone())
    with mock_fetcher(sample) as fetcher:
        assert recheck(store,fetcher,sources,clock=lambda:NOW+timedelta(hours=25))
    assert tuple(store.db.execute('SELECT * FROM notice_sources').fetchone())==discovery
