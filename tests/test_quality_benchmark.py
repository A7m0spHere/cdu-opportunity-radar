import csv
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from cdu_radar.cli import main
from cdu_radar.quality_benchmark import export_package, import_labels, evaluate_package, load_package, COLUMNS
from test_auto_publish import auto_service, NOW, add
from test_featured import fill, raw_path
from test_rss_service import loopback_only


def package(service,tmp_path):
    store=service[0];analyzer,state,_=fill(store,tmp_path)
    directory=tmp_path/'benchmark';raw=raw_path(store)
    before=raw.read_bytes();state_before=state.read_bytes()
    export_package(raw,directory,reference_time=NOW.isoformat(),limit=50,state_db=state,analyzer=analyzer)
    assert raw.read_bytes()==before and state.read_bytes()==state_before
    return directory,raw


def label(directory, *, origin='human', changes=None):
    path=directory/'review-labels.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    data,manifest=load_package(directory);records={r['sample_id']:r for r in data['records']}
    for row in rows:
        r=records[row['sample_id']];quote=r['content']
        row.update(objective_featured='true',importance='high',criterion_codes='student_rights',evidence_quote=quote,
            expected_type='notice',expected_domains='student_aid',expected_topics='none',classification_quote=quote,
            undergraduate_relevant='true',undergraduate_quote=quote,action_basis='true',action_quote=quote,
            label_origin=origin,confirmed_guideline='true',notes='合成软件测试标签，不代表真实效果。')
        if changes:row.update(changes)
    output=directory/'filled.csv'
    with output.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)
    return output


def test_multi_axis_export_is_blank_private_readonly_and_insufficient_sample(auto_service,tmp_path):
    directory,raw=package(auto_service,tmp_path)
    with (directory/'review-labels.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    assert rows and all(not r['objective_featured'] and not r['expected_type'] and not r['label_origin'] for r in rows)
    sampling=json.loads((directory/'sampling.json').read_text('utf-8'))
    assert sampling['insufficient_sample'] and sampling['distributions']['state']['failed']>0
    before=raw.read_bytes();report=evaluate_package(directory,raw_db=raw,split='all')
    assert report['human_labeled_samples']==0 and report['development_error_examples']==[]
    assert all(v['labeled_subset_metrics'] is None for v in report['featured'].values())
    assert report['undergraduate']['full_cohort_metrics'] is None and report['classification']['effective']['type']['error_rate'] is None
    assert report['model_requests']==report['source_writes']==0 and before==raw.read_bytes()


def test_labels_append_and_failed_model_samples_remain_in_metrics(auto_service,tmp_path):
    directory,raw=package(auto_service,tmp_path);labels=tmp_path/'human.sqlite3'
    filled=label(directory)
    result=import_labels(directory,filled,labels,labeler='synthetic-test-reviewer')
    assert result['imported']>0
    assert import_labels(directory,filled,labels,labeler='synthetic-test-reviewer')['imported']==0
    report=evaluate_package(directory,raw_db=raw,labels_db=labels,split='all')
    assert report['human_labeled_samples']==report['total']
    assert report['featured']['effective']['labeled_subset_metrics']['confusion_matrix']['fn']>0
    assert report['featured']['model']['abstained']>0
    assert report['classification']['model']['type']['not_predicted_ids']
    assert report['classification']['effective']['type']['labeled']==report['total']
    assert report['undergraduate']['missed_ids']
    assert report['development_error_examples'] and len(report['development_error_examples'])<=5
    assert not evaluate_package(directory,raw_db=raw,labels_db=labels)['development_error_examples']


@pytest.mark.parametrize('change',[{'evidence_quote':'原文中不存在的内容'}, {'expected_topics':'invalid'},
    {'confirmed_guideline':'false'},{'undergraduate_quote':''},{'expected_domains':'research|research'},
    {'objective_featured':'true','criterion_codes':''}])
def test_invalid_import_is_atomic(auto_service,tmp_path,change):
    directory,raw=package(auto_service,tmp_path);labels=tmp_path/'labels.sqlite3';before=raw.read_bytes()
    filled=label(directory,changes=change)
    with pytest.raises(ValueError):import_labels(directory,filled,labels,labeler='test')
    assert not labels.exists() and raw.read_bytes()==before


def test_synthetic_labels_never_count_as_human_and_cannot_mask_human(auto_service,tmp_path):
    directory,raw=package(auto_service,tmp_path);labels=tmp_path/'labels.sqlite3'
    with pytest.raises(ValueError):import_labels(directory,label(directory,origin='synthetic'),labels,labeler='test')
    import_labels(directory,label(directory),labels,labeler='test')
    import_labels(directory,label(directory,origin='synthetic',changes={'objective_featured':'false'}),labels,labeler='test',allow_synthetic=True)
    report=evaluate_package(directory,raw_db=raw,labels_db=labels,split='all')
    assert report['human_labeled_samples']==report['total'] and report['synthetic_labeled_samples']==0
    separate=tmp_path/'synthetic.sqlite3'
    import_labels(directory,label(directory,origin='synthetic'),separate,labeler='test',allow_synthetic=True)
    assert evaluate_package(directory,raw_db=raw,labels_db=separate,split='all')['human_labeled_samples']==0
    assert evaluate_package(directory,raw_db=raw,labels_db=separate,split='all')['featured']['rule']['labeled_subset_metrics'] is None


def test_frozen_predictions_source_update_and_foreign_db_are_rejected(auto_service,tmp_path):
    directory,raw=package(auto_service,tmp_path);labels=tmp_path/'labels.sqlite3'
    import_labels(directory,label(directory),labels,labeler='test')
    add(auto_service[0],title='更新后的原文通知',body='本科生须核对课程调整与教学安排，具体要求以官方原文为准。')
    report=evaluate_package(directory,raw_db=raw,labels_db=labels,split='all')
    assert report['stale_samples']==1 and report['human_labeled_samples']==report['total']-1
    before=raw.read_bytes()
    with pytest.raises(ValueError,match='independent'):import_labels(directory,label(directory),raw,labeler='test')
    assert raw.read_bytes()==before
    path=directory/'predictions.json';value=json.loads(path.read_text('utf-8'));value['predictions'][0]['rule']['featured']=False
    path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError):load_package(directory)


def test_cli_export_evaluate_and_no_overwrite(auto_service,tmp_path,capsys):
    raw=raw_path(auto_service[0]);directory=tmp_path/'cli'
    assert main(['quality-benchmark','export','--db',str(raw),'--directory',str(directory),'--as-of',NOW.isoformat()])==0
    output=tmp_path/'report.json'
    argv=['quality-benchmark','evaluate','--db',str(raw),'--directory',str(directory),'--split','all','--output',str(output)]
    assert main(argv)==0 and json.loads(output.read_text('utf-8'))['human_labeled_samples']==0
    before=output.read_bytes();assert main(argv)==2 and output.read_bytes()==before
