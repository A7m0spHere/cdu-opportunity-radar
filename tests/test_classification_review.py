from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_queue import JobQueue
from cdu_radar.classification import classify
from cdu_radar.llm import LLMConfig
from cdu_radar.review_contract import ReviewAnalyzer, mock_payload
from cdu_radar.review_api import validate_public_v4
from cdu_radar.models import Article
from cdu_radar.lan_preview import upstream_read
from test_auto_publish import auto_service, NOW, add, URL
from test_rss_service import request, loopback_only


@pytest.mark.parametrize('title,body,kind,absent',[
    ('关于做好2027届本科毕业设计（论文）工作的通知','规范AI工具使用，毕业论文学术不端检测。','notice',{'design','ai','humanities'}),
    ('关于普通话水平测试的考前通知','已报名考生参加计算机辅助测试。','notice',{'computing'}),
    ('期末考试工作通知','不得使用QQ、微信等聊天软件。','notice',{'computing'}),
    ('我院程序设计竞赛首次摘金','本次比赛取得好成绩。','news',{'design'}),
    ('学院大赛顺利举办','参赛队伍完成了决赛答辩。','news',set()),
    ('开拓杯评审圆满落幕','本次评审已经完成。','news',set()),
    ('交流分享会顺利举办','学院举办了交流分享。','news',set()),
    ('程序设计竞赛获奖报道','在创新创业学院和教务处支持下完成了比赛。','news',{'innovation','design'}),
])
def test_audited_classification_regressions(title,body,kind,absent):
    c=classify(title,body)
    assert c['type']==kind and not set(c['topics'])&absent
    assert c['version']==2


def analyzer(mock=True):
    return ReviewAnalyzer(LLMConfig('https://provider.example.test/v1','test-model',retries=0,
        response_format='json_object',analysis_mode='classification_review_v2'),mock=mock)


def seed_review(store,tmp_path, *, kind='notice', quote='请各学院提交教务材料，具体工作事项如下。'):
    add(store,title='工作事项',body=quote+' 本文仅用于合成分类验证，完整内容以原文为准。')
    a=analyzer();repo=AnalysisRepository(store);notice=repo.input(1);supplied,_=a.prepare(notice)
    payload=mock_payload(supplied)
    payload['classification']={'type':kind,'domains':['academic_affairs'],'topics':[],
        'reason':'根据教务材料提交安排判定为通知公告。','evidence':[
            {'field':'type','value':kind,'quote':quote},
            {'field':'domain','value':'academic_affairs','quote':quote}]}
    result=a.validate(payload,notice,supplied,NOW)
    state=tmp_path/'review.sqlite3';raw=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    with JobQueue(state,raw) as q:
        q.scan(a,notice_ids=[1])
        q.db.execute('UPDATE jobs SET state=?,result=?',(result['state'],json.dumps(result,ensure_ascii=False)))
    return a,state,payload,notice,supplied


def test_model_can_correct_unknown_without_legacy_keyword_agreement(store,tmp_path):
    a,_,p,n,s=seed_review(store,tmp_path)
    assert classify(n.title,n.content)['type']=='unknown'
    result=a.validate(p,n,s,NOW)
    assert result['classification_valid'] and result['classification']['type']=='notice'
    assert result['publication_permission'] is False
    p['classification']['evidence'][0]['quote']='这是原文中没有的通知安排'
    with pytest.raises(ValueError,match='Unlocatable'):a.validate(p,n,s,NOW)


def test_bad_semantic_topic_does_not_overwrite_rule_classification(store,tmp_path):
    a,_,p,n,s=seed_review(store,tmp_path,quote='请各学院提交教务材料，规范毕业设计安排。')
    p['classification']['topics']=['design']
    p['classification']['evidence'].append({'field':'topic','value':'design','quote':'规范毕业设计安排。'})
    result=a.validate(p,n,s,NOW)
    assert result['state']=='partial' and not result['classification_valid']


def test_selection_restricts_scan_and_dispatch_even_with_other_queued_jobs(store,tmp_path):
    add(store);add(store,url=URL.replace('990001','990002'))
    a=analyzer();raw=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    with JobQueue(tmp_path/'queue',raw) as q:
        assert q.scan(a,notice_ids=[2])['scanned']==1
        assert {r[0] for r in q.db.execute('SELECT notice_id FROM jobs')}=={2}
        q.scan(a)
        assert q.claim(a,notice_ids=[2])['notice_id']==2
        assert q.claim(a,notice_ids=[2]) is None
        assert q.db.execute('SELECT state FROM jobs WHERE notice_id=1').fetchone()[0]=='queued'
        with pytest.raises(ValueError):q.scan(a,notice_ids=[999])


def test_explicit_revalidation_keeps_ledger_and_checks_model_identity(store,tmp_path):
    a,_,payload,n,s=seed_review(store,tmp_path)
    old=analyzer();old.version+=':old-validator'
    raw=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    with JobQueue(tmp_path/'revalidation.sqlite3',raw) as q:
        q.scan(old,notice_ids=[1])
        result=old.validate(payload,n,s,NOW)
        q.db.execute('UPDATE jobs SET state=?,result=?',(result['state'],json.dumps(result)))
        ledger=q.ledger()
        output=q.revalidate(a,[1],old.version)
        assert output['model_requests']==0 and output['revalidated'][0]['state']=='succeeded'
        assert q.ledger()==ledger
        current=json.loads(q.db.execute('SELECT result FROM jobs WHERE pipeline_version=?',(a.version,)).fetchone()[0])
        assert current['revalidated_from']['pipeline_version']==old.version
        changed=ReviewAnalyzer(replace(a.config,model='different-model'),mock=True)
        with pytest.raises(ValueError,match='identity changed'):q.revalidate(changed,[1],old.version)


def sleeping_child(sender,*args):
    import time
    time.sleep(10)


def test_worker_stop_terminates_model_subprocess(store,tmp_path):
    import threading,time,multiprocessing
    from cdu_radar.async_worker import isolated_run
    a,_,_,n,_=seed_review(store,tmp_path)
    stop=threading.Event();stop.set();started=time.monotonic()
    result,error=isolated_run(a,n,NOW,lambda e:None,target=sleeping_child,stop_event=stop)
    assert result is None and error=='worker_lost' and time.monotonic()-started<5
    assert not multiprocessing.active_children()


def test_v4_model_projection_filters_rss_and_legacy_compatibility(auto_service,tmp_path,monkeypatch):
    store,c,server,_,_=auto_service
    a,state,_,_,_=seed_review(store,tmp_path)
    assert c.refresh()
    old=request(server,'/api/notices/v2')[2];c.analysis_analyzer=a;c.analysis_state=state;c.data_mode='offline_replay'
    monkeypatch.setattr(a,'analyze',lambda *a,**k:pytest.fail('HTTP invoked model'))
    assert c.refresh()
    status,_,body=request(server,'/api/notices/v4?type=notice&domain=academic_affairs')
    assert status==200
    p=json.loads(body);validate_public_v4(p)
    assert p['counts']['all']==1 and p['classification_review']['corrected']==1
    item=p['items'][0];assert item['classification']['method']=='llm_reviewed'
    assert item['classification_review']['rule']['type']=='unknown'
    path=p['subscription']['url'];status,_,rss=request(server,path)
    assert status==200 and len(ET.fromstring(rss).findall('./channel/item'))==1
    assert ET.fromstring(rss).findtext('./channel/item/{urn:cdu-radar:rss:v2}type')=='notice'
    assert request(server,'/api/notices/v2')[2]!=b''
    # Only lifetimes/refresh stamps differ on v2, not the legacy classification.
    previous=json.loads(old)['items'][0]['classification']
    assert json.loads(request(server,'/api/notices/v2')[2])['items'][0]['classification']==previous
    assert upstream_read(server.server_address[1],'/api/notices/v4')[0]==200
    forged=json.loads(body);forged['items'][0]['classification_review']['private_path']='private'
    with pytest.raises(ValueError):validate_public_v4(forged)
    # Source updates invalidate cached model correction immediately.
    add(store,title='更新后的工作事项',body='请各学院重新提交教学材料，原先安排现已调整，具体内容请核对新原文。')
    assert c.refresh()
    stale=json.loads(request(server,'/api/notices/v4')[2])['items'][0]
    assert stale['classification']['method']=='rules' and stale['classification_review']['state']=='stale'


def test_unavailable_review_queue_preserves_all_originals(auto_service,tmp_path):
    store,c,server,_,_=auto_service;add(store)
    c.analysis_analyzer=analyzer();c.analysis_state=tmp_path/'missing';c.data_mode='offline_replay'
    assert c.refresh()
    p=json.loads(request(server,'/api/notices/v4')[2]);validate_public_v4(p)
    assert len(p['items'])==1 and p['classification_review']['failed']==1
    assert request(server,'/rss/v2/all.xml')[0]==200
    assert request(server,'/api/notices/v4?domain=private')[0]==400


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI only')
def test_dpapi_roundtrip_stores_no_plaintext(tmp_path):
    from cdu_radar.credentials import save_key,read_key
    key='synthetic-key-only-for-dpapi-test';path=tmp_path/'key.dpapi'
    save_key(path,key)
    assert read_key(path)==key and key.encode() not in path.read_bytes()
