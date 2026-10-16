import re
import logging
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import Article, ArticleLink, NoticeLink, ParseError, SourceUnavailable, official_url

DATE = re.compile(r"(?<!\d)(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})(?:日)?(?!\d)")
ARTICLE_PATH = re.compile(r"^/info/\d+/\d+\.htm$")
LOG = logging.getLogger(__name__)
LIST_SPECS = {
    "campus": ("div[id^='div_items_id_']", ".div_itemtitle", None, ".other"),
    "innovation": ("div.right ul.list", "li", None, "span.sspan"),
    "art": ("ul.list-ul", "li", None, "span.sspan"),
    "film": ("ul.list-ul", "li", None, "span.sspan"),
    "pharmacy": (".newlist1 > ul.list", "li", "h3", "span"),
    "electronics": ("div.list > ul", "li", None, "i"),
    "construction": ("div.list > ul", "li", None, "i"),
}
HEADINGS = {"innovation":"h3", "art":".article-title", "film":".article-title",
            "pharmacy":"h2.tit", "electronics":".content-title h4", "construction":".conth1", "teaching":".art-tit h3"}


def clean_text(value: str) -> str:
    return " ".join(value.split())


def parse_date(text: str) -> str | None:
    match = DATE.search(text)
    if not match:
        return None
    try:
        return date(*(int(n) for n in match.groups())).isoformat()
    except ValueError as exc:
        raise ParseError(f"Invalid publication date: {match.group(0)}") from exc


class VsbAdapter:
    """Two observed templates; never scan the whole page for arbitrary dates."""

    def __init__(self, kind: str):
        if kind not in {"computer", "business", "education", "teaching"} | set(LIST_SPECS):
            raise ParseError(f"Unimplemented adapter: {kind}")
        self.kind = kind

    def parse_list(self, html: bytes | str, url: str) -> list[NoticeLink]:
        soup = BeautifulSoup(html, "html.parser")
        if self.kind in {"computer","education"}:
            root = soup.select_one(".div_list")
            rows = root.select(".div_title") if root else []
        elif self.kind == "business":
            root = soup.select_one("ul.jtfylb")
            rows = root.find_all("li", recursive=False) if root else []
        else:
            if self.kind not in LIST_SPECS:
                raise ParseError(f"List template not implemented: {self.kind}")
            root_selector, row_selector, _, _ = LIST_SPECS[self.kind]
            root = soup.select_one(root_selector)
            rows = root.select(row_selector) if root else []
        if not rows:
            raise ParseError(f"{self.kind}: notice list structure missing or empty")
        result = {}
        for row in rows:
            anchor = row.find("a", href=True)
            if anchor is None:
                raise ParseError(f"{self.kind}: notice row has no article link")
            target = official_url(urljoin(url, anchor["href"]))
            if not ARTICLE_PATH.fullmatch(urlsplit(target).path) and self.kind in {"computer","business","education","campus"}:
                raise ParseError(f"{self.kind}: unexpected article path: {target}")
            if self.kind in {"computer","education"}:
                title = clean_text(anchor.get("title") or anchor.get_text("", strip=True))
                date_node = row.select_one(".date")
            elif self.kind == "business":
                title_node = anchor.find("p")
                title = clean_text(title_node.get_text("", strip=True)) if title_node else ""
                date_node = anchor.find("span")
            else:
                _,_,title_selector,date_selector = LIST_SPECS[self.kind]
                title_node = anchor.select_one(title_selector) if title_selector else anchor
                title = clean_text(anchor.get("title") or (title_node.get_text("",strip=True) if title_node else ""))
                date_node = row.select_one(date_selector)
            if not title:
                raise ParseError(f"{self.kind}: empty notice title")
            if date_node is None:
                raise ParseError(f"{self.kind}: publication date node missing")
            date_text = clean_text(date_node.get_text("", strip=True))
            if self.kind in {"art","film"}:
                year_node = date_node.find("font")
                month_day = clean_text("".join(str(t) for t in date_node.find_all(string=True,recursive=False)))
                if year_node is None or not re.fullmatch(r"\d{2}\.\d{2}",month_day):
                    raise ParseError(f"{self.kind}: split publication date structure changed")
                date_text = year_node.get_text(strip=True) + "-" + month_day.replace(".","-")
            published = parse_date(date_text)
            if date_text and published is None:
                raise ParseError(f"{self.kind}: unrecognized publication date: {date_text}")
            result.setdefault(target, NoticeLink(title, target, published))
        return list(result.values())

    def next_page(self, html, url, column_url=None):
        soup = BeautifulSoup(html,"html.parser")
        anchor = next((a for a in soup.find_all("a",href=True) if clean_text(a.get_text()) in {"下页","下一页"}),None)
        if anchor is None:
            return None
        target = official_url(urljoin(url,anchor["href"]))
        base = urlsplit(column_url or url)
        parts = urlsplit(target)
        # All observed VSB pagination stays under the original column stem.
        prefix = base.path.rsplit(".",1)[0] + "/"
        if (parts.scheme != base.scheme or parts.netloc != base.netloc or
                not parts.path.startswith(prefix) or not re.fullmatch(r"\d+\.htm",parts.path[len(prefix):]) or parts.query):
            raise ParseError(f"Pagination escaped configured column: {target}")
        return target

    def parse_article(self, html: bytes | str, url: str) -> Article:
        soup = BeautifulSoup(html, "html.parser")
        # VSB's content form and IDs are verified from both real sites.
        form = soup.find("form", attrs={"name": "_newscontent_fromname"})
        if not form:
            text=soup.get_text(' ',strip=True)
            if len(text)<400 and re.search(r'页面正在修复中，请稍后重试|系统维护中，请稍后再试',text):
                raise SourceUnavailable('Official article currently shows a maintenance page')
            raise ParseError(f"{url}: VSB article form missing")
        body = form.find(id=re.compile(r"^vsb_content(?:_\d+)?$"))
        if not body:
            raise ParseError(f"{url}: VSB article body missing")
        if self.kind in {"computer","education","campus"}:
            heading = form.select_one(".div_title")
            if heading:
                title = clean_text("".join(str(t) for t in heading.find_all(string=True, recursive=False)))
            else:
                title = ""
        elif self.kind == "business":
            heading = form.find("h1")
            title = clean_text(heading.get_text("", strip=True)) if heading else ""
        else:
            heading = form.select_one(HEADINGS[self.kind])
            title = clean_text(heading.get_text("",strip=True)) if heading else ""
        if not title:
            raise ParseError(f"{url}: article title missing")
        # Find labelled publication metadata outside the body, not a deadline in text.
        published = None
        label = r"(?:发布时间|时间|日期)\s*[：:]"
        for text_node in form.find_all(string=re.compile(label)):
            if body in text_node.parents:
                continue
            metadata = re.split(label, str(text_node), maxsplit=1)[1]
            published = parse_date(metadata)
            if published is None:
                raise ParseError(f"{url}: malformed publication metadata")
            break
        def readable_link(anchor):
            value = urljoin(url,anchor.get("href",""))
            parts = urlsplit(value)
            if parts.scheme not in {"http","https"} or not parts.hostname or parts.username or parts.password:
                return None
            return ArticleLink(clean_text(anchor.get_text("",strip=True)),value)
        links = tuple(link for a in body.find_all("a",href=True) if (link:=readable_link(a)))
        attachments = tuple(link for a in form.find_all("a",href=True)
            if ("/system/_content/download.jsp" in a["href"] or re.search(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar)(?:\?|$)",a["href"],re.I))
            and (link:=readable_link(a)))
        for unwanted in body.select("script, style, noscript, img, iframe"):
            unwanted.decompose()
        # Keep inline Chinese spans joined, while retaining paragraph/table boundaries.
        for block in body.find_all(["p", "div", "li", "tr", "h1", "h2", "h3"]):
            block.append("\n")
        for br in body.find_all("br"):
            br.replace_with("\n")
        for cell in body.find_all(["td", "th"]):
            cell.append(" ")
        content = "\n".join(line for raw in body.get_text().splitlines() if (line := clean_text(raw)))
        if len(content) < 20:
            raise ParseError(f"{url}: body empty, image-only, or too short")
        return Article(title, published, content,links,attachments)


def article_adapter(url: str) -> VsbAdapter:
    from .sources import content_source
    _,template = content_source(url)
    return VsbAdapter(template)
