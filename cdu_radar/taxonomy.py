"""M6 rule-derived facets; no storage, model calls or publication decisions."""
from datetime import date
import re
from urllib.parse import parse_qsl

from .models import InvalidFilter

POLICY='m6-rules-v1'
TYPES={'notice':'通知公告','news':'新闻动态','event':'活动与学术交流',
       'competition':'竞赛','research':'科研与创新实践','unknown':'未分类'}
TOPICS={'ai':'人工智能','computing':'计算机','pharmacy':'药学','design':'艺术与设计',
        'innovation':'创新创业','sports':'体育','humanities':'人文社科'}
TOPIC_PATTERNS={
    'ai':r'人工智能|机器学习|大模型|(?<![A-Za-z])AI(?![A-Za-z])',
    'computing':r'计算机|编程|软件|网络安全|(?<![A-Za-z])(?:computer|software)(?![A-Za-z])',
    'pharmacy':r'药学|药物|制药|药品',
    'design':r'艺术|美术|设计|数字媒体',
    'innovation':r'创新创业|创业|创新实践',
    'sports':r'体育|运动会|篮球|足球|排球',
    'humanities':r'文学|哲学|法学|历史学|社会科学|人文|新闻传播',
}
STATUSES={'possible':'可能仍可参与','closed':'明确已截止','unknown':'报名未知'}
FILTER_KEYS=('source','type','topic','status','view','category','published','from','to')
API_V2='/api/notices/v2'
CLASS_FIELDS={'version','policy','method','type','type_state','topics','topic_state','model_state'}
V2_ITEM_FIELDS={'guid','title','source_name','source_url','source_published_date','summary','category',
    'registration_status','listing','source_checked_at','first_aggregated_at','publisher_id',
    'discovery_source_ids','classification'}
V2_TOP_FIELDS={'schema_version','items','categories','counts','refreshed_at','data_mode','valid_for_ms',
    'poll_after_ms','taxonomy_version','filter_options','applied_filters'}


def classify(title, content, rule_category=None):
    text=title+'\n'+content
    if re.search(r'公示|评审|认定|经费|报销|选课|停水|停电|教学调整',title):kind='notice'
    elif rule_category=='news' or re.search(r'成功举办|顺利举行|圆满落幕|活动掠影|成功召开|获奖|新闻|动态',title):kind='news'
    elif rule_category in {'competition','hackathon'}:kind='competition'
    elif rule_category=='project_recruitment':kind='research'
    elif rule_category in {'lecture','workshop','other_activity'}:kind='event'
    elif rule_category=='administrative':kind='notice'
    elif re.search(r'竞赛|比赛|挑战赛',title):kind='competition'
    elif re.search(r'科研.*(?:申报|招募)|研究实践|研究助理|课题招募',title):kind='research'
    elif re.search(r'讲座|学术报告|研讨会|志愿活动',title):kind='event'
    elif re.search(r'通知|公告',title):kind='notice'
    else:kind='unknown'
    topics=[key for key,pattern in TOPIC_PATTERNS.items() if re.search(pattern,text,re.I)]
    return {'version':1,'policy':POLICY,'method':'rules','type':kind,
        'type_state':'unclassified' if kind=='unknown' else 'classified','topics':topics,
        'topic_state':'tagged' if topics else 'unclassified','model_state':'not_analyzed'}


def options(catalog):
    publishers={}
    for source in catalog:
        if source.column_url and source.status=='verified' and source.parser_supported and source.adapter:
            publishers[source.unit_id or source.id]=source.college
    pairs=lambda values:[{'id':key,'label':label} for key,label in values.items()]
    return {'sources':pairs(dict(sorted(publishers.items()))),'types':pairs(TYPES),
            'topics':pairs(TOPICS),'statuses':pairs(STATUSES),
            'date_presence':[{'id':'known','label':'有来源日期'},{'id':'unknown','label':'来源日期未知'}]}


def query_values(path):
    base,mark,query=path.partition('?')
    if base!=API_V2 or len(path)>2048 or len(query)>1024 or re.search(r'%(?![0-9a-fA-F]{2})',query):
        raise InvalidFilter('Invalid filter request')
    try:
        pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True,encoding='utf-8',errors='strict',max_num_fields=len(FILTER_KEYS)) if mark and query else []
    except (ValueError,UnicodeError) as exc:
        raise InvalidFilter('Invalid filter encoding') from exc
    if len({k for k,v in pairs})!=len(pairs) or any(k not in FILTER_KEYS or not v or len(v)>100 for k,v in pairs):
        raise InvalidFilter('Unknown, repeated or empty filter')
    return dict(pairs)


def parse_filters(path, filter_options, categories):
    supplied=query_values(path)
    applied={key:('' if key in {'from','to'} else 'all') for key in FILTER_KEYS}
    applied.update(supplied)
    enums={'source':{'all',*(p['id'] for p in filter_options['sources'])},
           'type':{'all',*TYPES},'status':{'all',*STATUSES},'view':{'all','current','history'},
           'category':{'all',*categories},'published':{'all','known','unknown'}}
    if any(applied[key] not in values for key,values in enums.items()):
        raise InvalidFilter('Unsupported filter value')
    topics=applied['topic'].split(',')
    if len(topics)!=len(set(topics)) or not set(topics)<={'all','unclassified',*TOPICS} or (len(topics)>1 and {'all','unclassified'} & set(topics)):
        raise InvalidFilter('Unsupported topic combination')
    applied['topic']=','.join(sorted(topics))
    for key in ('from','to'):
        value=applied[key]
        if value:
            try:
                if not re.fullmatch(r'20\d{2}-\d{2}-\d{2}',value) or date.fromisoformat(value).isoformat()!=value:
                    raise ValueError()
            except ValueError as exc:raise InvalidFilter('Invalid source date range') from exc
    if applied['from'] and applied['to'] and applied['from']>applied['to']:
        raise InvalidFilter('Inverted source date range')
    if applied['published']=='unknown' and (applied['from'] or applied['to']):
        raise InvalidFilter('Unknown dates cannot have a date range')
    return applied


def filter_payload(payload,path):
    f=parse_filters(path,payload['filter_options'],payload['categories'])
    def match(item):
        classification=item['classification'];day=item['source_published_date']
        return (f['source'] in {'all',item['publisher_id']}
            and f['type'] in {'all',classification['type']}
            and (f['topic']=='all' or f['topic']=='unclassified' and not classification['topics']
                 or set(f['topic'].split(','))<=set(classification['topics']))
            and f['status'] in {'all',item['registration_status']}
            and f['view'] in {'all',item['listing']}
            and f['category'] in {'all',item['category']}
            and (f['published']=='all' or (day is not None)==(f['published']=='known'))
            and (not f['from'] or day is not None and day>=f['from'])
            and (not f['to'] or day is not None and day<=f['to']))
    items=[item for item in payload['items'] if match(item)]
    return dict(payload,items=items,applied_filters=f,counts={'all':len(items),
        **{scope:sum(i['listing']==scope for i in items) for scope in ('current','history','unknown')}})


def validate_public_v2(payload):
    """LAN projection guard: reject unknown fields instead of forwarding internals."""
    if not isinstance(payload,dict) or set(payload)!=V2_TOP_FIELDS or payload.get('schema_version')!=2 or payload.get('taxonomy_version')!=1:
        raise ValueError('Unexpected v2 fields')
    if not isinstance(payload['items'],list) or not isinstance(payload['filter_options'],dict):
        raise ValueError('Invalid v2 collection')
    if set(payload['filter_options'])!={'sources','types','topics','statuses','date_presence'} or not isinstance(payload['applied_filters'],dict) or set(payload['applied_filters'])!=set(FILTER_KEYS) or not all(isinstance(v,str) for v in payload['applied_filters'].values()):
        raise ValueError('Unexpected filter fields')
    for values in payload['filter_options'].values():
        if not isinstance(values,list) or any(not isinstance(v,dict) or set(v)!={'id','label'} or not all(isinstance(x,str) for x in v.values()) for v in values):
            raise ValueError('Invalid filter options')
    for item in payload['items']:
        if not isinstance(item,dict) or set(item)!=V2_ITEM_FIELDS:
            raise ValueError('Unexpected v2 item fields')
        c=item['classification']
        if not isinstance(c,dict) or set(c)!=CLASS_FIELDS or c['type'] not in TYPES or c['version']!=1 or c['policy']!=POLICY or c['method']!='rules' or c['model_state']!='not_analyzed':
            raise ValueError('Invalid classification')
        if not isinstance(c['topics'],list) or any(t not in TOPICS for t in c['topics']) or len(c['topics'])!=len(set(c['topics'])):
            raise ValueError('Invalid topic tags')
        if c['type_state'] not in {'classified','unclassified'} or c['topic_state'] not in {'tagged','unclassified'}:
            raise ValueError('Invalid classification states')
        if not isinstance(item['publisher_id'],str) or not isinstance(item['discovery_source_ids'],list) or not all(isinstance(v,str) for v in item['discovery_source_ids']):
            raise ValueError('Invalid source identities')
