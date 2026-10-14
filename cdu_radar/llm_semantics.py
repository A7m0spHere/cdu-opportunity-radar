"""Conservative fact guards; location alone never proves a conclusion."""
import re
from .rule_analysis import RuleAnalyzer
from .evidence import locate, contexts


def factual_source(notice, quote):
    return locate(notice, quote).source in {"title", "content"}


def supports_eligibility(value, quote, notice):
    if not factual_source(notice, quote):return False
    rules = RuleAnalyzer()
    text = notice.title + "\n" + notice.content
    clauses = contexts(notice, quote)
    if value == "eligible":
        # A caption saying本科生 does not remove college/cohort/recommendation limits.
        if any(rules.match(key, text) for key in ["teacher_only", "graduate_only", "restricted"]):return False
        if re.search(r"(?:仅接受|仅面向|只限|限本院|限本专业)|(?:报名对象|参赛对象|面向|仅限)[^。；\n]{0,50}(?:专业|年级|大[一二三四]|20\d{2}级)|(?:我校|成都大学)[^。；\n]{0,20}(?:20\d{2}级|大[一二三四])[^。；\n]{0,15}本科生|不得[^。；\n]{0,20}跨学院", text):return False
        return bool(clauses) and all(rules.match("undergraduate", clause) and not re.search(r"不(?:接受|允许)|不得|禁止|不符合", clause) for clause in clauses)
    if value == "ineligible":
        if rules.match("undergraduate", text):return False  # Conflicting qualifications need human review.
        return bool(clauses) and all(not re.search(r"并非|不是|不再|不只|不仅|未限定|不限于", clause) and
                    (rules.match("teacher_only", clause) or rules.match("graduate_only", clause) or
                    re.search(r"仅限(?!成都大学)[\u4e00-\u9fff]{2,12}大学[^。；\n]*(?:本科生|学生)", clause)) for clause in clauses)
    return False


def supports_policy(value, quote, notice):
    if not factual_source(notice, quote):return False
    clauses = contexts(notice, quote)
    return bool(clauses) and all(_supports_policy(value, clause, notice) for clause in clauses)


def _supports_policy(value, quote, notice):
    rules = RuleAnalyzer()
    if re.search(r"未(?:明确)?禁止|没有禁止|不禁止|未说明|未提及|是否|若|如果|建议|讨论|假设|尚待|拟|[？?]", quote):return False
    # The quoted allowance must describe this notice, not a hypothetical permission.
    if re.search(r"未(?:明确)?禁止|没有禁止|不禁止|未说明|未提及|是否|若|如(?:果)?|不得|禁止|不允许", quote) and value != "explicitly_prohibited":return False
    if value == "explicitly_prohibited":return bool(rules.match("prohibited_ai", quote))
    allowed = bool(rules.match("allowed_ai", quote))
    scoped = bool(rules.match("partial_ai", quote) or re.search(
        r"(?:AI|人工智能|AIGC)[^。；\n]{0,60}(?:图片|图像|插图|封面|摘要|论文|代码|翻译|润色|须|需|仅)|仅(?:允许|可|限)", quote, re.I))
    conflicting = bool(rules.match("prohibited_ai", notice.content))
    if value == "explicitly_allowed":return allowed and not scoped and not conflicting
    return value == "partially_allowed" and (allowed or bool(rules.match("partial_ai", quote))) and scoped


def supports_participation(value, quote, notice):
    if not factual_source(notice, quote):return False
    clauses = contexts(notice, quote)
    if value is True and any(re.search(r"不(?:接受|允许).*报名|不得报名|无需报名|报名已结束|报名已经截止|停止报名", clause) for clause in clauses):return False
    # Participation verbs inside a prize report describe past attendance.
    historical = bool(re.search(r"斩获|荣获|获奖|晋级[^。\n]*名单|公示[^。\n]*名单|圆满落幕|成功举办|活动掠影", notice.title))
    invitation = any(re.search(r"(?:报名|征集|招募|招收|征稿|参赛对象|报名对象|参与对象|参赛资格)", clause) for clause in clauses)
    if value is True:return invitation and (not historical or any(re.search(r"现(?:开始|面向)|即日起|欢迎.*(?:报名|参加)|现.*招募|新一届.*报名", clause) for clause in clauses))
    new_entry = bool(re.search(r"报名截止|报名时间|报名方式|报名入口|报名链接|参赛对象|报名对象|参与对象|现.*招募|欢迎.*报名|即日起.*报名", notice.content))
    return value is False and any(re.search(r"斩获|荣获|获奖|晋级|公示|落幕|成功举办|活动掠影|奖学金|补贴", clause) for clause in clauses) and not new_entry
