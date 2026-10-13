import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import Article, NoticeLink, ParseError, official_url

DATE = re.compile(r"(?<!\d)(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})(?:日)?(?!\d)")
ARTICLE_PATH = re.compile(r"^/info/\d+/\d+\.htm$")


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
        if kind not in {"computer", "business"}:
            raise ParseError(f"Unimplemented adapter: {kind}")
        self.kind = kind

    def parse_list(self, html: bytes | str, url: str) -> list[NoticeLink]:
        soup = BeautifulSoup(html, "html.parser")
        if self.kind == "computer":
            root = soup.select_one(".div_list")
            rows = root.select(".div_title") if root else []
        else:
            root = soup.select_one("ul.jtfylb")
            rows = root.find_all("li", recursive=False) if root else []
        if not rows:
            raise ParseError(f"{self.kind}: notice list structure missing or empty")
        result = {}
        for row in rows:
            anchor = row.find("a", href=True)
            if anchor is None:
                raise ParseError(f"{self.kind}: notice row has no article link")
            target = official_url(urljoin(url, anchor["href"]))
            if not ARTICLE_PATH.fullmatch(urlsplit(target).path):
                raise ParseError(f"{self.kind}: unexpected article path: {target}")
            if self.kind == "computer":
                title = clean_text(anchor.get("title") or anchor.get_text("", strip=True))
                date_node = row.select_one(".date")
            else:
                title_node = anchor.find("p")
                title = clean_text(title_node.get_text("", strip=True)) if title_node else ""
                date_node = anchor.find("span")
            if not title:
                raise ParseError(f"{self.kind}: empty notice title")
            if date_node is None:
                raise ParseError(f"{self.kind}: publication date node missing")
            date_text = clean_text(date_node.get_text("", strip=True))
            published = parse_date(date_text)
            if date_text and published is None:
                raise ParseError(f"{self.kind}: unrecognized publication date: {date_text}")
            result.setdefault(target, NoticeLink(title, target, published))
        return list(result.values())

    def parse_article(self, html: bytes | str, url: str) -> Article:
        soup = BeautifulSoup(html, "html.parser")
        # VSB's content form and IDs are verified from both real sites.
        form = soup.find("form", attrs={"name": "_newscontent_fromname"})
        if not form:
            raise ParseError(f"{url}: VSB article form missing")
        body = form.find(id=re.compile(r"^vsb_content(?:_\d+)?$"))
        if not body:
            raise ParseError(f"{url}: VSB article body missing")
        if self.kind == "computer":
            heading = form.select_one(".div_title")
            if heading:
                title = clean_text("".join(str(t) for t in heading.find_all(string=True, recursive=False)))
            else:
                title = ""
        else:
            heading = form.find("h1")
            title = clean_text(heading.get_text("", strip=True)) if heading else ""
        if not title:
            raise ParseError(f"{url}: article title missing")
        # Find labelled publication metadata outside the body, not a deadline in text.
        published = None
        for text_node in form.find_all(string=re.compile(r"发布时间\s*[：:]")):
            if body in text_node.parents:
                continue
            metadata = re.split(r"发布时间\s*[：:]", str(text_node), maxsplit=1)[1]
            published = parse_date(metadata)
            if published is None:
                raise ParseError(f"{url}: malformed publication metadata")
            break
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
        return Article(title, published, content)


def article_adapter(url: str) -> VsbAdapter:
    host = urlsplit(url).hostname
    by_host = {"computer.cdu.edu.cn": "computer", "bs.cdu.edu.cn": "business"}
    if host not in by_host:
        raise ParseError(f"No verified article adapter for host: {host}")
    return VsbAdapter(by_host[host])
