"""Versioned objective importance policy; personal relevance stays in filters.

Selection is an optional subset of quality-qualified originals. It never grants
publication permission or changes the M7 proposal/human history.
"""
from contextlib import contextmanager
from importlib.resources import files
import json
from pathlib import Path
import re
import sqlite3

from .analysis_models import fingerprint
from .async_contract import validate
from .async_queue import JobQueue
from .evidence import contexts, locate
from .llm import external_text

CRITERIA = {
    'student_rights':('学生权益与奖助',4,r'奖学金|助学金|助学贷款|困难补助|学生权益|学费减免'),
    'academic_affairs':('学业与课程事务',4,r'课程调整|教学调整|停课|调课|选课|补考|毕业审核|学籍|成绩复核|考试报名'),
    'safety_service':('安全与校园服务',3,r'安全预警|紧急通知|停水|停电|校园交通调整|宿舍搬迁|卫生防疫'),
    'participation':('学生参与机会',2,r'报名|招募|征集|参赛对象|参与对象'),
    'research':('科研与学术进展',2,r'科研|学术|研究|课题|讲座'),
    'major_news':('有明确依据的重要进展',3,r'国家级.{0,15}(?:获批|突破|成果)|重大科研突破|重要科研成果|正式获批'),
}
DEFAULTS={'model_threshold':'normal','rule_threshold':3}
LEVELS={'low':1,'normal':2,'high':3}
ANALYSIS_STATES={'not_analyzed','queued','running','retry_wait','budget_blocked','succeeded','partial',
    'failed','skipped','stale','outdated','lease_expired','unavailable'}
FAULTS={'failed','skipped','stale','outdated','lease_expired','unavailable'}
REASON_LABELS={'awaiting_analysis':'等待有效智能分析','importance_unknown':'重要性依据不足',
    'below_model_threshold':'模型分档未达阈值','no_rule_support':'入选依据未达规则阈值',
    'analysis_unavailable':'智能分析暂不可用','model_and_rule':'模型建议与原文规则依据一致'}


class FeaturedPolicy:
    def __init__(self, *, model_threshold='normal',rule_threshold=3):
        if model_threshold not in LEVELS or type(rule_threshold) is not int or not 1<=rule_threshold<=4:
            raise ValueError('Invalid featured threshold')
        self.settings={'model_threshold':model_threshold,'rule_threshold':rule_threshold}
        implementation=files('cdu_radar').joinpath('featured.py').read_text(encoding='utf-8').replace('\r\n','\n')
        self.version='featured-v1:'+fingerprint([self.settings,CRITERIA,implementation])[:24]

    def decide(self, notice, analysis, state):
        if state not in ANALYSIS_STATES:state='unavailable'
        output={'decision':'pending','analysis_state':state,'reason_codes':['awaiting_analysis'],
                'evidence':[],'importance':'unknown','uncertainties':[], 'policy_version':self.version}
        if state in FAULTS:output['reason_codes']=['analysis_unavailable']
        if state not in {'succeeded','partial'} or not analysis:return output
        level=analysis['importance']['level']
        output['importance']=level
        if level not in LEVELS:
            output['reason_codes']=['importance_unknown'];return output
        if LEVELS[level]<LEVELS[self.settings['model_threshold']]:
            output.update(decision='not_selected',reason_codes=['below_model_threshold']);return output
        # Only the original quotes supporting importance may justify inclusion.
        # Personal unit/topic preference and repost counts add no weight.
        evidence=[]
        for quote in analysis['proposal']['importance']['quotes']:
            if locate(notice,quote).source not in {'title','content'}:continue
            surrounding=contexts(notice,quote)
            if any(re.search(r'并非|不是|不属于|是否|若|如果|[？?]',c) for c in surrounding):continue
            for code,(label,weight,pattern) in CRITERIA.items():
                if re.search(pattern,quote) and not any(e['criterion']==code for e in evidence):
                    evidence.append({'criterion':code,'label':label,'quote':external_text(quote)[:240]})
        qualified=[e for e in evidence if CRITERIA[e['criterion']][1]>=self.settings['rule_threshold']]
        # A model-high student opportunity can qualify at weight 2, provided
        # the public card still states that date/eligibility may be unknown.
        if not qualified and level=='high':
            qualified=[e for e in evidence if e['criterion']=='participation' and re.search(r'学生|本科生|研究生|在校生',e['quote'])
                       and not re.search(r'仅限教师|仅限教职工|仅面向教师',e['quote'])]
        if not qualified:
            output.update(decision='not_selected',reason_codes=['no_rule_support']);return output
        facts=analysis['facts']
        unknown=[]
        if facts['eligibility_status']=='uncertain':unknown.append('eligibility_unknown')
        if facts['opportunity_status']=='unknown':unknown.append('registration_unknown')
        if analysis.get('attachments_content_available') is False and any(i.startswith('attachments:') for i in analysis['issues']):
            unknown.append('attachments_unread')
        output.update(decision='selected',reason_codes=['model_and_rule'],evidence=qualified,uncertainties=unknown)
        return output


def analysis_for(queue, repo, analyzer, notice_id, now):
    notice=repo.input(notice_id)
    row=queue.db.execute('SELECT * FROM jobs WHERE notice_id=? AND input_hash=? AND pipeline_version=?',
                         (notice_id,notice.input_hash,analyzer.version)).fetchone()
    if not row:
        old=queue.db.execute('SELECT input_hash FROM jobs WHERE notice_id=? ORDER BY id DESC LIMIT 1',(notice_id,)).fetchone()
        return notice,None,('outdated' if old and old[0]==notice.input_hash else 'stale' if old else 'not_analyzed')
    state=row['state']
    if row['mode']!=('offline_mock' if analyzer.mock else 'provider'):return notice,None,'outdated'
    if state=='running' and row['lease_until']<=queue.clock():return notice,None,'lease_expired'
    if state not in {'succeeded','partial'}:return notice,None,state
    try:
        cached=json.loads(row['result'])
        supplied,_=analyzer.prepare(notice)
        checked=validate(cached['proposal'],notice,supplied,analyzer,now)
        return notice,checked,checked['state']
    except (ValueError,TypeError,KeyError):return notice,None,'failed'


@contextmanager
def analysis_reader(state_db, raw_db):
    """Hold queue read snapshot while making one coherent optional projection."""
    with JobQueue(state_db,raw_db,read_only=True) as queue:
        queue.db.execute('BEGIN')
        try:yield queue
        finally:queue.db.rollback()


def decisions(repo, items, *, state_db=None, raw_db=None, analyzer=None, policy=None, now=None):
    policy=policy or FeaturedPolicy();result={}
    if state_db is None or analyzer is None:
        return {i['notice_id']:policy.decide(None,None,'not_analyzed') for i in items}
    try:
        with analysis_reader(state_db,raw_db) as queue:
            for item in items:
                notice,analysis,state=analysis_for(queue,repo,analyzer,item['notice_id'],now)
                result[item['notice_id']]=policy.decide(notice,analysis,state)
        return result
    except (OSError,ValueError,sqlite3.Error,TypeError,KeyError):
        # Never reuse last good model output or block the all-information view.
        return {i['notice_id']:policy.decide(None,None,'unavailable') for i in items}


def scope_stats(entries):
    counts={key:sum(e['featured']['decision']==key for e in entries) for key in ['selected','not_selected','pending']}
    failed=sum(e['featured']['analysis_state'] in FAULTS for e in entries)
    complete=counts['selected']+counts['not_selected']
    state='degraded' if failed else 'pending' if counts['pending'] else 'ready'
    return {'state':state,'coverage':{'total':len(entries),'complete':complete,
        **counts,'failed':failed,'not_analyzed':sum(e['featured']['analysis_state']=='not_analyzed' for e in entries),
        'in_progress':sum(e['featured']['analysis_state'] in {'queued','running','retry_wait','budget_blocked'} for e in entries),
        'uncertain':sum(e['featured']['decision']=='pending' and e['featured']['analysis_state'] in {'succeeded','partial'} for e in entries)}}


def overview(entries, policy):
    return {**scope_stats(entries),'policy_version':policy.version,
        'ranges':{scope:scope_stats([e for e in entries if e['listing']==scope]) for scope in ['current','history']},
        'personalization':'filters_only','evaluation_status':'human_benchmark_pending'}
