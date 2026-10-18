from dataclasses import replace
from pathlib import Path

from bs4 import BeautifulSoup
import httpx
import pytest

from cdu_radar.adapters import VsbAdapter
from cdu_radar.auto_publish import AutoRepository
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher
from cdu_radar.models import ParseError
from cdu_radar.sources import load_sources


ROOT = Path(__file__).parent / 'fixtures' / 'teaching'
SOURCE_ID = 'department-jw-notices'
URL = 'https://jw.cdu.edu.cn/info/1043/12471.htm'


def test_real_teaching_list_preserves_pinned_date_and_non_ai_notices():
    adapter = VsbAdapter('teaching')
    links = adapter.parse_list((ROOT / 'list.html').read_bytes(), 'https://jw.cdu.edu.cn/jwgg.htm')
    assert len(links) == 3
    assert links[0].title == '教务系统登录指南'
    assert links[0].published_at == '2025-09-05'
    assert links[2].canonical_url == URL
    assert links[2].published_at == '2026-10-15'
    assert '学籍信息网上查询核对' in links[2].title
    assert adapter.next_page((ROOT / 'list.html').read_bytes(), 'https://jw.cdu.edu.cn/jwgg.htm') == 'https://jw.cdu.edu.cn/jwgg/274.htm'


def test_real_teaching_article_has_original_date_and_clean_body():
    article = VsbAdapter('teaching').parse_article((ROOT / 'article.html').read_bytes(), URL)
    assert article.title == '关于做好2026级新生学籍信息网上查询核对工作的通知'
    assert article.published_at == '2026-10-15'
    assert '新生入学学籍电子注册' in article.content
    assert '一审' not in article.content


@pytest.mark.parametrize('change,message', [
    (lambda html: html.replace('class="text-list"', 'class="changed"'), 'structure missing'),
    (lambda html: html.replace('class="span"', 'class="changed"'), 'date node missing'),
    (lambda html: html.replace('2026-10-15', '2026-02-30'), 'Invalid publication date'),
    (lambda html: html.replace('info/1043/12471.htm', 'login.htm'), 'unexpected article path'),
])
def test_teaching_list_changes_fail_explicitly(change, message):
    with pytest.raises(ParseError, match=message):
        VsbAdapter('teaching').parse_list(change((ROOT / 'list.html').read_text('utf-8')), 'https://jw.cdu.edu.cn/jwgg.htm')


def test_teaching_pagination_cannot_leave_column():
    with pytest.raises(ParseError, match='escaped'):
        VsbAdapter('teaching').next_page('<a href="bslc/sjjx/1.htm">下页</a>', 'https://jw.cdu.edu.cn/jwgg.htm')


def test_pdf_only_teaching_guide_is_not_published_as_readable_content():
    html = '<form name="_newscontent_fromname"><div class="art-tit"><h3>教务系统登录指南</h3></div><div id="vsb_content"><script>showVsbpdfIframe("guide.pdf")</script><p><br/></p></div></form>'
    with pytest.raises(ParseError, match='body empty, image-only, or too short'):
        VsbAdapter('teaching').parse_article(html, 'https://jw.cdu.edu.cn/info/1043/12082.htm')


def test_teaching_collection_dedup_publication_and_revocation(store, monkeypatch):
    catalog = load_sources()
    source = next(s for s in catalog if s.id == SOURCE_ID)
    store.sync_sources(catalog)
    soup = BeautifulSoup((ROOT / 'list.html').read_bytes(), 'html.parser')
    for row in soup.select('div.text-list li'):
        if row.select_one('a')['href'] != 'info/1043/12471.htm':
            row.decompose()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/robots.txt':
            return httpx.Response(404)
        body = (ROOT / 'article.html').read_bytes() if request.url.path.startswith('/info/') else str(soup).encode()
        return httpx.Response(200, content=body)

    with Fetcher(transport=httpx.MockTransport(handler), interval=0) as fetcher:
        assert collect(store, fetcher, [source], limit=1)[0]['inserted'] == 1
        assert collect(store, fetcher, [source], limit=1)[0]['unchanged'] == 1
    row = store.db.execute('SELECT * FROM notices').fetchone()
    assert row['content_source_id'] == 'department-jw'
    article = VsbAdapter('teaching').parse_article((ROOT / 'article.html').read_bytes(), URL)
    assert store.save_notice('computer', URL, article) == 'unchanged'
    assert len(store.origins(row['id'])) == 2
    plan = AutoRepository(store.db).feed_plan()
    assert len(plan['items']) == 1
    assert plan['items'][0]['source_name'] == '教务处（招生办公室）'
    assert plan['items'][0]['source_url'] == URL
    assert all('/system/_content/' not in path for path in calls)
    assert all(s.status == 'pending' for s in catalog if s.unit_id == 'department-jw' and s.column_url and s.id != SOURCE_ID)
    # Revoking this specific column must still close the publisher gate.
    monkeypatch.setattr('cdu_radar.auto_publish.load_sources', lambda: [replace(s, status='pending') if s.id == SOURCE_ID else s for s in catalog])
    revoked = AutoRepository(store.db).feed_plan()
    assert not revoked['items']
    assert revoked['excluded'][0]['reason'] == 'untrusted_or_missing_source'
    assert store.db.execute('SELECT count(*) FROM notices').fetchone()[0] == 1
