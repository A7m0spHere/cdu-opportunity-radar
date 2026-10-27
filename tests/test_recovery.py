"""Recovery, concurrent guarded reads and durable original revision regressions."""
from contextlib import contextmanager
from threading import Event,Thread
import hashlib
import json
from datetime import timedelta
import sqlite3
import httpx
from xml.etree import ElementTree as ET

import pytest

from cdu_radar.managed_worker import supervise
from cdu_radar import discovery_failures
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher
from cdu_radar.recheck import collect_cycle
from cdu_radar.adapters import VsbAdapter
from cdu_radar.models import ParseError
from cdu_radar.featured import ValidationCache,analysis_for
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_queue import JobQueue
from test_auto_publish import auto_service,add,BODY,URL
from test_rss_service import loopback_only,request

def test_format_circuit_recovers_without_losing_following_round():
    stop=Event();states=[];waits=[];clock=[0]
    results=iter([{'handled':[{'error':'schema_validation'}],'stop_reason':'consecutive_format_failures'},
                  {'handled':[{'error':'schema_validation'}],'stop_reason':'consecutive_format_failures'},
                  {'handled':[{'state':'succeeded','committed':True,'error':None}]}])
    def wait(seconds):
        waits.append(seconds);clock[0]+=seconds
        if len(waits)==3:stop.set()
        return stop.is_set()
    supervise(lambda:next(results),stop,states.append,clock=lambda:clock[0],wait=wait)
    assert waits==[60,300,60]
    assert states[-1]['state']=='stopped' and states[-1]['handled']==3
    assert states[-1]['failed_tasks']==2 and states[-1]['last_success_at']==360

def test_credentials_block_without_repeated_billable_requests():
    stop=Event();calls=[];states=[]
    def work():calls.append(1);return {'handled':[{'error':'authentication'}]}
    def wait(seconds):stop.set();return True
    supervise(work,stop,states.append,wait=wait)
    assert len(calls)==1
    assert any(s['state']=='blocked' and s['reason']=='authentication' for s in states)

def test_unexpected_round_error_is_recorded_and_recovers():
    stop=Event();states=[];calls=[]
    def work():
        calls.append(1)
        if len(calls)==1:raise ValueError('private diagnostic must not be logged')
        return {'handled':[]}
    def wait(seconds):
        if len(calls)==2:stop.set()
        return stop.is_set()
    supervise(work,stop,states.append,wait=wait)
    assert len(calls)==2 and any(s['reason']=='ValueError' for s in states)

def test_refresh_build_does_not_hold_global_response_lock(auto_service,monkeypatch):
    store,c,server,_,_=auto_service;entered=Event();release=Event();errors=[]
    original=c.reader
    @contextmanager
    def slow_writer():
        entered.set();assert release.wait(4)
        with original() as repo:yield repo
    monkeypatch.setattr(c,'reader',slow_writer)
    def run():
        try:c.refresh()
        except BaseException as exc:errors.append(exc)
    thread=Thread(target=run);thread.start();assert entered.wait(2)
    try:
        # Only the refresh thread is blocked; readers use their own context.
        monkeypatch.setattr(c,'reader',original)
        assert request(server,'/feed.xml')[0]==200
    finally:release.set();thread.join(4)
    assert not errors and not thread.is_alive()

def test_readers_are_concurrent_but_source_writes_cannot_commit(auto_service):
    store,c,server,_,_=auto_service;results=[]
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    with c.read_gate() as snap:
        assert snap is not None
        t=Thread(target=lambda:results.append(request(server,'/feed.xml')[0]));t.start();t.join(2)
        assert results==[200]
        with sqlite3.connect(path,timeout=.05) as writer:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                writer.execute('UPDATE notices SET title=title||\' changed\'');writer.commit()
            writer.rollback()

def test_caller_error_is_not_caught_or_double_yielded(auto_service):
    _,c,_,_,_=auto_service
    with pytest.raises(ValueError,match='caller'):
        with c.read_gate() as snap:raise ValueError('caller')

def test_original_revision_and_excerpt_are_machine_readable(auto_service):
    store,c,server,point,_=auto_service
    route='/rss/v2/all.xml?audience=undergraduate';ns='{urn:cdu-radar:rss:v2}'
    status,_,body=request(server,route);assert status==200
    item=ET.fromstring(body).find('channel/item')
    old=item.findtext(ns+'content_revision')
    assert old==hashlib.sha256(BODY.encode()).hexdigest()
    assert item.findtext(ns+'excerpt') and 'RSS pubDate' not in item.findtext(ns+'excerpt')
    add(store,body=BODY+'\n新增原文办理要求。',at=point[0]);assert c.refresh()
    item=ET.fromstring(request(server,route)[2]).find('channel/item')
    assert item.findtext(ns+'content_revision')!=old

def test_failed_url_cooling_is_persistent_and_parser_versioned(store,monkeypatch):
    discovery_failures.initialize(store.db)
    discovery_failures.failed(store.db,'computer',URL,'html_parse',now=10)
    assert discovery_failures.cooling(store.db,'computer',URL,now=100)
    assert not discovery_failures.cooling(store.db,'computer',URL,now=21611)
    monkeypatch.setattr(discovery_failures,'parser_version',lambda:'new-parser')
    assert not discovery_failures.cooling(store.db,'computer',URL,now=100)

def test_failed_pinned_article_does_not_block_following_valid_notice(store,sources,sample):
    source=next(s for s in sources if s.id=='computer');calls=[]
    def handler(req):
        calls.append(str(req.url))
        if req.url.path=='/robots.txt':return httpx.Response(404)
        if '/info/' not in req.url.path:return httpx.Response(200,content=sample('computer_list.html'))
        if req.url.path.endswith('12341.htm'):return httpx.Response(200,text='<html>unsupported image only</html>')
        return httpx.Response(200,content=sample('computer_article.html'))
    with Fetcher(transport=httpx.MockTransport(handler),interval=0,retries=0) as fetcher:
        result=collect(store,fetcher,[source],limit=1,skip_known=True,tolerate_failures=3)[0]
        assert result['failed']==1 and result['inserted']==1
        before=len([p for p in calls if p.endswith('12341.htm')])
        collect(store,fetcher,[source],limit=1,skip_known=True,tolerate_failures=3)
        assert len([p for p in calls if p.endswith('12341.htm')])==before

def test_due_originals_get_budget_before_discovery(store,sources,sample):
    source=next(s for s in sources if s.id=='computer');wire=[]
    for i in range(16):add(store,url=f'https://computer.cdu.edu.cn/info/1036/{990001+i}.htm')
    def handler(req):
        wire.append(req.url.path)
        if req.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=sample('computer_article.html' if '/info/' in req.url.path else 'computer_list.html'))
    with Fetcher(transport=httpx.MockTransport(handler),interval=0,retries=0) as fetcher:
        result=collect_cycle(store,fetcher,[source],limit=1,recheck_limit=12,request_budget=48)
    assert len(result['rechecks'])==12 and result['requests']<=48
    assert '/info/' in wire[1]

@pytest.mark.parametrize('kind,title,body,detail',[
    ('business','<h1>合成公示</h1>','（公示期已过）','expired_stub'),
    ('teaching','<div class="art-tit"><h3>合成考试通知</h3></div>',
     '<script>var vsb_pdf_image_data=["/__local/synthetic.jpg"];</script>一审：二审：三审：','pdf_rendered_images')])
def test_unreadable_originals_are_typed_not_fabricated(kind,title,body,detail):
    html='<form name="_newscontent_fromname">'+title+'<div id="vsb_content">'+body+'</div></form>'
    with pytest.raises(ParseError) as caught:VsbAdapter(kind).parse_article(html,URL)
    assert caught.value.detail==detail

def test_cached_v3_validation_rechecks_live_input_and_stored_result(store,tmp_path,monkeypatch):
    from test_automatic_review import setup
    from test_featured import raw_path
    from test_auto_publish import NOW
    a,n,_,_,checked=setup(store,tmp_path);cache=ValidationCache();calls=[];original=a.validate
    def validate(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(a,'validate',validate)
    with JobQueue(tmp_path/'jobs.sqlite3',raw_path(store)) as q:
        q.scan(a,notice_ids=[1])
        with q.db:q.db.execute('UPDATE jobs SET state=?,result=?',(checked['state'],json.dumps(checked)))
        repo=AnalysisRepository(store)
        first=analysis_for(q,repo,a,1,NOW,validation_cache=cache)
        second=analysis_for(q,repo,a,1,NOW,validation_cache=cache)
        assert first[2]==second[2]=='succeeded' and len(calls)==1
        first[1]['classification']['type']='tampered'
        assert analysis_for(q,repo,a,1,NOW,validation_cache=cache)[1]['classification']['type']=='research'
        with q.db:q.db.execute("UPDATE jobs SET result='{}'")
        assert analysis_for(q,repo,a,1,NOW,validation_cache=cache)[2]=='failed'

def test_cached_static_facts_do_not_cache_clock_or_original_mutations(store):
    from cdu_radar.auto_publish import AutoRepository
    from test_auto_publish import NOW
    body='合成校园竞赛报名，面向我校在校本科生。报名截止：2026年10月15日21:00。'
    add(store,body=body)
    first=AutoRepository(store.db).feed_plan(NOW)['items'][0]
    later=AutoRepository(store.db).feed_plan(NOW+timedelta(hours=2))['items'][0]
    assert first['registration_status']=='possible' and later['registration_status']=='closed'
    add(store,body=body.replace('21:00','23:00'),at=NOW+timedelta(hours=2))
    changed=AutoRepository(store.db).feed_plan(NOW+timedelta(hours=2))['items'][0]
    assert changed['registration_status']=='possible'
