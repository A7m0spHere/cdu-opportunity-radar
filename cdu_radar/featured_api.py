"""M8 additive public v3. Old v1/v2 and legacy RSS remain unchanged."""
from urllib.parse import urlencode

from .featured import ANALYSIS_STATES, CRITERIA, REASON_LABELS, overview
from .models import InvalidFilter
from .taxonomy import API_V2, FILTER_KEYS, V2_TOP_FIELDS, V2_ITEM_FIELDS, filter_payload, validate_public_v2

API_V3='/api/notices/v3'


def split_query(path):
    # Reuse the same strict encoding/duplicate/length bounds with one extra axis.
    base,mark,query=path.partition('?')
    if base!=API_V3:raise InvalidFilter('Invalid v3 route')
    # query_values has a nine-field bound; check ten via a separate parse first.
    from urllib.parse import parse_qsl
    import re
    if len(path)>2048 or len(query)>1024 or re.search(r'%(?![0-9a-fA-F]{2})',query):raise InvalidFilter('Invalid v3 query')
    try:pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,encoding='utf-8',errors='strict',max_num_fields=len(FILTER_KEYS)+1) if mark and query else []
    except (ValueError,UnicodeError) as exc:raise InvalidFilter('Invalid v3 query') from exc
    values={}
    for key,value in pairs:
        if key in values or key not in {*FILTER_KEYS,'selection'} or not value or len(value)>100:raise InvalidFilter('Invalid v3 filter')
        values[key]=value
    selection=values.pop('selection','all')
    if selection not in {'all','featured'}:raise InvalidFilter('Invalid selection')
    return API_V2+('?' + urlencode(values) if values else ''),selection


def project(payload, path, by_guid, policy):
    v2_path,selection=split_query(path)
    filtered=filter_payload(payload,v2_path)
    entries=[dict(item,featured=by_guid[item['guid']]) for item in filtered['items']]
    state=overview(entries,policy)
    if selection=='featured':entries=[e for e in entries if e['featured']['decision']=='selected']
    return dict(filtered,schema_version=3,items=entries,featured=state,
        applied_filters=dict(filtered['applied_filters'],selection=selection),
        counts={'all':len(entries),**{scope:sum(i['listing']==scope for i in entries) for scope in ['current','history','unknown']}},
        subscription={'available':False,'url':None,'reason':'filtered_rss_not_implemented'})


def validate_public_v3(payload):
    import re
    if not isinstance(payload,dict) or set(payload)!=V2_TOP_FIELDS|{'featured','subscription'} or payload.get('schema_version')!=3:
        raise ValueError('Unexpected v3 fields')
    base={k:v for k,v in payload.items() if k in V2_TOP_FIELDS}
    if not isinstance(base['applied_filters'],dict) or set(base['applied_filters'])!={*FILTER_KEYS,'selection'} or base['applied_filters']['selection'] not in {'all','featured'}:
        raise ValueError('Invalid v3 filters')
    base['applied_filters']={k:v for k,v in base['applied_filters'].items() if k!='selection'}
    base['schema_version']=2
    base['items']=[]
    for item in payload['items']:
        if not isinstance(item,dict) or set(item)!=V2_ITEM_FIELDS|{'featured'}:raise ValueError('Unexpected v3 item')
        base['items'].append({k:v for k,v in item.items() if k in V2_ITEM_FIELDS})
        f=item['featured']
        if not isinstance(f,dict) or set(f)!={'decision','analysis_state','reason_codes','evidence','importance','uncertainties','policy_version'}:
            raise ValueError('Unexpected featured fields')
        if f['decision'] not in {'selected','not_selected','pending'} or f['analysis_state'] not in ANALYSIS_STATES or f['importance'] not in {'high','normal','low','unknown'}:
            raise ValueError('Invalid featured state')
        if not isinstance(f['policy_version'],str) or not re.fullmatch(r'featured-v1:[a-f0-9]{24}',f['policy_version']):raise ValueError('Invalid featured version')
        for key,choices in [('reason_codes',REASON_LABELS),('uncertainties',{'eligibility_unknown','registration_unknown','attachments_unread'})]:
            if not isinstance(f[key],list) or any(v not in choices for v in f[key]):raise ValueError('Invalid featured reasons')
        if not isinstance(f['evidence'],list) or len(f['evidence'])>len(CRITERIA):raise ValueError('Invalid featured evidence')
        for e in f['evidence']:
            if not isinstance(e,dict) or set(e)!={'criterion','label','quote'} or e['criterion'] not in CRITERIA or e['label']!=CRITERIA[e['criterion']][0] or not isinstance(e['quote'],str) or not 0<len(e['quote'])<=240:
                raise ValueError('Invalid public evidence')
    validate_public_v2(base)
    f=payload['featured']
    if not isinstance(f,dict) or set(f)!={'state','policy_version','coverage','ranges','personalization','evaluation_status'} or f['state'] not in {'ready','pending','degraded'} or f['personalization']!='filters_only' or f['evaluation_status']!='human_benchmark_pending':
        raise ValueError('Invalid featured overview')
    if not isinstance(f['ranges'],dict) or set(f['ranges'])!={'current','history'}:raise ValueError('Invalid range coverage')
    for stats in [dict(state=f['state'],coverage=f['coverage']),*f['ranges'].values()]:validate_stats(stats)
    if not isinstance(f['policy_version'],str) or not re.fullmatch(r'featured-v1:[a-f0-9]{24}',f['policy_version']):raise ValueError('Invalid overview version')
    if any(i['featured']['policy_version']!=f['policy_version'] for i in payload['items']):raise ValueError('Inconsistent featured policy')
    if payload['subscription']!={'available':False,'url':None,'reason':'filtered_rss_not_implemented'}:
        raise ValueError('Unsupported filtered subscription')


def validate_stats(stats):
    if not isinstance(stats,dict) or set(stats)!={'state','coverage'}:raise ValueError('Invalid range fields')
    c=stats['coverage']
    if not isinstance(c,dict) or set(c)!={'total','complete','selected','not_selected','pending','failed','not_analyzed','in_progress','uncertain'} or any(type(v) is not int or v<0 for v in c.values()):
        raise ValueError('Invalid featured coverage')
    if c['total']!=c['complete']+c['pending'] or c['complete']!=c['selected']+c['not_selected'] or c['failed']>c['pending'] or c['not_analyzed']>c['pending']:
        raise ValueError('Inconsistent featured coverage')
    if c['pending']!=c['failed']+c['not_analyzed']+c['in_progress']+c['uncertain']:raise ValueError('Inconsistent pending coverage')
    expected='degraded' if c['failed'] else 'pending' if c['pending'] else 'ready'
    if stats['state']!=expected:raise ValueError('Inconsistent featured state')
