"""Conservative, explainable candidate prefilter. No model or external tools."""
from dataclasses import replace
from datetime import datetime,time
from importlib.resources import files
import json
from pathlib import Path
import re

from .analysis_models import AnalysisInput,Evidence,NoticeAnalysis,SHANGHAI,fingerprint,reference_datetime
from .models import utc_now

DATE=re.compile(r"(?<!\d)(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?(?:\s*(\d{1,2})[：:](\d{2}))?")
FIELDS={
 "internal_deadline":re.compile(r"(?:校内|学校|学院).*(?:报名|推荐|提交).*(?:截止|截至)|校内截止"),
 "registration_deadline":re.compile(r"报名截止|报名截至|报名最后|最终截止|主办方截止|征集截止|提交截止"),
 "registration_start_date":re.compile(r"报名开始|开始报名时间|报名启动时间"),
 "internal_event_date":re.compile(r"校内选拔(?:赛)?(?:时间|日期)"),
 "event_start_date":re.compile(r"比赛时间|举办时间|活动时间|讲座时间|开赛时间"),
}
IMPLEMENTATION_HASH=fingerprint(files("cdu_radar").joinpath("rule_analysis.py").read_text(encoding="utf-8").replace("\r\n","\n"))


def load_rules(path=None):
    raw=Path(path).read_text(encoding="utf-8") if path else files("cdu_radar").joinpath("rules.json").read_text(encoding="utf-8")
    data=json.loads(raw)
    required={"competition","participation","hackathon","workshop","lecture","project","results","administrative","direct_ai",
              "ai_applicable","allowed_ai","prohibited_ai","partial_ai","teacher_only","graduate_only","undergraduate","restricted","closed","injection"}
    if not isinstance(data,dict) or data.get("schema_version")!=1 or set(data)-{"schema_version"}!=required:raise ValueError("Invalid rules schema")
    for key in required:
        if not isinstance(data[key],list) or any(not isinstance(p,str) or not p for p in data[key]):raise ValueError("Rule patterns must be nonempty strings")
        for pattern in data[key]:
            try:re.compile(pattern,re.I)
            except re.error as exc:raise ValueError(f"Invalid regex in rule group {key}") from exc
    return data


def deadline_moment(value):
    parsed=datetime.fromisoformat(value)
    if len(value)==10:return datetime.combine(parsed.date(),time.max,SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def refresh_time(result,reference_time):
    now=reference_datetime(reference_time)
    start=None
    if result.registration_start_date:
        start=datetime.fromisoformat(result.registration_start_date)
        if start.tzinfo is None:start=start.replace(tzinfo=SHANGHAI)
    status="unknown"
    if result.status_basis=="explicit_closed":status="closed"
    elif result.registration_deadline:
        status="closed" if deadline_moment(result.registration_deadline)<now else "open"
        # A passed internal route with a later external deadline is not universally open.
        if status=="open" and result.internal_deadline and deadline_moment(result.internal_deadline)<now:status="unknown"
        elif status=="open" and start and start>now:status="upcoming"
    elif start and start>now:status="upcoming"
    decision="exclude" if status=="closed" else result.base_decision
    return replace(result,opportunity_status=status,candidate_decision=decision,reference_time=now.isoformat(),
                   needs_manual_review=result.needs_manual_review or (status=="unknown" and result.base_decision!="exclude"))


class RuleAnalyzer:
    def __init__(self,rules_path=None):
        self.rules=load_rules(rules_path)
        self.version="rules-v3:"+fingerprint([IMPLEMENTATION_HASH,self.rules])[:12]
        self.patterns={k:[re.compile(p,re.I) for p in values] for k,values in self.rules.items() if k!="schema_version"}

    def match(self,key,text):
        return next((m.group() for p in self.patterns[key] if (m:=p.search(text))),None)

    def analyze(self,notice:AnalysisInput,*,reference_time:datetime):
        evidence=[];reasons=[]
        def note(field,kind,quote,reason,value=None):
            evidence.append(Evidence(field,kind,quote[:240],reason,value));reasons.append(reason)
        title=notice.title
        if self.match("injection",title):
            note("input_integrity","unknown",title,"标题包含疑似模型指令，移出规则判断")
            title=""
        lines=[]
        for line in notice.content.splitlines():
            if self.match("injection",line):note("input_integrity","unknown",line,"发现疑似模型指令，作为不可信文本移出规则判断")
            else:lines.append(line)
        content="\n".join(lines)
        text=title+"\n"+content
        result_hit=self.match("results",title)
        participation_title=self.match("participation",title)
        category="unknown"
        if result_hit and not participation_title:
            category="news";note("category","inference",result_hit,"标题表明结果报道或历史总结，不按报名机会处理")
        elif hit:=self.match("administrative",title):
            category="administrative";note("category","inference",hit,"标题为明确行政、资助公示或常规教学事务")
        else:
            for key,kind in [("hackathon","hackathon"),("competition","competition"),("workshop","workshop"),("lecture","lecture"),("project","project_recruitment")]:
                if hit:=self.match(key,title):category=kind;note("category","inference",hit,"按标题确定候选信息类型");break
            if category=="unknown" and (hit:=self.match("participation",text)):
                category="other_activity";note("category","inference",hit,"存在参与信号，保留待核验，不据此断言是比赛")
        relevance="uncertain"
        allowed=self.match("allowed_ai",text);prohibited=self.match("prohibited_ai",text);partial=self.match("partial_ai",text)
        topic_text="\n".join(clause for clause in re.split(r"[\n。；;，,]",text)
                             if not any(self.match(key,clause) for key in ["allowed_ai","prohibited_ai","partial_ai"]))
        if hit:=self.match("direct_ai",topic_text):
            relevance="direct_ai";note("relevance_level","inference",hit,"涉及 AI 学习/应用技术，但关键词本身不代表可报名")
        elif hit:=self.match("ai_applicable",text):
            relevance="ai_applicable";note("relevance_level","inference",hit,"跨学科创作或实践可能使用 AI，尚不代表主办方允许")
        elif allowed:
            relevance="ai_applicable";note("relevance_level","inference",allowed,"AI 仅作为允许使用的参与工具，不据此认定活动主题直接研究 AI")
        elif prohibited:
            relevance="unrelated";note("relevance_level","inference",prohibited,"只在禁用政策中出现 AI，未识别到 AI 学习实践主题")
        elif category in {"administrative","news"}:
            relevance="unrelated";note("relevance_level","inference",title,"未发现本轮 AI 学习实践的参与价值")
        else:note("relevance_level","unknown","","未能确定 AI 相关性，避免按无关键词直接排除")
        policy="unspecified"
        if allowed and prohibited:
            policy="partially_allowed" if partial else "unspecified"
            note("ai_use_policy","unknown",allowed+" / "+prohibited,"AI 使用说明存在限制或冲突，需要核对完整规则")
        elif partial:policy="partially_allowed";note("ai_use_policy","fact",partial,"原文明确限制 AI 使用范围")
        elif prohibited:policy="explicitly_prohibited";note("ai_use_policy","fact",prohibited,"原文明确禁止 AI 使用")
        elif allowed:policy="explicitly_allowed";note("ai_use_policy","fact",allowed,"原文明确允许 AI 使用")
        else:note("ai_use_policy","unknown","","未明确 AI 使用政策，不能把可能适用当成允许")
        eligibility="uncertain"
        target_lines=[line for line in content.splitlines() if re.search(r"参赛对象|报名对象|参赛资格|参与对象|面向|仅限|本科生",line)]
        target="；".join(target_lines[:3])[:500] or None
        if hit:=self.match("teacher_only",text):eligibility="ineligible";note("eligibility_status","fact",hit,"明确面向教师，不适合普通本科生")
        elif hit:=self.match("graduate_only",text):eligibility="ineligible";note("eligibility_status","fact",hit,"明确研究生专属资格")
        elif re.search(r"仅限(?!成都大学)[\u4e00-\u9fff]{2,12}大学.*(?:本科生|学生)",text):
            eligibility="ineligible";note("eligibility_status","inference",target or title,"明确其他院校专属，成都大学本科生资格不满足")
        elif hit:=self.match("restricted",text):note("eligibility_status","fact",hit,"存在学院、专业、年级或推荐限制，不能视为全校本科生均可参加")
        elif hit:=self.match("undergraduate",text):eligibility="eligible";note("eligibility_status","fact",hit,"原文明确覆盖本科生群体，仍须核验其他细则")
        else:note("eligibility_status","unknown",target or "","没有明确本科生资格，来源学院不能替代资格依据")
        dates={key:[] for key in FIELDS};dates["event_end_date"]=[]
        for clause in re.split(r"[\n。；;，,]",content):
            field=next((name for name,pattern in FIELDS.items() if pattern.search(clause)),None)
            if field is None:continue
            if re.search(r"暂定|预计|拟定|可能",clause):
                note(field,"unknown",clause,"时间为暂定或预计，不当作确定日期");continue
            matches=list(DATE.finditer(clause))
            if not matches:
                note(field,"unknown",clause,"日期没有明确年份或格式不支持，保留未知");continue
            values=[]
            range_pair=field=="event_start_date" and len(matches)==2 and re.search(r"至|到|—|~",clause)
            for index,match in enumerate(matches):
                y,m,d,h,minute=match.groups()
                try:
                    stamp=datetime(int(y),int(m),int(d),int(h or 0),int(minute or 0),tzinfo=SHANGHAI)
                    value=stamp.isoformat() if h is not None else stamp.date().isoformat()
                except ValueError:note(field,"unknown",match.group(),"日期无效，未推断修正");continue
                evidence_field="event_end_date" if range_pair and index==1 else field
                values.append(value);note(evidence_field,"fact",clause[max(0,match.start()-80):match.end()+60],"保留带明确年份和用途标签的日期",value)
            if range_pair and len(values)==2:
                dates[field].append(values[0]);dates["event_end_date"].append(values[1])
            else:dates[field].extend(values)
        extracted={}
        for field,values in dates.items():
            unique=list(dict.fromkeys(values));extracted[field]=unique[0] if len(unique)==1 else None
            if len(unique)>1:note(field,"unknown","","多个日期/赛道，不压缩为一个确定时间")
        if extracted["registration_deadline"] is None and not any(e.field=="registration_deadline" for e in evidence):
            note("registration_deadline","unknown","","未找到明确最终报名截止；发布日期和比赛时间不作替代")
        organizer_match=re.search(r"(?:主办单位|主办方)[：:]\s*([^\n。；]{2,80})",content)
        organizer=organizer_match.group(1).strip() if organizer_match else None
        if organizer:note("organizer","fact",organizer,"只采用明确标注的主办方，不把发布学院当主办方")
        registration_urls=[]
        for link in notice.body_links:
            if re.search(r"报名|注册|registration",link.name,re.I):registration_urls.append(link.url);note("registration_url","fact",link.name,"使用原文明确命名的报名链接",link.url)
        for match in re.finditer(r"(?:报名链接|报名网址|注册入口|线上预报名|在线报名)[：:]\s*[^\n。]{0,80}?\s*(https?://[^\s<>，。；]+)",content):
            from .analysis_models import InputLink
            try:InputLink("",match.group(1))
            except ValueError:continue
            registration_urls.append(match.group(1));note("registration_url","fact",match.group(),"使用明确报名标签后的原文 URL",match.group(1))
        unique_urls=list(dict.fromkeys(registration_urls))
        registration_url=unique_urls[0] if len(unique_urls)==1 else None
        if len(unique_urls)>1:note("registration_url","unknown","","多个报名入口，未指定一个通用 URL")
        if notice.attachments:note("attachments","unknown","","附件未解析；其中可能存在资格、赛道或日期信息")
        if not content.strip():note("content","unknown","","缺失可分析正文，只保留标题候选，不推断无限制参与")
        explicit_closed=self.match("closed",content)
        basis="explicit_closed" if explicit_closed else "deadline" if extracted["registration_deadline"] else "registration_start" if extracted["registration_start_date"] else "unknown"
        if explicit_closed:note("opportunity_status","fact",explicit_closed,"明确报名或征集已结束")
        base="uncertain"
        if category in {"news","administrative"} or eligibility=="ineligible":base="exclude"
        elif policy=="explicitly_prohibited" and relevance!="direct_ai":base="exclude"
        elif category!="unknown" and relevance in {"direct_ai","ai_applicable"} and eligibility=="eligible" and policy!="explicitly_prohibited" and content.strip():base="retain"
        needs_review=(base!="exclude" and any(e.kind=="unknown" for e in evidence)) or not content.strip() or any(e.field=="input_integrity" for e in evidence)
        result=NoticeAnalysis(notice.notice_id,notice.content_hash,notice.input_hash,self.version,utc_now(),category,relevance,eligibility,policy,"unknown",
            tuple(reasons),tuple(evidence),needs_review,base,base,event_name=title if category not in {"news","administrative","unknown"} else None,
            organizer=organizer,target_audience=target,registration_url=registration_url,
            requirements_summary=target,reference_time=reference_datetime(reference_time).isoformat(),status_basis=basis,**extracted)
        return refresh_time(result,reference_time)
