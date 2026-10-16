from pathlib import Path

import httpx
import pytest

from cdu_radar.adapters import VsbAdapter
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher
from cdu_radar.models import ParseError, SourceUnavailable
from cdu_radar.sources import content_source, load_sources

ROOT=Path(__file__).parent/'fixtures'/'m5b'
IDS={'xsc':'department-xsc-column-148','tuanwei':'department-tuanwei-column-149'}


@pytest.mark.parametrize('unit,count,first_date',[('xsc',3,'2026-10-13'),('tuanwei',3,'2026-10-12')])
def test_real_campus_list_and_detail(unit,count,first_date):
    source=next(s for s in load_sources() if s.id==IDS[unit])
    adapter=VsbAdapter(source.adapter)
    links=adapter.parse_list((ROOT/f'{unit}_list.html').read_bytes(),source.column_url)
    assert len(links)==count and links[0].published_at==first_date
    url=f'https://{unit}.cdu.edu.cn/info/{"1015/11470" if unit=="xsc" else "1012/3213"}.htm'
    article=adapter.parse_article((ROOT/f'{unit}_article.html').read_bytes(),url)
    assert article.published_at==('2026-10-08' if unit=='xsc' else first_date)
    assert len(article.content)>100 and len(article.attachments)>0
    assert '发布时间' not in article.title and '一审' not in article.content
    assert content_source(url)[0]==f'department-{unit}'


@pytest.mark.parametrize('html',[
    '<html>unknown template</html>',
    '<div id="div_items_id_test"><div class="div_itemtitle"><a href="/login.htm">标题</a><span class="other">2026-10-13</span></div></div>',
    '<div id="div_items_id_test"><div class="div_itemtitle"><a href="/info/1/1.htm">标题</a></div></div>',
    '<div id="div_items_id_test"><div class="div_itemtitle"><a href="https://evil.invalid/info/1/1.htm">标题</a><span class="other">2026-10-13</span></div></div>',
])
def test_campus_changed_structure_missing_date_and_unsafe_links_fail(html):
    from cdu_radar.models import RadarError
    with pytest.raises(RadarError):
        VsbAdapter('campus').parse_list(html,'https://xsc.cdu.edu.cn/index/tzgg.htm')


def test_campus_maintenance_and_missing_body_do_not_become_content():
    adapter=VsbAdapter('campus');url='https://xsc.cdu.edu.cn/info/1015/11470.htm'
    with pytest.raises(SourceUnavailable):
        adapter.parse_article('<html>系统维护中，请稍后再试</html>',url)
    with pytest.raises(ParseError):
        adapter.parse_article('<form name="_newscontent_fromname"><div class="div_title">标题</div></form>',url)


@pytest.mark.parametrize('unit',['xsc','tuanwei'])
def test_campus_collection_idempotence_and_known_pending_publisher_gate(store,unit):
    catalog=load_sources();source=next(s for s in catalog if s.id==IDS[unit]);store.sync_sources(catalog)
    # Match the first list date on this synthetic response; the real clipped
    # article above independently asserts its original date.
    detail=(ROOT/f'{unit}_article.html').read_bytes()
    if unit=='xsc':detail=detail.replace('2026年10月08日'.encode(),'2026年10月13日'.encode())
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        if req.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=detail if '/info/' in req.url.path else (ROOT/f'{unit}_list.html').read_bytes())
    with Fetcher(transport=httpx.MockTransport(handler),interval=0) as fetcher:
        assert collect(store,fetcher,[source],limit=1)[0]['inserted']==1
        assert collect(store,fetcher,[source],limit=1)[0]['unchanged']==1
    assert len(store.feed_rows(10))==1
    assert all('/system/_content/' not in url for url in calls)
    # Supporting the two departments must not implicitly verify teaching or
    # every pending column of the same campus.
    assert all(s.status!='verified' for s in load_sources() if s.unit_id=='department-jw')


def test_campus_bounded_pagination_and_cross_unit_link_identity():
    adapter=VsbAdapter('campus');url='https://xsc.cdu.edu.cn/index/tzgg.htm'
    assert adapter.next_page('<a href="tzgg/4.htm">下页</a>',url)=='https://xsc.cdu.edu.cn/index/tzgg/4.htm'
    with pytest.raises(ParseError,match='escaped'):
        adapter.next_page('<a href="https://tuanwei.cdu.edu.cn/index/tzgg/4.htm">下页</a>',url)
    html='<div id="div_items_id_fixture"><div class="div_itemtitle"><span class="other">2026-10-13</span><a href="https://jw.cdu.edu.cn/info/1043/12424.htm">转载</a></div></div>'
    link=adapter.parse_list(html,url)[0]
    assert link.canonical_url.startswith('https://jw.cdu.edu.cn/')
    assert content_source(link.canonical_url)[0]=='department-jw'
