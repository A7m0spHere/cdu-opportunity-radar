import json
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest

from cdu_radar.cli import main
from cdu_radar.rss_consumer import config, poll, export_run, parse_feed, FeedError, NS, V2
from test_auto_publish import auto_service, add, URL
from test_rss_service import request, loopback_only
from test_rss_v1 import setup_featured


def subscriptions(path='/rss/v2/all.xml?audience=undergraduate'):
    return config({'schema_version':1,'subscriptions':[{'name':'本科生综合','url':'http://127.0.0.1:8765'+path}]})


def body(rows=(), *, path='/rss/v2/all.xml?audience=undergraduate', **meta):
    root=ET.Element('rss',version='2.0');channel=ET.SubElement(root,'channel')
    def tag(node,name,value):ET.SubElement(node,name).text=str(value)
    for k,v in dict(canonical_path=path,truncated='false',total=len(rows),returned=len(rows),**meta).items():
        tag(channel,'{'+NS+'}'+k,v)
    for guid,title in rows:
        item=ET.SubElement(channel,'item')
        for k,v in dict(guid=guid,title=title,link=URL,description='<p>本科生须于10月25日前报名，具体资格未知。</p>').items():tag(item,k,v)
        ET.SubElement(item,'source',url=URL).text='计算机学院'
        for k,v in dict(source_published_date='unknown',registration_status='unknown',listing='unknown').items():tag(item,'{'+NS+'}'+k,v)
        for k,v in dict(type='competition',domains='competition',topics='ai,computing',undergraduate_state='relevant').items():tag(item,'{'+V2+'}'+k,v)
    return ET.tostring(root)


def client(content=b'',status=200,error=None):
    def handle(req):
        if error:raise error('synthetic failure',request=req)
        return httpx.Response(status,content=content)
    return httpx.Client(transport=httpx.MockTransport(handle))


def drafts(report):return report['subscriptions'][0]['drafts']


def test_initialization_new_duplicate_restart_and_same_guid_updates(tmp_path):
    state=tmp_path/'consumer.sqlite3';subs=subscriptions()
    with client(body([('old','既有通知')])) as c:
        first=poll(subs,state,client=c);assert not drafts(first)
        assert first['subscriptions'][0]['status']=='initialized'
    with client(body([('old','既有通知'),('new','新增通知')])) as c:
        report=poll(subs,state,client=c)
        assert [d['guid'] for d in drafts(report)]==['new']
        assert not drafts(poll(subs,state,client=c))
    with client(body([('old','既有通知'),('new','同 GUID 原文更新')])) as c:
        report=poll(subs,state,client=c)
        assert drafts(report)[0]['change']=='updated'
        text=drafts(report)[0]['markdown']
        assert URL in text and '来源日期：未知' in text and '参与资格：未知' in text and '报名状态：未知' in text
        assert report['qq_deliveries']==report['model_requests']==0
    # Re-export a committed run after a simulated output failure, with no poll.
    restored=export_run(state,tmp_path/'restored',report['run_id'])
    assert restored==report and (tmp_path/'restored'/'briefing.md').exists()
    assert export_run(state,tmp_path/'replay',first['run_id'])==first


@pytest.mark.parametrize('status,error,expected',[(503,None,'unavailable'),(429,None,'rate_limited'),
    (302,None,'http_error'),(200,httpx.ReadTimeout,'timeout'),(200,httpx.ConnectError,'connection_failed')])
def test_failed_feed_never_initializes_or_reuses_old_content(tmp_path,status,error,expected):
    state=tmp_path/'state.sqlite3';subs=subscriptions()
    with client(body([('old','已有通知')]),status,error) as c:
        result=poll(subs,state,client=c)
        assert result['subscriptions'][0]['status']==expected and not drafts(result)
    with client(body([('old','已有通知')])) as c:
        assert poll(subs,state,client=c)['subscriptions'][0]['status']=='initialized'


def test_healthy_empty_is_distinct_from_failure(tmp_path):
    with client(body()) as c:
        assert poll(subscriptions(),tmp_path/'state',client=c)['subscriptions'][0]['status']=='initialized'
        assert poll(subscriptions(),tmp_path/'state',client=c)['subscriptions'][0]['status']=='empty'
    with client(body([('first','首次真正新增')])) as c:
        assert len(drafts(poll(subscriptions(),tmp_path/'state',client=c)))==1


@pytest.mark.parametrize('content',[b'<rss>',b'<!DOCTYPE rss [<!ENTITY a "bad">]><rss/>',b'x'* (1024*1024+1)],ids=['malformed','entity','oversized'])
def test_invalid_xml_is_a_typed_failure(tmp_path,content):
    with client(content) as c:
        result=poll(subscriptions(),tmp_path/'state',client=c)
        assert result['subscriptions'][0]['status'] in {'invalid_xml','response_too_large'} and not drafts(result)


@pytest.mark.parametrize('field,value',[('topics','pharmacy'),('undergraduate_state','excluded'),('domains','career')])
def test_direction_mismatch_is_rejected(field,value):
    path='/rss/v2/all.xml?topic=ai&domain=competition&audience=undergraduate'
    root=ET.fromstring(body([('a','合成通知')],path=path));root.find('./channel/item/{'+V2+'}'+field).text=value
    with pytest.raises(FeedError,match='filter_mismatch'):
        parse_feed(ET.tostring(root),subscriptions(path)[0])


def test_truncation_duplicate_and_incomplete_featured_are_rejected():
    with pytest.raises(FeedError):parse_feed(body([('same','一'),('same','二')]),subscriptions()[0])
    content=body([('a','通知')]);root=ET.fromstring(content);root.find('./channel/{'+NS+'}truncated').text='true'
    with pytest.raises(FeedError,match='truncated_feed'):parse_feed(ET.tostring(root),subscriptions()[0])
    path='/rss/v2/featured.xml?domain=competition'
    content=body([],path=path,analysis_state='pending',coverage=json.dumps(dict(total=3,complete=2,pending=1,failed=0)))
    with pytest.raises(FeedError,match='featured_incomplete'):parse_feed(content,subscriptions(path)[0])


@pytest.mark.parametrize('url',['https://www.cdu.edu.cn/rss/v2/all.xml','http://localhost:8765/rss/v2/all.xml',
    'http://127.0.0.1:8765/approved.xml','http://user:secret@127.0.0.1:8765/rss/v2/all.xml',
    'http://127.0.0.1:8765/rss/v2/all.xml?topic=invalid','http://127.0.0.1:8765/rss/v2/all.xml#x'])
def test_fixed_url_and_filter_validation(url):
    with pytest.raises(ValueError):config({'schema_version':1,'subscriptions':[{'name':'x','url':url}]})


def test_subscriptions_have_independent_watermarks(tmp_path):
    data={'schema_version':1,'subscriptions':[{'name':'综合','url':'http://127.0.0.1:8765/rss/v2/all.xml?audience=undergraduate'},
        {'name':'AI','url':'http://127.0.0.1:8765/rss/v2/all.xml?audience=undergraduate&topic=ai'}]}
    subs=config(data)
    def handle(req):
        s=next(s for s in subs if s['url']==str(req.url))
        return httpx.Response(200,content=body([('same','通知')],path=s['url'].partition(':8765')[2]))
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        poll(subs[:1],tmp_path/'state',client=c)
        r=poll(subs,tmp_path/'state',client=c)
        assert r['subscriptions'][0]['status']=='ready' and r['subscriptions'][1]['status']=='initialized'


def test_live_loopback_all_three_directions_and_featured(auto_service,tmp_path):
    store,controller,server,*_=auto_service
    setup_featured(auto_service,tmp_path/'analysis')
    base=f'http://127.0.0.1:{server.server_address[1]}'
    subs=config({'schema_version':1,'subscriptions':[
        {'name':'本科生综合','url':base+'/rss/v2/all.xml?audience=undergraduate'},
        {'name':'AI与计算机','url':base+'/rss/v2/all.xml?topic=ai&audience=undergraduate'},
        {'name':'竞赛双创','url':base+'/rss/v2/featured.xml?domain=competition'}]})
    first=poll(subs,tmp_path/'consumer-state')
    assert all(s['status']=='initialized' for s in first['subscriptions'])
    add(store,url=URL.replace('990001','990099'),title='本科生人工智能竞赛报名通知',
        body='面向本科生开放人工智能竞赛报名，请于2026年10月25日前提交参赛材料，其他要求以原文为准。')
    assert controller.refresh()
    report=poll(subs,tmp_path/'consumer-state')
    assert len(report['subscriptions'][0]['drafts'])==len(report['subscriptions'][1]['drafts'])==1
    assert report['subscriptions'][2]['status']=='featured_incomplete' and not report['subscriptions'][2]['drafts']
    # All source writes are the synthetic producer operations above, never the consumer.
    before=store.db.total_changes
    poll(subs,tmp_path/'consumer-state')
    assert store.db.total_changes==before


def test_cli_export_and_foreign_state_refusal(tmp_path,capsys):
    with client(body()) as c:report=poll(subscriptions(),tmp_path/'state',client=c)
    assert main(['rss-consumer','export','--state-db',str(tmp_path/'state'),'--directory',str(tmp_path/'output')])==0
    assert json.loads(capsys.readouterr().out)['run_id']==report['run_id']
    assert main(['rss-consumer','export','--state-db',str(tmp_path/'state'),'--directory',str(tmp_path/'output')])==2
    import sqlite3
    with sqlite3.connect(tmp_path/'foreign') as d:d.execute('CREATE TABLE notices(id INTEGER)')
    before=(tmp_path/'foreign').read_bytes()
    with client(body()) as c:
        with pytest.raises(ValueError,match='not consumer'):poll(subscriptions(),tmp_path/'foreign',client=c)
    assert before==(tmp_path/'foreign').read_bytes()
