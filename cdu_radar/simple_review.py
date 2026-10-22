"""Offline human review UI; never predicts labels or modifies frozen packages."""
import csv
import io
import json
from pathlib import Path

from .classification import DOMAINS, TOPICS, TYPES
from .featured import CRITERIA
from .featured_benchmark import save_new, worksheet
from .quality_benchmark import COLUMNS, check_quote, load_package

VERSION = 'simple-review-v1'
FIELDS = {'featured', 'student', 'action', 'type'}
CHOICES = {'yes': 'true', 'no': 'false', 'unknown': 'unknown'}


def build_page(directory, output, *, batch_size=10, synthetic=False):
    if type(batch_size) is not int or not 1 <= batch_size <= 50:
        raise ValueError('Batch size must be 1..50')
    data, manifest = load_package(directory, require_current=False)
    # Deliberately omit predictions, rule classes, weights and development/holdout
    # labels. Preserve the sealed split only in the original package.
    records = [dict(sample_id=r['sample_id'], input_hash=r['input_hash'], title=r['title'],
                    content=r['content'], url=r['canonical_url'], date=r['published_at'],
                    source=r['content_source'] or r['publisher_id'], attachments=len(r['attachments']))
               for r in sorted(data['records'], key=lambda r: (r['split'] != 'development', int(r['sample_id'])))]
    payload = dict(version=VERSION, benchmark_id=manifest['benchmark_id'], dataset_id=data['dataset_id'],
                   reference_time=data['reference_time'], batch_size=batch_size, records=records,
                   origin='synthetic' if synthetic else 'human',
                   types=dict(TYPES), domains=dict(DOMAINS), topics=dict(TOPICS),
                   criteria={key: value[0] for key, value in CRITERIA.items()})
    encoded = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026')
    template = Path(__file__).with_name('web').joinpath('simple-review.html').read_text('utf-8')
    save_new(output, template.replace('/*__REVIEW_DATA__*/', encoded))
    return dict(output=str(output), samples=len(records), initial_batch=min(batch_size, len(records)),
                answers_prefilled=0, model_requests=0, synthetic=synthetic)


def convert_answers(directory, input_path, output, *, allow_synthetic=False):
    data, manifest = load_package(directory, require_current=False)
    from .llm_json import strict_json
    path = Path(input_path)
    if path.stat().st_size > 2*1024*1024:
        raise ValueError('Answer file exceeds 2 MiB')
    value = strict_json(path.read_bytes())
    origin = value.get('origin')
    if (value.get('version') != VERSION or value.get('benchmark_id') != manifest['benchmark_id']
            or value.get('dataset_id') != data['dataset_id']
            or origin not in ({'human', 'synthetic'} if allow_synthetic else {'human'})):
        raise ValueError('Answers belong to another package/version or are synthetic')
    records = {r['sample_id']: r for r in data['records']}
    answers = value.get('records')
    if not isinstance(answers, list) or len(answers) > len(records):
        raise ValueError('Invalid answer list')
    rows = []; seen = set()
    for item in answers:
        ident = item.get('sample_id')
        if ident in seen or ident not in records or item.get('input_hash') != records[ident]['input_hash']:
            raise ValueError('Unknown, duplicate or changed sample')
        seen.add(ident)
        if item.get('confirmed') is not True:
            continue
        record = records[ident]; selected = item.get('answers', {})
        if (set(selected) != FIELDS or any(selected[k] not in CHOICES for k in FIELDS-{'type'})
                or selected['type'] not in {*TYPES, 'unknown'}):
            raise ValueError('All four questions require an explicit answer')
        quotes = item.get('quotes', [])
        if not isinstance(quotes, list) or any(not isinstance(q, str) or not q.strip() for q in quotes):
            raise ValueError('Invalid source quote')
        evidence = '\n'.join(quotes)
        decisive = any(selected[k] != 'unknown' for k in FIELDS)
        check_quote(evidence, record, required=decisive)
        criteria = item.get('criteria', [])
        if (not isinstance(criteria, list) or len(criteria) != len(set(criteria))
                or not set(criteria) <= set(CRITERIA)
                or selected['featured'] == 'yes' and not criteria):
            raise ValueError('A positive featured answer needs a selected reason')
        row = {key: '' for key in COLUMNS}; row.update(worksheet(record, data))
        row.update(objective_featured=CHOICES[selected['featured']],
                   criterion_codes='|'.join(criteria) if selected['featured'] == 'yes' else '',
                   evidence_quote=evidence if selected['featured'] != 'unknown' else '',
                   undergraduate_relevant=CHOICES[selected['student']],
                   undergraduate_quote=evidence if selected['student'] != 'unknown' else '',
                   action_basis=CHOICES[selected['action']],
                   action_quote=evidence if selected['action'] != 'unknown' else '',
                   expected_type=selected['type'], confirmed_guideline='true', label_origin=origin)
        for field, enum in [('domains', DOMAINS), ('topics', TOPICS)]:
            tags = item.get(field)
            if tags is None:
                continue  # Unreviewed optional dimensions remain blank.
            if (not isinstance(tags, list) or len(tags) != len(set(tags)) or not set(tags) <= set(enum)
                    or 'none' in tags):
                raise ValueError('Invalid optional tags')
            if tags or item.get(field+'_reviewed') is True:
                row['expected_'+field] = '|'.join(tags) if tags else 'none'
        if selected['type'] != 'unknown' or row['expected_domains'] or row['expected_topics']:
            check_quote(evidence, record, required=True)
            row['classification_quote'] = evidence
        note = item.get('note', '')
        if not isinstance(note, str) or len(note) > 600:
            raise ValueError('Note must be at most 600 characters')
        labels = {'yes': '是', 'no': '否', 'unknown': '看不出来（原文依据不足或本人无法判断）'}
        row['notes'] = ('本人逐项选择：值得精选='+labels[selected['featured']]+'；本科生实用='+labels[selected['student']]
                        +'；有报名/办理/参与事项='+labels[selected['action']]+'；主要类型='
                        +(TYPES[selected['type']] if selected['type'] in TYPES else '分不清（本人无法判断）')
                        +('。补充：'+note if note else ''))
        rows.append(row)
    if not rows:
        raise ValueError('No explicitly confirmed answers; no CSV was created')
    content = io.StringIO(newline=''); writer = csv.DictWriter(content, fieldnames=COLUMNS)
    writer.writeheader(); writer.writerows(rows)
    save_new(output, content.getvalue(), encoding='utf-8-sig')
    return dict(converted=len(rows), skipped_unconfirmed=len(answers)-len(rows), output=str(output),
                origin=origin, model_requests=0, source_writes=0)
