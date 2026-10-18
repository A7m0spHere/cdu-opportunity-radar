"""Versioned campus classification, independent of the legacy opportunity filter."""
import re

from .analysis_models import fingerprint
from .taxonomy import TYPES, TOPICS

DOMAINS = {
    'academic_affairs': '教务考试', 'student_aid': '奖助评优',
    'competition': '竞赛双创', 'research': '学术科研', 'career': '就业实习',
    'campus_service': '校园服务', 'student_affairs': '学生事务', 'culture_sports': '文体活动',
}
DOMAIN_PATTERNS = {
    'academic_affairs': r'教务|教学|学籍|选课|评教|考试|考前|普通话.*测试|毕业(?:设计|论文|答辩)|学业预警|课程',
    'student_aid': r'奖学金|助学金|助学贷款|困难补助|荣誉称号|评优评奖|评奖评优',
    'competition': r'竞赛|大赛|挑战赛|比赛|开拓杯|科创杯|创新创业|创业训练|双创',
    'research': r'科研|课题|研究助理|学术报告|学术讲座|研讨会|研究实践',
    'career': r'就业|招聘|实习招募|实习岗位|职业规划|生涯发展|创业经验',
    'campus_service': r'停水|停电|安全预警|紧急通知|校园服务|维修|宿舍服务',
    'student_affairs': r'学生社团|社团|学生会|团课|志愿服务|社会实践|入学教育|素质测评',
    'culture_sports': r'艺术展演|文艺|运动会|篮球|足球|排球|体育赛事|音乐会|作品展',
}
NEWS = r'斩获|荣获|获奖|摘金|夺金|取得好成绩|获佳绩|载誉|喜报|恭喜|圆满(?:结束|落幕)|成功(?:举办|召开)|顺利(?:举办|举行|召开)|活动掠影|评审.*落幕|获.*立项|成立暨|举行.*仪式'
AFFAIRS = r'公示|名单|考前|学籍|选课|评教|学业预警|期末考试|毕业.*(?:工作|通知|检测|答辩)|工作方案|实施方案|工作总结|经费|报销|停水|停电|教学调整|推荐.*裁判'
ENGINEERING_DESIGN = r'程序设计|系统设计|电路设计|电子设计|芯片设计|教学设计|课程设计|毕业设计|设计思路|创新设计'
POLICY_OR_TOOL = r'规范.*(?:AI|人工智能).*使用|杜绝.*(?:人工智能|AI).*代写|AI工具使用|AIGC检测|聊天软件|计算机辅助测试|不得随意操作计算机'
TOPIC_RULES = {
    'ai': r'人工智能|机器学习|大模型|AIGC|(?<![A-Za-z])AI(?![A-Za-z])',
    'computing': r'计算机|编程|编写程序|程序设计|软件开发|网络安全|信息安全|算法|ICPC|ACM|嵌入式|芯片|RK3588|STM32|开发板',
    'pharmacy': r'药学|药物|制药|药品|药用|饮片|穿心莲|天麻',
    'design': r'艺术|美术|视觉传达|工业设计|创意设计|数字媒体|数字艺术|动画|服装设计|环境设计',
    'innovation': r'创新创业|创业|创新实践|双创|商业化',
    'sports': r'体育赛事|运动会|篮球赛|足球赛|排球赛|竞技体育',
    'humanities': r'文学研究|文学作品|哲学|法学|历史学|社会科学|人文|新闻传播',
}


def clauses(text):
    return [s.strip() for s in re.split(r'[\n。；;]', text) if s.strip()]


def topic_supported(topic, text):
    """Require subject context; incidental tools and compound substrings do not qualify."""
    for sentence in clauses(text):
        sentence=re.sub(r'(?:创新创业|双创|计算机|美术与设计|药学)(?:学院|处)|教务处','',sentence)
        cleaned = re.sub(ENGINEERING_DESIGN, '', sentence) if topic == 'design' else sentence
        if topic in {'ai', 'computing'} and re.search(POLICY_OR_TOOL, sentence, re.I):
            continue
        if re.search(TOPIC_RULES[topic], cleaned, re.I):
            return True
    return False


def main_text(title, content):
    paragraphs = [p for p in content.splitlines() if len(p.strip()) >= 15]
    return title + '\n' + '\n'.join(paragraphs[:2])


def rule_type(title, content):
    if re.search(r'公示|拟录名单|评选结果.*通报|推荐.*裁判', title):
        return 'notice'
    if re.search(NEWS, title):
        return 'news'
    if re.search(AFFAIRS, title) and not re.search(r'竞赛|大赛|挑战赛|比赛',title):
        return 'notice'
    lead = main_text('', content)
    if not re.search(r'通知|公告|报名|招募|征集|关于', title) and re.search(r'本网新闻|近日|于.{0,35}(?:举办|举行|召开)|已经|已成功|完成了|分享了', lead):
        return 'news'
    if re.search(r'竞赛|大赛|挑战赛|比赛|黑客松|hackathon', title, re.I):
        return 'competition'
    if re.search(r'科研.*(?:申报|招募)|研究实践|研究助理|课题招募|项目招募|成员招募', title):
        return 'research'
    if re.search(r'讲座|学术报告|研讨会|工作坊|训练营|志愿活动|招募会员', title):
        return 'event'
    if re.search(r'通知|公告|方案|安排', title):
        return 'notice'
    return 'unknown'


def classify(title, content):
    text = main_text(title, content)
    domains = [k for k, pattern in DOMAIN_PATTERNS.items() if re.search(pattern, title)]
    if not domains:
        context=re.sub(r'(?:创新创业|双创)(?:学院|处)|教务处','',text)
        domains = [k for k, pattern in DOMAIN_PATTERNS.items() if re.search(pattern, context)]
    kind = rule_type(title, content)
    # General academic administration is not a cross-disciplinary activity.
    if re.search(AFFAIRS, title) and 'academic_affairs' in domains:
        domains = ['academic_affairs']
    topics = [k for k in TOPICS if topic_supported(k, text)]
    return {'version': 2, 'policy': policy_version(), 'method': 'rules', 'type': kind,
            'domains': domains, 'topics': topics,
            'type_state': 'unclassified' if kind == 'unknown' else 'classified',
            'topic_state': 'tagged' if topics else 'unclassified', 'model_state': 'not_analyzed'}


def policy_version():
    from importlib.resources import files
    code = files('cdu_radar').joinpath('classification.py').read_text('utf-8').replace('\r\n', '\n')
    return 'campus-taxonomy-v2:' + fingerprint(code)[:16]


def type_supported(kind, quote):
    if kind == 'news':
        return bool(re.search(NEWS + r'|近日|举办了|举行了|召开了|完成了|顺利完成|已(?:举办|举行|结束)|评审.*完成', quote))
    if kind == 'notice':
        return bool(re.search(AFFAIRS + r'|通知|公告|现将|请各|安排|方案', quote))
    if kind == 'competition':
        return bool(re.search(r'竞赛|大赛|比赛|挑战赛|黑客松|hackathon', quote, re.I))
    if kind == 'research':
        return bool(re.search(r'科研|课题|研究|项目.*招募|创新实践', quote))
    if kind == 'event':
        return bool(re.search(r'讲座|报告|交流|研讨|工作坊|训练营|志愿|活动|招募会员', quote))
    return False
