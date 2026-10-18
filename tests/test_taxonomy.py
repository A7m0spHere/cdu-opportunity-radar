from dataclasses import replace
import json
from pathlib import Path
import threading

import pytest

from cdu_radar.auto_service import AutoFeedController
from cdu_radar.coverage import inventory, markdown
from cdu_radar.models import Article
from cdu_radar.sources import load_sources
from cdu_radar.taxonomy import API_V2, classify, validate_public_v2
from cdu_radar.rss_service import make_server
from cdu_radar.cli import main
from test_auto_publish import auto_service, add, NOW, URL, BODY, entries
from test_rss_service import loopback_only, request


def api(server,suffix=''):
    status,headers,body=request(server,API_V2+suffix)
    return status,headers,json.loads(body) if status==200 else body


@pytest.mark.parametrize('title,body,kind,topics',[
    ('人工智能编程竞赛报名','本科生可参加的人工智能与计算机软件竞赛。','competition',{'ai','computing'}),
    ('学院科研动态','我院近期开展药学课题交流，本文是学院动态报道。','news',{'pharmacy'}),
    ('普通教学调整通知','课程时间调整，请全体同学关注官方安排。','notice',set()),
    ('科研项目招募','招募本科生参加创新实践研究。','research',{'innovation'}),
    ('药学学术报告','本场讲座介绍药物研究的近期进展。','event',{'pharmacy'}),
    ('一段尚未分类的信息','这是无明显类型或主题词的独立测试内容。','unknown',set()),
])
def test_type_and_topics_are_separate_and_explainably_unclassified(title,body,kind,topics):
    c=classify(title,body)
    assert c['type']==kind and set(c['topics'])==topics
    assert c['model_state']=='not_analyzed'
    assert c['type_state']==('unclassified' if kind=='unknown' else 'classified')
    assert c['topic_state']==('tagged' if topics else 'unclassified')


def test_ai_word_boundaries_do_not_tag_email_fragments():
    assert 'ai' not in classify('普通通知','mail plain training sample')['topics']


def test_v1_exact_fields_and_rss_unchanged_while_v2_adds_contract(auto_service):
    store,c,server,_,_=auto_service
    old_rss=request(server,'/feed.xml')[2]
    old=json.loads(request(server,'/api/notices')[2])
    assert set(old)=={'schema_version','items','categories','counts','refreshed_at','data_mode','valid_for_ms','poll_after_ms'}
    assert 'classification' not in old['items'][0]
    status,h,payload=api(server)
    assert status==200 and payload['schema_version']==2 and payload['taxonomy_version']==1
    validate_public_v2(payload)
    entry=payload['items'][0]
    assert entry['publisher_id']=='computer' and entry['discovery_source_ids']==['computer']
    assert entry['guid']==old['items'][0]['guid'] and entry['category']==old['items'][0]['category']
    assert request(server,'/feed.xml')[2]==old_rss
    assert 'ETag' not in h and 'no-store' in h['Cache-Control']
    assert request(server,API_V2,headers={'If-None-Match':'*'})[0]==200
    assert request(server,API_V2,method='HEAD')[2]==b''


def test_combined_facets_multi_topics_do_not_duplicate_or_drop_ordinary_notices(auto_service):
    store,c,server,_,_=auto_service
    add(store,title='人工智能计算机竞赛报名',body=BODY+'\n人工智能、计算机和软件均可作为参赛主题。')
    add(store,url=URL.replace('990001','990002'),title='普通课程调整通知',body='这是全体同学的课程调整通知，详细安排请查看原文。')
    add(store,url=URL.replace('990001','990003'),title='药学学院新闻动态',body='药学学院近期开展工作交流，本文为例行新闻动态。')
    assert c.refresh()
    payload=api(server)[2]
    assert payload['counts']['all']==3
    selected=api(server,'?source=computer&type=competition&topic=computing,ai&status=possible&from=2026-10-01&to=2026-10-20')[2]
    assert selected['counts']['all']==1 and selected['items'][0]['source_url']==URL
    assert selected['applied_filters']['topic']=='ai,computing'
    assert len({i['guid'] for i in payload['items']})==3
    assert api(server,'?type=notice')[2]['counts']['all']==1
    assert api(server,'?type=news&topic=pharmacy')[2]['counts']['all']==1
    # Source is a publisher identity, never the label or a host/URL fragment.
    assert api(server,'?source=computer.cdu.edu.cn')[0]==400


def test_unknown_dates_are_not_filled_from_first_aggregation(auto_service):
    store,c,server,_,_=auto_service
    add(store,url=URL.replace('990001','990002'),title='日期未知的普通通知',published=None)
    assert c.refresh()
    unknown=api(server,'?published=unknown')[2]['items']
    assert len(unknown)==1 and unknown[0]['source_published_date'] is None
    assert len(api(server,'?from=2026-10-01')[2]['items'])==1
    assert api(server,'?published=unknown&from=2026-10-01')[0]==400
    assert api(server,'?from=2026-10-15&to=2026-10-15')[2]['counts']['all']==1


@pytest.mark.parametrize('query',[
    '?source=computer&source=computer','?source=','?topic=ai,ai','?topic=all,ai',
    '?type=DROP%20TABLE%20notices','?source=https%3A%2F%2Fevil.invalid','?file=../test.sqlite3',
    '?status=ready','?view=featured','?from=2026-02-30','?from=2026-10-20&to=2026-10-01',
    '?topic=%GG','?topic=%FF','?topic='+'a'*110,
])
def test_invalid_filter_inputs_are_rejected_without_file_or_network_access(auto_service,query):
    _,_,server,_,_=auto_service
    status,_,body=api(server,query)
    assert status==400 and body==b'Invalid notice filters\n'


def test_repost_filters_by_publisher_not_discovery_column(store,tmp_path,loopback_only):
    store.sync_sources(load_sources())
    url='https://msys.cdu.edu.cn/info/1173/990001.htm'
    store.save_notice('innovation',url,Article('合成艺术设计比赛报名','2026-10-15',BODY,content_source_id='msys'),seen_at=NOW.isoformat())
    store.save_notice('computer',url,Article('合成艺术设计比赛报名','2026-10-15',BODY,content_source_id='msys'),seen_at=NOW.isoformat())
    path=Path(store.db.execute('PRAGMA database_list').fetchone()[2]);c=AutoFeedController(path,tmp_path/'cross-state',clock=lambda:NOW)
    assert c.refresh();server=make_server(c,port=0);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);thread.start()
    try:
        item=api(server,'?source=msys')[2]['items'][0]
        assert item['publisher_id']=='msys' and item['discovery_source_ids']==['computer','innovation']
        assert api(server,'?source=computer')[2]['items']==[]
        assert len(entries(server))==1
    finally:server.shutdown();server.server_close();thread.join();c.close()


def test_filtered_json_never_bypasses_expiry_or_source_change(auto_service):
    store,c,server,_,mono=auto_service
    assert api(server,'?source=computer')[0]==200
    add(store,body=BODY+'\n原文修改使派生数据立即失效。')
    assert api(server,'?source=computer')[0]==503
    assert c.refresh() and api(server,'?source=computer')[0]==200
    mono[0]=31
    assert api(server,'?type=competition')[0]==503


def test_publisher_revocation_without_items_invalidates_filter_metadata(auto_service,monkeypatch):
    from cdu_radar import auto_publish as module
    _,c,server,_,_=auto_service
    catalog=load_sources()
    monkeypatch.setattr(module,'load_sources',lambda:[replace(s,status='pending') if s.unit_id=='department-tuanwei' else s for s in catalog])
    assert api(server)[0]==503
    assert c.refresh()
    assert api(server,'?source=department-tuanwei')[0]==400


def test_filter_views_match_legacy_rss_and_do_not_write_database(auto_service):
    store,c,server,_,_=auto_service
    before=store.db.total_changes
    for scope in ('current','history'):
        items=api(server,'?view='+scope)[2]['items']
        assert {i['guid'] for i in items}=={i.id for i in entries(server,'/'+scope+'.xml')}
    assert store.db.total_changes==before


def test_inventory_counts_queue_and_observation_semantics_no_network(capsys):
    report=inventory();s=report['summary']
    assert (s['units'],s['registered_entries'],s['candidate_columns'],s['unique_candidate_urls'])==(42,186,153,152)
    assert s['collection_validated_columns']==s['implemented_columns']==12
    assert s['remaining_columns']==141 and s['long_term_healthy_columns'] is None
    assert report['network_requests']==0
    assert all(c['long_term_health']=='not_established' and c['current_accessibility']=='not_checked' for c in report['columns'])
    assert {b for b in report['batch_counts'] if b.startswith('B')}=={'B0','B1','B2','B3'}
    assert all(not c['collection_validated'] for c in report['columns'] if c['batch'] in {'B1','B2','B3'})
    assert len(report['units'])==42 and all('parent_unit' in u for u in report['units'])
    assert '186' in markdown(report)
    assert main(['source-coverage','--format','json'])==0
    assert json.loads(capsys.readouterr().out)['summary']==s
