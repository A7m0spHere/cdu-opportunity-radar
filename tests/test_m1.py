import gzip
import json
import socket
import sqlite3
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest

from cdu_radar.adapters import VsbAdapter, article_adapter
from cdu_radar.collect import collect
from cdu_radar.http import FetchError, Fetcher, ResolveTransport, classify_exception
from cdu_radar.models import Article, ArticleLink, ParseError
from cdu_radar.rss import build_rss
from cdu_radar.sources import load_sources
from cdu_radar.storage import SCHEMA, Store

ROOT=Path(__file__).parent/'fixtures'/'m1'


@pytest.mark.parametrize('kind,date',[
    ('innovation','2026-10-08'),('art','2023-05-08'),('film','2026-09-07'),
    ('pharmacy','2026-01-05'),('electronics','2026-09-03'),('construction','2025-11-18'),('education','2026-10-13')])
def test_real_new_list_templates(kind,date):
    base=next(s.column_url for s in load_sources() if s.adapter==kind)
    links=VsbAdapter(kind).parse_list((ROOT/(kind+'_list.html')).read_bytes(),base)
    assert len(links)==3
    assert links[0].published_at==date
    assert links[0].title and '/info/' in links[0].canonical_url


@pytest.mark.parametrize('kind,date',[
    ('innovation','2026-09-21'),('art','2026-09-30'),('film','2026-04-29'),
    ('pharmacy','2026-01-05'),('electronics','2026-09-03'),('construction','2025-11-18'),('education','2026-10-13'),('teaching','2026-09-04')])
def test_real_new_article_templates(kind,date):
    article=VsbAdapter(kind).parse_article((ROOT/(kind+'_article.html')).read_bytes(),'https://computer.cdu.edu.cn/info/1/1.htm')
    assert article.published_at==date
    assert len(article.content)>25
    assert '上一条' not in article.content and '下一篇' not in article.content


def test_real_teaching_attachment_is_recorded_without_download():
    article=VsbAdapter('teaching').parse_article((ROOT/'teaching_article.html').read_bytes(),'https://jw.cdu.edu.cn/info/1043/12424.htm')
    assert len(article.attachments)==1
    assert article.attachments[0].name.endswith('.docx')
    assert article.attachments[0].url.startswith('https://jw.cdu.edu.cn/system/_content/download.jsp?')


@pytest.mark.parametrize('kind',['innovation','art','film','pharmacy','electronics','construction','education'])
def test_new_empty_or_changed_lists_fail(kind):
    with pytest.raises(ParseError,match='structure missing'):
        VsbAdapter(kind).parse_list('<html>网页结构已经变化</html>','https://computer.cdu.edu.cn/a.htm')


def test_bounded_pagination_link():
    source=next(s for s in load_sources() if s.id=='innovation')
    adapter=VsbAdapter('innovation')
    html=(ROOT/'innovation_list.html').read_bytes()
    assert adapter.next_page(html,source.column_url)== 'https://cxcyxy.cdu.edu.cn/jyjx/xkjs/6.htm'
    with pytest.raises(ParseError,match='escaped'):
        adapter.next_page('<a href="https://msys.cdu.edu.cn/info/1/1.htm">下页</a>',source.column_url)
    assert adapter.next_page('<html></html>',source.column_url) is None


@pytest.mark.parametrize('pages,limit,expected_pages,expected_notices',[(1,10,1,3),(2,10,2,4),(5,2,1,2),(5,10,2,4)])
def test_collection_page_and_article_boundaries(store,sources,sample,pages,limit,expected_pages,expected_notices):
    source=next(s for s in sources if s.id=='computer')
    calls=[]
    page1=sample('computer_list.html').decode().replace('</body>','<a href="xkjs/1.htm">下页</a></body>')
    page2=sample('computer_list.html').decode().replace('12341.htm','12344.htm').replace('../info/','../../info/').replace('</body>','<a href="1.htm">下页</a></body>')
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/robots.txt':return httpx.Response(404)
        content=page2 if request.url.path.endswith('/1.htm') and '/info/' not in request.url.path else page1
        if '/info/' in request.url.path:content=sample('computer_article.html')
        return httpx.Response(200,content=content.encode() if isinstance(content,str) else content)
    with Fetcher(transport=httpx.MockTransport(handler),interval=0) as fetcher:
        results=collect(store,fetcher,[source],limit=limit,max_pages=pages)
    assert results[0]['pages_read']==expected_pages
    assert len(store.feed_rows(20))==expected_notices
    assert calls.count('/cxcy/xkjs/1.htm') <=1


def test_cross_college_discovery_and_publication_dates(store,sample):
    all_sources=load_sources();store.sync_sources(all_sources)
    source=next(s for s in all_sources if s.id=='innovation')
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        page=(ROOT/'innovation_list.html').read_bytes() if request.url.host=='cxcyxy.cdu.edu.cn' else (ROOT/'art_article.html').read_bytes()
        return httpx.Response(200,content=page)
    with Fetcher(transport=httpx.MockTransport(handler),interval=0) as fetcher:
        results=collect(store,fetcher,[source],limit=1)
    assert results[0]['inserted']==1
    row=store.feed_rows(1)[0]
    assert row['source_id']=='innovation' and row['content_source_id']=='msys'
    assert row['canonical_url']=='https://msys.cdu.edu.cn/info/1173/21018.htm'
    article=VsbAdapter('art').parse_article((ROOT/'art_article.html').read_bytes(),row['canonical_url'])
    assert store.save_notice('msys',row['canonical_url'],article)=='unchanged'
    assert len(store.origins(row['id']))==2 and len(store.feed_rows(20))==1


def test_cross_college_repost_date_does_not_override_original(store):
    store.sync_sources(load_sources())
    source=next(s for s in load_sources() if s.id=='ftva')
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=(ROOT/('film_list.html' if request.url.host=='ftva.cdu.edu.cn' else 'teaching_article.html')).read_bytes())
    with Fetcher(transport=httpx.MockTransport(handler),interval=0) as fetcher:
        result=collect(store,fetcher,[source],limit=1)
    assert result[0]['failed']==0
    row=store.feed_rows(1)[0]
    assert row['published_at']=='2026-09-04' and row['content_source_id']=='department-jw'
    assert store.db.execute('SELECT published_at FROM notice_sources WHERE notice_id=?',(row['id'],)).fetchone()[0]=='2026-09-07'


def test_body_links_are_absolute_and_unsafe_links_not_kept(sample):
    html=sample('computer_article.html').decode().replace('</div><div id="div_vote_id">','<p><a href="../../files/a.pdf">附件 A</a><a href="https://example.org/rules">赛事规则</a><a href="javascript:evil()">坏链接</a></p></div><div id="div_vote_id">')
    article=VsbAdapter('computer').parse_article(html,'https://computer.cdu.edu.cn/info/1/1.htm')
    assert {link.url for link in article.links}=={'https://computer.cdu.edu.cn/files/a.pdf','https://example.org/rules'}
    assert article.attachments[0].name=='附件 A'


def test_m0_database_migration_keeps_records_and_guids(tmp_path):
    path=tmp_path/'m0.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO sources VALUES ('computer','计算机学院','https://computer.cdu.edu.cn',NULL,NULL,NULL,'computer','verified',NULL,'2026-10-13','',NULL,NULL)")
        db.execute("INSERT INTO notices VALUES (7,'computer','旧通知','https://computer.cdu.edu.cn/info/1/1.htm','2026-10-01','2026-10-02T00:00:00+00:00','2026-10-02T00:00:00+00:00','旧正文','hash','2026-10-02T00:00:00+00:00',NULL)")
        db.execute("INSERT INTO notice_sources VALUES (7,'computer','2026-10-02T00:00:00+00:00','2026-10-02T00:00:00+00:00')")
    for _ in range(2):
        with Store(path) as store:
            store.sync_sources(load_sources())
            row=store.feed_rows(1)[0]
            assert row['id']==7 and row['content']=='旧正文' and row['discovered_at']=='2026-10-02T00:00:00+00:00'
            assert row['content_source_id']=='computer'
            assert ET.fromstring(build_rss(store)).findtext('./channel/item/guid')==row['canonical_url']
            assert store.db.execute('PRAGMA foreign_key_check').fetchall()==[]
            assert store.db.execute('PRAGMA user_version').fetchone()[0]==1


def test_content_and_attachment_update_preserves_guid(store):
    url='https://computer.cdu.edu.cn/info/1/1.htm'
    initial=Article('标题','2026-10-01','旧正文')
    store.save_notice('computer',url,initial)
    before=ET.fromstring(build_rss(store)).findtext('./channel/item/guid')
    revised=replace(initial,content='修订正文',attachments=(ArticleLink('通知.pdf','https://computer.cdu.edu.cn/a.pdf'),))
    assert store.save_notice('computer',url,revised)=='updated'
    assert ET.fromstring(build_rss(store)).findtext('./channel/item/guid')==before
    assert len(store.feed_rows(10))==1 and len(store.links(store.feed_rows(1)[0]['id'],'attachment'))==1


@pytest.mark.parametrize('exc,category,scope',[
    (httpx.ConnectError('[SSL: UNEXPECTED_EOF_WHILE_READING]'),'tls_handshake','environment'),
    (httpx.ConnectError('certificate_verify_failed'),'tls_certificate','environment'),
    (httpx.ProxyError('proxy unavailable'),'proxy','environment'),
    (httpx.ReadTimeout('read timed out'),'readtimeout','request'),
    (httpx.RemoteProtocolError('connection closed'),'protocol','request'),
    (httpx.DecodingError('invalid gzip'),'response_decode','request')])
def test_request_failure_categories(exc,category,scope):
    assert classify_exception(exc)==(category,scope)


def test_dns_exception_chain_is_distinct():
    exc=httpx.ConnectError('connect failed');exc.__cause__=socket.gaierror('getaddrinfo failed')
    assert classify_exception(exc)==('dns','environment')


def test_explicit_dns_override_retains_host_sni():
    transport=ResolveTransport({'cxcyxy.cdu.edu.cn':'202.115.80.140'},{'cxcyxy.cdu.edu.cn'})
    transport.inner.close()
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,content=b'HTML')
    transport.inner=httpx.MockTransport(handler)
    with transport:
        transport.handle_request(httpx.Request('GET','https://cxcyxy.cdu.edu.cn/jyjx/xkjs.htm'))
    assert calls[0].url.host=='202.115.80.140'
    assert calls[0].headers['host']=='cxcyxy.cdu.edu.cn'
    assert calls[0].extensions['sni_hostname']=='cxcyxy.cdu.edu.cn'


@pytest.mark.parametrize('mapping',[{'cxcyxy.cdu.edu.cn':'127.0.0.1'},{'unknown.cdu.edu.cn':'202.115.80.140'}])
def test_resolve_rejects_private_address_and_unknown_host(mapping):
    with pytest.raises(ValueError,match='configured host'):
        ResolveTransport(mapping,{'cxcyxy.cdu.edu.cn'})


def test_gzip_stream_is_not_decompressed_twice():
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=gzip.compress('中文 HTML'.encode()),headers={'content-encoding':'gzip','content-type':'text/html'})
    with Fetcher(transport=httpx.MockTransport(handler),interval=0) as fetcher:
        assert fetcher.fetch('https://computer.cdu.edu.cn/a.htm')[0].decode()=='中文 HTML'


def test_unconfigured_official_host_never_requested():
    def handler(request):raise AssertionError('Must not request unknown host')
    with Fetcher(transport=httpx.MockTransport(handler)) as fetcher:
        with pytest.raises(FetchError,match='Unconfigured'):
            fetcher.fetch('https://unknown.cdu.edu.cn/info/1/1.htm')


def test_retry_state_distinguishes_permanent_and_parser_failures(store,sources):
    source=next(s for s in sources if s.id=='computer')
    def handler(request):return httpx.Response(404 if request.url.path=='/robots.txt' else 410)
    with Fetcher(transport=httpx.MockTransport(handler),interval=0) as fetcher:
        result=collect(store,fetcher,[source])
    assert result[0]['error_scope']=='permanent' and not result[0]['retryable']
    assert result[0]['parse_failures']==0


def test_failed_source_is_retried_on_next_run(store,sources,sample):
    source=next(s for s in sources if s.id=='computer')
    def failed(request):raise httpx.ConnectError('[SSL: UNEXPECTED_EOF_WHILE_READING]',request=request)
    with Fetcher(transport=httpx.MockTransport(failed),interval=0,retries=0) as fetcher:
        first=collect(store,fetcher,[source])
    assert first[0]['error_category']=='tls_handshake'
    def recovered(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=sample('computer_article.html' if '/info/' in request.url.path else 'computer_list.html'))
    with Fetcher(transport=httpx.MockTransport(recovered),interval=0) as fetcher:
        second=collect(store,fetcher,[source],limit=1)
    assert second[0]['inserted']==1
    state=store.db.execute("SELECT status,error_category FROM sources WHERE id='computer'").fetchone()
    assert tuple(state)==('verified',None)


def test_diagnostic_does_not_expose_proxy_credentials(monkeypatch):
    from scripts.diagnose_source import redact_error
    value='http://test-user:example-only@localhost:1234'
    monkeypatch.setenv('HTTPS_PROXY',value)
    result=redact_error(httpx.ProxyError('proxy failed: '+value))
    assert 'test-user' not in result['detail'] and 'example-only' not in result['detail']


def test_stream_size_limit_is_enforced_before_parse():
    events=[]
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(200,content=b'x'*(3*1024*1024+1))
    with Fetcher(transport=httpx.MockTransport(handler),interval=0,event_sink=lambda *args:events.append(args)) as fetcher:
        with pytest.raises(FetchError,match='exceeds 3 MiB') as error:
            fetcher.fetch('https://computer.cdu.edu.cn/a.htm')
    assert error.value.category=='response_size' and not error.value.retryable
    assert events[-1][4:6]==(200,'response_size')


def test_directory_candidate_links_are_observed_not_generated():
    from scripts.discover_sources import candidates
    html='<a href="notify.htm">通知公告</a><a href="javascript:void(0)">学术活动</a><a href="https://other.test/a.htm">学科竞赛</a>'
    assert candidates(html,'https://computer.cdu.edu.cn/')==[{'column_name':'通知公告','column_url':'https://computer.cdu.edu.cn/notify.htm'}]


def test_shared_domain_does_not_guess_publishing_college(monkeypatch):
    import cdu_radar.sources as catalog
    source=next(s for s in load_sources() if s.id=='computer')
    other=replace(source,id='another',unit_id='another',college='另一个学院')
    monkeypatch.setattr(catalog,'load_sources',lambda:[source,other])
    with pytest.raises(ParseError,match='Ambiguous publishing unit'):
        catalog.content_source('https://computer.cdu.edu.cn/info/1/1.htm')
