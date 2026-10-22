import csv
import hashlib
import json

import pytest

from cdu_radar.quality_benchmark import export_package, import_labels, load_package, evaluate_package
from cdu_radar.simple_review import build_page, convert_answers, VERSION
from test_auto_publish import auto_service, NOW
from test_featured import raw_path
from test_rss_service import loopback_only


def package(service, tmp_path):
    directory = tmp_path/'review'
    export_package(raw_path(service[0]), directory, reference_time=NOW.isoformat())
    return directory


def document(directory, *, confirmed=True):
    data, manifest = load_package(directory); record = data['records'][0]
    return dict(version=VERSION, benchmark_id=manifest['benchmark_id'], dataset_id=data['dataset_id'], origin='synthetic',
                records=[dict(sample_id=record['sample_id'], input_hash=record['input_hash'], confirmed=confirmed,
                              answers=dict(featured='yes', student='yes', action='no', type='notice'),
                              criteria=['student_rights'], quotes=[record['content']], domains=None, topics=None, note='合成软件测试')])


def write_answers(directory, value):
    path = directory/'answers.json'; path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def test_page_has_no_predictions_or_prefilled_answers_and_preserves_package(auto_service, tmp_path):
    directory = package(auto_service, tmp_path)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    output = tmp_path/'simple.html'; result = build_page(directory, output)
    text = output.read_text('utf-8'); data = json.loads(text.split('<script id="review-data" type="application/json">')[1].split('</script>')[0])
    assert result['answers_prefilled'] == 0
    assert all('answers' not in record and 'split' not in record and 'type' not in record for record in data['records'])
    assert 'predictions' not in data and data['origin'] == 'human'
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    with pytest.raises(FileExistsError):
        build_page(directory, output)


def test_confirmed_export_round_trips_to_existing_importer_without_filling_optional_labels(auto_service, tmp_path):
    directory = package(auto_service, tmp_path); value = document(directory)
    path = write_answers(directory, value); output = tmp_path/'filled.csv'
    result = convert_answers(directory, path, output, allow_synthetic=True)
    assert result['converted'] == 1
    with output.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]['expected_domains'] == rows[0]['expected_topics'] == rows[0]['importance'] == ''
    labels = tmp_path/'labels.sqlite3'
    assert import_labels(directory, output, labels, labeler='synthetic-ui-test', allow_synthetic=True)['imported'] == 1
    report = evaluate_package(directory, raw_db=raw_path(auto_service[0]), labels_db=labels, split='all')
    assert report['human_labeled_samples'] == 0


@pytest.mark.parametrize('change', ['unconfirmed', 'missing_answer', 'foreign_quote', 'no_reason', 'foreign_package', 'duplicate'])
def test_incomplete_or_tampered_answers_do_not_create_output(auto_service, tmp_path, change):
    directory = package(auto_service, tmp_path); value = document(directory)
    item = value['records'][0]
    if change == 'unconfirmed': item['confirmed'] = False
    if change == 'missing_answer': del item['answers']['student']
    if change == 'foreign_quote': item['quotes'] = ['这句话不在冻结原文中。']
    if change == 'no_reason': item['criteria'] = []
    if change == 'foreign_package': value['dataset_id'] = 'another-dataset'
    if change == 'duplicate': value['records'].append(item.copy())
    path = write_answers(directory, value); output = tmp_path/'invalid.csv'
    with pytest.raises(ValueError):
        convert_answers(directory, path, output, allow_synthetic=True)
    assert not output.exists()


def test_test_mode_cannot_silently_become_human_labels(auto_service, tmp_path):
    directory = package(auto_service, tmp_path); path = write_answers(directory, document(directory))
    output = tmp_path/'answers.csv'
    with pytest.raises(ValueError):
        convert_answers(directory, path, output)
    assert not output.exists()


def test_unknowns_remain_unknown_and_need_no_fabricated_evidence(auto_service, tmp_path):
    directory = package(auto_service, tmp_path); value = document(directory)
    value['records'][0].update(answers={key: 'unknown' for key in ('featured', 'student', 'action', 'type')}, quotes=[], criteria=[])
    output = tmp_path/'unknown.csv'
    convert_answers(directory, write_answers(directory, value), output, allow_synthetic=True)
    with output.open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['objective_featured'] == row['undergraduate_relevant'] == row['expected_type'] == 'unknown'
    assert row['evidence_quote'] == row['classification_quote'] == ''
    assert '无法判断' in row['notes']


def test_source_text_cannot_break_out_of_embedded_json(auto_service, tmp_path, monkeypatch):
    import cdu_radar.simple_review as review
    directory = package(auto_service, tmp_path); data, manifest = load_package(directory)
    data['records'][0]['title'] = '</script><script>throw Error("source executed")</script>&'
    monkeypatch.setattr(review, 'load_package', lambda _, **kwargs: (data, manifest))
    output = tmp_path/'escaped.html'; build_page(directory, output)
    html = output.read_text('utf-8')
    assert '<script>throw Error' not in html
    embedded = json.loads(html.split('<script id="review-data" type="application/json">')[1].split('</script>')[0])
    assert embedded['records'][0]['title'] == data['records'][0]['title']
