"""Public v4 effective classification; no provider or credential access."""
from collections import Counter
from copy import deepcopy
import re
from urllib.parse import parse_qsl, urlencode
from xml.etree import ElementTree as ET

from .classification import TYPES, TOPICS, DOMAINS, classify
from .featured import overview, scope_stats
from .models import InvalidFilter
from .taxonomy import API_V2, FILTER_KEYS, parse_filters, validate_public_v2
from . import rss_v1

API_V4 = '/api/notices/v4'
RSS_ROUTES = ('/rss/v2/all.xml', '/rss/v2/featured.xml')
REVIEW_STATES = {'not_analyzed','pending','reviewed','uncertain','failed','stale'}


def split_query(path, options, categories):
    base, _, query = path.partition('?')
    if base not in {API_V4,*RSS_ROUTES} or len(path)>2048 or len(query)>1024 or re.search(r'%(?![0-9a-fA-F]{2})',query):
        raise InvalidFilter('Invalid review filter')
    try:pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,encoding='utf-8',errors='strict',max_num_fields=11)
    except (ValueError,UnicodeError) as exc:raise InvalidFilter('Invalid review filter') from exc
    values={}
    for key,value in pairs:
        if key in values or key not in {*FILTER_KEYS,'selection','domain'} or not value or len(value)>100:
            raise InvalidFilter('Invalid review filter')
        values[key]=value
    selection=values.pop('selection','featured' if base==RSS_ROUTES[1] else 'all')
    if selection not in {'all','featured'} or base in RSS_ROUTES and selection!=('featured' if base==RSS_ROUTES[1] else 'all'):
        raise InvalidFilter('Invalid selection')
    domain=values.pop('domain','all')
    parts=domain.split(',')
    if len(parts)!=len(set(parts)) or not set(parts)<={'all','unclassified',*DOMAINS} or len(parts)>1 and {'all','unclassified'}&set(parts):
        raise InvalidFilter('Invalid domain')
    f=parse_filters(API_V2+('?' + urlencode(values) if values else ''),options,categories)
    return dict(f,domain=','.join(sorted(parts)),selection=selection)


def canonical_path(filters):
    values=[(k,v) for k,v in filters.items() if k!='selection' and v not in {'','all'}]
    return RSS_ROUTES[1 if filters['selection']=='featured' else 0]+('?' + urlencode(values) if values else '')


def review(item, row, analysis, state):
    base=classify(row['title'],row['content'])
    result={'state':'not_analyzed','changed':False,'rule':deepcopy(base),'evidence':[],'reason':None}
    if state in {'queued','running','retry_wait','budget_blocked'}:result['state']='pending'
    elif state in {'outdated','stale'}:result['state']='stale'
    elif state in {'failed','skipped','lease_expired','unavailable'}:result['state']='failed'
    elif state in {'succeeded','partial'}:
        result['state']='uncertain'
        if analysis and analysis.get('classification_valid') is True:
            model=analysis['classification']
            changed=any((base[k] if k=='type' else sorted(base[k]))!=(model[k] if k=='type' else sorted(model[k])) for k in ('type','domains','topics'))
            base.update(type=model['type'],domains=model['domains'],topics=model['topics'],method='llm_reviewed',
                        model_state='reviewed',type_state='classified',topic_state='tagged' if model['topics'] else 'unclassified')
            result.update(state='reviewed',changed=changed,evidence=analysis['classification_evidence'],reason=analysis['classification_reason'])
    return dict(item,classification=base,classification_review=result)


def review_summary(items):
    counts=Counter(i['classification_review']['state'] for i in items)
    return {'total':len(items),**{s:counts[s] for s in sorted(REVIEW_STATES)},
            'corrected':sum(i['classification_review']['changed'] for i in items)}


def project(payload, path, rows, analyses, decisions, policy):
    opts=dict(payload['filter_options'],domains=[{'id':k,'label':v} for k,v in DOMAINS.items()])
    f=split_query(path,opts,payload['categories'])
    entries=[]
    for item in payload['items']:
        analysis,state=analyses.get(item['guid'],(None,'not_analyzed'))
        entries.append(dict(review(item,rows[item['source_url']],analysis,state),featured=decisions[item['guid']]))
    def matches(i):
        c=i['classification'];day=i['source_published_date']
        def tags(field,actual):
            return f[field]=='all' or (f[field]=='unclassified' and not actual) or (f[field]!='unclassified' and set(f[field].split(','))<=set(actual))
        return (f['source'] in {'all',i['publisher_id']} and f['type'] in {'all',c['type']} and tags('domain',c['domains']) and tags('topic',c['topics'])
            and f['status'] in {'all',i['registration_status']} and f['view'] in {'all',i['listing']}
            and f['category'] in {'all',i['category']} and (f['published']=='all' or (day is not None)==(f['published']=='known'))
            and (not f['from'] or day is not None and day>=f['from']) and (not f['to'] or day is not None and day<=f['to']))
    cohort=[i for i in entries if matches(i)]
    entries=[i for i in cohort if f['selection']=='all' or i['featured']['decision']=='selected']
    return dict(payload,schema_version=4,taxonomy_version=2,filter_options=opts,items=entries,applied_filters=f,
        classification_review=review_summary(cohort),featured=overview(cohort,policy),
        counts={'all':len(entries),**{s:sum(i['listing']==s for i in entries) for s in ['current','history','unknown']}},
        subscription={'available':True,'url':canonical_path(f),'reason':None})


def render_rss(payload,path,first_featured=None):
    """The input is already filtered with exactly the effective v4 classifications."""
    featured=path.partition('?')[0]==RSS_ROUTES[1]
    stats=scope_stats(payload['items'])
    if featured and payload['featured']['coverage']['total'] and not payload['featured']['coverage']['complete']:
        raise ValueError('Featured analysis not ready')
    base=dict(payload,items=[dict(i,category=TYPES[i['classification']['type']]) for i in payload['items']],categories=list(TYPES.values()))
    # Render identity/time/escaping through the existing bounded RSS implementation.
    route=rss_v1.ROUTES[1 if featured else 0]
    collection={i['guid']:i['featured'] for i in payload['items']} if featured else None
    body=rss_v1.render(base,route,collection=collection,first_featured=first_featured)
    root=ET.fromstring(body);channel=root.find('channel')
    channel.find('{'+rss_v1.NS+'}canonical_path').text=payload['subscription']['url']
    by_guid={i['guid']:i for i in payload['items']}
    for item in channel.findall('item'):
        entry=by_guid[item.findtext('guid')]
        for name,value in [('type',entry['classification']['type']),('domains',','.join(entry['classification']['domains'])),
                           ('topics',','.join(entry['classification']['topics'])),('classification_state',entry['classification_review']['state'])]:
            ET.SubElement(item,'{urn:cdu-radar:rss:v2}'+name).text=value
    result=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    if len(result)>rss_v1.MAX_BYTES:raise ValueError('RSS exceeds byte limit')
    return result


def validate_public_v4(payload):
    """Closed LAN projection; no private queue fields may cross the proxy."""
    from .featured_api import validate_public_v3
    from .taxonomy import V2_TOP_FIELDS
    if not isinstance(payload,dict) or set(payload)!=V2_TOP_FIELDS|{'featured','subscription','classification_review'} or payload.get('schema_version')!=4 or payload.get('taxonomy_version')!=2:
        raise ValueError('Unexpected v4 fields')
    base=deepcopy(payload);base.pop('classification_review');base.update(schema_version=3,taxonomy_version=1)
    if set(base['filter_options'])!={'sources','types','topics','statuses','date_presence','domains'}:raise ValueError('Invalid filter options')
    if base['filter_options'].pop('domains')!=[{'id':k,'label':v} for k,v in DOMAINS.items()]:raise ValueError('Invalid domains')
    for key in ['domain','selection']:
        if key not in base['applied_filters']:raise ValueError('Missing v4 filter')
    base['applied_filters'].pop('domain')
    counts=payload['classification_review']
    if not isinstance(counts,dict) or set(counts)!={'total','corrected',*REVIEW_STATES} or any(type(v) is not int or v<0 for v in counts.values()) or sum(counts[s] for s in REVIEW_STATES)!=counts['total'] or counts['corrected']>counts['reviewed']:
        raise ValueError('Invalid review counts')
    for item in base['items']:
        review_info=item.pop('classification_review',None);c=item['classification']
        if not isinstance(review_info,dict) or set(review_info)!={'state','changed','rule','evidence','reason'} or review_info['state'] not in REVIEW_STATES or type(review_info['changed']) is not bool:
            raise ValueError('Invalid review metadata')
        for value in [c,review_info['rule']]:
            if not isinstance(value,dict) or set(value)!={'version','policy','method','type','domains','topics','type_state','topic_state','model_state'} or value['version']!=2 or not re.fullmatch(r'campus-taxonomy-v2:[a-f0-9]{16}',value['policy']) or value['method'] not in {'rules','llm_reviewed'} or value['model_state'] not in {'not_analyzed','reviewed'}:
                raise ValueError('Invalid v4 classification')
            if value['type'] not in TYPES or any(not isinstance(value[k],list) or len(value[k])!=len(set(value[k])) or any(v not in enum for v in value[k]) for k,enum in [('domains',DOMAINS),('topics',TOPICS)]):raise ValueError('Invalid classification labels')
        if review_info['rule']['method']!='rules' or review_info['rule']['model_state']!='not_analyzed':raise ValueError('Invalid rule provenance')
        if (review_info['state']=='reviewed')!=(c['method']=='llm_reviewed') or (c['method']=='llm_reviewed')!=(c['model_state']=='reviewed') or review_info['changed'] and review_info['state']!='reviewed':raise ValueError('Inconsistent review provenance')
        if len(review_info['evidence'])>24 or review_info['reason'] is not None and (not isinstance(review_info['reason'],str) or len(review_info['reason'])>600):raise ValueError('Invalid review reason')
        for e in review_info['evidence']:
            if not isinstance(e,dict) or set(e)!={'field','value','quote'} or e['field'] not in {'type','domain','topic'} or e['value'] not in {'type':TYPES,'domain':DOMAINS,'topic':TOPICS}[e['field']] or not isinstance(e['quote'],str) or not 0<len(e['quote'])<=240:raise ValueError('Invalid review evidence')
        from .taxonomy import POLICY
        item['classification']={k:v for k,v in c.items() if k!='domains'}
        item['classification'].update(version=1,policy=POLICY,method='rules',model_state='not_analyzed')
    base['subscription']['url']=rss_v1.canonical_path(payload['applied_filters']['selection'],payload['applied_filters'])
    validate_public_v3(base)
    if payload['subscription']!={'available':True,'url':canonical_path(payload['applied_filters']),'reason':None}:
        raise ValueError('Invalid v4 subscription')
