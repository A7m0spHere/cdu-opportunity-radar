import html
import os
import re
import tempfile
from datetime import datetime, time, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import utc_now

# XML 1.0 excludes most control characters and lone surrogates.
INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_text(value: str) -> str:
    return INVALID_XML.sub("", value)


def build_rss(store, *, limit: int = 30) -> bytes:
    if limit <= 0:
        raise ValueError("limit must be positive")
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")

    def tag(parent, name, text, **attrs):
        ET.SubElement(parent, name, attrs).text = xml_text(text)

    tag(channel, "title", "成都大学原始通知 · M0 技术验证")
    tag(channel, "link", "https://www.cdu.edu.cn/")
    tag(channel, "description", "未经比赛筛选的本地技术验证 Feed，可能包含新闻和公示。")
    tag(channel, "language", "zh-cn")
    tag(channel, "lastBuildDate", format_datetime(datetime.fromisoformat(utc_now())))
    for row in store.feed_rows(limit):
        item = ET.SubElement(channel, "item")
        tag(item, "title", row["title"])
        tag(item, "link", row["canonical_url"])
        tag(item, "guid", row["canonical_url"], isPermaLink="true")
        if row["published_at"]:
            # Original precision is date-only; midnight is RSS serialization convention.
            published = datetime.combine(datetime.fromisoformat(row["published_at"]).date(), time(),
                                         tzinfo=timezone(timedelta(hours=8)))
            date_note = f"网页发布日期：{row['published_at']}（原文仅日期；RSS 时间按北京时间 00:00 表示）"
        else:
            published = datetime.fromisoformat(row["discovered_at"])
            date_note = "网页发布日期：未知；RSS pubDate 使用首次发现时间。"
        tag(item, "pubDate", format_datetime(published))
        origins = store.origins(row["id"])
        origin_note = "；".join(f"{s['college']} / {s['column_name']}" for s in origins)
        text = (f"来源栏目学院：{origin_note}\n官方原文：{row['canonical_url']}\n{date_note}\n"
                f"首次发现（UTC）：{row['discovered_at']}\n\n{row['content'][:400]}")
        tag(item, "description", "<p>" + html.escape(xml_text(text)).replace("\n", "<br/>") + "</p>")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def export_rss(store, output: str | Path, *, limit: int = 30) -> int:
    feed = build_rss(store, limit=limit)
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".rss-", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(feed)
        os.replace(temp_name, destination)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return len(ET.fromstring(feed).findall("./channel/item"))
