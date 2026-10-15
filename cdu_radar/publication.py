"""Local human publication decisions, independent of labels and predictions."""
import hashlib
import json
import re
import uuid

from .analysis_models import fingerprint, reference_datetime, InputLink, NoticeAnalysis, ManualLabel
from .evidence import locate, normalize
from .llm_semantics import supports_participation, supports_policy
from .screening_dates import date_reason, conflicting_dates, DATE_FIELDS, role
from .llm_schema import quoted_dates
from .rule_analysis import deadline_moment
from .models import utc_now
from .privacy import sanitize

SCHEMA = """
CREATE TABLE IF NOT EXISTS publication_reviews (
 id INTEGER PRIMARY KEY, notice_id INTEGER NOT NULL REFERENCES notices(id),
 notice_content_hash TEXT NOT NULL, notice_version_hash TEXT NOT NULL,
 review_status TEXT NOT NULL CHECK(review_status IN ('approved','rejected','needs_revision','revoked')),
 reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL, review_reason TEXT NOT NULL,
 approved_title TEXT, approved_summary TEXT, approved_source_url TEXT, approved_source_name TEXT,
 approved_payload TEXT, confirmation_token TEXT NOT NULL, supersedes_id INTEGER REFERENCES publication_reviews(id));
CREATE TABLE IF NOT EXISTS publication_state (
 notice_id INTEGER PRIMARY KEY REFERENCES notices(id), current_review_id INTEGER NOT NULL REFERENCES publication_reviews(id),
 source_url TEXT NOT NULL UNIQUE, guid TEXT NOT NULL UNIQUE,
 publication_status TEXT NOT NULL CHECK(publication_status IN ('ready','published','withheld','revoked')),
 published_at TEXT, first_exported_at TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS publication_events (
 id INTEGER PRIMARY KEY, notice_id INTEGER NOT NULL REFERENCES notices(id), review_id INTEGER REFERENCES publication_reviews(id),
 action TEXT NOT NULL, actor TEXT NOT NULL, recorded_at TEXT NOT NULL, reason TEXT NOT NULL, details TEXT NOT NULL);
"""


def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def migrate(db):
    version = max(5,db.execute('PRAGMA user_version').fetchone()[0])
    extra=''
    if table_exists(db,'publication_state') and 'first_exported_at' not in {r[1] for r in db.execute('PRAGMA table_info(publication_state)')}:
        extra='ALTER TABLE publication_state ADD COLUMN first_exported_at TEXT;'
    db.executescript('BEGIN;\n'+SCHEMA+extra+f'\nPRAGMA user_version={version}; COMMIT;')


def snapshot(db, ident):
    row = db.execute('SELECT * FROM notices WHERE id=?',(ident,)).fetchone()
    if row is None:raise ValueError('Notice does not exist')
    material = {k:row[k] for k in ['id','title','canonical_url','content_hash','published_at','content_source_id','content_domain']}
    material['body_sha256']=hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
    material['links'] = [tuple(r) for r in db.execute('SELECT kind,name,url FROM notice_links WHERE notice_id=? ORDER BY kind,url',(ident,))]
    return row,fingerprint(material)


def record_event(db, ident, review_id, action, actor, reason, details=None, at=None):
    db.execute('INSERT INTO publication_events (notice_id,review_id,action,actor,recorded_at,reason,details) VALUES (?,?,?,?,?,?,?)',
        (ident,review_id,action,actor,at or utc_now(),reason,json.dumps(details or {},ensure_ascii=False)))


def source_updated(db, ident, at):
    """Runs inside collection's transaction; never creates human approvals."""
    if not table_exists(db,'publication_state'):return
    row = db.execute('SELECT s.*,r.notice_version_hash,r.review_status FROM publication_state s JOIN publication_reviews r ON r.id=s.current_review_id WHERE s.notice_id=?',(ident,)).fetchone()
    if row and row['review_status']=='approved' and row['publication_status'] in {'ready','published'}:
        _,version = snapshot(db,ident)
        if version != row['notice_version_hash']:
            db.execute("UPDATE publication_state SET publication_status='withheld' WHERE notice_id=?",(ident,))
            record_event(db,ident,row['current_review_id'],'source_changed','system','Source version changed; human reconfirmation required',{'previous_version':row['notice_version_hash'],'current_version':version},at)


def text(value, name, limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:raise ValueError(f'Invalid {name}')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{20,}',value):raise ValueError(f'Unsafe {name}')
    return value.strip()


def full_statement(notice, quote):
    """Do not publish a substring that strips a negation or permission scope."""
    target=normalize(quote).rstrip('。；')
    return any(normalize(c).rstrip('。；')==target for c in [notice.title,*re.split(r'[。；\n]',notice.content)])


def registration_constraints(content):
    """Read expiry constraints without filling or certifying any draft fact.

    Keep dates on contact lines internally; no identities or source excerpts are
    returned. Publication metadata, event dates and tentative dates are excluded.
    """
    observed={f:set() for f in ['registration_deadline','internal_deadline']}
    closed=False
    for clause in re.split(r'[，,。；\n]',sanitize(content,remove_identity_lines=False)):
        clause=normalize(clause)
        if re.search(r'报名已结束|报名已经截止|征集已结束|停止报名',clause) and not re.search(r'并非|不是|尚未|是否|若|如果',clause):closed=True
        if re.search(r'暂定|预计|拟定|可能|发布日期|发布时间',clause):continue
        try:dates,pairs=quoted_dates(clause)
        except ValueError:continue
        field=role(clause)
        if field in observed:observed[field].update(dates)
        elif pairs and re.search(r'报名时间|报名期间',clause):observed['registration_deadline'].update(p[1] for p in pairs)
    return observed,closed


def source_expiry_reason(content, now):
    observed,closed=registration_constraints(content)
    if closed:return 'source_explicitly_closed'
    if any(values and all(deadline_moment(v)<now for v in values) for values in observed.values()):return 'source_registration_expired'
    return None


class PublicationRepository:
    def __init__(self, repository):
        self.repository=repository;self.db=repository.db

    def current(self, ident):
        if not table_exists(self.db,'publication_state'):return None
        row=self.db.execute('SELECT r.*,s.guid,s.publication_status,s.published_at,s.first_exported_at,s.revoked_at FROM publication_state s JOIN publication_reviews r ON r.id=s.current_review_id WHERE s.notice_id=?',(ident,)).fetchone()
        return dict(row) if row else None

    def effective(self, ident, now=None):
        now=reference_datetime(now);raw,version=snapshot(self.db,ident);r=self.current(ident)
        if r is None:return {'review_status':'pending','publication_status':'unpublished','valid':False,'notice_version_hash':version}
        valid=r['review_status']=='approved' and r['publication_status'] in {'ready','published'} and r['notice_version_hash']==version
        # M4-B collection preserves all old approval rows/events as read-only.
        # A separate tombstone prevents reverting text from reviving an old approval.
        if table_exists(self.db,'legacy_publication_invalidations') and self.db.execute(
                'SELECT 1 FROM legacy_publication_invalidations WHERE review_id=?',(r['id'],)).fetchone():valid=False
        invalid_reason=None
        if valid:
            if now > reference_datetime(json.loads(r['approved_payload'])['valid_until']):invalid_reason='approval_expired'
            else:invalid_reason=source_expiry_reason(raw['content'],now)
            valid=invalid_reason is None
        effective='needs_revision' if r['review_status']=='approved' and not valid else r['review_status']
        return dict(r,effective_review_status=effective,valid=valid,current_notice_version_hash=version,invalid_reason=invalid_reason)

    def advisory_records(self, table, ident, input_hash, content_hash, errors):
        records=[]
        if not table_exists(self.db,table):return records
        validator=NoticeAnalysis.from_dict if table=='notice_analyses' else lambda a:ManualLabel(**a)
        for row in self.db.execute(f'SELECT id,payload FROM {table} WHERE notice_id=? ORDER BY id DESC',(ident,)):
            try:
                a=json.loads(row['payload'])
                if not isinstance(a,dict):raise TypeError('Advisory record must be an object')
                validator(a)
            except json.JSONDecodeError:
                errors.append({'table':table,'record_id':row['id'],'error_category':'invalid_json'});continue
            except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
                errors.append({'table':table,'record_id':row['id'],'error_category':'invalid_record'});continue
            a['matches_current_input']=a.get('input_hash')==input_hash
            if table=='notice_analyses':a['matches_current_content']=a.get('content_hash')==content_hash
            records.append(a)
        return records

    def show(self, ident, now=None):
        row,version=snapshot(self.db,ident)
        input_hash=self.repository.input(ident).input_hash
        advisory_errors=[]
        analyses=self.advisory_records('notice_analyses',ident,input_hash,row['content_hash'],advisory_errors)
        labels=self.advisory_records('manual_labels',ident,input_hash,row['content_hash'],advisory_errors)
        old_reviews=[]
        if table_exists(self.db,'analysis_reviews'):
            old_reviews=[dict(r) for r in self.db.execute('SELECT * FROM analysis_reviews WHERE notice_id=? ORDER BY id DESC',(ident,))]
        facts_to_check=['当前是否仍有参与入口','报名截止、校内截止与举办日期的用途','院校/专业/地区/年级/选拔限制','AI 许可及用途范围','附件正文未解析','评测推荐和旧分析审核均不是发布批准']
        return {'notice':dict(row,title=sanitize(row['title'],False),content=sanitize(row['content'])),
            'notice_version_hash':version,'sources':[dict(r) for r in self.repository.store.origins(ident)],
            'analyses':analyses,'evaluation_labels':labels,'evaluation_labels_are_publication_approval':False,
            'legacy_analysis_reviews':old_reviews,'publication':self.effective(ident,now),'checks_before_approval':facts_to_check,
            'advisory_errors':advisory_errors}

    def queue(self, now=None, all_statuses=False):
        result=[]
        for n in self.db.execute('SELECT id,title,canonical_url,published_at FROM notices ORDER BY id'):
            p=self.effective(n['id'],now);status=p.get('effective_review_status',p['review_status'])
            if all_statuses or status in {'pending','needs_revision'}:
                result.append(dict(n,notice_id=n['id'],review_status=status,publication_status=p['publication_status'],valid=p['valid'],notice_version_hash=p.get('current_notice_version_hash',p.get('notice_version_hash'))))
        return result

    def template(self, ident):
        n,_=snapshot(self.db,ident)
        return {'title':sanitize(n['title'],False),'summary':'请填写人工确认后的活动简介；资格、日期和 AI 使用规则请填 facts，不在简介中断言。',
            'source_url':n['canonical_url'],'participation_quote':'请填写标题或正文中邀请报名/参与的直接引用',
            'valid_until':None,'facts':{**{f:None for f in sorted(DATE_FIELDS)},'eligibility':None,'ai_use_policy':None}}

    def validate_draft(self, ident, draft, now=None):
        n=self.repository.input(ident);raw,_=snapshot(self.db,ident);now=reference_datetime(now)
        keys={'title','summary','source_url','participation_quote','valid_until','facts'}
        if not isinstance(draft,dict) or set(draft)!=keys:raise ValueError('Draft must match publication template')
        title=text(draft['title'],'title',200);summary=text(draft['summary'],'summary',1200)
        # Key facts use controlled, evidenced fields; never silently bless prose claims.
        claims=r'20\d{2}[-年./]\d{1,2}|\d{1,2}月\d{1,2}日|报名截止|报名时间|参赛对象|报名对象|资格|(?:允许|禁止|可使用)[^。]{0,20}(?:AI|人工智能)|(?:AI|人工智能)[^。]{0,20}(?:允许|禁止)|(?:所有|全体|全校|任何)[^。]{0,15}(?:学生|本科)|(?:本科生|大学生|学生)[^。]{0,12}(?:均可|都可|可报名|可参加)'
        if re.search(claims,summary) or re.search(claims,title):raise ValueError('Put date/eligibility/AI permission claims in evidenced facts, not title/summary')
        if draft['source_url']!=n.canonical_url:raise ValueError('Approved source URL must equal original canonical URL')
        InputLink('',draft['source_url'])
        quote=text(draft['participation_quote'],'participation_quote',1000)
        if not supports_participation(True,quote,n):raise ValueError('Participation quote lacks a supported invitation')
        observed,closed=registration_constraints(raw['content'])
        if source_expiry_reason(raw['content'],now):raise ValueError('Original source registration route has expired or explicitly closed; need updated official information')
        until=draft['valid_until']
        if not isinstance(until,str) or 'T' not in until:raise ValueError('valid_until requires an explicit timestamp with timezone')
        point=reference_datetime(until)
        if point<=now:raise ValueError('Publication validity has expired')
        facts=draft['facts']
        if not isinstance(facts,dict) or set(facts)!=DATE_FIELDS|{'eligibility','ai_use_policy'}:raise ValueError('Invalid facts fields')
        trusted={};unknown=[]
        for field,entry in facts.items():
            if entry is None:trusted[field]=None;unknown.append(field);continue
            if not isinstance(entry,dict) or set(entry)!={'value','quote'}:raise ValueError('Facts require value and quote')
            q=text(entry['quote'],field+' quote',1000)
            if locate(n,q).source not in {'title','content'}:raise ValueError(field+': fact quote not found in original title/body')
            if field in DATE_FIELDS:
                if date_reason(n,field,q,entry['value'])!='supported' or conflicting_dates(n,field,entry['value']):raise ValueError(field+': date value/role is unsupported or conflicting')
            elif field=='eligibility':
                if entry['value']!=q or not full_statement(n,q):raise ValueError('Eligibility must preserve a complete quoted statement, not infer universal eligibility')
            elif not full_statement(n,q) or not supports_policy(entry['value'],q,n):raise ValueError('AI policy is not supported within its complete claimed scope')
            trusted[field]=dict(entry,quote=q)
        deadlines=[deadline_moment(trusted[f]['value']) for f in ['registration_deadline','internal_deadline'] if trusted[f]]
        # A unique source deadline constrains the maintenance window even when
        # the curator leaves that fact unknown. Multiple dates stay separate;
        # do not choose a min/max as a supposed confirmed deadline.
        deadlines.extend(deadline_moment(next(iter(values))) for values in observed.values() if len(values)==1)
        if deadlines:point=min([point,*deadlines])
        if point<=now:raise ValueError('Verified registration route has closed')
        if trusted['registration_start_date'] and trusted['registration_deadline'] and reference_datetime(trusted['registration_start_date']['value'])>deadline_moment(trusted['registration_deadline']['value']):raise ValueError('Registration starts after its deadline')
        # Unknown time/qualification is visibly preserved, never a positive eligibility claim.
        source=self.db.execute('SELECT name FROM units WHERE id=?',(raw['content_source_id'],)).fetchone()
        name=source[0] if source else '成都大学官方通知（具体发布单位待核验）'
        return {'title':title,'summary':summary,'source_url':n.canonical_url,'source_name':name,'participation_quote':quote,
            'valid_until':point.isoformat(),'facts':trusted,'unknown_fields':sorted(unknown)}

    def preview(self, ident, status, reviewer, reason, draft=None, ack_unknown=False, ack_correction=False, now=None):
        if status not in {'approved','rejected','needs_revision','revoked'}:raise ValueError('Invalid publication review status')
        reviewer=text(reviewer,'reviewer',120);reason=text(reason,'review_reason',1000)
        n,version=snapshot(self.db,ident);current=self.current(ident)
        approved=self.validate_draft(ident,draft,now) if status=='approved' else None
        if approved and approved['unknown_fields'] and not ack_unknown:raise ValueError('Unknown facts require --ack-unknown; they remain explicitly unknown in RSS')
        prior=self.db.execute("SELECT * FROM publication_reviews WHERE notice_id=? AND review_status='approved' ORDER BY id DESC LIMIT 1",(ident,)).fetchone() if table_exists(self.db,'publication_reviews') else None
        correction=bool(approved and current and current['published_at'] and prior and (prior['notice_version_hash']!=version or json.loads(prior['approved_payload'])!=approved))
        if correction and not ack_correction:raise ValueError('Published content correction requires --ack-correction; RSS cannot retract or resend QQ messages')
        material={'notice_id':ident,'notice_content_hash':n['content_hash'],'notice_version_hash':version,'review_status':status,
            'reviewer':reviewer,'review_reason':reason,'approved':approved,'previous_review_id':current['id'] if current else None,
            'ack_unknown':bool(ack_unknown),'ack_correction':bool(ack_correction)}
        shown=self.show(ident,now)
        return dict(material,confirmation_token=fingerprint(material),correction_required=correction,
            preview=True,checks_before_approval=shown['checks_before_approval'],advisory_errors=shown['advisory_errors'])

    def decide(self, ident, status, reviewer, reason, *, confirm, draft=None, ack_unknown=False, ack_correction=False, now=None):
        # Caller holds BEGIN IMMEDIATE; recheck source and review revision before writing.
        plan=self.preview(ident,status,reviewer,reason,draft,ack_unknown,ack_correction,now)
        current=self.current(ident)
        if current and current['confirmation_token']==confirm and current['notice_version_hash']==plan['notice_version_hash']:
            replay={k:plan[k] for k in ['notice_id','notice_content_hash','notice_version_hash','review_status','reviewer','review_reason','approved','previous_review_id','ack_unknown','ack_correction']}
            replay['previous_review_id']=current['supersedes_id']
            if fingerprint(replay)!=confirm:raise ValueError('Confirmation belongs to a different review operation')
            return {'review_id':current['id'],'idempotent':True,'publication':self.effective(ident,now)}
        if confirm!=plan['confirmation_token']:raise ValueError('Confirmation is stale or does not match the preview')
        at=reference_datetime(now).isoformat();a=plan['approved']
        cur=self.db.execute('INSERT INTO publication_reviews (notice_id,notice_content_hash,notice_version_hash,review_status,reviewer,reviewed_at,review_reason,approved_title,approved_summary,approved_source_url,approved_source_name,approved_payload,confirmation_token,supersedes_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,plan['notice_content_hash'],plan['notice_version_hash'],status,plan['reviewer'],at,plan['review_reason'],
             a['title'] if a else None,a['summary'] if a else None,a['source_url'] if a else None,a['source_name'] if a else None,
             json.dumps(a,ensure_ascii=False,sort_keys=True) if a else None,confirm,current['id'] if current else None))
        rid=cur.lastrowid;n,_=snapshot(self.db,ident)
        guid='urn:uuid:'+str(uuid.uuid5(uuid.NAMESPACE_URL,'cdu-opportunity-radar:approved:'+n['canonical_url']))
        self.db.execute('INSERT INTO publication_state (notice_id,current_review_id,source_url,guid,publication_status,revoked_at) VALUES (?,?,?,?,?,?) ON CONFLICT(notice_id) DO UPDATE SET current_review_id=excluded.current_review_id,publication_status=excluded.publication_status,revoked_at=excluded.revoked_at',
            (ident,rid,n['canonical_url'],guid,'ready' if a else 'revoked' if status=='revoked' else 'withheld',at if status=='revoked' else None))
        record_event(self.db,ident,rid,status,reviewer,reason,{'correction_required':plan['correction_required'],'prior_guid':current['guid'] if current else None},at)
        return {'review_id':rid,'idempotent':False,'publication':self.effective(ident,now),'correction_required':plan['correction_required']}

    def history(self, ident):
        snapshot(self.db,ident)
        return {'reviews':[dict(r) for r in self.db.execute('SELECT * FROM publication_reviews WHERE notice_id=? ORDER BY id',(ident,))] if table_exists(self.db,'publication_reviews') else [],
                'events':[dict(r) for r in self.db.execute('SELECT * FROM publication_events WHERE notice_id=? ORDER BY id',(ident,))] if table_exists(self.db,'publication_events') else [],
                'legacy_analysis_reviews':[dict(r) for r in self.db.execute('SELECT * FROM analysis_reviews WHERE notice_id=? ORDER BY id',(ident,))] if table_exists(self.db,'analysis_reviews') else []}

    def feed_plan(self, now=None):
        rows=[];excluded=[]
        for n in self.db.execute('SELECT id FROM notices ORDER BY id'):
            state=self.effective(n[0],now)
            if state['valid']:rows.append(state)
            elif state.get('review_status')=='approved':excluded.append({'notice_id':n[0],'reason':state.get('invalid_reason') or 'stale_or_expired_or_withheld'})
        rows.sort(key=lambda r:(r['published_at'] or r['reviewed_at'],r['guid']),reverse=True)
        material=[{k:r[k] for k in ['id','notice_id','notice_version_hash','approved_payload','guid','published_at','first_exported_at','publication_status']} for r in rows]
        return {'items':rows,'excluded':excluded,'new_publications':sum(r['first_exported_at'] is None for r in rows),
            'plan_hash':fingerprint(material),'preview':True}
