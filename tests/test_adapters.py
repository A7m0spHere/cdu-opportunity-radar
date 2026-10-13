import pytest
from bs4 import BeautifulSoup

from cdu_radar.adapters import VsbAdapter, article_adapter
from cdu_radar.models import ParseError, RadarError, official_url


@pytest.mark.parametrize("kind,title,date,url", [
    ("computer", "我院学子在全国大学生嵌入式芯片与系统设计竞赛中斩获全国三等奖", "2026-09-14",
     "https://computer.cdu.edu.cn/info/1036/12341.htm"),
    ("business", "【评优评奖】成都大学商学院关于2025-2026学年研究生国家奖学金拟推荐人选的公示", "2026-10-13",
     "https://bs.cdu.edu.cn/info/1041/13254.htm"),
])
def test_real_list_excerpt(sample, kind, title, date, url):
    base = "https://computer.cdu.edu.cn/cxcy/xkjs.htm" if kind == "computer" else "https://bs.cdu.edu.cn/xsgz2/tzgg.htm"
    links = VsbAdapter(kind).parse_list(sample(kind + "_list.html"), base)
    assert len(links) == 3
    assert (links[0].title, links[0].published_at, links[0].canonical_url) == (title, date, url)


@pytest.mark.parametrize("kind,date,expected", [
    ("computer", "2026-09-14", "根据中国数学会的通知精神"),
    ("business", "2026-10-13", "按照公开、公平、公正原则"),
])
def test_real_body_excerpt(sample, kind, date, expected):
    article = VsbAdapter(kind).parse_article(sample(kind + "_article.html"), "https://computer.cdu.edu.cn/info/1/1.htm")
    assert article.published_at == date
    assert expected in article.content
    assert "查看" not in article.content and "<script" not in article.content
    assert article.title.startswith("【")


def test_full_title_and_duplicate_links(sample):
    soup = BeautifulSoup(sample("computer_list.html"), "html.parser")
    duplicate = BeautifulSoup(str(soup.select_one(".div_item")), "html.parser")
    soup.select_one(".div_list").append(duplicate)
    # Display truncation must not replace a full title available in the title attribute.
    soup.select_one(".div_title a").string = "我院学子..."
    links = VsbAdapter("computer").parse_list(str(soup), "https://computer.cdu.edu.cn/cxcy/xkjs.htm")
    assert len(links) == 3
    assert "斩获全国三等奖" in links[0].title


@pytest.mark.parametrize("html", ["<html>页面正在修复中</html>", "<div class='div_list'></div>"])
def test_structure_missing_is_error(html):
    with pytest.raises(ParseError, match="structure missing"):
        VsbAdapter("computer").parse_list(html, "https://computer.cdu.edu.cn/cxcy/xkjs.htm")


def test_missing_date_node_is_structure_error(sample):
    text = sample("computer_list.html").decode().replace('class="date"', 'class="changed"')
    with pytest.raises(ParseError, match="date node missing"):
        VsbAdapter("computer").parse_list(text, "https://computer.cdu.edu.cn/cxcy/xkjs.htm")


def test_invalid_calendar_date(sample):
    text = sample("computer_list.html").decode().replace("2026-09-14", "2026-02-30")
    with pytest.raises(ParseError, match="Invalid publication date"):
        VsbAdapter("computer").parse_list(text, "https://computer.cdu.edu.cn/cxcy/xkjs.htm")


def test_empty_publication_date_allowed(sample):
    text = sample("computer_list.html").decode().replace("2026-09-14", "")
    assert VsbAdapter("computer").parse_list(text, "https://computer.cdu.edu.cn/cxcy/xkjs.htm")[0].published_at is None


def test_body_and_title_change_fail_explicitly(sample):
    text = sample("computer_article.html").decode()
    with pytest.raises(ParseError, match="body missing"):
        VsbAdapter("computer").parse_article(text.replace('id="vsb_content_2"', 'id="changed"'), "url")
    with pytest.raises(ParseError, match="title missing"):
        VsbAdapter("computer").parse_article(text.replace('class="div_title"', 'class="changed"'), "url")


def test_deadline_is_not_publication_date(sample):
    text = sample("computer_article.html").decode().replace("发布时间：2026年09月14日", "")
    text = text.replace("</div></div>", "<p>报名截止：2026年10月31日</p></div></div>")
    assert VsbAdapter("computer").parse_article(text, "url").published_at is None


def test_inline_chinese_is_not_split_by_spans(sample):
    text = sample("business_article.html").decode().replace("按照公开", "<span>按照</span><span>公开</span>")
    assert "按照公开" in VsbAdapter("business").parse_article(text, "url").content


def test_navigation_is_not_article_body():
    with pytest.raises(ParseError, match="form missing"):
        VsbAdapter("computer").parse_article("<h1>网站标题</h1><div>很多网站导航和页脚信息</div>", "url")


def test_image_only_not_stored():
    html = '<form name="_newscontent_fromname"><div class="div_title">标题</div><div id="vsb_content_2"><img src="x"></div></form>'
    with pytest.raises(ParseError, match="too short"):
        VsbAdapter("computer").parse_article(html, "url")


@pytest.mark.parametrize("url", ["https://evil.test/info/1/1.htm", "https://cdu.edu.cn.evil.test/a", "https://u:p@computer.cdu.edu.cn/a", "file:///tmp/a", "https://computer.cdu.edu.cn:invalid/a", "https://[invalid/a"])
def test_only_official_urls(url):
    with pytest.raises(RadarError):
        official_url(url)


def test_unknown_template_is_not_guessed():
    with pytest.raises(ParseError, match="No verified"):
        article_adapter("https://yj.cdu.edu.cn/info/1/1.htm")
