import json
from xml.etree import ElementTree as ET

import pytest

from cdu_radar.undergraduate import assess, validate
from cdu_radar.review_api import validate_public_v4
from cdu_radar.lan_preview import upstream_read
from test_auto_publish import auto_service, add, URL
from test_rss_service import request, loopback_only


@pytest.mark.parametrize('title,body,state',[
    ('关于本科毕业论文工作的通知','各学院、2027届本科毕业生：学生须于10月30日前提交开题报告，并及时核对导师信息。','relevant'),
    ('教师教学创新大赛方案','参赛教师提交教学设计，鼓励教师指导本科生开展实践。','excluded'),
    ('本科毕业论文总结暨启动会通知','请各学院教学秘书参加会议，讨论本科生培养方案。','excluded'),
    ('研究生奖学金公示','仅面向全日制研究生，公示期间可申请复核。','excluded'),
    ('研究生与本科生联合实践招募','面向本科生和研究生招募实践成员，请于10月25日前提交申请。','relevant'),
    ('本科生国家奖学金公示','本次奖学金评审名单现予以公示，学生如有异议请于10月25日前提出。','relevant'),
    ('学生程序设计竞赛获奖报道','学生已经报名参加下一轮比赛，现回顾获奖情况。','excluded'),
    ('学院开展大学生创新大赛动员会','5月12日，学院召开大赛动员会。会上，为学生讲解参赛方式。','excluded'),
    ('大学生竞赛院级选拔','2026年4月19日，学院开展项目答辩。经过专家评审，最终推荐5个项目。','excluded'),
    ('普通话水平测试考前通知','各位考生请提前三天打印准考证，考试当天须携带证件原件。','relevant'),
    ('学生选修课学习通知','同学们务必于10月30日前完成课程学习，逾期将影响课程成绩。','relevant'),
    ('合成课程调整通知','本科生须登录教务系统核对课程时间调整，具体安排以原文为准。','relevant'),
    ('图书馆服务时间调整通知','图书馆开放时间调整至每日八点至二十二点，请提前安排学习时间。','relevant'),
    ('学术讲座预告','欢迎全校师生参加学术讲座，活动时间为10月25日，地点为报告厅。','relevant'),
    ('关于推荐大赛裁判的通知','教师自主申请，学院报送材料，为本科生提供实践指导。','excluded'),
    ('工作方案','各学院于10月25日前报送材料，附件中有学生名单。','uncertain'),
    ('活动招募通知','请有关人员报名，具体适用对象与时间见附件。','uncertain'),
])
def test_undergraduate_scope_regressions(title,body,state):
    result=assess(title,body);validate(result)
    assert result['state']==state
    if state=='relevant':
        assert all(q in body for q in result['summary'].splitlines())
        assert all(q in title or q in body for q in result['evidence'])
    else:assert result['summary'] is None


def test_summary_prefers_student_actions_over_staff_reporting_and_never_exposes_contacts():
    body=('各学院及相关本科生：\n监考教师须提前核对试卷和考场安排，按时上报学院。\n'
          '请各学院在10月20日前完成材料汇总报送工作。\n'
          '本科生须在10月25日前登录教务系统核对考试安排。\n'
          '考试当天，考生须携带证件原件并提前到达考场。\n'
          '联系电话：13800000000，联系人：某老师。')
    result=assess('期末考试安排通知',body);validate(result)
    assert result['state']=='relevant' and '10月25日' in result['summary']
    assert not any(s in result['summary'] for s in ['监考教师','10月20日','13800000000','某老师'])
    assert len(result['summary'])<=420


def test_source_url_does_not_hide_student_deadline_or_promote_staff_instructions():
    body=('各学院：\n我处已完成新生学籍网上注册工作，具体事项通知如下。\n'
          '2026级本科新生须于2026年10月26日前登录学信网（http://www.chsi.com.cn），核实自己的学籍信息。\n'
          '2、各学院应指定专人负责，督促学生及时完成学籍自查工作。')
    result=assess('关于新生学籍信息核对工作的通知',body);validate(result)
    assert '2026年10月26日' in result['summary'] and 'http://www.chsi.com.cn' in result['summary']
    assert '各学院应指定' not in result['summary'] and '我处已完成' not in result['summary']


def test_undergraduate_http_rss_proxy_and_legacy_consistency(auto_service,monkeypatch):
    store,controller,server,_,_=auto_service
    add(store,title='本科生选课通知',body='本科生须于10月25日前登录教务系统完成选课，逾期请按学校要求办理。')
    add(store,url=URL.replace('990001','990002'),title='教师教学创新大赛通知',body='教师提交教学设计方案，本科生可配合教师参与课堂展示，具体材料见附件。')
    add(store,url=URL.replace('990001','990003'),title='研究生奖学金通知',body='全日制研究生须提交奖学金申请，其他办理要求和材料以本通知为准。')
    assert controller.refresh()
    before=store.db.total_changes
    status,_,body=request(server,'/api/notices/v4?audience=undergraduate')
    assert status==200
    payload=json.loads(body);validate_public_v4(payload)
    assert [i['title'] for i in payload['items']]==['本科生选课通知']
    assert payload['items'][0]['summary']==payload['items'][0]['undergraduate']['summary']
    assert 'audience=undergraduate' in payload['subscription']['url']
    rss=ET.fromstring(request(server,payload['subscription']['url'])[2])
    assert {i.findtext('guid') for i in rss.findall('./channel/item')}=={i['guid'] for i in payload['items']}
    assert payload['items'][0]['summary'] in rss.findtext('./channel/item/description')
    assert upstream_read(server.server_address[1],'/api/notices/v4?audience=undergraduate')[0]==200
    assert len(json.loads(request(server,'/api/notices/v4')[2])['items'])==3
    assert len(json.loads(request(server,'/api/notices/v2')[2])['items'])==3
    assert store.db.total_changes==before
    forged=json.loads(body);forged['items'][0]['undergraduate']['private_path']='private'
    with pytest.raises(ValueError):validate_public_v4(forged)
    for query in ['audience=teacher','audience=undergraduate&audience=all','audience=']:
        assert request(server,'/api/notices/v4?'+query)[0]==400
    # Raw changes immediately change the reading scope, without model/cache reuse.
    add(store,title='教师教学创新大赛通知',body='全体教师须提交教学设计，本科生配合教师参与课堂展示，其他工作按方案办理。')
    assert controller.refresh()
    assert not json.loads(request(server,'/api/notices/v4?audience=undergraduate')[2])['items']
