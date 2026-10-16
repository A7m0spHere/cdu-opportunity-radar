"""Offline source inventory and staged queue; observations are never live probes."""
from collections import Counter, defaultdict
from importlib.resources import files
import json

from .sources import load_sources

BATCHES={
    'B1':{'computer-column-15','business-column-45','msys-column-59'},
    'B2':{'department-jw-column-89','department-kjc-column-136','department-kjc-column-139'},
    'B3':{'wenxin-column-107','law-column-53','lvyou-column-40'},
}


def inventory():
    catalog=load_sources()
    units=json.loads(files('cdu_radar').joinpath('units.json').read_text(encoding='utf-8'))
    by_unit=defaultdict(list);host_units=defaultdict(set);templates=defaultdict(set)
    for s in catalog:
        unit=s.unit_id or s.id;by_unit[unit].append(s)
        if s.official_domain:host_units[s.official_domain].add(unit)
        if s.column_url and s.status=='verified' and s.parser_supported and s.adapter:templates[unit].add(s.adapter)
    columns=[]
    for s in catalog:
        if not s.column_url:continue
        unit=s.unit_id or s.id
        verified=s.status=='verified' and s.parser_supported and bool(s.adapter) and bool(s.last_verified_at)
        ambiguous=len(host_units.get(s.official_domain,set()))>1
        family=s.adapter or s.content_adapter or (next(iter(templates[unit])) if len(templates[unit])==1 else None)
        batch='B0' if verified else 'blocked_identity' if ambiguous else next((key for key,ids in BATCHES.items() if s.id in ids),'later')
        columns.append({'id':s.id,'publisher_id':unit,'unit_type':s.unit_type,'name':s.college,
            'column':s.column_name,'url':s.column_url,'column_type':s.column_type,
            'accessible_last_observed':s.accessible,'articles_last_observed':s.articles_found,
            'observation_at':s.last_checked_at,'implemented':bool(s.adapter and s.parser_supported),
            'collection_validated':bool(verified),'validated_at':s.last_verified_at,
            'long_term_health':'not_established','current_accessibility':'not_checked',
            'adapter':s.adapter,'candidate_adapter':family,'adapter_family':family or 'inspect_new_template',
            'batch':batch,'identity_ambiguous':ambiguous,'catalog_status':s.status,
            'error_category_last_observed':s.error_category})
    unit_rows=[]
    for unit in units:
        candidates=[c for c in columns if c['publisher_id']==unit['id']]
        count=sum(c['collection_validated'] for c in candidates)
        unit_rows.append({'id':unit['id'],'name':unit['name'],'unit_type':unit['unit_type'],
            'parent_unit':unit.get('parent_unit'),'homepage':unit.get('homepage'),
            'candidate_columns':len(candidates),'implemented_columns':sum(c['implemented'] for c in candidates),
            'validated_columns':count,'remaining_columns':len(candidates)-count,
            'missing_column_url':not candidates,'long_term_health':'not_established'})
    return {'schema_version':1,'mode':'offline_catalog_inventory','network_requests':0,
        'summary':{'units':len(units),'unit_types':dict(Counter(u['unit_type'] for u in units)),
            'registered_entries':len(catalog),'candidate_columns':len(columns),
            'unique_candidate_urls':len({c['url'] for c in columns}),
            'accessible_last_observed':sum(c['accessible_last_observed'] is True for c in columns),
            'implemented_columns':sum(c['implemented'] for c in columns),
            'collection_validated_columns':sum(c['collection_validated'] for c in columns),
            'remaining_columns':sum(not c['collection_validated'] for c in columns),
            'units_without_registered_columns':sum(u['missing_column_url'] for u in unit_rows),
            'long_term_healthy_columns':None},
        'units':unit_rows,'columns':columns,
        'batch_counts':dict(Counter(c['batch'] for c in columns)),
        'adapter_family_counts':dict(Counter(c['adapter_family'] for c in columns if not c['collection_validated'])),
        'semantics':['Candidate rows are not implemented sources; shared URLs are counted separately and also deduplicated.',
            'Accessibility is the last catalog observation, not a current network result.',
            'candidate_adapter is an unverified reuse hypothesis, not parser support.',
            'A successful bounded trial does not establish long-term health.',
            'Shared-domain publisher identity requires manual resolution before collection/publication.',
            'Per-batch additions require list/detail fixtures, bounded live evidence and before/after quality reports.']}


def markdown(report):
    s=report['summary']
    lines=['# 官方来源覆盖盘点','',
        f"{s['units']} 个单位/平台；{s['registered_entries']} 条登记；{s['candidate_columns']} 条候选栏目 / {s['unique_candidate_urls']} 个不同 URL。",
        f"已实现 {s['implemented_columns']}；已完成有界实采验证 {s['collection_validated_columns']}；待接入 {s['remaining_columns']}。长期健康：未建立。",'',
        '离线读取当前登记；历史可访问不代表当前可访问，候选模板不代表已支持。0 个候选表示栏目尚未登记，不表示该单位没有信息或已经覆盖。','',
        '| 单位 ID | 单位 | 类型 | 上级 | 候选栏目 | 已验证 | 待接入 |',
        '| --- | --- | --- | --- | ---: | ---: | ---: |']
    lines += [f"| {u['id']} | {u['name']} | {u['unit_type']} | {u['parent_unit'] or '—'} | {u['candidate_columns']} | {u['validated_columns']} | {u['remaining_columns']} |" for u in report['units']]
    lines += ['', '| 批次 | 栏目 ID | 类型 | 待验证模板 | 官方栏目 |', '| --- | --- | --- | --- | --- |']
    lines += [f"| {c['batch']} | {c['id']} | {c['column_type']} | {c['candidate_adapter'] or '需检查新模板'} | {c['url']} |" for c in report['columns'] if c['batch'] in BATCHES]
    lines += ['', '| 身份待确认栏目 ID | 登记单位 | 官方栏目 |', '| --- | --- | --- |']
    lines += [f"| {c['id']} | {c['name']} | {c['url']} |" for c in report['columns'] if c['identity_ambiguous']]
    return '\n'.join(lines)+'\n'


def coverage_command(args):
    report=inventory()
    print(json.dumps(report,ensure_ascii=False,indent=2) if args.format=='json' else markdown(report),end='\n' if args.format=='json' else '')
    return 0
