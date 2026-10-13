from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import feedparser
import pytest

from cdu_radar.cli import main
from cdu_radar.models import Article
from cdu_radar.rss import build_rss, export_rss
from cdu_radar.sources import load_sources


def test_rss_reader_xml_chinese_escaping_and_unknown_date(store, tmp_path):
    url = "https://computer.cdu.edu.cn/info/1/1.htm?x=1&y=2"
    article = Article('中文 AI & 创作 <测试>', None, '中文正文 <script>alert(1)</script> & 特殊字符\x00内容')
    store.save_notice("computer", url, article, "2026-10-13T02:03:04+00:00")
    store.save_notice("business", url, article, "2026-10-14T02:03:04+00:00")
    output = tmp_path / "feed.xml"
    assert export_rss(store, output, limit=10) == 1
    xml = ET.parse(output)
    item = xml.find("./channel/item")
    assert item.findtext("title") == article.title
    assert item.findtext("link") == url
    assert item.find("guid").attrib["isPermaLink"] == "true"
    assert parsedate_to_datetime(item.findtext("pubDate")).isoformat() == "2026-10-13T02:03:04+00:00"
    assert "网页发布日期：未知" in item.findtext("description")
    assert "计算机学院" in item.findtext("description") and "商学院" in item.findtext("description")
    assert "&lt;script&gt;" in item.findtext("description")
    feed = feedparser.parse(output.read_bytes())
    assert not feed.bozo
    assert len(feed.entries) == 1
    assert feed.entries[0].title == article.title
    assert "alert(1)" in feed.entries[0].summary


def test_published_date_and_feed_limit(store):
    for i, date in enumerate(["2026-10-01", "2026-10-03", "2026-10-02"]):
        store.save_notice("computer", f"https://computer.cdu.edu.cn/info/1/{i}.htm", Article(f"标题{i}",date,"通知正文"))
    xml = ET.fromstring(build_rss(store, limit=2))
    items = xml.findall("./channel/item")
    assert [i.findtext("title") for i in items] == ["标题1", "标题2"]
    assert parsedate_to_datetime(items[0].findtext("pubDate")).isoformat() == "2026-10-03T00:00:00+08:00"
    assert "原文仅日期" in items[0].findtext("description")


def test_empty_feed_is_valid_rss(store):
    feed = feedparser.parse(build_rss(store))
    assert not feed.bozo and feed.version == "rss20"
    assert not feed.entries


def test_cli_export_missing_db(tmp_path, capsys):
    assert main(["export-rss", "--db", str(tmp_path / "absent.db")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_cli_refuses_overwriting_database(store):
    db = store.db.execute("PRAGMA database_list").fetchone()[2]
    assert main(["export-rss", "--db", db, "--output", db]) == 2
    assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("args", [["collect", "--limit", "0"], ["export-rss", "--limit", "-1"], ["collect", "--source", "typo"]])
def test_cli_rejects_invalid_input(args):
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2


def test_cli_sources_and_export_no_network(store, tmp_path, capsys):
    db = store.db.execute("PRAGMA database_list").fetchone()[2]
    assert main(["sources", "--db", db]) == 0
    assert "化学化工学院" in capsys.readouterr().out
    output = str(tmp_path / "feed.xml")
    assert main(["export-rss", "--db", db, "--output", output]) == 0
    assert '"items": 0' in capsys.readouterr().out


def test_source_catalog_does_not_invent_unknown_url():
    sources = load_sources()
    assert len(sources) >= 30
    chemistry = next(s for s in sources if s.college == "化学化工学院")
    assert chemistry.homepage is None and chemistry.column_url is None
    innovation = next(s for s in sources if s.id == "innovation")
    assert innovation.adapter == "innovation" and innovation.parser_supported
