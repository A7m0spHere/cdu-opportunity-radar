"""Bounded official-directory/homepage discovery; never a search-engine crawler.

Writes evidence to ignored data/artifacts. Review before changing package config.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from cdu_radar.http import Fetcher
from cdu_radar.models import RadarError, utc_now

LABEL = re.compile(r"通知|公告|竞赛|创新创业|学术|讲座|学生活动|学生工作|学工|实践教学|科研动态|科技活动|学生出境|出境学习|海外实习|志愿服务|社会实践")


def candidates(html, base):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text("", strip=True).split())
        # A 'more' link may be the only clickable link under a labelled heading.
        if re.fullmatch(r"(?:更多|more)[+>\s]*|[>\s]*更多",title,re.I):
            for parent in list(a.parents)[:4]:
                headings=parent.find_all(["h2","h3","h4","strong"],recursive=True)
                heading=next((h.get_text('',strip=True) for h in headings if LABEL.search(h.get_text())),None)
                if heading:
                    title=heading
                    break
        href = a["href"]
        url = urljoin(base, href)
        parts = urlsplit(url)
        if (not LABEL.search(title) or len(title) > 25 or not title or
                parts.hostname != urlsplit(base).hostname or parts.scheme not in {"http", "https"} or
                "/info/" in parts.path or href.startswith(("#", "javascript:")) or
                not re.search(r"\.(?:htm|html|jsp)$", parts.path, re.I)):
            continue
        result.setdefault(url, {"column_name": title, "column_url": url})
    return list(result.values())


def run():
    output = Path("data/m1-discovery.json")
    raw = Path("artifacts/m1")
    raw.mkdir(parents=True, exist_ok=True)
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"units": [], "columns": []}
    existing = {u["id"]: u for u in previous["units"]}
    with Fetcher(timeout=8, retries=0, trust_env=False) as fetcher:
        html, directory_url = fetcher.fetch("https://www.cdu.edu.cn/yxsz.htm")
        (raw / "directory.html").write_bytes(html)
        soup = BeautifulSoup(html, "html.parser")
        units = []
        for a in soup.select(".content .box .con a[href]"):
            title = a.get_text(strip=True)
            home = urljoin(directory_url, a["href"]) if a["href"] != "#" else None
            host = urlsplit(home).hostname if home else None
            sid = host.split(".")[0] if host else "chemistry-unconfirmed"
            sid = {"bs": "business", "cxcyxy": "innovation"}.get(sid, sid)
            if title in {"基础医学院", "护理学院"}:
                sid = "basic-medicine" if title == "基础医学院" else "nursing"
            parent = a.find_parent("dl")
            parent_unit = None
            if parent:
                first = parent.parent.find("a", recursive=False)
                parent_unit = first.get_text(strip=True) if first else None
            kind = "下属学院" if parent and "学院" in title else "国际部" if title == "国际部" else "医学中心" if title == "医学中心" else "学院平台" if title == "中国-东盟艺术学院" else "学院"
            units.append({"id":sid, "name":title,"unit_type":kind,"parent_unit":parent_unit,
                          "homepage":home,"official_domain":host,"directory_url":directory_url})
        institution = soup.find("a", string=re.compile("组织机构"))
        if institution:
            inst_url = urljoin(directory_url, institution["href"])
            try:
                org, final = fetcher.fetch(inst_url)
                (raw / "organization.html").write_bytes(org)
                parsed = BeautifulSoup(org, "html.parser")
                depts = {}
                for a in parsed.select(".content .box .con a[href]"):
                    name = a.get_text(strip=True)
                    home = urljoin(final, a["href"])
                    host = urlsplit(home).hostname or ""
                    if (re.search("教务处|研究生处|团委|学生处|学生工作|科技处|社科处|国际合作|图书馆", name)
                            and host.endswith(".cdu.edu.cn") and host != "www.cdu.edu.cn"):
                        sid = "department-" + host.split('.')[0]
                        depts.setdefault(sid, {"id":sid,"name":name,"unit_type":"校级部门", "parent_unit":None,
                                                "homepage":home,"official_domain":host,"directory_url":final})
                units.extend(depts.values())
            except RadarError as exc:
                previous["organization_error"] = str(exc)
        previous["checked_at"] = utc_now()
        for unit in units:
            if unit["id"] in existing and existing[unit["id"]].get("checked_at"):
                # Refresh directory names/relationships without losing previous checks.
                existing[unit["id"]].update(unit)
                continue
            checked = {**unit, "checked_at":utc_now(), "accessible":None,"error":None}
            if not unit["homepage"]:
                checked.update(accessible=False,error="official directory provides only # placeholder")
            else:
                try:
                    html, final = fetcher.fetch(unit["homepage"])
                    (raw / (unit["id"] + "_home.html")).write_bytes(html)
                    parsed = BeautifulSoup(html,"html.parser")
                    checked.update(accessible=True,final_homepage=final, page_title=parsed.title.get_text(strip=True) if parsed.title else None)
                    discovered = candidates(html, final)
                    for column in discovered:
                        previous["columns"].append({**column, "unit_id":unit["id"], "discovered_from":final,"checked_at":utc_now(),
                            "accessible":None,"articles_found":None,"parser_supported":False,"collection_status":"pending", "error":None})
                    checked["candidate_count"] = len(discovered)
                except RadarError as exc:
                    checked.update(accessible=False,error=str(exc))
            previous["units"].append(checked)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding="utf-8")
            print(unit["id"], checked["accessible"], checked.get("candidate_count",0), checked["error"], flush=True)
    return previous


if __name__ == "__main__":
    run()
