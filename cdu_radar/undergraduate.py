"""Evidence-based undergraduate reading scope; never infers participation eligibility."""
import re

from .classification import rule_type
from .privacy import sanitize

STATES={'relevant','excluded','uncertain'}
REASONS={
    'student_affairs':'本科生学业、权益或生活事务',
    'student_opportunity':'面向学生的参与机会',
    'useful_information':'对本科生有阅读参考价值的信息',
    'campus_service':'面向校园的实用服务',
    'staff_only':'教师或内部工作事务',
    'graduate_only':'研究生专属事务',
    'retrospective':'成果宣传或活动回顾',
    'audience_unclear':'本科生适用对象依据不足',
    'no_practical_action':'未发现学生办理事项或参与机会',
}
STUDENT=r'本科|本专科|专升本|大学生|在校生|全校学生|学生|同学|毕业生|新生|考生|师生|学生社团'
UNDERGRAD=r'本科|本专科|专升本|本硕|全体在校生|全校学生'
STAFF=r'教师教学|全员课堂教学|教师.{0,15}(?:大赛|申报|评选|培训|职称)|教职工|师资|职称|裁判|导师.{0,6}聘任|教学(?:改革|成果|评估)|本科教学.{0,8}评估|总结暨启动会|工作交流会'
AFFAIRS=r'选(?:修)?课|退课|重.{0,3}修|补修|补考|考试|考前|普通话.{0,12}测试|学籍|学业预警|评教|课程(?:调整|安排)|调课|停课|毕业.{0,12}(?:设计|论文|答辩|审核)|缴费|退费|学分认定|素质测评|奖学金|助学金|助学贷款|困难补助|评优|评奖|荣誉|社团|社会实践|公示|名单'
SERVICE=r'停水|停电|宿舍|图书馆|校园交通|校车|食堂|校园网|安全预警|卫生防疫'
ACTION=r'报名|招募|征集|申请|申报|参赛|竞赛|大赛|选拔|讲座|学术报告|工作坊|训练营|实习|招聘|志愿服务'
DETAIL=r'对象|范围|条件|资格|时间|截止|日期|地点|方式|流程|提交|登录|完成|办理|缴费|打印|携带|确认|报名|申请|注意|提醒|公示|异议|请.{0,15}(?:学生|同学|考生)'


def snippets(content):
    """Only unchanged, sanitized source spans can become public digest evidence."""
    clean=sanitize(content)
    result=[]
    for match in re.finditer(r'[^\n。！？；]+[。！？；]?',clean):
        remaining=match.group().strip()
        while remaining:
            end=len(remaining) if len(remaining)<=240 else remaining.rfind('，',0,240)+1 or 240
            quote=remaining[:end].strip();remaining=remaining[end:].strip()
            if not 10<=len(quote)<=240 or quote not in content or '[' in quote:continue
            if re.search(r'联系人|联系电话|一审|二审|三审|指导教师|编辑[：:]',quote):continue
            result.append(quote)
    return result


def assess(title, content):
    """Reading relevance is separate from class, importance, and current registration."""
    lead=content[:1800];main=title+'\n'+lead
    output={'state':'uncertain','reason':'audience_unclear','evidence':[],'summary':None}
    def decision(state,reason,quotes=()):
        return dict(output,state=state,reason=reason,evidence=list(quotes))
    if re.search(STAFF,title):return decision('excluded','staff_only')
    if re.search(r'研究生|硕士|博士|博士后',title) and not re.search(UNDERGRAD,main):
        return decision('excluded','graduate_only')
    if re.search(r'(?:仅限|仅面向|对象[为是：:]?)[^。\n]{0,20}(?:研究生|硕士|博士|教师|教职工)',lead) and not re.search(UNDERGRAD,main):
        return decision('excluded','graduate_only' if re.search(r'研究生|硕士|博士',lead) else 'staff_only')
    reported=(not re.search(r'关于|通知|公告|报名|招募|公示',title) and
        re.search(r'(?:\d{1,2}月\d{1,2}日[^。\n]{0,70}(?:召开|举办|进行|开展|答辩))|会上[，,]|经过.{0,15}评审.{0,25}最终',lead))
    if rule_type(title,content)=='news' or reported or re.search(r'活动掠影|评选结果.{0,8}通报|分享.{0,12}经验|走进新生入学教育',title):
        return decision('excluded','retrospective')
    quotes=snippets(content)
    audience=[q for q in quotes if re.search(STUDENT,q)]
    if not re.search(STUDENT,main) and not re.search(SERVICE,title):return output
    if re.search(SERVICE,title):reason='campus_service'
    elif re.search(AFFAIRS,title):reason='student_affairs'
    elif re.search(ACTION,title) and re.search(STUDENT,main):reason='student_opportunity'
    else:return decision('uncertain','no_practical_action')
    # Confirm an actual instruction, rather than selecting a student's incidental mention.
    instructions=[q for q in quotes if re.search(DETAIL,q)]
    if not instructions:return decision('uncertain','no_practical_action')
    # Prefer student-facing actions; internal reporting and generic background are not a digest.
    def internal(quote):
        numbered=re.sub(r'^(?:[（(]?\d+[）).、]\s*)','',quote)
        if re.search(r'^(?:各学院|学院|各部门|教务处|学校|教师|监考教师|任课教师|指导教师|教学院长|教学秘书)',numbered):
            return not bool(re.search(r'^[^：:]{0,50}[：:](?:学生|本科生|同学|考生)(?:须|务必|需|请|应)',numbered))
        return bool(re.search(
        r'请各学院|教学院长|(?:监考|任课|指导)教师.{0,12}(?:须|应|需)|'
        r'(?:学院|教务处|教学单位|团委).{0,15}(?:务必|必须|高度重视)',quote))
    useful=[q for q in instructions if not internal(q) and not re.search(r'^附件[：:]|^[一二三四五六七八九十]+、[^。]{0,20}[：:]$',q)]
    if not useful:return decision('uncertain','no_practical_action')
    def score(quote):
        if re.search(r'我处已|已经完成|现已完成',quote):return 0
        return (4*bool(re.search(STUDENT,quote))+
                5*bool(re.search(r'请|务必|须|登录|缴费|打印|查询|确认|核对|截止|公示期',quote))+
                4*bool(re.search(r'\d{1,2}月\d{1,2}日|\d{4}[-年]\d{1,2}[-月]',quote)))
    ranked=sorted(enumerate(useful),key=lambda pair:(-score(pair[1]),pair[0]))
    chosen=[]
    for _,quote in ranked:
        if not score(quote):continue
        if quote in chosen:continue
        if sum(map(len,chosen))+len(quote)+len(chosen)>420:continue
        chosen.append(quote)
        if len(chosen)==3:break
    if not chosen:return decision('uncertain','no_practical_action')
    chosen.sort(key=useful.index)
    evidence=list(dict.fromkeys(([title] if re.search(STUDENT+'|'+SERVICE,title) else audience[:1])+chosen))[:4]
    return {'state':'relevant','reason':reason,'evidence':evidence,'summary':'\n'.join(chosen)}


def validate(value):
    if not isinstance(value,dict) or set(value)!={'state','reason','evidence','summary'} or value['state'] not in STATES or value['reason'] not in REASONS:
        raise ValueError('Invalid undergraduate scope')
    reasons={'relevant':{'student_affairs','student_opportunity','campus_service','useful_information'},
             'excluded':{'staff_only','graduate_only','retrospective'},
             'uncertain':{'audience_unclear','no_practical_action'}}
    if value['reason'] not in reasons[value['state']]:raise ValueError('Inconsistent undergraduate scope')
    if not isinstance(value['evidence'],list) or len(value['evidence'])>4 or any(not isinstance(q,str) or not 0<len(q)<=500 for q in value['evidence']):
        raise ValueError('Invalid undergraduate evidence')
    if value['state']=='relevant':
        evidence_lines={line for quote in value['evidence'] for line in quote.splitlines()}
        if not isinstance(value['summary'],str) or not 0<len(value['summary'])<=420 or any(q not in evidence_lines for q in value['summary'].splitlines()):
            raise ValueError('Invalid undergraduate summary')
    elif value['summary'] is not None:raise ValueError('Unexpected undergraduate summary')
