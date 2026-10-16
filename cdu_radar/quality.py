"""Offline quality metrics over one consistent, read-only rollback SQLite snapshot."""
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from statistics import median

from .analysis_models import AnalysisInput, fingerprint, reference_datetime
from .auto_publish import AutoRepository, MAX_SOURCE_AGE
from .sources import load_sources

PARTICIPATION = {'competition','hackathon','project_recruitment','workshop','lecture','other_activity'}
MODES = ('database_snapshot','offline_replay','mixed_baseline_and_live')


def moment(value):
    if not value:
        return None
    try:
        # Collection timestamps need an explicit timezone, unlike source dates.
        if 'T' not in value:
            return None
        return reference_datetime(value)
    except (ValueError, TypeError):
        return None


def distribution(values):
    return {'samples':len(values), 'min':min(values) if values else None,
            'median':median(values) if values else None, 'max':max(values) if values else None}


def opening_quote(item, content, now):
    clauses=[c.strip() for c in re.split(r'[。；\n]',content)]
    if any(re.search(r'报名(?:开始|开放)?(?:时间|日期|入口|通道)?[^。；\n]{0,6}(?:另行通知|待定|未开放|未开始|暂停|停止|关闭)',c) for c in clauses):
        return None
    for clause in clauses:
        if re.search(r'并非|不是|尚未|是否|若|如果|暂不|停止|未开始|无需|不接受|勿|误以为|说法|网传|未经确认|计划|拟于|暂定|预计',clause):
            continue
        if re.match(r'^(?:本(?:次|届)(?:竞赛|比赛|活动|项目)[，,:：]?\s*)?(?:即日起(?:开始|开放|接受|启动|正在)?报名|现(?:已)?开始报名|正在报名|报名(?:现)?已(?:经)?开始)',clause):
            return clause
    start=item['facts']['registration_start_date']
    return start['quote'] if start and reference_datetime(start['value'])<=now else None


def quality_counts(items, rows, analyzer, now):
    counts = dict(qualified=len(items), participation_candidates=0, possible=0,
                  opening_evidence_supported=0, registration_unknown=0,
                  closed=0, historical=0, administrative=0, non_student_target=0,
                  rule_category_unknown=0, eligibility_unknown=0, expired_or_non_participation=0)
    evidence = []
    for item in items:
        row = rows[item['notice_id']]
        notice = AnalysisInput(row['id'],row['content_hash'],fingerprint(dict(row)),row['title'],row['content'],
            row['canonical_url'],(item['source_name'],),row['published_at'],content_source=item['source_name'])
        result = analyzer.analyze(notice, reference_time=now)
        # A competition for staff and a credit/result disclosure are not new
        # student entry points merely because they contain competition/lecture.
        targets=re.findall(r'(?:^|\n)(?:[一二三四五六七八九十\d]+[、.．]\s*)?(?:参赛对象|报名对象|参与对象)[：:]\s*([^\n。；]+)',row['content'])
        non_student=bool(targets) and all(re.search(r'宿管老师|教职工|教师|科研人员',target)
            and not re.search(r'学生|本科|研究生|师生|同学|不限|包括|例如|等|为主|除外|排除|不要求|不需要|不含|并非|不是',target) for target in targets)
        administrative = result.category=='administrative' or '公示' in row['title']
        candidate = (result.category in PARTICIPATION and result.eligibility_status!='ineligible'
                     and not non_student and not administrative
                     and item['listing']!='history' and item['category']!='其他通知')
        counts['participation_candidates'] += candidate
        counts['possible'] += item['registration_status']=='possible'
        counts['registration_unknown'] += item['registration_status']=='unknown'
        counts['closed'] += item['registration_status']=='closed'
        counts['historical'] += item['listing']=='history'
        counts['administrative'] += administrative
        counts['non_student_target'] += non_student
        counts['rule_category_unknown'] += result.category=='unknown'
        counts['eligibility_unknown'] += result.eligibility_status=='uncertain'
        counts['expired_or_non_participation'] += (item['listing']=='history' or administrative or non_student or result.category=='news')
        opening = opening_quote(item,row['content'],now)
        supported = bool(candidate and item['registration_status']=='possible' and opening
                         and result.eligibility_status=='eligible' and item['audience'])
        counts['opening_evidence_supported'] += supported
        if supported:
            evidence.append({'notice_id':row['id'],'url':row['canonical_url'],
                'opening_quote':opening,'audience_quote':item['audience'],
                'deadline':item['facts']['registration_deadline'],
                'scope':'Explicit body evidence only; attachments and all individual qualifications are not verified.'})
    return counts, evidence


def build_report(db_path, *, as_of=None, window_hours=72, data_mode='database_snapshot'):
    if data_mode not in MODES or not isinstance(window_hours,int) or not 1<=window_hours<=87600:
        raise ValueError('Invalid report mode or window (1..87600 hours)')
    path = Path(db_path).resolve()
    if not path.is_file():
        raise ValueError('Source database does not exist')
    # Refuse WAL before SQLite can create shared-memory sidecars. This project
    # serves rollback journals; immutable=1 would silently ignore a live WAL.
    with path.open('rb') as handle:
        header = handle.read(20)
    if len(header)>=20 and (header[18]==2 or header[19]==2):
        raise ValueError('Quality reports require a rollback-journal database snapshot')
    now = reference_datetime(as_of)
    start = now-timedelta(hours=window_hours)
    def in_window(value):
        parsed = moment(value)
        return parsed is not None and start<=parsed<now
    catalog = load_sources()
    implemented = [s for s in catalog if s.column_url and s.status=='verified' and s.parser_supported and s.adapter]
    db = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete','truncate','persist'}:
            raise ValueError('Rollback journal required')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'notices','notice_sources','sources','notice_health','auto_publications','collection_runs'}
        if not required<=tables:
            raise ValueError('Automatic publication schema missing; report never migrates databases')
        rows = {r['id']:dict(r) for r in db.execute('SELECT * FROM notices')}
        urls = [r['canonical_url'] for r in rows.values()]
        if len(urls)!=len(set(urls)):
            raise ValueError('Duplicate notice URL rows; refuse ambiguous quality totals')
        repo = AutoRepository(db)
        plan = repo.feed_plan(now)
        counts, evidence = quality_counts(plan['items'],rows,repo.analyzer,now)
        new_ids = {i for i,r in rows.items() if in_window(r['discovered_at'])}
        updated_ids = {i for i,r in rows.items() if in_window(r['updated_at'])
                       and moment(r['updated_at']) and moment(r['discovered_at'])
                       and moment(r['updated_at'])>moment(r['discovered_at'])}
        cohort_ids = new_ids|updated_ids
        cohort_counts,_ = quality_counts([i for i in plan['items'] if i['notice_id'] in cohort_ids],rows,repo.analyzer,now)
        runs = [dict(r) for r in db.execute('SELECT * FROM collection_runs')] if 'collection_runs' in tables else []
        window_runs = [r for r in runs if in_window(r['finished_at'])]
        run_keys = ('inserted','updated','unchanged','failed','parse_failures','duplicates','pages_read')
        run_counts = {k:sum(r.get(k,0) or 0 for r in window_runs) for k in run_keys}
        associations = {}
        for row in db.execute('SELECT notice_id,source_id FROM notice_sources'):
            associations.setdefault(row['source_id'],set()).add(row['notice_id'])
        source_rows = {r['id']:dict(r) for r in db.execute('SELECT * FROM sources')}
        qualified_ids = {i['notice_id'] for i in plan['items']}
        source_metrics = []
        for source in implemented:
            state = source_rows.get(source.id,{})
            history=[r for r in runs if r['source_id']==source.id]
            successes=[moment(r['finished_at']) for r in history if not r.get('error') and not r['failed']]
            success=max((at for at in successes if at is not None),default=None)
            source_metrics.append({'source_id':source.id,'name':source.college,'column':source.column_name,
                'observed_unique_notices':len(associations.get(source.id,set())),
                'qualified_unique_notices':len(associations.get(source.id,set()) & qualified_ids),
                'new_unique_notices':len(associations.get(source.id,set()) & new_ids),
                'latest_status':state.get('status','not_observed'),
                'last_success_at':success.isoformat() if success else None,
                'never_collected':not history,
                'latest_attempt_failed':state.get('status')=='failed' or bool(state.get('last_error')),
                'success_unverified_or_expired':success is None or success>now or now-success>MAX_SOURCE_AGE})
        lag_days=[];missing_dates=invalid_dates=0
        for row in rows.values():
            observed = moment(row['discovered_at'])
            try:
                published = date.fromisoformat(row['published_at']) if row['published_at'] else None
            except ValueError:
                invalid_dates += 1;continue
            if published is None or observed is None:
                missing_dates += 1;continue
            lag = (observed.date()-published).days
            if lag<0:
                invalid_dates += 1;continue
            lag_days.append(lag)
        return {'schema_version':1,'quality_policy_version':'m5b-quality-v1',
            'report_implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'classifier_version':repo.analyzer.version,'data_mode':data_mode,'as_of':now.isoformat(),
            'read_only':True,'network_requests':0,'llm_calls':0,
            'window':{'start_inclusive':start.isoformat(),'end_exclusive':now.isoformat(),
                'new_unique_notices':len(new_ids),'latest_updated_unique_notices':len(updated_ids),
                'new_or_updated_unique_notices':len(cohort_ids),'collection_runs':len(window_runs),
                'collection_event_totals':run_counts,'qualified_cohort':cohort_counts},
            'snapshot':{'raw_unique_notices':len(rows),**counts,
                'excluded':dict(Counter(i['reason'] for i in plan['excluded']))},
            'catalog':{'registered_entries':len(catalog),'candidate_columns':sum(bool(s.column_url) for s in catalog),
                'unique_candidate_urls':len({s.column_url for s in catalog if s.column_url}),
                'implemented_verified_columns':len(implemented),
                'observed_columns':sum(bool(v) for v in associations.values())},
            'source_health':{'denominator':len(implemented),
                'never_collected':sum(s['never_collected'] for s in source_metrics),
                'latest_attempt_failed':sum(s['latest_attempt_failed'] for s in source_metrics),
                'success_unverified_or_expired':sum(s['success_unverified_or_expired'] for s in source_metrics)},
            'sources':source_metrics,'opening_evidence':evidence,
            'latency':{'exact_hours':distribution([]),
                'exact_uncomputable_missing_source_time_or_timezone':len(rows),
                'calendar_day_lag':distribution(lag_days),'missing_dates_or_collection_time':missing_dates,
                'invalid_or_negative_dates':invalid_dates},
            'semantics':[
                'Quality is a deterministic publication/evidence snapshot, not human labels or model accuracy.',
                'Unknown, historical, administrative and closed overlap. Do not add these counts.',
                'Per-source counts overlap for reposts; global counts use unique final URLs.',
                'Window is [start,end); updates count only the latest stored update, not historical update events.',
                'Collection totals are events; duplicate/unchanged attempts are not new unique notices.',
                'Catalog coverage is the current configuration, not historical collection coverage or uptime.',
                'Source dates have no clock time/timezone; calendar-day lag is not exact discovery latency.',
                'Opening evidence does not verify attachments, individual eligibility or current live availability.',
                'Participation candidates exclude explicit staff-only targets and disclosure titles; all qualified notices remain in the aggregate.',
                'Offline replay and mixed data do not constitute current official-site verification.']}
    finally:
        db.close()


def render_markdown(report):
    snapshot=report['snapshot'];window=report['window'];catalog=report['catalog']
    lines=['# 校园信息质量报告','',f"模式：{report['data_mode']}；参考时间：{report['as_of']}",
        f"统计窗口：[{window['start_inclusive']}, {window['end_exclusive']})",'',
        '| 指标 | 数量 |','| --- | ---: |',
        f"| 新发现独立原文 | {window['new_unique_notices']} |",
        f"| 最近实质更新原文 | {window['latest_updated_unique_notices']} |",
        f"| 库内独立原文 | {snapshot['raw_unique_notices']} |",
        f"| 质量合格 | {snapshot['qualified']} |",
        f"| 非历史参与候选 | {snapshot['participation_candidates']} |",
        f"| 可能仍可参与 | {snapshot['possible']} |",
        f"| 正文有开放及本科生对象证据 | {snapshot['opening_evidence_supported']} |",
        f"| 报名未知 | {snapshot['registration_unknown']} |",
        f"| 明确已截止 | {snapshot['closed']} |",
        f"| 历史信息 | {snapshot['historical']} |",
        f"| 规则行政/公示内容 | {snapshot['administrative']} |",
        f"| 明确非学生对象 | {snapshot['non_student_target']} |",
        f"| 规则类型未知 | {snapshot['rule_category_unknown']} |",
        f"| 资格未知 | {snapshot['eligibility_unknown']} |",'',
        f"来源覆盖：{catalog['implemented_verified_columns']} 个已实现并验证栏目 / {catalog['candidate_columns']} 个候选栏目；登记项共 {catalog['registered_entries']}。",
        f"精确收录延迟：不可计算（缺来源时分秒/时区 {report['latency']['exact_uncomputable_missing_source_time_or_timezone']} 篇）。",
        f"来源日期至采集日的日历差：{json.dumps(report['latency']['calendar_day_lag'],ensure_ascii=False)}；不等于精确延迟。",'',
        '| 来源 ID | 关联原文 | 合格 | 从未在本库采集 | 最近尝试失败 | 成功时间未核验/超期 |',
        '| --- | ---: | ---: | --- | --- | --- |']
    lines += [f"| {s['source_id']} | {s['observed_unique_notices']} | {s['qualified_unique_notices']} | {s['never_collected']} | {s['latest_attempt_failed']} | {s['success_unverified_or_expired']} |" for s in report['sources']]
    lines += ['', '排除原因：'+json.dumps(snapshot['excluded'],ensure_ascii=False),
              '', '各状态和来源计数可能重叠，不可直接加总；候选与开放证据不是人工真值或报名资格保证。',
              '报告离线只读，不重新验证官网；离线回放/混合数据不能作为当前联网事实。','']
    return '\n'.join(lines)


def quality_command(args):
    try:
        report=build_report(args.db,as_of=args.as_of,window_hours=args.window_hours,data_mode=args.data_mode)
        body=render_markdown(report) if args.format=='markdown' else json.dumps(report,ensure_ascii=False,indent=2)+'\n'
        if args.output:
            output=args.output.absolute();db=args.db.resolve()
            if output.is_symlink():
                raise ValueError('Output must not be a symbolic link')
            resolved=output.resolve()
            if resolved in (db,*(db.with_name(db.name+suffix) for suffix in ('-wal','-shm','-journal'))):
                raise ValueError('Output must not overwrite the source database or its sidecars')
            # Exclusive creation also refuses symlinks/existing/private files.
            with output.open('x',encoding='utf-8',newline='\n') as handle:
                handle.write(body)
        else:
            print(body,end='')
        return 0
    except (OSError,sqlite3.Error,ValueError,TypeError) as exc:
        import sys
        print(f'Quality report failed: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2
