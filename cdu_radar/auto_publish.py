"""Deterministic aggregation; human decisions never gate publishing, no model cache."""
from collections import Counter
from datetime import date, datetime, timedelta
from email.utils import format_datetime
import hashlib
import html
import json
import re
from xml.etree import ElementTree as ET

from .analysis_models import AnalysisInput, SHANGHAI, fingerprint, reference_datetime
from .models import official_url, RadarError
from .sources import load_sources
from .adapters import ARTICLE_PATH
from .rule_analysis import RuleAnalyzer, deadline_moment

SCHEMA = """
CREATE TABLE IF NOT EXISTS notice_health (
 notice_id INTEGER PRIMARY KEY REFERENCES notices(id),
 status TEXT NOT NULL CHECK(status IN ('valid','parse_failed','unavailable','withdrawn')),
 checked_at TEXT NOT NULL, validated_hash TEXT, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auto_publications (
 notice_id INTEGER PRIMARY KEY REFERENCES notices(id), guid TEXT NOT NULL UNIQUE,
 first_published_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_publication_invalidations (
 review_id INTEGER PRIMARY KEY);
"""
NS = 'urn:cdu-radar:auto:v1'
ET.register_namespace('radar', NS)
STATUS = {'possible':'可能仍可参与', 'closed':'明确已经截止', 'unknown':'报名状态未知'}
MAX_SOURCE_AGE = timedelta(hours=72)
HISTORICAL_AGE = timedelta(days=90)


def initialize(db):
    # An independent schema: no publication_reviews/state/events migration or writes.
    db.executescript('BEGIN;\n' + SCHEMA + '\nCOMMIT;')


def version_hash(db, ident):
    row = db.execute('SELECT * FROM notices WHERE id=?', (ident,)).fetchone()
    return fingerprint({k:row[k] for k in (
        'title','canonical_url','published_at','content','content_hash','content_source_id','content_domain')})


def parsed(db, ident, at, changed=False):
    if changed:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_reviews'").fetchone():
            db.execute("INSERT OR IGNORE INTO legacy_publication_invalidations SELECT id FROM publication_reviews WHERE notice_id=? AND review_status='approved'",(ident,))
    db.execute("""INSERT INTO notice_health VALUES (?,'valid',?,?,'parsed')
        ON CONFLICT(notice_id) DO UPDATE SET status=CASE WHEN notice_health.status='withdrawn'
        THEN 'withdrawn' ELSE 'valid' END, checked_at=excluded.checked_at,
        validated_hash=excluded.validated_hash,reason=CASE WHEN notice_health.status='withdrawn'
        THEN notice_health.reason ELSE 'parsed' END""", (ident, at, version_hash(db, ident)))
    row=db.execute('SELECT title,content,canonical_url FROM notices WHERE id=?',(ident,)).fetchone()
    withdrawal = any(re.search(r'(?:本通知|本公告|该通知)(?:现)?(?:已|予以|正式)?(?:撤回|撤销|作废)|(?:本活动|本次活动)(?:已|现已|正式)?取消',clause)
        and not re.search(r'如果|如若|尚未|并未|并非|不会|未被',clause)
        for clause in re.split(r'[。；\n]',row['title']+'\n'+row['content']))
    if withdrawal:failed(db,row['canonical_url'],'withdrawn','explicit_source_withdrawal',at)


def failed(db, url, status, reason, at):
    """Retain the old body for diagnosis; invalidate its eligibility immediately."""
    if status not in {'parse_failed','unavailable','withdrawn'}:
        raise ValueError('Invalid source health status')
    db.execute("""INSERT INTO notice_health (notice_id,status,checked_at,reason)
        SELECT id,?,?,? FROM notices WHERE canonical_url=?
        ON CONFLICT(notice_id) DO UPDATE SET status=CASE WHEN notice_health.status='withdrawn'
        THEN 'withdrawn' ELSE excluded.status END, checked_at=excluded.checked_at,
        reason=CASE WHEN notice_health.status='withdrawn' THEN notice_health.reason ELSE excluded.reason END""", (status,at,reason,url))


def excerpt(content):
    # A contiguous source excerpt, not a generated claim; the offset is auditable.
    start = next((m.start() for m in re.finditer(r'[^\s]', content)), 0)
    return content[start:start+240], start


def trusted_publisher(db, row, catalog):
    from urllib.parse import urlsplit
    try:
        url = official_url(row['canonical_url'])
    except (RadarError, ValueError, TypeError):return None
    if url != row['canonical_url'] or not ARTICLE_PATH.fullmatch(urlsplit(url).path):return None
    origins = {r[0] for r in db.execute('SELECT source_id FROM notice_sources WHERE notice_id=?', (row['id'],))}
    if not origins & {s.id for s in catalog}:return None
    host = urlsplit(url).hostname
    publishers = [s for s in catalog if s.official_domain==host]
    units = {s.unit_id or s.id for s in publishers}
    if len(units)!=1 or row['content_source_id'] not in units or row['content_domain']!=host:return None
    return publishers[0].college


def time_fields(result, content):
    """Only explicit, single-purpose source dates survive independent evidence checks."""
    from .screening_dates import date_reason, conflicting_dates
    values = {}
    for field in ('registration_deadline','internal_deadline','registration_start_date'):
        value = getattr(result, field)
        quotes = [e.quote for e in result.evidence if e.field==field and e.kind=='fact' and e.value==value]
        if value and quotes and all(date_reason(content,field,q,value)=='supported' for q in quotes) and not conflicting_dates(content,field,value):
            values[field] = {'value':value,'quote':quotes[0]}
        else:values[field] = None
    return values


def describe(row, publisher, now, analyzer):
    content = row['content']
    summary, start = excerpt(content)
    notice = AnalysisInput(row['id'],row['content_hash'],fingerprint(dict(row)),row['title'],content,
        row['canonical_url'],(publisher,),row['published_at'],content_source=publisher)
    warnings = ['自动分类仅供浏览，不代表官方认定；摘要为原文节选。', '附件正文未解析；完整要求以官方原文为准。']
    result = None
    try:
        result = analyzer.analyze(notice, reference_time=now)
        facts = time_fields(result, notice)
    except (ValueError, TypeError, AttributeError):
        facts = {key:None for key in ('registration_deadline','internal_deadline','registration_start_date')}
        warnings.append('规则辅助分析异常；仅展示基础信息，报名状态未知。')
    category = '其他通知'
    historical = False
    administrative = bool(result and result.category=='administrative') or bool(re.search(
        r'经费报销|财务报销|岗位聘任|人事公告|(?:奖学金|助学金|荣誉称号|补贴|推荐名单).*公示', row['title']))
    if result:
        historical = result.category=='news'
        if administrative:category='其他通知'
        elif result.category in {'competition','hackathon'} or analyzer.match('competition',row['title']):category='学科竞赛'
        elif result.category=='project_recruitment' or re.search(r'科研|研究实践|创新实践|课题|项目招募',row['title']):category='科研与创新实践'
        elif result.relevance_level=='direct_ai' or re.search(r'计算机|软件|编程|网络安全',row['title']):category='AI / 计算机相关活动'
        elif result.category in {'workshop','lecture','other_activity'}:category='校园活动'
        event_date=result.event_end_date or result.event_start_date
        if event_date and deadline_moment(event_date)<now:historical=True
    # A report about something already held cannot become a current opportunity.
    if re.search(r'已举办|已举行|圆满(?:结束|落幕)|成功举办|顺利举行|获奖|活动掠影', row['title']):historical=True
    source_date = row['published_at']
    if source_date and now.date()-date.fromisoformat(source_date)>HISTORICAL_AGE:historical=True
    status = 'unknown'
    deadlines = [f['value'] for key,f in facts.items() if key in {'registration_deadline','internal_deadline'} and f]
    explicit_closed = any(re.search(r'报名已结束|报名已经截止|征集已结束|停止报名', c) and
        not re.search(r'并非|不是|尚未|是否|若|如果', c) for c in re.split(r'[。；\n]', content))
    if explicit_closed or any(deadline_moment(v)<now for v in deadlines):status='closed'
    elif facts['registration_deadline'] and not historical and not administrative:
        start_fact = facts['registration_start_date']
        # Conflicting, tentative or yearless constraints never imply openness.
        uncertain = bool(result and any(e.kind=='unknown' and e.field in facts for e in result.evidence))
        beginning = datetime.fromisoformat(start_fact['value']) if start_fact else None
        if beginning and beginning.tzinfo is None:beginning=beginning.replace(tzinfo=SHANGHAI)
        if not uncertain and (not beginning or beginning<=now):status='possible'
    listing = 'history' if historical or status=='closed' else 'current' if status=='possible' else 'unknown'
    audience = None
    for clause in re.split(r'[。；\n]', content):
        if re.match(r'\s*(?:参赛对象|报名对象|参与对象|参赛资格)[：:]', clause):
            audience = clause.strip();break
    return {'notice_id':row['id'],'title':row['title'],'source_name':publisher,'source_url':row['canonical_url'],
        'source_published_date':source_date,'summary':summary,
        'summary_evidence':{'field':'content','start':start,'end':start+len(summary),'quote':summary,'content_hash':row['content_hash']},
        'category':category,'registration_status':status,'listing':listing,'facts':facts,
        'audience':audience,'warnings':warnings}


class AutoRepository:
    def __init__(self, db):
        self.db=db
        self.analyzer=RuleAnalyzer()

    def feed_plan(self, now=None):
        now=reference_datetime(now)
        # Reload for every live plan, once per batch rather than once per row.
        # Revoking a source still invalidates the very next HTTP read gate.
        catalog=[s for s in load_sources() if s.status=='verified' and s.parser_supported and s.adapter]
        rows=self.db.execute('SELECT * FROM notices ORDER BY canonical_url').fetchall()
        items=[];excluded=[]
        for row in rows:
            ident=row['id'];reason=None
            health=self.db.execute('SELECT * FROM notice_health WHERE notice_id=?',(ident,)).fetchone()
            publisher=trusted_publisher(self.db,row,catalog)
            if not publisher:reason='untrusted_or_missing_source'
            elif not row['title'].strip() or len(row['content'].strip())<20:reason='invalid_content'
            elif hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_hash']:reason='content_hash_mismatch'
            elif not health:reason='not_revalidated'
            elif health['status']!='valid':reason=health['status']
            else:
                checked=reference_datetime(health['checked_at'])
                if checked>now or now-checked>MAX_SOURCE_AGE:reason='source_check_expired'
                elif health['validated_hash']!=version_hash(self.db,ident):reason='unparsed_source_change'
            if not reason and row['published_at'] is not None:
                try:
                    if date.fromisoformat(row['published_at']).isoformat()!=row['published_at'] or date.fromisoformat(row['published_at'])>now.date():reason='invalid_source_date'
                except ValueError:reason='invalid_source_date'
            if reason:
                excluded.append({'notice_id':ident,'reason':reason});continue
            item=describe(row,publisher,now,self.analyzer)
            item['source_checked_at']=health['checked_at']
            state=self.db.execute('SELECT * FROM auto_publications WHERE notice_id=?',(ident,)).fetchone()
            item['guid']='urn:cdu-radar:notice:'+hashlib.sha256(row['canonical_url'].encode()).hexdigest()
            item['first_published_at']=state['first_published_at'] if state else None
            if state and state['guid']!=item['guid']:raise ValueError('Stored automatic identity mismatch')
            items.append(item)
        # Do not include wall clock in the fingerprint: only effective state changes invalidate it.
        return {'items':items,'excluded':excluded,'plan_hash':fingerprint([items,excluded]),
            'statuses':dict(Counter(i['registration_status'] for i in items)),
            'listings':dict(Counter(i['listing'] for i in items))}

    def reserve(self, now):
        plan=self.feed_plan(now)
        for item in plan['items']:
            self.db.execute('INSERT OR IGNORE INTO auto_publications VALUES (?,?,?)',
                (item['notice_id'],item['guid'],now.isoformat()))
        return plan


def render(items, scope='all'):
    root=ET.Element('rss',version='2.0');channel=ET.SubElement(root,'channel')
    def tag(parent,name,value,**attrs):
        from .rss import xml_text
        ET.SubElement(parent,name,attrs).text=xml_text(value)
    tag(channel,'title','成都大学 · 校园机会自动聚合')
    tag(channel,'link','https://www.cdu.edu.cn/')
    tag(channel,'description','可信官方来源自动聚合；报名未知与历史通知单独标记；pubDate 为首次聚合发布登记时间。')
    tag(channel,'language','zh-cn')
    for row in items:
        if scope!='all' and row['listing']!=scope:continue
        item=ET.SubElement(channel,'item')
        tag(item,'title',row['title']);tag(item,'link',row['source_url'])
        tag(item,'guid',row['guid'],isPermaLink='false')
        tag(item,'pubDate',format_datetime(datetime.fromisoformat(row['first_published_at'])))
        tag(item,'source',row['source_name'],url=row['source_url']);tag(item,'category',row['category'])
        for key in ('registration_status','listing','source_published_date'):
            tag(item,'{'+NS+'}'+key,row[key] or 'unknown')
        tag(item,'{'+NS+'}summary_evidence',json.dumps(row['summary_evidence'],ensure_ascii=False,sort_keys=True))
        lines=[row['summary'],'官方来源：'+row['source_name'],'来源发布日期：'+(row['source_published_date'] or '未知'),
            '信息类别：'+row['category'],'报名状态：'+STATUS[row['registration_status']],
            '列表：'+{'current':'当前机会','history':'历史信息','unknown':'报名状态未知'}[row['listing']],
            '参与对象（原文）：'+(row['audience'] or '未知')]
        for key,label in [('registration_deadline','报名截止'),('internal_deadline','校内报名/推荐截止'),('registration_start_date','报名开始')]:
            fact=row['facts'][key];lines.append(label+'：'+(fact['quote'] if fact else '未知'))
        lines += ['活动时间：未知', 'RSS pubDate 为首次聚合发布登记时间，不是原文发布日期。', *row['warnings']]
        text='<p>'+html.escape('\n'.join(lines)).replace('\n','<br/>')+'</p>'
        text+='<p><a href="'+html.escape(row['source_url'],quote=True)+'">官方原文</a></p>'
        tag(item,'description',text)
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)
