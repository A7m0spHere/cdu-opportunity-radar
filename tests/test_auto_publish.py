"""M4-B: isolated DBs, offline HTML and literal-loopback HTTP only."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from xml.etree import ElementTree as ET

import feedparser
import httpx
import pytest

from cdu_radar.auto_publish import AutoRepository, NS, failed, MAX_SOURCE_AGE
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.cli import main
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher, FetchError
from cdu_radar.models import Article, ArticleLink, ParseError
from cdu_radar.rss_service import make_server
from cdu_radar.storage import Store
from test_rss_service import loopback_only, request

NOW=reference_datetime('2026-10-15T20:00:00+08:00')
URL='https://computer.cdu.edu.cn/info/1036/990001.htm'
BODY='欢迎参加合成校园竞赛，本条仅用于边界测试。\n参与对象：我校在校本科生。\n报名截止：2026年10月20日。'


def add(store, *, url=URL, body=BODY, title='合成校园竞赛报名', published='2026-10-15', at=NOW):
    return store.save_notice('computer',url,Article(title,published,body),seen_at=at.isoformat())


def plan(store, at=NOW):return AutoRepository(store.db).feed_plan(at)


@pytest.fixture
def auto_service(store,tmp_path,loopback_only):
    add(store)
    point=[NOW];mono=[0.0]
    db=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    c=AutoFeedController(db,tmp_path/'自动聚合 state',clock=lambda:point[0],monotonic=lambda:mono[0])
    assert c.refresh()
    s=make_server(c,port=0)
    t=threading.Thread(target=s.serve_forever,kwargs={'poll_interval':0.02},daemon=True);t.start()
    try:yield store,c,s,point,mono
    finally:s.shutdown();s.server_close();t.join();c.close()


def entries(s,path='/feed.xml'):
    status,h,body=request(s,path)
    assert status==200
    parsed=feedparser.parse(body)
    assert not parsed.bozo
    return parsed.entries


def test_automatic_official_notice_without_any_review(auto_service):
    store,c,s,_,_=auto_service
    e=entries(s)[0]
    assert e.link==URL and e.source.title=='计算机学院'
    assert '人工批准' not in e.title and '报名截止' in e.summary
    tables={r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not tables & {'publication_reviews','publication_state','publication_events','manual_labels','analysis_reviews','notice_analyses'}
    assert len(entries(s,'/current.xml'))==1 and not entries(s,'/history.xml')
    assert request(s,'/approved.xml')[0]==410
    assert request(s,'/health')[0]==200
    assert request(s,'/feed.xml',method='HEAD')[2]==b''
    assert request(s,'/feed.xml',headers={'If-None-Match':request(s,'/feed.xml')[1]['ETag']})[0]==304


def test_duplicate_update_guid_time_and_restart(auto_service,tmp_path):
    store,c,s,point,_=auto_service
    old=entries(s)[0]
    assert add(store)=='unchanged'
    assert len(entries(s))==1
    point[0]+=timedelta(minutes=1)
    assert add(store,body=BODY+'\n新增的原文说明：合成 & <中文>。',at=point[0])=='updated'
    assert request(s,'/feed.xml')[0]==503
    assert c.refresh()
    e=entries(s)[0]
    assert (e.id,e.published)==(old.id,old.published) and '新增的原文说明' in e.summary
    db=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    other=AutoFeedController(db,tmp_path/'restart',clock=lambda:point[0])
    try:
        assert other.refresh()
        restarted=feedparser.parse(other.snapshot.body).entries[0]
        assert (restarted.id,restarted.published)==(e.id,e.published)
    finally:other.close()


@pytest.mark.parametrize('body,published,status,listing',[
    (BODY,'2026-10-15','possible','current'),
    (BODY.replace('10月20日','10月14日'),'2026-10-15','closed','history'),
    ('合成校园活动通知，欢迎同学关注。此条没有任何明确的报名日期。','2026-10-15','unknown','unknown'),
    ('合成校园讲座通知。举办时间：2025年10月20日。欢迎关注。','2025-10-01','unknown','history'),
    (BODY,'2024-10-15','unknown','history'),
    ('合成活动说明。报名已结束。后续有关事宜请关注官方通知。','2026-10-15','closed','history'),
    (BODY.replace('2026年10月20日','10月20日'),'2026-10-15','unknown','unknown'),
    (BODY.replace('报名截止：','报名截止暂定：'),'2026-10-15','unknown','unknown'),
    (BODY+'\n报名截止：2026年10月22日。','2026-10-15','unknown','unknown'),
    (BODY+'\n报名开始：2026年10月19日。','2026-10-15','unknown','unknown'),
    (BODY+'\n校内报名截止：2026年10月14日。','2026-10-15','closed','history'),
    (BODY+'\n举办时间：2026年10月10日。','2026-10-15','unknown','history'),
])
def test_time_states_never_guess_openness(store,body,published,status,listing):
    add(store,body=body,published=published)
    item=plan(store)['items'][0]
    assert (item['registration_status'],item['listing'])==(status,listing)


@pytest.mark.parametrize('url',[
    '', 'https://evil.invalid/info/1036/1.htm','https://new.cdu.edu.cn/info/1/1.htm',
    'https://computer.cdu.edu.cn.evil.invalid/info/1/1.htm',
    'https://computer.cdu.edu.cn:8888/info/1/1.htm',
    'https://someone@computer.cdu.edu.cn/info/1/1.htm',
    'https://computer.cdu.edu.cn/login.html',
])
def test_missing_or_unverified_source_blocks_publication(store,url):
    add(store,url=url)
    p=plan(store)
    assert not p['items'] and p['excluded'][0]['reason']=='untrusted_or_missing_source'


@pytest.mark.parametrize('status',['parse_failed','unavailable','withdrawn'])
def test_failure_removal_and_recovery(auto_service,status):
    store,c,s,point,_=auto_service
    old=entries(s)[0]
    with store.db:failed(store.db,URL,status,'synthetic fault',point[0].isoformat())
    assert request(s,'/feed.xml',headers={'If-None-Match':request(s,'/feed.xml')[1].get('ETag','')})[0]==503
    assert c.refresh() and not entries(s)
    add(store)
    assert c.refresh()
    if status=='withdrawn':assert not entries(s)  # successful collection cannot restore a tombstone
    else:
        e=entries(s)[0];assert (e.id,e.published)==(old.id,old.published)


def test_source_date_unknown_and_summary_evidence(auto_service):
    store,c,s,point,_=auto_service
    add(store,url=URL.replace('990001','990002'),published=None,body='这是没有公开发布日期和资格条款的合成测试通知。欢迎查看原始内容。')
    assert c.refresh()
    root=ET.fromstring(c.snapshot.body)
    item=next(i for i in root.findall('./channel/item') if i.findtext('link')!=URL)
    assert item.findtext('{'+NS+'}source_published_date')=='unknown'
    evidence=json.loads(item.findtext('{'+NS+'}summary_evidence'))
    row=store.db.execute('SELECT content FROM notices WHERE canonical_url=?',(item.findtext('link'),)).fetchone()
    assert row[0][evidence['start']:evidence['end']]==evidence['quote']
    assert '参与对象（原文）：未知' in item.findtext('description')
    assert '来源发布日期：未知' in item.findtext('description')
    assert item.findtext('pubDate') and '不是原文发布日期' in item.findtext('description')


def test_expiry_and_no_static_cache_fallback(auto_service,monkeypatch):
    store,c,s,point,mono=auto_service
    etag=request(s,'/feed.xml')[1]['ETag']
    from cdu_radar import auto_service as module
    original=module.atomic_write
    monkeypatch.setattr(module,'atomic_write',lambda *a:(_ for _ in ()).throw(OSError('synthetic disk fault')))
    assert not c.refresh() and request(s,'/feed.xml',headers={'If-None-Match':etag})[0]==503
    assert not c.output.exists()
    monkeypatch.setattr(module,'atomic_write',original)
    assert c.refresh()
    mono[0]=31
    assert request(s,'/feed.xml',headers={'If-None-Match':etag})[0]==503
    assert c.refresh()
    point[0]+=MAX_SOURCE_AGE+timedelta(seconds=1)
    assert request(s,'/feed.xml')[0]==503 and c.refresh() and not entries(s)


def test_no_model_or_bot_import_needed(store,tmp_path,monkeypatch):
    import builtins
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.startswith(('astrbot','napcat','openai')) or name in {'llm','llm_cli','llm_workflow'}:
            raise AssertionError('Automatic publication must not load model or bot code: '+name)
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    add(store)
    c=AutoFeedController(Path(store.db.execute('PRAGMA database_list').fetchone()[2]),tmp_path/'isolated',clock=lambda:NOW)
    try:assert c.refresh() and len(feedparser.parse(c.snapshot.body).entries)==1
    finally:c.close()


def test_collector_marks_previously_parsed_article_failed(store,sources,sample):
    url='https://computer.cdu.edu.cn/info/1036/12341.htm'
    add(store,url=url)
    def transport(req):
        if req.url.path=='/robots.txt':return httpx.Response(404)
        if req.url.path=='/cxcy/xkjs.htm':return httpx.Response(200,content=sample('computer_list.html'))
        return httpx.Response(200,text='<html>template changed</html>',headers={'content-type':'text/html'})
    with Fetcher(transport=httpx.MockTransport(transport),interval=0) as fetcher:
        result=collect(store,fetcher,[s for s in sources if s.id=='computer'],limit=1)
    assert result[0]['parse_failures']==1
    assert plan(store)['excluded'][0]['reason']=='parse_failed'
    assert store.db.execute('SELECT count(*) FROM notices').fetchone()[0]==1


def test_background_publishes_new_notice(auto_service):
    store,c,s,point,_=auto_service
    c.start(0.1)
    add(store,url=URL.replace('990001','990002'))
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if c.snapshot and len(feedparser.parse(c.snapshot.body).entries)==2:break
        time.sleep(0.03)
    assert len(entries(s))==2


@pytest.mark.parametrize('mutation',['missing','wal','corrupt'])
def test_source_database_fail_closed(auto_service,tmp_path,mutation):
    store,c,s,_,_=auto_service
    if mutation=='wal':
        store.db.execute('PRAGMA journal_mode=WAL')
    elif mutation=='missing':
        original=c.db;c.db=tmp_path/'absent.sqlite3'
    else:
        original=c.db;bad=tmp_path/'bad.sqlite3';bad.write_bytes(b'not sqlite');c.db=bad
    assert request(s,'/feed.xml',headers={'If-None-Match':'*'})[0]==503
    assert not c.refresh()
    if mutation!='wal':c.db=original


@pytest.mark.parametrize('value',['2026-02-30','2030-10-15'])
def test_invalid_source_date_not_substituted(store,value):
    add(store,published=value)
    assert not plan(store)['items'] and plan(store)['excluded'][0]['reason']=='invalid_source_date'


def test_rule_failure_keeps_basic_aggregation(store,monkeypatch):
    add(store)
    p=AutoRepository(store.db)
    monkeypatch.setattr(p.analyzer,'analyze',lambda *a,**k:(_ for _ in ()).throw(ValueError('synthetic rule failure')))
    item=p.feed_plan(NOW)['items'][0]
    assert item['registration_status']=='unknown' and item['summary'] in BODY


def test_history_and_labels_are_unchanged_by_auto_update(store,tmp_path):
    from test_publication import setup, decide, NOW as LEGACY_NOW
    ident,pub=setup(store);decide(pub,ident)
    tables=('publication_reviews','publication_state','publication_events','analysis_reviews','manual_labels')
    before={t:[tuple(r) for r in store.db.execute('SELECT * FROM '+t)] for t in tables}
    row=store.db.execute('SELECT * FROM notices WHERE id=?',(ident,)).fetchone()
    store.save_notice('computer',row['canonical_url'],Article(row['title'],row['published_at'],row['content']+'\n合成变更。'),seen_at=NOW.isoformat())
    c=AutoFeedController(Path(store.db.execute('PRAGMA database_list').fetchone()[2]),tmp_path/'history',clock=lambda:NOW)
    try:assert c.refresh()
    finally:c.close()
    assert before=={t:[tuple(r) for r in store.db.execute('SELECT * FROM '+t)] for t in tables}


def test_cli_aggregate_and_retired_approval_writes(store,tmp_path,capsys):
    add(store)
    path=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    assert main(['aggregate','--db',str(path),'--as-of',NOW.isoformat()])==0
    assert json.loads(capsys.readouterr().out)['items'][0]['listing']=='current'
    for args in [ ['serve-approved-rss','--db',str(path),'--state-dir',str(tmp_path/'legacy')],
                  ['export-approved-rss','--db',str(path),'--confirm','anything'],
                  ['review','set','--db',str(path),'--notice-id','1','--status','approved','--reviewer','test','--reason','test','--confirm'] ]:
        assert main(args)==2
    assert not (tmp_path/'legacy').exists()


def test_real_html_collection_to_automatic_http(store,sources,sample,tmp_path,loopback_only):
    from test_collect_storage import transport_for
    with Fetcher(transport=transport_for(sample),interval=0) as fetcher:
        results=collect(store,fetcher,[s for s in sources if s.id=='computer'],limit=1)
    assert results[0]['inserted']==1
    from cdu_radar.models import utc_now
    point=reference_datetime(utc_now())
    c=AutoFeedController(Path(store.db.execute('PRAGMA database_list').fetchone()[2]),tmp_path/'real-html',clock=lambda:point)
    try:
        assert c.refresh()
        parsed=feedparser.parse(c.snapshot.body)
        assert not parsed.bozo and len(parsed.entries)==1
        assert parsed.entries[0].link=='https://computer.cdu.edu.cn/info/1036/12341.htm'
        assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='publication_reviews'").fetchone()
    finally:c.close()


@pytest.mark.parametrize('title,category',[
    ('合成数学竞赛报名','学科竞赛'),('合成科研项目招募','科研与创新实践'),
    ('合成人工智能讲座','AI / 计算机相关活动'),('合成校园工作坊','校园活动'),('合成其他通知','其他通知')])
def test_browsing_categories(store,title,category):
    add(store,title=title,body='这是一条合成测试说明，正文只用于分类边界验证，不对应真实活动。')
    assert plan(store)['items'][0]['category']==category


def test_read_gate_holds_rollback_lock_until_response_finishes(auto_service):
    store,c,s,point,_=auto_service
    db=c.db
    started=threading.Event();committed=threading.Event()
    def update():
        with sqlite3.connect(db,timeout=3) as writer:
            started.set()
            writer.execute("UPDATE notice_health SET status='unavailable' WHERE notice_id=1")
            writer.commit();committed.set()
    with c.read_gate() as snapshot:
        assert snapshot
        worker=threading.Thread(target=update);worker.start()
        assert started.wait(1)
        assert not committed.wait(0.05)
    worker.join(4)
    assert committed.is_set() and request(s,'/feed.xml')[0]==503


def test_source_expiry_changes_current_list_and_stable_pubdate(auto_service):
    store,c,s,point,_=auto_service
    add(store,body=BODY.replace('10月20日','10月15日'))
    assert c.refresh()
    old=entries(s)[0]
    point[0]=reference_datetime('2026-10-16T00:00:00+08:00')
    assert request(s,'/current.xml',headers={'If-None-Match':'*'})[0]==503
    assert c.refresh() and not entries(s,'/current.xml')
    new=entries(s,'/history.xml')[0]
    assert (new.id,new.published)==(old.id,old.published)


@pytest.mark.parametrize('fault',['missing_url','body_changed_without_parse','missing_health','withdrawn'])
def test_quality_mutations_gate_http_immediately(auto_service,fault):
    store,c,s,point,_=auto_service
    with store.db:
        if fault=='missing_url':store.db.execute("UPDATE notices SET canonical_url='' WHERE id=1")
        elif fault=='body_changed_without_parse':store.db.execute("UPDATE notices SET content=content||'unexpected' WHERE id=1")
        elif fault=='missing_health':store.db.execute('DELETE FROM notice_health')
        else:failed(store.db,URL,'withdrawn','synthetic withdrawal',point[0].isoformat())
    assert request(s,'/feed.xml',headers={'If-None-Match':'*'})[0]==503
    assert c.refresh() and not entries(s)


def test_path_portability_spaces_unicode_and_state_identity(store,tmp_path):
    add(store)
    directory=tmp_path/'中文 & spaces'/'路径'
    directory.mkdir(parents=True)
    db=directory/'源 数据.sqlite3'
    with sqlite3.connect(db) as target:store.db.backup(target)
    state=directory/'状态'
    c=AutoFeedController(db,state,clock=lambda:NOW)
    try:assert c.refresh()
    finally:c.close()
    marker=json.loads((state/'owner.json').read_text('utf-8'))
    assert marker['kind']=='m4b-auto-rss-state-v1'
    from cdu_radar.rss_service import FeedController
    with pytest.raises(ValueError,match='another source'):FeedController(db,state)
    assert not list(state.glob('*.tmp'))


def test_explicit_source_withdrawal_is_not_published(store):
    add(store,body=BODY+'\n本通知已撤回，后续安排将另行通知，请关注官方网站。')
    assert not plan(store)['items'] and plan(store)['excluded'][0]['reason']=='withdrawn'
    add(store)
    assert not plan(store)['items']
    with store.db:failed(store.db,URL,'unavailable','synthetic later outage',NOW.isoformat())
    add(store)
    assert plan(store)['excluded'][0]['reason']=='withdrawn'


def test_pending_content_unit_not_accepted_through_trusted_discovery(store):
    from cdu_radar.sources import load_sources
    store.sync_sources(load_sources())
    store.save_notice('computer','https://kjc.cdu.edu.cn/info/1010/90001.htm',
        Article('合成转载通知','2026-10-15',BODY,content_source_id='department-kjc'),seen_at=NOW.isoformat())
    assert not plan(store)['items']


def test_source_list_failure_does_not_invent_article_withdrawal(store,sources):
    add(store)
    class BrokenList:
        def fetch(self,url):raise FetchError('synthetic list outage')
    result=collect(store,BrokenList(),[s for s in sources if s.id=='computer'],limit=1)
    assert result[0]['failed']==1
    assert len(plan(store)['items'])==1
    assert not plan(store,NOW+MAX_SOURCE_AGE+timedelta(seconds=1))['items']


def test_scheduled_collection_to_publication_without_approval(store,sample,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from test_collect_storage import transport_for
    from cdu_radar import auto_service as module
    from cdu_radar import http
    # Immediate first scheduled collection, mock official network; the publisher
    # and collector are actual independent threads with the real SQLite gate.
    monkeypatch.setattr(http,'Fetcher',lambda **kwargs:Fetcher(transport=transport_for(sample),interval=0))
    observed=[]
    class Server:
        server_address=('127.0.0.1',0)
        def __init__(self,controller):self.controller=controller
        def serve_forever(self,**kwargs):
            until=time.monotonic()+8
            while time.monotonic()<until:
                with self.controller.read_gate() as snap:
                    if snap and feedparser.parse(snap.body).entries:
                        observed.extend(feedparser.parse(snap.body).entries);raise KeyboardInterrupt
                time.sleep(0.04)
            raise AssertionError('Scheduled notice did not reach the automatic publisher')
        def server_close(self):pass
    monkeypatch.setattr(module,'make_server',lambda c,*args:Server(c))
    args=SimpleNamespace(db=Path(store.db.execute('PRAGMA database_list').fetchone()[2]),
        state_dir=tmp_path/'scheduled',bind='127.0.0.1',port=0,max_age=30,interval=0.1,
        collect_interval=3600,source=['computer'],limit=1,no_env_proxy=True)
    assert module.serve_command(args)==0
    assert len(observed)==1 and observed[0].link.endswith('/12341.htm')
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='publication_reviews'").fetchone()
    assert not any(t.name=='official-collector' for t in threading.enumerate())


def test_collect_cancellation_does_not_fetch_new_sources(store,sources):
    stop=threading.Event();stop.set()
    class NoFetch:
        def fetch(self,url):raise AssertionError('Collector ignored cancellation')
    assert collect(store,NoFetch(),sources,stop_event=stop)==[]
