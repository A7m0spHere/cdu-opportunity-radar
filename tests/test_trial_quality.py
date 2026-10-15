import pytest
import httpx

from cdu_radar.adapters import VsbAdapter
from cdu_radar.models import Article,ParseError,SourceUnavailable
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher


@pytest.mark.parametrize('message',['页面正在修复中，请稍后重试','系统维护中，请稍后再试'],ids=['repair','maintenance'])
def test_observed_maintenance_page_is_unavailable_not_a_new_template(message):
    with pytest.raises(SourceUnavailable) as error:
        VsbAdapter('business').parse_article('<html><title>结果</title><body>'+message+'</body></html>',
            'https://bs.cdu.edu.cn/info/1041/13262.htm')
    assert error.value.retryable and error.value.category=='source_maintenance'


def test_unknown_missing_form_remains_parse_failure():
    with pytest.raises(ParseError):
        VsbAdapter('business').parse_article('<html>Unknown article structure</html>',
            'https://bs.cdu.edu.cn/info/1041/13262.htm')


def test_maintenance_recheck_withholds_previously_saved_notice(store,sources):
    source=next(s for s in sources if s.id=='business')
    url='https://bs.cdu.edu.cn/info/1041/13262.htm'
    body='本条是独立数据库中的旧正文合成夹具，用来验证维护页不会保留旧内容发布。'
    store.save_notice(source.id,url,Article('合成历史正文',None,body))
    def response(req):
        if req.url.path=='/robots.txt':return httpx.Response(404)
        if req.url.path==source.column_url.split('bs.cdu.edu.cn')[1]:
            return httpx.Response(200,text='<ul class="jtfylb"><li><a href="'+url+'"><p>测试通知</p><span>2026-10-15</span></a></li></ul>',headers={'content-type':'text/html'})
        return httpx.Response(200,text='<title>结果</title>页面正在修复中，请稍后重试',headers={'content-type':'text/html'})
    with Fetcher(transport=httpx.MockTransport(response),interval=0) as fetcher:
        result=collect(store,fetcher,[source],limit=1)
    assert result[0]['failed']==1 and result[0]['parse_failures']==0
    assert result[0]['error_category']=='source_maintenance' and result[0]['retryable']
    assert store.db.execute('SELECT status FROM notice_health').fetchone()[0]=='unavailable'
    assert store.db.execute('SELECT content FROM notices').fetchone()[0]==body
