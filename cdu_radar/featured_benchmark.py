"""Frozen stratified worksheets and separate append-only featured truth.

Old M2 labels, model predictions and synthetic fixtures are never human truth.
This module has no model transport or label inference.
"""
from collections import Counter, defaultdict
import csv
import io
import json
import re
from pathlib import Path
import sqlite3

from .analysis_models import fingerprint, reference_datetime
from .async_queue import source_snapshot
from .evaluation import matrix_metrics
from .evidence import locate
from .featured import CRITERIA, FeaturedPolicy, analysis_for, analysis_reader
from .models import utc_now
from .privacy import spreadsheet_text
from .taxonomy import classify

GUIDELINE='campus-objective-importance-v1'
IDENTITY=['dataset_id','sample_id','input_hash','content_hash','publisher_id','type','stratum','split',
    'canonical_url','title','content_excerpt','attachment_count','reference_time','guideline_version','sampling_weight']
MANUAL=['objective_featured','importance','criterion_codes','evidence_quote','personal_relevance_note','notes','label_origin','confirmed_guideline']
COLUMNS=IDENTITY+MANUAL


def save_new(path, text, *, encoding='utf-8'):
    with Path(path).open('x',encoding=encoding,newline='') as stream:stream.write(text)


def worksheet(record, dataset):
    row={k:record[k] for k in IDENTITY if k not in {'dataset_id','reference_time','guideline_version'}}
    row.update(dataset_id=dataset['dataset_id'],reference_time=dataset['reference_time'],guideline_version=GUIDELINE)
    return {k:spreadsheet_text(str(v)) for k,v in row.items()}


def export_dataset(raw_db, directory, *, limit=36, seed='featured-v1', reference_time=None, policy=None):
    if type(limit) is not int or not 1<=limit<=200 or not isinstance(seed,str) or not 1<=len(seed)<=100:raise ValueError('Invalid sampling bound')
    now=reference_datetime(reference_time);policy=policy or FeaturedPolicy();pool=[]
    with source_snapshot(raw_db) as repo:
        from .auto_publish import AutoRepository
        plan=AutoRepository(repo.db).feed_plan(now)
        eligible={i['notice_id'] for i in plan['items']}
        raw_total=repo.db.execute('SELECT COUNT(*) FROM notices').fetchone()[0]
        for row in repo.db.execute('SELECT id,content_source_id FROM notices ORDER BY id'):
            if row['id'] not in eligible:continue
            n=repo.input(row['id']);c=classify(n.title,n.content)
            publisher=row['content_source_id'] or 'unknown'
            # Explicit content classes enrich the unit/type strata without using
            # predicted importance as truth or dropping low-profile sources.
            codes=[key for key,(_,_,pattern) in CRITERIA.items() if re.search(pattern,n.title+'\n'+n.content)]
            bucket='student_rights' if 'student_rights' in codes else 'academic_affairs' if 'academic_affairs' in codes else c['type']
            stratum=publisher+'|'+bucket
            pool.append({'sample_id':str(n.notice_id),'input_hash':n.input_hash,'content_hash':n.content_hash,
                'publisher_id':publisher,'type':c['type'],'stratum':stratum,'canonical_url':n.canonical_url,
                'title':n.title,'content_excerpt':n.content[:1200],'content':n.content,
                'attachment_count':len(n.attachments),'published_at':n.published_at,
                'sources':list(n.sources),'content_source':n.content_source,
                'attachments':[{'name':a.name,'url':a.url} for a in n.attachments],
                'body_links':[{'name':a.name,'url':a.url} for a in n.body_links]})
    groups=defaultdict(list)
    for r in pool:groups[r['stratum']].append(r)
    for rows in groups.values():rows.sort(key=lambda r:fingerprint([seed,r['canonical_url']]))
    sampled=[]
    # Deterministic round-robin gives small strata a chance before large ones.
    ordered=sorted(groups,key=lambda key:fingerprint([seed,key]));depth=0
    while len(sampled)<min(limit,len(pool)):
        for key in ordered:
            if depth<len(groups[key]) and len(sampled)<limit:sampled.append(dict(groups[key][depth]))
        depth+=1
    frequencies=Counter(r['stratum'] for r in sampled)
    split_order=sorted(sampled,key=lambda r:fingerprint([seed,'split',r['canonical_url']]))
    for i,r in enumerate(split_order):
        r['split']='holdout' if i%4==0 else 'development'
        r['sampling_weight']=round(len(groups[r['stratum']])/frequencies[r['stratum']],8)
    data={'schema_version':1,'guideline_version':GUIDELINE,'reference_time':now.isoformat(),'seed':seed,
        'sampling':'fixed_hash_round_robin_unit_content_strata','population':len(pool),'sampled':len(sampled),
        'raw_population':raw_total,'quality_excluded':dict(Counter(i['reason'] for i in plan['excluded'])),
        'policy_version':policy.version,'policy_settings':policy.settings,
        'strata':{k:{'population':len(v),'sampled':frequencies[k],
            'inclusion_fraction':frequencies[k]/len(v),'weight':len(v)/frequencies[k] if frequencies[k] else None} for k,v in sorted(groups.items())},
        'not_representative_of':'all university notices; pool is existing local database only',
        'records':sorted(sampled,key=lambda r:int(r['sample_id']))}
    data['dataset_id']='featured-dataset:'+fingerprint(data)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    save_new(directory/'dataset.json',json.dumps(data,ensure_ascii=False,indent=2))
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=COLUMNS);writer.writeheader()
    for record in data['records']:writer.writerow(worksheet(record,data))
    save_new(directory/'labels.csv',output.getvalue(),encoding='utf-8-sig')
    return {k:v for k,v in data.items() if k!='records'}


def load_dataset(path):
    from .llm_json import strict_json
    data=strict_json(Path(path).read_bytes())
    if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('guideline_version')!=GUIDELINE:
        raise ValueError('Invalid featured dataset')
    identity=data.get('dataset_id');sealed={k:v for k,v in data.items() if k!='dataset_id'}
    if identity!='featured-dataset:'+fingerprint(sealed):raise ValueError('Frozen dataset was changed')
    records=data['records']
    if len({r['sample_id'] for r in records})!=len(records) or len({r['canonical_url'] for r in records})!=len(records):
        raise ValueError('Duplicate frozen samples')
    return data


def frozen_input(record):
    from .analysis_models import AnalysisInput, InputLink
    return AnalysisInput(int(record['sample_id']),record['content_hash'],record['input_hash'],record['title'],record['content'],
        record['canonical_url'],tuple(record['sources']),record['published_at'],
        tuple(InputLink(**a) for a in record['attachments']),tuple(InputLink(**a) for a in record['body_links']),record['content_source'])


def import_labels(dataset_path, csv_path, labels_db, *, labeler, allow_synthetic=False):
    dataset=load_dataset(dataset_path)
    if not isinstance(labeler,str) or not labeler.strip() or len(labeler)>100:raise ValueError('Labeler is required')
    # Resolve collision before creating any labels file. Do not open source or M2 databases writable.
    path=Path(labels_db).absolute()
    if path.is_symlink() or path.resolve() in {Path(dataset_path).resolve(),Path(csv_path).resolve()}:raise ValueError('Invalid label database path')
    prepared=[];seen=set();by_id={r['sample_id']:r for r in dataset['records']}
    with Path(csv_path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=COLUMNS:raise ValueError('Unexpected featured CSV columns')
        for row in reader:
            if None in row or any(v is None for v in row.values()):raise ValueError('Malformed featured CSV')
            ident=row['sample_id']
            if ident in seen or ident not in by_id:raise ValueError('Duplicate/unknown sample')
            seen.add(ident)
            if any(row[k]!=worksheet(by_id[ident],dataset)[k] for k in IDENTITY):raise ValueError('Frozen CSV identity changed')
            manual={k:row[k].strip() for k in MANUAL}
            if not any(manual.values()):continue
            if manual['confirmed_guideline']!='true' or manual['label_origin'] not in ({'human','synthetic'} if allow_synthetic else {'human'}):
                raise ValueError('Explicit new-guideline human confirmation required')
            if manual['objective_featured'] not in {'true','false','unknown'} or manual['importance'] not in {'high','normal','low','unknown'}:
                raise ValueError('Invalid human importance label')
            if any(len(v)>2000 for v in manual.values()) or not manual['notes']:raise ValueError('Label notes required/bounded')
            codes=manual['criterion_codes'].split('|') if manual['criterion_codes'] else []
            if len(codes)!=len(set(codes)) or any(c not in CRITERIA for c in codes):raise ValueError('Invalid human criteria')
            quote=manual['evidence_quote']
            if quote and locate(frozen_input(by_id[ident]),quote).source not in {'title','content'}:raise ValueError('Human quote is outside frozen input')
            if manual['objective_featured']=='true' and (not codes or not quote):raise ValueError('Positive human label needs criteria and evidence')
            prepared.append((ident,by_id[ident]['input_hash'],manual))
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as preflight:
            tables={r[0] for r in preflight.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables!={'featured_labels'}:raise ValueError('Labels require a new or featured-only database')
    with sqlite3.connect(path) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and tables!={'featured_labels'}:raise ValueError('Labels must use a separate featured-only database')
        db.execute('''CREATE TABLE IF NOT EXISTS featured_labels (
            id INTEGER PRIMARY KEY,dataset_id TEXT NOT NULL,sample_id TEXT NOT NULL,input_hash TEXT NOT NULL,
            labeler TEXT NOT NULL,labeled_at TEXT NOT NULL,payload TEXT NOT NULL)''')
        inserted=0
        for ident,input_hash,manual in prepared:
            payload=json.dumps(manual,sort_keys=True,ensure_ascii=False)
            previous=db.execute('SELECT payload,labeler FROM featured_labels WHERE dataset_id=? AND sample_id=? ORDER BY id DESC LIMIT 1',(dataset['dataset_id'],ident)).fetchone()
            if previous and previous[0]==payload and previous[1]==labeler:continue
            db.execute('INSERT INTO featured_labels(dataset_id,sample_id,input_hash,labeler,labeled_at,payload) VALUES (?,?,?,?,?,?)',
                (dataset['dataset_id'],ident,input_hash,labeler,utc_now(),payload));inserted+=1
    return {'imported':inserted,'dataset_id':dataset['dataset_id'],'source_raw_writes':0,'old_m2_labels_read':0}


def truth_rows(path,dataset,include_synthetic):
    if path is None:return {}
    db=sqlite3.connect(Path(path).resolve(strict=True).as_uri()+'?mode=ro',uri=True)
    try:
        rows=db.execute('SELECT sample_id,payload,input_hash FROM featured_labels WHERE dataset_id=? ORDER BY id',(dataset['dataset_id'],))
        result={}
        for ident,payload,input_hash in rows:
            value=json.loads(payload)
            # A later synthetic test label must never mask an earlier human record.
            if value['label_origin']=='human' or include_synthetic:result[ident]=(value,input_hash)
        return result
    finally:db.close()


def evaluate_dataset(dataset_path, raw_db, *, labels_db=None, state_db=None, analyzer=None, split='holdout', include_synthetic=False):
    dataset=load_dataset(dataset_path)
    if split not in {'holdout','development','all'}:raise ValueError('Invalid evaluation split')
    policy=FeaturedPolicy(**dataset['policy_settings'])
    if policy.version!=dataset['policy_version']:raise ValueError('Policy implementation changed; create a new benchmark revision')
    labels=truth_rows(labels_db,dataset,include_synthetic);predictions=[]
    from contextlib import ExitStack
    with ExitStack() as stack:
        repo=stack.enter_context(source_snapshot(raw_db));queue=None;queue_error=False
        from .auto_publish import AutoRepository
        current_plan=AutoRepository(repo.db).feed_plan(reference_datetime(dataset['reference_time']))
        current_eligible={i['notice_id'] for i in current_plan['items']}
        if state_db and analyzer:
            try:queue=stack.enter_context(analysis_reader(state_db,raw_db))
            except (OSError,ValueError,sqlite3.Error):queue_error=True
        for r in dataset['records']:
            if split!='all' and r['split']!=split:continue
            truth_entry=labels.get(r['sample_id']);truth=truth_entry[0] if truth_entry and truth_entry[1]==r['input_hash'] else None
            n=frozen_input(r);state='unavailable' if queue_error else 'not_analyzed';analysis=None
            try:
                current=repo.input(n.notice_id)
                if current.input_hash!=n.input_hash:state='stale'
                elif n.notice_id not in current_eligible:state='source_unqualified'
                elif queue:_,analysis,state=analysis_for(queue,repo,analyzer,n.notice_id,reference_datetime(dataset['reference_time']))
            except ValueError:state='stale'
            decision=policy.decide(n,analysis,state)
            predictions.append({'sample_id':r['sample_id'],'canonical_url':r['canonical_url'],'title':r['title'],
                'publisher_id':r['publisher_id'],'type':r['type'],'stratum':r['stratum'],'split':r['split'],
                'sampling_weight':r['sampling_weight'],'state':state,'decision':decision['decision'],
                'reason_codes':decision['reason_codes'],'evidence':decision['evidence'],
                'truth':None if not truth or truth['objective_featured']=='unknown' or state=='stale' else truth['objective_featured']=='true',
                'label_origin':truth['label_origin'] if truth else None,'human_unknown':bool(truth and truth['objective_featured']=='unknown')})
    def summarize(rows):
        labelled=[r for r in rows if r['truth'] is not None]
        matrix={k:0 for k in ['tp','fp','fn','tn']};conditional={k:0 for k in matrix}
        for r in labelled:
            positive=r['decision']=='selected';key='tp' if positive and r['truth'] else 'fp' if positive else 'fn' if r['truth'] else 'tn'
            matrix[key]+=1
            if r['decision']!='pending':conditional[key]+=1
        return {'total':len(rows),'valid_labels':len(labelled),'human_labels':sum(r['label_origin']=='human' and r['truth'] is not None for r in rows),
            'synthetic_labels':sum(r['label_origin']=='synthetic' and r['truth'] is not None for r in rows),
            'human_unknown':sum(r['human_unknown'] for r in rows),'unlabeled':sum(r['label_origin'] is None for r in rows),
            'label_coverage':len(labelled)/len(rows) if rows else None,
            'selected':sum(r['decision']=='selected' for r in rows),'pending':sum(r['decision']=='pending' for r in rows),
            'failed':sum(r['state'] in {'failed','unavailable','stale','outdated','skipped','lease_expired'} for r in rows),
            'not_analyzed':sum(r['state']=='not_analyzed' for r in rows),'stale':sum(r['state']=='stale' for r in rows),
            'quality_excluded':sum(r['state']=='source_unqualified' for r in rows),
            'analysis_coverage':sum(r['state'] in {'succeeded','partial'} for r in rows)/len(rows) if rows else None,
            'uncertain_ratio':sum(r['decision']=='pending' for r in rows)/len(rows) if rows else None,
            'labeled_subset_metrics':matrix_metrics(matrix) if labelled else None,
            'decisive_subset_metrics':matrix_metrics(conditional) if sum(conditional.values()) else None,
            'full_benchmark_metrics':matrix_metrics(matrix) if rows and len(labelled)==len(rows) else None,
            'important_missed_count':matrix['fn'] if labelled else None,
            'important_missed_ids':[r['sample_id'] for r in labelled if r['truth'] and r['decision']!='selected']}
    groups={}
    for axis in ['publisher_id','type','stratum']:
        groups[axis]={key:summarize([r for r in predictions if r[axis]==key]) for key in sorted({r[axis] for r in predictions})}
    report={'schema_version':1,'dataset_id':dataset['dataset_id'],'guideline_version':GUIDELINE,'policy_version':policy.version,
        'model':analyzer.model_name if analyzer else None,'pipeline_version':analyzer.version if analyzer else None,
        'prompt_version':analyzer.prompt_version if analyzer else None,'schema_contract':analyzer.schema_version if analyzer else None,
        'split':split,'reference_time':dataset['reference_time'],'summary':summarize(predictions),'groups':groups,
        'distributions':{'population_strata':dataset['strata'],'sample_types':dict(Counter(r['type'] for r in predictions))},
        'missed_examples':[r for r in predictions if r['truth'] is True and r['decision']!='selected'],
        'false_positive_examples':[r for r in predictions if r['truth'] is False and r['decision']=='selected'],
        'predictions':predictions,'production_validated':False,
        'evaluation_origin':'synthetic_allowed' if include_synthetic else 'new_guideline_human_only',
        'limitations':['Labeled-subset display recall counts pending positives as missed, not model negatives.',
            'Conditional decisive metrics exclude failures/unknowns and are not full-cohort accuracy.',
            'Sampling weights describe the local database pool only; no university-wide population estimate.',
            'M2 opportunity labels are not imported; no automatic tuning on holdout.']}
    report['evaluation_id']='featured-evaluation:'+fingerprint(report)
    return report
