"""Student page and JSON share the exact live RSS gate; no new data channel."""
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import threading
from xml.etree import ElementTree as ET

import pytest

from cdu_radar.auto_publish import NS, AutoRepository, failed, MAX_SOURCE_AGE
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.models import Article
from cdu_radar.rss_service import make_server
from test_auto_publish import auto_service, add, entries, NOW, URL, BODY, plan
from test_rss_service import loopback_only, request


def api(server):
    status,headers,body=request(server,'/api/notices')
    return status,headers,json.loads(body) if status==200 else body


def test_page_and_assets_exact_public_routes(auto_service):
    store,c,s,_,_=auto_service
    for path,mime in [('/','text/html'),('/app.js','text/javascript'),('/app.css','text/css'),('/favicon.svg','image/svg+xml')]:
        status,h,body=request(s,path)
        assert status==200 and h['Content-Type'].startswith(mime) and body
        assert 'no-store' in h['Cache-Control'] and "script-src 'self'" in h['Content-Security-Policy']
        assert h['Cross-Origin-Resource-Policy']=='same-origin' and h['Referrer-Policy']=='no-referrer'
        assert request(s,path,method='HEAD')[2]==b''
    assert '校园机会雷达' in request(s,'/')[2].decode()
    assert request(s,'/approved.xml')[0]==410


@pytest.mark.parametrize('path',[
    '/index.html','/web/index.html','/api','/api/notices/','/api/notices?file=../test.sqlite3',
    '/api/notices?scope=current','/app.js?v=1','/favicon.ico','/data','/test.sqlite3','/owner.json',
    '/publisher.lock','/approved.xml?x=1','/../auto_publish.py','/%2e%2e/sources.json','/admin','/.env',
])
def test_no_directory_alias_or_admin_route(auto_service,path):
    _,_,s,_,_=auto_service
    assert request(s,path)[0]==404


@pytest.mark.parametrize('method',['POST','PUT','PATCH','DELETE','OPTIONS'])
def test_page_api_is_readonly(auto_service,method):
    _,_,s,_,_=auto_service
    for path in ('/','/api/notices','/app.js'):
        assert request(s,path,method=method)[0]==405


def test_json_rss_consistency_times_and_no_304(auto_service):
    store,c,s,_,_=auto_service
    status,h,payload=api(s)
    assert status==200 and h['Content-Type']=='application/json; charset=utf-8'
    assert 'ETag' not in h and 'Access-Control-Allow-Origin' not in h
    assert request(s,'/api/notices',headers={'If-None-Match':'*'})[0]==200
    assert payload['schema_version']==1 and payload['data_mode']=='live'
    assert 0<payload['valid_for_ms']<=30000 and payload['poll_after_ms']==5000
    item=payload['items'][0]
    assert item['source_published_date']=='2026-10-15' and item['source_checked_at']==NOW.isoformat()
    assert item['first_aggregated_at']==NOW.isoformat() and item['listing']=='current'
    assert payload['counts']=={'all':1,'current':1,'history':0,'unknown':0}
    root=ET.fromstring(request(s,'/feed.xml')[2]);rss=root.find('./channel/item')
    assert item['guid']==rss.findtext('guid') and item['title']==rss.findtext('title')
    assert item['source_url']==rss.findtext('link') and item['category']==rss.findtext('category')
    assert item['registration_status']==rss.findtext('{'+NS+'}registration_status')
    assert not {'notice_id','content','facts','audience','summary_evidence','db','excluded'} & item.keys()
    before=store.db.total_changes
    for _ in range(3):assert api(s)[0]==200
    assert store.db.total_changes==before


def test_unknown_historical_not_closed_and_empty_current(auto_service):
    store,c,s,_,_=auto_service
    add(store,body='这是合成的历史讲座报道，没有报名时间。仅用来检查未知报名状态的显示。',published='2024-10-15',title='已举办的合成校园讲座')
    assert api(s)[0]==503 and c.refresh()
    payload=api(s)[2]
    item=payload['items'][0]
    assert item['listing']=='history' and item['registration_status']=='unknown'
    assert payload['counts']['current']==0 and not entries(s,'/current.xml')


@pytest.mark.parametrize('body,status,listing',[
    (BODY.replace('我校在校本科生','仅限教师'),'unknown','unknown'),
    (BODY.replace('我校在校本科生','仅限北京大学在校本科生'),'unknown','unknown'),
    (BODY.replace('我校在校本科生','仅面向研究生'),'unknown','unknown'),
    (BODY+'\n报名已截止，不再接受新报名。','closed','history'),
    (BODY+'\n报名已经截止，不再接受新报名。','closed','history'),
    (BODY+'\n并非报名已截止，仍按原通知时间办理。','possible','current'),
    (BODY+'\n如果报名已截止，请等待下一期通知。','possible','current'),
    (BODY.replace('参与对象：我校在校本科生。','参与对象未提供。'),'possible','current'),
    ('合成竞赛通知，参与对象和截止日期未提供。报名已截止，不再接受新报名。','closed','history'),
    (BODY.replace('我校在校本科生','仅限教师')+'\n报名已截止。','closed','history'),
],ids=['teachers-only','other-university-only','graduate-only','closed-short','closed-long',
       'negated-closure','conditional-closure','audience-unknown','closed-without-date','ineligible-and-closed'])
def test_eligibility_and_closure_updates_agree_across_api_and_rss(auto_service,body,status,listing):
    store,c,s,_,_=auto_service
    original=entries(s)[0]
    tag=request(s,'/current.xml')[1]['ETag']
    add(store,body=body)
    # A changed source invalidates both data channels, including conditional RSS.
    assert api(s)[0]==503
    assert request(s,'/current.xml',headers={'If-None-Match':tag})[0]==503
    assert c.refresh()
    payload=api(s)[2]
    item=payload['items'][0]
    assert (item['registration_status'],item['listing'])==(status,listing)
    assert payload['counts']=={'all':1,'current':int(listing=='current'),
        'history':int(listing=='history'),'unknown':int(listing=='unknown')}
    all_entries=entries(s)
    assert len(all_entries)==1
    updated=all_entries[0]
    assert (updated.id,updated.published)==(original.id,original.published)
    assert item['guid']==updated.id and item['source_url']==updated.link==URL
    root=ET.fromstring(request(s,'/feed.xml')[2]);rss=root.find('./channel/item')
    assert rss.findtext('{'+NS+'}registration_status')==status
    assert rss.findtext('{'+NS+'}listing')==listing
    for scope in ('current','history'):
        assert {e.id for e in entries(s,'/'+scope+'.xml')}==({item['guid']} if listing==scope else set())


@pytest.mark.parametrize('title',[
    '教师科研项目申报通知','学科竞赛经费报销通知','人工智能岗位聘任通知',
    '学科竞赛获奖推荐名单公示',
])
def test_administrative_deadline_never_becomes_current_opportunity(store,title):
    add(store,title=title,body='这是行政事务合成边界样例，供工作人员按要求办理。报名截止：2026年10月20日。')
    item=plan(store)['items'][0]
    assert item['category']=='其他通知' and item['listing']!='current'
    assert item['registration_status']=='unknown'


@pytest.mark.parametrize('fault',['withdrawn','parse_failed','unavailable','tamper','stale','missing_db','wal','write_error'])
def test_api_never_serves_stale_data_on_rss_failure(auto_service,tmp_path,monkeypatch,fault):
    store,c,s,point,mono=auto_service
    assert api(s)[0]==200
    if fault in {'withdrawn','parse_failed','unavailable'}:
        with store.db:failed(store.db,URL,fault,'synthetic',NOW.isoformat())
    elif fault=='tamper':
        with store.db:store.db.execute("UPDATE notices SET content=content||'tampered' WHERE id=1")
    elif fault=='stale':mono[0]=31
    elif fault=='missing_db':c.db=tmp_path/'missing.sqlite3'
    elif fault=='wal':store.db.execute('PRAGMA journal_mode=WAL')
    else:
        from cdu_radar import auto_service as module
        monkeypatch.setattr(module,'atomic_write',lambda *a:(_ for _ in ()).throw(OSError('synthetic')))
        assert not c.refresh()
    assert request(s,'/feed.xml',headers={'If-None-Match':'*'})[0]==503
    assert request(s,'/api/notices',headers={'If-None-Match':'*'})[0]==503
    # The shell contains no notices; it remains available to render the failure.
    assert request(s,'/')[0]==200
    assert URL.encode() not in request(s,'/')[2]


def test_pending_unit_stays_excluded_and_empty_data_is_normal(store,tmp_path,loopback_only):
    from cdu_radar.sources import load_sources
    store.sync_sources(load_sources())
    store.save_notice('computer','https://kjc.cdu.edu.cn/info/1010/90001.htm',
        Article('合成转载通知','2026-10-15',BODY,content_source_id='department-kjc'),seen_at=NOW.isoformat())
    c=AutoFeedController(Path(store.db.execute('PRAGMA database_list').fetchone()[2]),tmp_path/'pending',clock=lambda:NOW)
    assert c.refresh()
    s=make_server(c,port=0);t=threading.Thread(target=s.serve_forever,kwargs={'poll_interval':0.02},daemon=True);t.start()
    try:
        assert api(s)[2]['items']==[] and api(s)[2]['counts']['all']==0
        assert not entries(s) and request(s,'/')[0]==200
    finally:s.shutdown();s.server_close();t.join();c.close()


def test_updates_sorted_and_duplicate_url_not_republished(auto_service):
    store,c,s,point,_=auto_service
    add(store,url=URL.replace('990001','990002'),published=None,title='无日期的合成新通知')
    assert c.refresh()
    payload=api(s)[2];assert len(payload['items'])==2
    unknown=next(i for i in payload['items'] if i['source_published_date'] is None)
    assert unknown['source_checked_at'] and unknown['first_aggregated_at']
    before={i['guid'] for i in payload['items']}
    add(store,body=BODY+'\n原文更新：合成 & <script>文字</script>。')
    assert api(s)[0]==503 and c.refresh()
    new=api(s)[2]['items']
    assert {i['guid'] for i in new}==before and len({i['source_url'] for i in new})==2
    assert any('<script>' in i['summary'] for i in new)


def test_api_response_carries_remaining_validation_lifetime(auto_service):
    store,c,s,_,mono=auto_service
    mono[0]=25
    assert 0<api(s)[2]['valid_for_ms']<=5000
    mono[0]=31
    assert api(s)[0]==503


def test_windows_client_abort_does_not_log_handler_traceback(auto_service,monkeypatch,capsys):
    import http.client
    _,controller,server,_,_=auto_service
    original=controller.response_snapshot
    def aborted(snapshot,path):
        if path=='/api/notices':raise ConnectionAbortedError('Simulated Windows client abort')
        return original(snapshot,path)
    monkeypatch.setattr(controller,'response_snapshot',aborted)
    with pytest.raises(http.client.RemoteDisconnected):request(server,'/api/notices')
    assert 'Traceback' not in capsys.readouterr().err
    assert request(server,'/feed.xml')[0]==200


def test_source_allowlist_reloaded_for_each_read_gate(auto_service,monkeypatch):
    from dataclasses import replace
    from cdu_radar import auto_publish as module
    _,controller,server,_,_=auto_service
    original=module.load_sources()
    assert api(server)[2]['counts']['all']==1
    monkeypatch.setattr(module,'load_sources',lambda:[replace(s,status='pending') if s.id=='computer' else s for s in original])
    assert request(server,'/feed.xml')[0]==503 and api(server)[0]==503
    assert controller.refresh() and api(server)[2]['items']==[]
