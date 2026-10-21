"""M10-B: frozen, multi-axis human worksheet and cache-only evaluation."""
from collections import Counter
from contextlib import ExitStack
import csv
import io
import json
from pathlib import Path
import re
import sqlite3

from .analysis_models import fingerprint, reference_datetime
from .async_queue import source_snapshot
from .classification import classify, policy_version, TYPES, DOMAINS, TOPICS
from .evaluation import matrix_metrics
from .evidence import locate
from .featured import CRITERIA, FeaturedPolicy, LEVELS, analysis_reader, analysis_for
from .featured_benchmark import export_dataset, load_dataset, frozen_input, worksheet, IDENTITY, MANUAL, save_new
from .privacy import spreadsheet_text
from .review_api import review
from .undergraduate import assess, snippets

GUIDELINE='campus-quality-human-v1'
EXTRA=['expected_type','expected_domains','expected_topics','classification_quote',
    'undergraduate_relevant','undergraduate_quote','action_basis','action_quote']
COLUMNS=IDENTITY+MANUAL+EXTRA


def implementation_version():
    from importlib.resources import files
    return fingerprint([files('cdu_radar').joinpath(n).read_text('utf-8').replace('\r\n','\n')
                        for n in ['quality_benchmark.py','undergraduate.py','review_api.py']])


def export_package(raw_db, directory, *, reference_time, limit=50, seed='m10-quality-v1', state_db=None, analyzer=None):
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Sample limit must be 1..50')
    directory=Path(directory)
    # Reuse the immutable importance dataset, privacy sanitizer, splits and stratified sampling.
    export_dataset(raw_db,directory,limit=limit,seed=seed,reference_time=reference_time)
    data=load_dataset(directory/'dataset.json');now=reference_datetime(data['reference_time'])
    predictions=[];policy=FeaturedPolicy()
    with ExitStack() as stack:
        repo=stack.enter_context(source_snapshot(raw_db));queue=None;queue_error=False
        if state_db and analyzer:
            try:queue=stack.enter_context(analysis_reader(state_db,raw_db))
            except (ValueError,OSError,sqlite3.Error):queue_error=True
        from .auto_publish import AutoRepository
        plan=AutoRepository(repo.db).feed_plan(now)
        by_id={str(r['notice_id']):r for r in plan['items']}
        for r in data['records']:
            n=frozen_input(r);analysis=None;state='unavailable' if queue_error else 'not_analyzed'
            if queue:_,analysis,state=analysis_for(queue,repo,analyzer,n.notice_id,now)
            base=classify(n.title,n.content)
            effective=review({},dict(title=n.title,content=n.content),analysis,state)
            importance=analysis['importance']['level'] if analysis else 'unknown'
            decision=policy.decide(n,analysis,state)
            # Explicitly a simple rule baseline, separate from model+rule production selection.
            supported=[k for k,(_,weight,pattern) in CRITERIA.items() if weight>=policy.settings['rule_threshold']
                and any(re.search(pattern,q) and not re.search(r'并非|不是|是否|若|如果|[？?]',q) for q in [n.title,*snippets(n.content)])]
            action_quotes=[q for q in snippets(n.content) if re.search(r'须|务必|请|登录|办理|报名|提交|公示期|异议',q)]
            predictions.append(dict(sample_id=r['sample_id'],input_hash=r['input_hash'],state=state,
                rule=dict(classification=base,featured=bool(supported),criteria=supported),
                model=dict(classification=analysis['classification'] if analysis and analysis.get('classification_valid') else None,
                    featured=LEVELS[importance]>=LEVELS[policy.settings['model_threshold']] if importance in LEVELS else None),
                effective=dict(classification=effective['classification'],review_state=effective['classification_review']['state'],
                    featured=decision['decision']=='selected',decision=decision['decision']),
                undergraduate=effective['undergraduate'],action_basis=True if action_quotes else None,action_evidence=action_quotes[:3],
                registration_status=by_id[r['sample_id']]['registration_status'],listing=by_id[r['sample_id']]['listing']))
    manifest=dict(schema_version=1,guideline_version=GUIDELINE,dataset_id=data['dataset_id'],
        reference_time=data['reference_time'],classification_version=policy_version(),importance_version=policy.version,
        implementation_version=implementation_version(),pipeline_version=analyzer.version if analyzer else None,
        predictions=predictions,model_requests=0,old_labels_imported=0)
    manifest['benchmark_id']='quality-benchmark:'+fingerprint(manifest)
    save_new(directory/'predictions.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=COLUMNS);writer.writeheader()
    for record in data['records']:writer.writerow(worksheet(record,data))
    save_new(directory/'review-labels.csv',output.getvalue(),encoding='utf-8-sig')
    distribution={axis:dict(Counter(p[axis] for p in predictions)) for axis in ['state','listing','registration_status']}
    save_new(directory/'sampling.json',json.dumps(dict(target='30..50 real notices',sampled=data['sampled'],population=data['population'],
        insufficient_sample=data['sampled']<30,source_count=len({r['publisher_id'] for r in data['records']}),
        types=dict(Counter(p['rule']['classification']['type'] for p in predictions)),distributions=distribution,
        reference_policy='historical snapshot; not live source verification',split=dict(Counter(r['split'] for r in data['records'])),
        uncovered_time_states=[s for s in ['current','history','unknown'] if not distribution['listing'].get(s)],
        local_population_only=True),ensure_ascii=False,indent=2))
    # Readable original evidence without model predictions: avoid anchoring the reviewer.
    text='# 人工标注原文包\n\n填写 review-labels.csv；所有人工列为空。依据冻结原文逐项确认，unknown 不等于 false。\n\n'
    for r in data['records']:
        text+=f"## 样本 {r['sample_id']} · {r['title']}\n\n发布单位：{r['publisher_id']}；来源日期：{r['published_at'] or '未知'}；划分：{r['split']}\n\n原文：<{r['canonical_url']}>\n\n{r['content']}\n\n"
    save_new(directory/'原文核对.md',text)
    return dict(benchmark_id=manifest['benchmark_id'],sampled=data['sampled'],human_labels=0,model_requests=0,
                reference_time=data['reference_time'],directory=str(directory))


def load_package(directory):
    directory=Path(directory);data=load_dataset(directory/'dataset.json')
    from .llm_json import strict_json
    manifest=strict_json((directory/'predictions.json').read_bytes())
    if (manifest.get('schema_version')!=1 or manifest.get('guideline_version')!=GUIDELINE
            or manifest.get('dataset_id')!=data['dataset_id'] or manifest.get('benchmark_id')!='quality-benchmark:'+fingerprint({k:v for k,v in manifest.items() if k!='benchmark_id'})
            or manifest['implementation_version']!=implementation_version() or manifest['classification_version']!=policy_version()
            or manifest['importance_version']!=FeaturedPolicy().version):
        raise ValueError('Frozen quality package/version changed; export a new revision')
    if {p['sample_id'] for p in manifest['predictions']}!={r['sample_id'] for r in data['records']}:
        raise ValueError('Prediction/sample mismatch')
    return data,manifest


def check_quote(text, record, *, required=False):
    if required and not text:raise ValueError('Evidence quote required for a decisive label')
    if len(text)>2000:raise ValueError('Evidence too long')
    for quote in text.splitlines():
        if quote and locate(frozen_input(record),quote).source not in {'title','content'}:
            raise ValueError('Label evidence is outside frozen source text')


def import_labels(directory, input_path, labels_db, *, labeler, allow_synthetic=False):
    data,manifest=load_package(directory);by_id={r['sample_id']:r for r in data['records']}
    if not isinstance(labeler,str) or not 1<=len(labeler.strip())<=100:raise ValueError('Labeler required')
    prepared=[];seen=set()
    with Path(input_path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=COLUMNS:raise ValueError('Unexpected quality worksheet columns')
        for row in reader:
            if None in row or any(v is None for v in row.values()):raise ValueError('Malformed label row')
            ident=row['sample_id']
            if ident in seen or ident not in by_id:raise ValueError('Unknown/duplicate label')
            seen.add(ident);record=by_id[ident]
            if any(row[k]!=worksheet(record,data)[k] for k in IDENTITY):raise ValueError('Frozen identity changed')
            value={k:row[k].strip() for k in MANUAL+EXTRA}
            if not any(value.values()):continue
            if value['label_origin'] not in ({'human','synthetic'} if allow_synthetic else {'human'}) or value['confirmed_guideline']!='true' or not value['notes']:
                raise ValueError('Explicit human confirmation and notes required')
            if any(len(v)>2000 for v in value.values()):raise ValueError('Label exceeds bound')
            for k in ['objective_featured','undergraduate_relevant','action_basis']:
                if value[k] not in {'','true','false','unknown'}:raise ValueError('Invalid three-state label')
            if value['importance'] not in {'','unknown','low','normal','high'}:raise ValueError('Invalid importance')
            codes=value['criterion_codes'].split('|') if value['criterion_codes'] else []
            if len(codes)!=len(set(codes)) or not set(codes)<=set(CRITERIA):raise ValueError('Invalid criteria')
            if value['objective_featured']=='true' and not codes:raise ValueError('Positive featured label needs criterion')
            check_quote(value['evidence_quote'],record,required=value['objective_featured'] in {'true','false'})
            if value['expected_type'] not in {'','unknown',*TYPES}:raise ValueError('Invalid information type')
            for key,enum in [('expected_domains',DOMAINS),('expected_topics',TOPICS)]:
                tags=value[key].split('|') if value[key] not in {'','none','unknown'} else []
                if len(tags)!=len(set(tags)) or not set(tags)<=set(enum):raise ValueError('Invalid classification tags')
            classification_decisive=any(value[k] not in {'','unknown'} for k in ['expected_type','expected_domains','expected_topics'])
            check_quote(value['classification_quote'],record,required=classification_decisive)
            for field,quote in [('undergraduate_relevant','undergraduate_quote'),('action_basis','action_quote')]:
                check_quote(value[quote],record,required=value[field] in {'true','false'})
            prepared.append((ident,record['input_hash'],value))
    path=Path(labels_db).absolute()
    protected={p.resolve() for p in Path(directory).iterdir() if p.is_file()}|{Path(input_path).resolve()}
    if path.is_symlink() or path.resolve() in protected or any(str(p).lower().endswith(('-wal','-shm','-journal')) for p in (path,path.resolve())):
        raise ValueError('Independent label database required')
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}!={'quality_labels'}:
                raise ValueError('Labels must use an independent quality-only database')
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS quality_labels(id INTEGER PRIMARY KEY,benchmark_id TEXT,sample_id TEXT,input_hash TEXT,labeler TEXT,payload TEXT)')
        inserted=0
        for ident,input_hash,value in prepared:
            payload=json.dumps(value,ensure_ascii=False,sort_keys=True)
            old=db.execute('SELECT labeler,payload FROM quality_labels WHERE benchmark_id=? AND sample_id=? ORDER BY id DESC LIMIT 1',(manifest['benchmark_id'],ident)).fetchone()
            if old==(labeler,payload):continue
            db.execute('INSERT INTO quality_labels(benchmark_id,sample_id,input_hash,labeler,payload) VALUES (?,?,?,?,?)',
                (manifest['benchmark_id'],ident,input_hash,labeler,payload));inserted+=1
    return dict(imported=inserted,source_writes=0,old_labels_imported=0)


def binary(rows, truth_key, predict, *, display=True):
    labels=[(r,predict(r)) for r in rows if r['truth'].get(truth_key) in {'true','false'}]
    matrix=dict(tp=0,fp=0,fn=0,tn=0);abstained=0
    for r,p in labels:
        actual=r['truth'][truth_key]=='true';abstained+=p is None
        # Display recall includes abstained positive items as misses; never call it model-only accuracy.
        key='tp' if p and actual else 'fp' if p else 'fn' if actual else 'tn';matrix[key]+=1
    decisive=dict(tp=0,fp=0,fn=0,tn=0)
    for r,p in labels:
        if p is None:continue
        actual=r['truth'][truth_key]=='true'
        key='tp' if p and actual else 'fp' if p else 'fn' if actual else 'tn';decisive[key]+=1
    available=bool(labels and (display or not abstained))
    return dict(labeled=len(labels),unlabeled_or_unknown=len(rows)-len(labels),abstained=abstained,
        metric_target='display selection including missed pending positives' if display else 'decisive model predictions; abstention is not a negative',
        labeled_subset_metrics=matrix_metrics(matrix) if available else None,
        decisive_subset_metrics=matrix_metrics(decisive) if sum(decisive.values()) else None,
        full_cohort_metrics=matrix_metrics(matrix) if available and rows and len(labels)==len(rows) else None,
        metrics_complete=bool(available and rows and len(labels)==len(rows)),
        missed_ids=[r['sample_id'] for r,p in labels if r['truth'][truth_key]=='true' and not p],
        false_positive_ids=[r['sample_id'] for r,p in labels if r['truth'][truth_key]=='false' and p])


def evaluate_package(directory, *, raw_db, labels_db=None, split='holdout', include_synthetic=False):
    if split not in {'holdout','development','all'}:raise ValueError('Invalid split')
    data,manifest=load_package(directory);truth={}
    if labels_db:
        with sqlite3.connect(Path(labels_db).resolve(strict=True).as_uri()+'?mode=ro',uri=True) as db:
            for ident,input_hash,payload in db.execute('SELECT sample_id,input_hash,payload FROM quality_labels WHERE benchmark_id=? ORDER BY id',(manifest['benchmark_id'],)):
                value=json.loads(payload)
                if value['label_origin']=='human' or include_synthetic:truth[ident]=(input_hash,value)
    records={r['sample_id']:r for r in data['records']};rows=[]
    with source_snapshot(raw_db) as repo:
        for p in manifest['predictions']:
            r=records[p['sample_id']]
            if split!='all' and r['split']!=split:continue
            try:current=repo.input(int(p['sample_id']));stale=current.input_hash!=r['input_hash']
            except ValueError:stale=True
            entry=truth.get(p['sample_id']);value=entry[1] if entry and entry[0]==r['input_hash'] and not stale else {}
            rows.append(dict(p,publisher_id=r['publisher_id'],type=p['rule']['classification']['type'],split=r['split'],
                title=r['title'],canonical_url=r['canonical_url'],stale=stale,truth=value))
    featured={};classification={}
    for mode in ['rule','model','effective']:
        featured[mode]=binary(rows,'objective_featured',lambda r:None if r['stale'] else r[mode]['featured'],display=mode!='model')
        dimensions={}
        for field,key in [('type','expected_type'),('domains','expected_domains'),('topics','expected_topics')]:
            compared=0;errors=[];missing=[];distribution=Counter()
            for r in rows:
                expected=r['truth'].get(key)
                if not expected or expected=='unknown':continue
                compared+=1;pred=r[mode]['classification'] if not r['stale'] else None
                if not pred:missing.append(r['sample_id']);continue
                actual=pred[field];wanted=expected if field=='type' else ([] if expected=='none' else expected.split('|'))
                if (actual!=wanted if field=='type' else set(actual)!=set(wanted)):
                    errors.append(r['sample_id']);distribution[str(wanted)+' -> '+str(actual)]+=1
            dimensions[field]=dict(labeled=compared,errors=len(errors),error_ids=errors,not_predicted_ids=missing,
                predicted=compared-len(missing),error_distribution=dict(distribution),
                error_rate=len(errors)/compared if compared and not missing else None,
                decisive_subset_error_rate=len(errors)/(compared-len(missing)) if compared>len(missing) else None,
                unresolved_or_error_rate=(len(errors)+len(missing))/compared if compared else None)
        classification[mode]=dimensions
    undergraduate=binary(rows,'undergraduate_relevant',lambda r:r['undergraduate']['state']=='relevant')
    action=binary(rows,'action_basis',lambda r:r['action_basis'])
    examples=[]
    # Feedback is restricted to development. Holdout errors are measurements, never tuning suggestions.
    if split!='holdout':
        for r in rows:
            if r['split']!='development':continue
            kinds=[]
            for axis,report in [('featured',featured['effective']),('undergraduate',undergraduate),('action_basis',action)]:
                if r['sample_id'] in report['missed_ids']:kinds.append(axis+':missed')
                if r['sample_id'] in report['false_positive_ids']:kinds.append(axis+':false_positive')
            for axis,v in classification['effective'].items():
                if r['sample_id'] in v['error_ids']:kinds.append('classification:'+axis)
            if kinds:examples.append(dict(sample_id=r['sample_id'],title=r['title'],url=r['canonical_url'],errors=kinds,
                evidence={k:r['truth'].get(k) for k in ['evidence_quote','classification_quote','undergraduate_quote','action_quote']},
                repair_hint='Add a minimal development-only fixture for these exact source spans; inspect audience/type/evidence guards before adjusting rules.'))
    report=dict(schema_version=1,benchmark_id=manifest['benchmark_id'],reference_time=data['reference_time'],split=split,
        total=len(rows),human_labeled_samples=sum(r['truth'].get('label_origin')=='human' for r in rows),
        synthetic_labeled_samples=sum(r['truth'].get('label_origin')=='synthetic' for r in rows),
        stale_samples=sum(r['stale'] for r in rows),analysis_states=dict(Counter(r['state'] for r in rows)),
        featured=featured,classification=classification,undergraduate=undergraduate,action_basis=action,
        development_error_examples=examples[:5],
        groups={axis:{k:dict(total=len([r for r in rows if r[axis]==k]),
            human_labels=sum(bool(r['truth']) and r['truth'].get('label_origin')=='human' for r in rows if r[axis]==k))
            for k in sorted({r[axis] for r in rows})} for axis in ['publisher_id','type','listing']},
        model_requests=0,source_writes=0,old_labels_imported=0,production_validated=False,
        warning='没有有效人工标签，真实效果无法计算' if not any(r['truth'] for r in rows) else '仅代表固定本地样本；不代表全校准确率',
        limitations=['Historical frozen replay, not current source health or live eligibility.',
            'Failures, pending and not-analyzed samples remain in denominators and coverage.',
            'Abstained positives count as display misses, not model factual negatives.',
            'No human truth inferred from rules, model output, old M2 or synthetic examples.',
            'No automatic tuning or repair suggestions from holdout.'])
    report['evaluation_id']='quality-evaluation:'+fingerprint(report)
    return report
