"""Retention metrics against explicitly imported labels, never predictions as truth."""
from collections import Counter

from .analysis_models import reference_datetime
from .analysis_service import analyze_one
from .rule_analysis import RuleAnalyzer


def evaluate(repository,*,analyzer=None,reference_time=None,include_synthetic=False,min_samples=30,
             notice_ids=None,prediction_reader=None):
    analyzer=analyzer or RuleAnalyzer();fixed_reference=reference_time is not None;now=reference_datetime(reference_time)
    matrix={"tp":0,"fp":0,"fn":0,"tn":0};errors=[];by_category=Counter();uncertain=0;valid=0;unlabeled=0;stale_date=0;synthetic=0;stale_time=0;untimed=0
    dimensions=Counter();comparisons=Counter();predictions=[];missing=0;failed=0;positive_truth=0;negative_truth=0
    ids=repository.ids() if notice_ids is None else list(notice_ids)
    if len(set(ids))!=len(ids) or not set(ids)<=set(repository.ids()):raise ValueError("Invalid evaluation notice ids")
    for notice_id in ids:
        notice=repository.input(notice_id);truth=repository.latest_label(notice)
        if truth is None and include_synthetic:truth=repository.latest_label(notice,"synthetic")
        if truth is None or truth.worth_recommending is None:unlabeled+=1;continue
        point=now if fixed_reference else reference_datetime(truth.reference_time or truth.reference_date) if truth.reference_time or truth.reference_date else now
        if fixed_reference and truth.reference_date and truth.reference_date!=point.date().isoformat():stale_date+=1;continue
        if fixed_reference and truth.reference_time and reference_datetime(truth.reference_time)!=point:stale_time+=1;continue
        if truth.reference_time is None:untimed+=1
        valid+=1;positive_truth+=truth.worth_recommending is True;negative_truth+=truth.worth_recommending is False
        synthetic+=truth.label_origin=="synthetic"
        prediction=prediction_reader(notice_id,point) if prediction_reader else analyze_one(repository,notice_id,analyzer,point)[0]
        if prediction is None:
            missing+=1;predictions.append({"notice_id":notice_id,"truth":truth.worth_recommending,"decision":None,"state":"not_analyzed"})
            continue
        failed+=prediction.analysis_state=="failed"
        predictions.append({"notice_id":notice_id,"truth":truth.worth_recommending,"decision":prediction.candidate_decision,"state":prediction.analysis_state})
        uncertain+=prediction.candidate_decision=="uncertain"
        # High-recall prefilter: uncertain remains a candidate, not a published recommendation.
        positive=prediction.analysis_state=="failed" or prediction.candidate_decision!="exclude"
        key="tp" if positive and truth.worth_recommending else "fp" if positive else "fn" if truth.worth_recommending else "tn"
        matrix[key]+=1;types=[]
        if key=="fn":types.append("missed_opportunity")
        if key=="fp":types.append("false_positive")
        for field,kind in [("category","category_error"),("relevance_level","ai_relevance_error"),("eligibility_status","eligibility_error"),
                           ("ai_use_policy","ai_policy_error"),("opportunity_status","time_status_error"),
                           ("registration_deadline","registration_time_error"),("internal_deadline","internal_time_error")]:
            actual=getattr(truth,field)
            if actual is None:continue
            comparisons[kind]+=1
            if actual!=getattr(prediction,field):types.append(kind);dimensions[kind]+=1
        if truth.deadline_explicit is not None:
            comparisons["deadline_presence_error"]+=1
            explicit=any(e.field=="registration_deadline" and e.kind=="fact" and e.value for e in prediction.evidence)
            if truth.deadline_explicit!=bool(explicit):types.append("deadline_presence_error");dimensions["deadline_presence_error"]+=1
        if truth.student_opportunity is not None:
            comparisons["student_activity_error"]+=1
            predicted_activity=prediction.student_opportunity if prediction.student_opportunity is not None else prediction.category not in {"news","administrative","unknown"} and prediction.eligibility_status!="ineligible"
            if truth.student_opportunity!=predicted_activity:types.append("student_activity_error");dimensions["student_activity_error"]+=1
        if types:
            category=truth.category or "unlabeled_category";by_category[category]+=1
            errors.append({"notice_id":notice_id,"canonical_url":notice.canonical_url,"error_types":types,
                "truth_worth_recommending":truth.worth_recommending,"candidate_decision":prediction.candidate_decision,
                "truth_category":truth.category,"predicted_category":prediction.category})
    tp,fp,fn,tn=(matrix[k] for k in ["tp","fp","fn","tn"])
    precision=tp/(tp+fp) if tp+fp else None;recall=tp/(tp+fn) if tp+fn else None
    f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
    return {"reference_time":now.isoformat() if fixed_reference else None,"reference_policy":"fixed_as_of" if fixed_reference else "per_label_time",
        "analyzer_version":analyzer.version,"metric_target":"candidate_retention (retain + uncertain), not approval",
        "valid_labels":valid,"human_labels":valid-synthetic,"synthetic_labels":synthetic,"unlabeled_or_incomplete":unlabeled,"reference_date_mismatches":stale_date,
        "reference_time_mismatches":stale_time,"labels_without_exact_time":untimed,
        "positive_labels":positive_truth,"negative_labels":negative_truth,
        "precision":precision if not missing else None,"recall":recall if not missing else None,"f1":f1 if not missing else None,
        "confusion_matrix":matrix if not missing else None,"partial_confusion_matrix":matrix if missing else None,
        "uncertain_count":uncertain,"uncertain_ratio":uncertain/valid if valid else None,
        "failed_count":failed,"failure_ratio":failed/valid if valid else None,"not_analyzed_count":missing,
        "metrics_complete":not missing,"predictions":predictions,
        "errors_by_category":dict(by_category),"dimension_errors":dict(dimensions),"dimension_compared":dict(comparisons),"error_samples":errors,
        "sample_sufficient":valid>=min_samples,"warning":"没有有效人工标签，无法评估真实效果" if valid==0 else "样本不足，仅供开发评测" if valid<min_samples else "本地样本评测不等于生产验收",
        "production_validated":False,"evaluation_origin":"synthetic_included" if synthetic else "human_only"}
