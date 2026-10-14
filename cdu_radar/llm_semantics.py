"""Conservative fact guards; location alone never proves a conclusion."""
import re
from .rule_analysis import RuleAnalyzer
from .evidence import locate, contexts


def factual_source(notice, quote):
    return locate(notice, quote).source in {"title", "content"}


STUDENT_COVERAGE = re.compile(r"本科(?:生|学生)|本[、]?专科(?:学生|生)|在校大学生|普通高等学校在校生")
ADMISSION = re.compile(r"报名对象|参赛对象|参与对象|参赛资格|报名资格|面向|欢迎|可(?:参加|报名|参赛)|仅限|仅接受")


def admission_context(notice, quote, clauses):
    if all(ADMISSION.search(c) for c in clauses):return True
    match = locate(notice,quote)
    if match.source != "content" or len(clauses) != 1:return False
    # A qualification heading on the immediately preceding nonempty line is context,
    # not an invented addition to the model's quote.
    line_start = notice.content.rfind("\n",0,match.start) + 1
    previous = [line.strip() for line in notice.content[:line_start].splitlines() if line.strip()]
    return bool(previous and re.fullmatch(r"(?:[一二三四五六七八九十\d]+[、.．]\s*)?(?:报名对象|参赛对象|参与对象|参赛资格|报名资格)[：:]?",previous[-1]))


def eligibility_reason(value, quote, notice):
    """No applicant profile is available: restrictions remain unresolved."""
    if not factual_source(notice, quote):return "non_factual_source"
    rules = RuleAnalyzer()
    text = notice.title + "\n" + notice.content
    clauses = contexts(notice, quote)
    if value == "eligible":
        if any(rules.match(key, text) for key in ["teacher_only", "graduate_only", "restricted"]):return "restricted_or_conflicting_audience"
        restrictions = {
            "excluded_institution": r"外校|非(?:我校|本校|成都大学)|成都大学[^。；\n]{0,12}除外|(?:不含|不包括|排除)[^。；\n]{0,12}成都大学",
            "restricted_major_or_cohort": r"限本院|限本专业|指定(?:院校|高校|学校|专业|年级)|(?:报名对象|参赛对象|面向|仅限|只限|限于)[^。；\n]{0,50}(?:专业|年级|大[一二三四]|20\d{2}级)|(?:我校|成都大学)[^。；\n]{0,20}(?:20\d{2}级|大[一二三四])[^。；\n]{0,15}(?:本科|学生)|不得[^。；\n]{0,20}跨学院",
            "restricted_region": r"(?:报名对象|参赛对象|面向|仅限|只限|限于|仅接受|仅招收)[^。；\n]{0,50}(?:省内|市内|地区|[\u4e00-\u9fff]{2,8}省|北京市|上海市|天津市|重庆市)|(?:户籍|学籍|学校所在地)[^。；\n]{0,25}(?:地区|省|市)",
            "additional_conditions": r"(?:须|需|必须|要求|具备|熟悉|掌握)[^。；\n]{0,35}(?:推荐|选拔|面试|专业|基础|技能|编程|语言|经历|经验)|通过[^。；\n]{0,20}(?:面试|选拔)|(?:仅限|面向)[^。；\n]{0,25}(?:我院|本院|本学院|指定院校)",
            "restricted_institution_class": r"(?:参赛|就读)(?:院校|学校)[^。；\n]{0,25}(?:名单|指定|211|985|双一流)|(?:仅限|限于)(?!全国|普通高等|普通高校)[^，。；\n]{0,20}(?:高校|院校)",
        }
        for reason, pattern in restrictions.items():
            if re.search(pattern, text):return reason
        # A named school or unspecified school list is not a nationwide invitation.
        for clause in re.split(r"[。；\n]", text):
            if not (ADMISSION.search(clause) or STUDENT_COVERAGE.search(clause)):continue
            if re.search(r"(?:其他|其它|指定|部分|参赛|合作)(?:大学|高校|院校)|(?:我院|本院|本学院)[^。；\n]*学生", clause):return "restricted_institution"
            schools = re.findall(r"([\u4e00-\u9fff]{2,15}?(?:大学|学院))[^。；\n]{0,20}(?:本科|学生|在校生|大学生)", clause)
            if any(not school.endswith("成都大学") and not school.endswith("在校大学") and school != "全国大学" for school in schools):return "restricted_institution"
        if not clauses or not all(STUDENT_COVERAGE.search(c) for c in clauses):return "student_coverage_unrecognized"
        if not admission_context(notice,quote,clauses):return "missing_admission_context"
        if any(re.search(r"不(?:接受|允许|面向)|不得|禁止|不符合|并非|不是|尚未|若|如果|是否|[？?]", c) for c in clauses):return "negative_or_conditional_audience"
        return "supported"
    if value == "ineligible":
        # Explicitly exclusive other-school clauses can exclude CDU, broader conflicts cannot.
        other_school = bool(clauses) and all(re.search(r"仅限(?!成都大学)[\u4e00-\u9fff]{2,12}(?:大学|学院)[^。；\n]*(?:本科生|学生|在校生)", c) and not re.search(r"并非|不是|不限|不只|不仅", c) for c in clauses)
        if other_school and not re.search(r"成都大学.*(?:本科生|学生)|我校.*(?:本科生|学生)|全国.*(?:本科生|学生)", text):return "supported"
        if STUDENT_COVERAGE.search(text):return "conflicting_audience"
        supported = bool(clauses) and all(not re.search(r"并非|不是|不再|不只|不仅|未限定|不限于", clause) and
                    (rules.match("teacher_only", clause) or rules.match("graduate_only", clause) or
                    re.search(r"仅限(?!成都大学)[\u4e00-\u9fff]{2,12}大学[^。；\n]*(?:本科生|学生)", clause)) for clause in clauses)
        return "supported" if supported else "missing_exclusion_basis"
    return "unknown_value"


def supports_eligibility(value, quote, notice):
    return eligibility_reason(value, quote, notice) == "supported"


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
