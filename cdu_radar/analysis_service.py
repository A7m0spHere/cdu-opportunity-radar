from collections import Counter
from datetime import datetime

from .analysis_models import Evidence,NoticeAnalysis,reference_datetime
from .models import utc_now
from .rule_analysis import DATE,FIELDS,RuleAnalyzer,refresh_time


def validate_evidence(notice,result):
    supplied="\n".join([notice.title,notice.content,notice.canonical_url,notice.published_at or "",notice.content_source or "",*notice.sources,
                          *(a.name for a in notice.body_links),*(a.name for a in notice.attachments)])
    if any(e.kind=="fact" and e.quote not in supplied for e in result.evidence):raise ValueError("Fact quote is not supplied input")
    for field in [*FIELDS,"event_end_date"]:
        value=getattr(result,field)
        if value is None:continue
        found=False
        role=FIELDS["event_start_date" if field=="event_end_date" else field]
        for e in result.evidence:
            if e.field!=field or e.kind!="fact" or e.value!=value or not role.search(e.quote):continue
            detected=next((name for name,pattern in FIELDS.items() if pattern.search(e.quote)),None)
            if detected!=("event_start_date" if field=="event_end_date" else field):continue
            for m in DATE.finditer(e.quote):
                y,month,day,h,minute=m.groups()
                try:
                    stamp=datetime(int(y),int(month),int(day),int(h or 0),int(minute or 0))
                    quoted=stamp.isoformat()+"+08:00" if h is not None else stamp.date().isoformat()
                except ValueError:continue
                if quoted==value:found=True
        if not found:raise ValueError("Date value lacks a matching role-labelled fact quote")
    if result.registration_url:
        if result.registration_url not in notice.content and result.registration_url not in {a.url for a in notice.body_links}:raise ValueError("Registration URL was not supplied")


def validate_output(notice,result,analyzer):
    if not isinstance(result,NoticeAnalysis):raise ValueError("Analyzer must return NoticeAnalysis")
    if (result.notice_id,result.content_hash,result.input_hash,result.analyzer_version)!=(notice.notice_id,notice.content_hash,notice.input_hash,analyzer.version):
        raise ValueError("Analyzer output identity mismatch")
    if result.model_name!=getattr(analyzer,"model_name",None):raise ValueError("Analyzer model identity mismatch")
    if result.review_status!="pending" or result.reviewed_at or result.review_reason:raise ValueError("Analyzer cannot approve or reject")
    validate_evidence(notice,result)


def analyze_one(repository,notice_id,analyzer=None,reference_time=None):
    analyzer=analyzer or RuleAnalyzer()
    now=reference_datetime(reference_time)
    notice=repository.input(notice_id)
    try:result=repository.cached(notice,analyzer.version)
    except (ValueError,TypeError):result=None
    model_name=getattr(analyzer,"model_name",None)
    cached=result is not None and result.analysis_state=="success" and result.model_name==model_name
    if cached:
        try:
            validate_output(notice,result,analyzer)
            if hasattr(analyzer,"validate_cached"):analyzer.validate_cached(notice,result)
        except (ValueError,TypeError):cached=False
    if not cached:
        try:
            result=analyzer.analyze(notice,reference_time=now)
            validate_output(notice,result,analyzer)
        except Exception as exc:
            # Do not retain exception text/full provider responses or credentials.
            category=getattr(exc,"category",None)
            allowed={"timeout","connection","authentication","forbidden","rate_limited","server_error","http_error",
                     "response_too_large","invalid_api_json","invalid_api_response","missing_choices","missing_message",
                     "model_refusal","unexpected_tool_call","incomplete_output","no_final_text","invalid_json",
                     "schema_validation","missing_api_key","empty_body","input_too_long"}
            code=category if category in allowed else type(exc).__name__
            reason=f"分析失败：{code}；保留原文并转待审核"
            result=NoticeAnalysis(notice.notice_id,notice.content_hash,notice.input_hash,analyzer.version,utc_now(),
                "unknown","uncertain","uncertain","unspecified","unknown",(reason,),
                (Evidence("analysis_state","unknown","",reason),),True,analysis_state="failed",model_name=getattr(analyzer,"model_name",None))
        repository.save(result)
    return repository.apply_review(refresh_time(result,now)),cached


def analyze_all(repository,*,analyzer=None,reference_time=None):
    analyzer=analyzer or RuleAnalyzer();now=reference_datetime(reference_time)
    results=[];cache_hits=0
    for notice_id in repository.ids():
        result,cached=analyze_one(repository,notice_id,analyzer,now)
        results.append(result);cache_hits+=cached
    return results,{"notices":len(results),"cache_hits":cache_hits,"analyzed":len(results)-cache_hits,
        "categories":dict(Counter(r.category for r in results)),"relevance":dict(Counter(r.relevance_level for r in results)),
        "decisions":dict(Counter(r.candidate_decision for r in results)),"statuses":dict(Counter(r.opportunity_status for r in results)),
        "needs_manual_review":sum(r.needs_manual_review for r in results),"failed":sum(r.analysis_state=="failed" for r in results),
        "reference_time":now.isoformat(),"analyzer_version":analyzer.version}
