"""M7/M8 synthetic objective-importance and public-projection regressions."""
import csv
import io
import json
from pathlib import Path
import sqlite3

import pytest

from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_cli import configuration
from cdu_radar.async_contract import mock_payload, validate
from cdu_radar.async_queue import JobQueue
from cdu_radar.featured import FeaturedPolicy, analysis_for, analysis_reader
from cdu_radar.featured_api import API_V3, validate_public_v3
from cdu_radar.featured_benchmark import export_dataset, load_dataset, import_labels, evaluate_dataset, COLUMNS
from cdu_radar.cli import main
from cdu_radar.models import Article
from cdu_radar.lan_preview import upstream_read
from test_auto_publish import auto_service, NOW, add, URL
from test_rss_service import loopback_only, request


CASES=[(r['title'],r['content'],r['model_level'],r['truth'],r['criterion']) for r in
       json.loads((Path(__file__).parent/'fixtures/featured_cases.json').read_text(encoding='utf-8'))['cases']]


def raw_path(store):return Path(store.db.execute('PRAGMA database_list').fetchone()[2])


def fill(store,tmp_path):
    a,_=configuration(None,True);repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=store.db
    # Existing fixture row becomes the first case, preserving its URL/GUID.
    for i,(title,body,*_) in enumerate(CASES,1):
        url=URL if i==1 else URL.replace('990001',str(990000+i))
        if i==2:url=url.replace('computer.cdu.edu.cn','bs.cdu.edu.cn')
        store.save_notice('computer' if i!=2 else 'business',url,Article(title,'2024-10-15' if i==3 else '2026-10-15',body),seen_at=NOW.isoformat())
    # Same final URL discovered by another college remains one original.
    store.save_notice('business',URL,Article(CASES[0][0],'2026-10-15',CASES[0][1]),seen_at=NOW.isoformat())
    state=tmp_path/'analysis.sqlite3'
    with JobQueue(state,raw_path(store)) as q:
        q.scan(a)
        for i,(_,body,level,*_) in enumerate(CASES,1):
            if level=='queued':continue
            row=q.db.execute('SELECT id FROM jobs WHERE notice_id=?',(i,)).fetchone()
            if level=='failed':q.db.execute("UPDATE jobs SET state='failed',error='timeout' WHERE id=?",(row[0],));continue
            n=repo.input(i);s,_=a.prepare(n);p=mock_payload(s)
            p['importance']={'kind':'inference','level':level,'reason':'合成重要性建议，不是真实模型结果','quotes':[body]}
            result=validate(p,n,s,a,NOW)
            q.db.execute('UPDATE jobs SET state=?,result=? WHERE id=?',(result['state'],json.dumps(result,ensure_ascii=False),row[0]))
    return a,state,repo


def v3(server,query=''):
    status,h,body=request(server,API_V3+query)
    return status,h,json.loads(body) if status==200 else body


def test_objective_rules_personal_filters_and_failed_unknown_are_separate(auto_service,tmp_path):
    store,c,server,_,_=auto_service;a,state,repo=fill(store,tmp_path)
    p=FeaturedPolicy()
    with analysis_reader(state,raw_path(store)) as q:
        values=[]
        for i in range(1,len(CASES)+1):
            n,result,status=analysis_for(q,repo,a,i,NOW)
            values.append(p.decide(n,result,status))
    assert [v['decision'] for v in values]==['selected','not_selected','selected','selected','not_selected','pending','pending']
    assert 'registration_unknown' in values[0]['uncertainties']
    assert values[5]['analysis_state']=='failed' and values[6]['analysis_state']=='queued'
    assert values[0]['evidence'][0]['criterion']=='student_rights'
    assert store.db.execute('SELECT COUNT(*) FROM notices').fetchone()[0]==7


def test_v3_all_featured_shared_filters_history_and_no_original_loss(auto_service,tmp_path,monkeypatch):
    store,c,server,_,_=auto_service;a,state,_=fill(store,tmp_path)
    monkeypatch.setattr(type(a),'analyze',lambda *args,**kwargs:pytest.fail('HTTP attempted model analysis'))
    monkeypatch.setattr(a.provider,'complete',lambda *args,**kwargs:pytest.fail('HTTP attempted provider transport'))
    c.data_mode='offline_replay';c.analysis_analyzer=a;c.analysis_state=state
    assert c.refresh()
    old=request(server,'/feed.xml')[2];v2=json.loads(request(server,'/api/notices/v2')[2])
    status,h,all_items=v3(server)
    assert status==200 and len(all_items['items'])==7
    validate_public_v3(all_items)
    assert 'ETag' not in h and 'no-store' in h['Cache-Control']
    assert all_items['featured']['state']=='degraded'
    assert all_items['featured']['coverage']=={'total':7,'complete':5,'selected':3,'not_selected':2,'pending':2,'failed':1,'not_analyzed':0,'in_progress':1,'uncertain':0}
    selected=v3(server,'?selection=featured')[2]
    assert len(selected['items'])==3 and len({i['guid'] for i in selected['items']})==3
    assert v3(server,'?selection=featured&type=competition&topic=ai,computing')[2]['counts']['all']==1
    historical=v3(server,'?selection=featured&view=history')[2]
    assert historical['counts']['all']==1 and historical['items'][0]['listing']=='history'
    assert all_items['featured']['ranges']['current']['state']=='ready'
    assert all_items['featured']['ranges']['history']['state']=='ready'
    assert historical['items'][0]['registration_status']=='unknown'
    assert v3(server,'?selection=featured&type=competition&status=possible')[2]['counts']['all']==0
    assert {i['guid'] for i in all_items['items']}=={i['guid'] for i in v2['items']}
    assert request(server,'/feed.xml')[2]==old
    assert all_items['subscription']=={'available':True,'url':'/rss/v1/all.xml','reason':None}
    assert request(server,API_V3,headers={'If-None-Match':'*'})[0]==200


def test_pending_degraded_empty_and_source_gate_do_not_hide_all(auto_service,tmp_path):
    store,c,s,_,_=auto_service
    pending=v3(s,'?selection=featured')[2]
    assert pending['featured']['state']=='pending' and pending['items']==[]
    a,_=configuration(None,True);c.analysis_analyzer=a;c.analysis_state=tmp_path/'missing';c.data_mode='offline_replay'
    degraded=v3(s,'?selection=featured')[2]
    assert degraded['featured']['state']=='degraded' and degraded['items']==[]
    assert v3(s)[2]['counts']['all']==1 and request(s,'/feed.xml')[0]==200
    empty=v3(s,'?type=news&selection=featured')[2]
    assert empty['featured']['state']=='ready' and empty['featured']['coverage']['total']==0
    add(store,body='原文已经实质改变，旧快照必须先失效，不能显示旧的通知内容。')
    assert v3(s)[0]==503 and request(s,'/api/notices/v2')[0]==503


def test_queue_change_applies_without_source_refresh_and_invalid_cache_not_reused(auto_service,tmp_path):
    store,c,s,_,_=auto_service;a,state,_=fill(store,tmp_path)
    c.data_mode='offline_replay';c.analysis_analyzer=a;c.analysis_state=state;assert c.refresh()
    assert v3(s,'?selection=featured')[2]['counts']['all']==3
    with sqlite3.connect(state) as db:
        db.execute("UPDATE jobs SET state='failed',error='timeout' WHERE notice_id=1")
        row=db.execute('SELECT result FROM jobs WHERE notice_id=3').fetchone()
        result=json.loads(row[0]);result['proposal']['importance']['quotes']=['虚构官方事实']
        db.execute('UPDATE jobs SET result=? WHERE notice_id=3',(json.dumps(result),))
    payload=v3(s,'?selection=featured')[2]
    assert payload['counts']['all']==1 and payload['featured']['state']=='degraded'
    assert v3(s)[2]['counts']['all']==7


@pytest.mark.parametrize('query',['?selection=wrong','?selection=featured&selection=all','?selection=',
    '?path=../analysis.sqlite3','?selection=featured&type=oops','?selection=featured&topic=ai,ai',
    '?from=2026-12-31&to=2026-01-01','?%zz=1','?source=%FF','?selection=featured&topic=all,ai'])
def test_v3_strict_queries(auto_service,query):
    assert v3(auto_service[2],query)[0]==400


def test_v3_proxy_whitelist_and_no_private_model_data(auto_service,tmp_path):
    store,c,s,_,_=auto_service;a,state,_=fill(store,tmp_path)
    c.data_mode='offline_replay';c.analysis_analyzer=a;c.analysis_state=state;assert c.refresh()
    status,body,_,_=upstream_read(s.server_address[1],API_V3+'?selection=featured')
    assert status==200
    text=body.decode()
    for key in ['input_hash','proposal','model_name','lease_token','request_reservations','notice_id','api_key']:
        assert '"'+key+'"' not in text
    for target,key in [('item','input_hash'),('featured','private_path'),('evidence','raw_model_response'),('overview','state_db'),('subscription','token')]:
        payload=json.loads(body)
        node=payload['items'][0] if target=='item' else payload['items'][0]['featured'] if target=='featured' else payload['items'][0]['featured']['evidence'][0] if target=='evidence' else payload['featured'] if target=='overview' else payload['subscription']
        node[key]='must reject'
        with pytest.raises(ValueError):validate_public_v3(payload)


def test_important_teacher_admin_cannot_sneak_in_as_student_opportunity(store,tmp_path):
    store.save_notice('computer',URL,Article('教师职称报名','2026-10-15','仅限教师报名参与职称评审，此为日常行政安排，请工作人员核对。'))
    repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=store.db
    n=repo.input(1);a,_=configuration(None,True);s,_=a.prepare(n);p=mock_payload(s)
    p['importance']={'kind':'inference','level':'high','reason':'synthetic','quotes':[n.content]}
    r=validate(p,n,s,a,NOW)
    assert FeaturedPolicy().decide(n,r,r['state'])['decision']=='not_selected'


def write_labels(directory, *, origin='human', changes=None):
    data=load_dataset(directory/'dataset.json')
    with (directory/'labels.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    for row in rows:
        ident=int(row['sample_id']);title,body,_,positive,criterion=CASES[ident-1]
        row.update(objective_featured='true' if positive else 'false',importance='high' if positive else 'low',
            criterion_codes=criterion,evidence_quote=body if positive else '',personal_relevance_note='个人学院偏好不改变客观标签',
            notes='合成标注，用于软件回归；不是真实人工效果',label_origin=origin,confirmed_guideline='true')
    if changes:changes(rows)
    path=directory/'filled.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader();w.writerows(rows)
    return path


def test_frozen_sampling_splits_weights_no_old_labels_and_zero_metrics(auto_service,tmp_path):
    store,_,_,_,_=auto_service;fill(store,tmp_path)
    raw=raw_path(store);before=raw.read_bytes()
    for name in ['one','two']:
        export_dataset(raw,tmp_path/name,limit=6,reference_time=NOW)
    d=load_dataset(tmp_path/'one/dataset.json')
    assert d==load_dataset(tmp_path/'two/dataset.json')
    assert d['population']==7 and d['sampled']==6 and set(r['split'] for r in d['records'])=={'holdout','development'}
    assert len({r['canonical_url'] for r in d['records']})==6
    assert all(r['sampling_weight']>=1 for r in d['records'])
    report=evaluate_dataset(tmp_path/'one/dataset.json',raw,split='all')
    assert report['summary']['valid_labels']==0 and report['summary']['labeled_subset_metrics'] is None
    assert report['summary']['analysis_coverage']==0 and raw.read_bytes()==before


def test_new_human_labels_report_full_matrix_fn_and_source_type_groups(auto_service,tmp_path):
    store,_,_,_,_=auto_service;a,state,_=fill(store,tmp_path);directory=tmp_path/'dataset';raw=raw_path(store)
    export_dataset(raw,directory,limit=20,reference_time=NOW)
    csv_path=write_labels(directory);labels=tmp_path/'human.sqlite3'
    assert import_labels(directory/'dataset.json',csv_path,labels,labeler='offline test human interface')['imported']==7
    assert import_labels(directory/'dataset.json',csv_path,labels,labeler='offline test human interface')['imported']==0
    report=evaluate_dataset(directory/'dataset.json',raw,labels_db=labels,state_db=state,analyzer=a,split='all')
    m=report['summary']['full_benchmark_metrics']
    assert m['confusion_matrix']=={'tp':3,'fp':0,'fn':2,'tn':2}
    assert m['precision']==1 and m['recall']==pytest.approx(3/5)
    assert {r['sample_id'] for r in report['missed_examples']}=={'6','7'}
    assert report['summary']['analysis_coverage']==pytest.approx(5/7)
    assert 'publisher_id' in report['groups'] and report['production_validated'] is False
    assert report['summary']['decisive_subset_metrics']['confusion_matrix']=={'tp':3,'fp':0,'fn':0,'tn':2}
    holdout=evaluate_dataset(directory/'dataset.json',raw,labels_db=labels,split='holdout')
    assert all(p['split']=='holdout' for p in holdout['predictions'])


@pytest.mark.parametrize('change',['identity','quote','confirmation','origin','duplicate','criterion'])
def test_bad_import_is_transactional_and_never_alters_old_tables(auto_service,tmp_path,change):
    store,_,_,_,_=auto_service;fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'dataset'
    export_dataset(raw,directory,reference_time=NOW)
    def mutate(rows):
        if change=='identity':rows[1]['input_hash']='0'*64
        if change=='quote':rows[1]['evidence_quote']='这句话并不在原文'
        if change=='confirmation':rows[1]['confirmed_guideline']='false'
        if change=='origin':rows[1]['label_origin']='model'
        if change=='duplicate':rows.append(dict(rows[0]))
        if change=='criterion':rows[1]['criterion_codes']='spread_heat'
    csv_path=write_labels(directory,changes=mutate);labels=tmp_path/'bad-labels.sqlite3';before=raw.read_bytes()
    with pytest.raises(ValueError):import_labels(directory/'dataset.json',csv_path,labels,labeler='test')
    assert not labels.exists() and raw.read_bytes()==before


def test_synthetic_truth_never_becomes_real_metrics_and_legacy_db_refused(auto_service,tmp_path):
    store,_,_,_,_=auto_service;fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'dataset'
    export_dataset(raw,directory,reference_time=NOW);csv_path=write_labels(directory,origin='synthetic');labels=tmp_path/'labels'
    with pytest.raises(ValueError):import_labels(directory/'dataset.json',csv_path,labels,labeler='test')
    import_labels(directory/'dataset.json',csv_path,labels,labeler='synthetic-test',allow_synthetic=True)
    report=evaluate_dataset(directory/'dataset.json',raw,labels_db=labels,split='all')
    assert report['summary']['valid_labels']==0 and report['summary']['full_benchmark_metrics'] is None
    before=raw.read_bytes()
    with pytest.raises(ValueError):import_labels(directory/'dataset.json',csv_path,raw,labeler='test',allow_synthetic=True)
    assert raw.read_bytes()==before


def test_frozen_manifest_mutation_and_stale_truth_are_explicit(auto_service,tmp_path):
    store,_,_,_,_=auto_service;a,state,_=fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'dataset'
    export_dataset(raw,directory,reference_time=NOW);labels=tmp_path/'labels'
    import_labels(directory/'dataset.json',write_labels(directory),labels,labeler='offline test')
    store.save_notice('computer',URL,Article('本科生困难补助申请通知','2026-10-15',CASES[0][1]+'原文修订了条件。'))
    report=evaluate_dataset(directory/'dataset.json',raw,labels_db=labels,state_db=state,analyzer=a,split='all')
    assert report['summary']['stale']==1 and report['summary']['valid_labels']==6
    assert report['summary']['full_benchmark_metrics'] is None
    data=json.loads((directory/'dataset.json').read_text(encoding='utf-8'));data['records'][0]['title']='篡改'
    (directory/'dataset.json').write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError):load_dataset(directory/'dataset.json')


def test_threshold_and_negation_have_versioned_auditable_reasons(store):
    text='本次助学金面向在校学生，请及时查看正式通知。'
    store.save_notice('computer',URL,Article('助学金通知','2026-10-15',text),seen_at=NOW.isoformat())
    repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=store.db
    a,_=configuration(None,True);n=repo.input(1);s,_=a.prepare(n);p=mock_payload(s)
    p['importance']={'kind':'inference','level':'normal','reason':'synthetic','quotes':[text]}
    r=validate(p,n,s,a,NOW);normal=FeaturedPolicy();strict=FeaturedPolicy(model_threshold='high')
    assert normal.version!=strict.version
    assert normal.decide(n,r,r['state'])['decision']=='selected'
    assert strict.decide(n,r,r['state'])['reason_codes']==['below_model_threshold']
    text='这不是助学金通知，而是普通工作人员的行政安排，请按既有流程办理。'
    store.save_notice('computer',URL,Article('一般说明','2026-10-15',text),seen_at=NOW.isoformat())
    n=repo.input(1);s,_=a.prepare(n);p=mock_payload(s)
    p['importance']={'kind':'inference','level':'high','reason':'synthetic','quotes':[text]}
    r=validate(p,n,s,a,NOW)
    assert normal.decide(n,r,r['state'])['reason_codes']==['no_rule_support']


def test_unknown_completed_analysis_is_pending_not_fake_empty_ready(auto_service,tmp_path):
    store,c,s,_,_=auto_service;a,state,repo=fill(store,tmp_path)
    c.analysis_analyzer=a;c.analysis_state=state;c.data_mode='offline_replay';assert c.refresh()
    with sqlite3.connect(state) as db:
        for i in range(1,8):
            n=repo.input(i);supplied,_=a.prepare(n);r=validate(mock_payload(supplied),n,supplied,a,NOW)
            db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=?',(r['state'],json.dumps(r),i))
    p=v3(s,'?selection=featured')[2]
    assert p['featured']['state']=='pending' and p['items']==[]
    assert p['featured']['coverage']['uncertain']==7 and p['featured']['coverage']['in_progress']==0
    validate_public_v3(p)


def test_queue_disappears_or_version_changes_cannot_reuse_selected_cards(auto_service,tmp_path):
    store,c,s,_,_=auto_service;a,state,_=fill(store,tmp_path)
    c.analysis_analyzer=a;c.analysis_state=state;c.data_mode='offline_replay';assert c.refresh()
    assert v3(s,'?selection=featured')[2]['counts']['all']==3
    c.analysis_state=tmp_path/'missing'
    p=v3(s,'?selection=featured')[2]
    assert p['featured']['state']=='degraded' and p['items']==[]
    c.analysis_state=state
    from cdu_radar.async_contract import AsyncAnalyzer
    from dataclasses import replace
    c.analysis_analyzer=AsyncAnalyzer(replace(a.config,max_output_tokens=1024),mock=True)
    p=v3(s,'?selection=featured')[2]
    assert p['items']==[] and p['featured']['coverage']['failed']==7
    assert v3(s)[2]['counts']['all']==7 and request(s,'/current.xml')[0]==200


def test_v3_expiry_and_withdrawal_still_503_before_any_subset(auto_service,tmp_path):
    store,c,s,_,mono=auto_service;a,state,_=fill(store,tmp_path)
    c.analysis_analyzer=a;c.analysis_state=state;c.data_mode='offline_replay';assert c.refresh()
    mono[0]=31
    assert request(s,API_V3+'?selection=featured',headers={'If-None-Match':'*'})[0]==503
    mono[0]=32;assert c.refresh()
    with store.db:store.db.execute("UPDATE notice_health SET status='withdrawn' WHERE notice_id=1")
    assert v3(s,'?selection=featured')[0]==503
    assert c.refresh()
    assert len(v3(s)[2]['items'])==6 and len(v3(s,'?selection=featured')[2]['items'])==2


def test_empty_small_strata_and_weights_are_transparent(auto_service,tmp_path):
    store,_,_,_,_=auto_service;fill(store,tmp_path);directory=tmp_path/'small'
    export_dataset(raw_path(store),directory,limit=2,reference_time=NOW)
    d=load_dataset(directory/'dataset.json')
    assert d['population']==7 and d['sampled']==2
    assert sum(s['population'] for s in d['strata'].values())==7
    assert any(s['sampled']==0 and s['weight'] is None for s in d['strata'].values())
    for r in d['records']:
        s=d['strata'][r['stratum']]
        assert r['sampling_weight']==pytest.approx(s['population']/s['sampled'])


def test_label_revisions_append_and_false_positive_examples_are_visible(auto_service,tmp_path):
    store,_,_,_,_=auto_service;a,state,_=fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'dataset'
    export_dataset(raw,directory,reference_time=NOW);labels=tmp_path/'labels'
    import_labels(directory/'dataset.json',write_labels(directory),labels,labeler='interface-test-reviewer')
    def revise(rows):rows[0].update(objective_featured='false',notes='合成修订：用于检查误报与追加历史')
    import_labels(directory/'dataset.json',write_labels(directory,changes=revise),labels,labeler='interface-test-reviewer')
    with sqlite3.connect(labels) as db:
        assert db.execute('SELECT COUNT(*) FROM featured_labels').fetchone()[0]==8
        assert json.loads(db.execute("SELECT payload FROM featured_labels WHERE sample_id='1' ORDER BY id LIMIT 1").fetchone()[0])['objective_featured']=='true'
    report=evaluate_dataset(directory/'dataset.json',raw,labels_db=labels,state_db=state,analyzer=a,split='all')
    assert [r['sample_id'] for r in report['false_positive_examples']]==['1']
    assert report['summary']['full_benchmark_metrics']['confusion_matrix']=={'tp':2,'fp':1,'fn':2,'tn':2}


def test_unknown_human_label_and_quality_failure_not_claimed_as_model_negatives(auto_service,tmp_path):
    store,_,_,_,_=auto_service;a,state,_=fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'dataset'
    export_dataset(raw,directory,reference_time=NOW);labels=tmp_path/'labels'
    def unknown(rows):rows[1].update(objective_featured='unknown',importance='unknown',notes='合成人工未知，不转成负例')
    import_labels(directory/'dataset.json',write_labels(directory,changes=unknown),labels,labeler='interface-test-reviewer')
    with store.db:store.db.execute("UPDATE notice_health SET status='withdrawn' WHERE notice_id=1")
    report=evaluate_dataset(directory/'dataset.json',raw,labels_db=labels,state_db=state,analyzer=a,split='all')
    assert report['summary']['human_unknown']==1 and report['summary']['valid_labels']==6
    assert report['summary']['quality_excluded']==1 and report['summary']['full_benchmark_metrics'] is None
    assert next(p for p in report['predictions'] if p['sample_id']=='1')['state']=='source_unqualified'


def test_cli_frozen_export_cache_only_evaluate_and_refuse_overwrite(auto_service,tmp_path,capsys,monkeypatch):
    store,_,_,_,_=auto_service;a,state,_=fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'dataset';output=tmp_path/'report.json'
    from cdu_radar.async_contract import AsyncAnalyzer
    monkeypatch.setattr(AsyncAnalyzer,'analyze',lambda *a,**k:pytest.fail('Benchmark attempted a model request'))
    assert main(['featured-benchmark','export','--db',str(raw),'--directory',str(directory),'--as-of',NOW.isoformat()])==0
    capsys.readouterr()
    argv=['featured-benchmark','evaluate','--dataset',str(directory/'dataset.json'),'--db',str(raw),
          '--analysis-state',str(state),'--analysis-mock','--split','all','--output',str(output)]
    assert main(argv)==0
    p=json.loads(capsys.readouterr().out)
    assert p['summary']['valid_labels']==0 and p['analysis_mode']=='offline_mock'
    before=output.read_bytes();assert main(argv)==2 and output.read_bytes()==before


def test_public_overview_rejects_hidden_fields_and_inconsistent_counts(auto_service):
    p=v3(auto_service[2])[2]
    validate_public_v3(p)
    p['featured']['coverage']['pending']=0
    with pytest.raises(ValueError):validate_public_v3(p)


@pytest.mark.parametrize('suffix',['-journal','-wal','-shm','-JOURNAL','-WAL','-SHM'])
def test_label_database_refuses_sqlite_reserved_sidecars_before_creation(auto_service,tmp_path,suffix):
    store,_,_,_,_=auto_service;fill(store,tmp_path);raw=raw_path(store);directory=tmp_path/'labels-dataset'
    export_dataset(raw,directory,reference_time=NOW);csv_path=write_labels(directory)
    destination=Path(str(raw)+suffix);original=raw.read_bytes()
    with pytest.raises(ValueError,match='sidecar'):
        import_labels(directory/'dataset.json',csv_path,destination,labeler='interface-test-reviewer')
    assert not destination.exists() and raw.read_bytes()==original
    add(store,url=URL.replace('990001','990099'))
    assert store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


@pytest.mark.parametrize('basis,criterion',[
    ('本次助学金面向在校学生。','student_rights'),
    ('请查看校园交通调整安排。','safety_service'),
    ('国家级科研成果正式获批。','major_news'),
])
def test_public_evidence_window_preserves_late_criterion_and_literal_source(store,basis,criterion):
    body='这是供审查用的普通合成说明，与入选依据分别记录。'*15+basis
    assert 240<len(body)<500
    store.save_notice('computer',URL,Article('合成普通通知','2026-10-15',body),seen_at=NOW.isoformat())
    repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=store.db
    n=repo.input(1);a,_=configuration(None,True);s,_=a.prepare(n);p=mock_payload(s)
    p['importance']={'kind':'inference','level':'high','reason':'synthetic boundary','quotes':[body]}
    analysis=validate(p,n,s,a,NOW);decision=FeaturedPolicy().decide(n,analysis,analysis['state'])
    assert decision['decision']=='selected'
    evidence=next(e for e in decision['evidence'] if e['criterion']==criterion)
    assert basis in evidence['quote'] and evidence['quote'] in body and len(evidence['quote'])<=240


def test_analysis_sqlite_lock_degrades_fast_without_blocking_legacy_rss(auto_service,tmp_path):
    import threading
    import time
    store,c,s,_,_=auto_service;a,state,_=fill(store,tmp_path)
    c.analysis_analyzer=a;c.analysis_state=state;c.data_mode='offline_replay';assert c.refresh()
    rss=request(s,'/feed.xml')[2];response=[];errors=[]
    locker=sqlite3.connect(state,isolation_level=None);locker.execute('BEGIN EXCLUSIVE')
    def fetch():
        try:response.append(v3(s))
        except Exception as exc:errors.append(type(exc).__name__)
    worker=threading.Thread(target=fetch,daemon=True)
    try:
        started=time.monotonic();worker.start();worker.join(2)
        assert not worker.is_alive(), 'Optional analysis lock exceeded response budget'
        assert not errors and response[0][0]==200
        assert response[0][2]['featured']['state']=='degraded' and response[0][2]['counts']['all']==7
        for path in ['/feed.xml','/api/notices','/api/notices/v2']:
            assert request(s,path)[0]==200
        assert request(s,'/feed.xml')[2]==rss and time.monotonic()-started<3
    finally:locker.rollback();locker.close();worker.join(12)
    assert v3(s,'?selection=featured')[2]['counts']['all']==3
