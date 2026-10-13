"""Inspect bounded discovered columns; retain homepage/link provenance."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit,urljoin

import httpx
from bs4 import BeautifulSoup

from cdu_radar.http import Fetcher
from cdu_radar.models import RadarError, utc_now
from scripts.discover_sources import candidates


def run(limit=None):
    path = Path("data/m1-discovery.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    resolve_path = Path("data/m1-dns-overrides.json")
    mapping = json.loads(resolve_path.read_text(encoding="utf-8")) if resolve_path.exists() else {}
    hosts = {u["official_domain"] for u in report["units"] if u["official_domain"]} | {"www.cdu.edu.cn"}
    failed = {u["official_domain"] for u in report["units"] if u.get("accessible") is False and u["official_domain"]}
    # Explicit diagnosis helper only; collector never silently changes DNS.
    with httpx.Client(timeout=10,trust_env=False) as client:
        for host in sorted(failed - set(mapping)):
            try:
                response = client.get("https://dns.google/resolve",params={"name":host,"type":"A"})
                response.raise_for_status()
                answers = [a["data"] for a in response.json().get("Answer",[]) if a["type"]==1]
                if answers:
                    mapping[host] = answers[0]
            except (httpx.HTTPError,ValueError):
                pass
            time.sleep(2)
    resolve_path.write_text(json.dumps(mapping,indent=2),encoding="utf-8")
    with Fetcher(timeout=8,retries=0,trust_env=False,resolve=mapping,allowed_hosts=hosts) as fetcher:
        for unit in report["units"]:
            if unit.get("accessible") is False and unit["official_domain"] in mapping:
                try:
                    html, final = fetcher.fetch(unit["homepage"])
                    Path("artifacts/m1",unit["id"]+"_home.html").write_bytes(html)
                    unit.update(accessible=True, error=None, network_mode="explicit_dns_override", checked_at=utc_now())
                    known = {c["column_url"] for c in report["columns"] if c["unit_id"]==unit["id"]}
                    for column in candidates(html,final):
                        if column["column_url"] not in known:
                            report["columns"].append({**column,"unit_id":unit["id"],"discovered_from":final,"checked_at":utc_now(),
                                "accessible":None,"articles_found":None,"parser_supported":False,"collection_status":"pending","error":None})
                except RadarError as exc:
                    unit.update(error=str(exc),error_category=exc.category)
                path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
                print('unit',unit['id'],unit['accessible'],flush=True)
        attempted=0
        for column in report["columns"]:
            if column.get("column_checked_at"):
                continue
            if limit and attempted>=limit:
                break
            attempted+=1
            column["column_checked_at"]=utc_now()
            try:
                html,final=fetcher.fetch(column['column_url'])
                soup=BeautifulSoup(html,'html.parser')
                anchors=[a for a in soup.find_all('a',href=True) if '/info/' in urljoin(final,a['href'])]
                title=soup.title.get_text(strip=True) if soup.title else ''
                maintenance='页面正在修复中' in soup.get_text()
                column.update(accessible=not maintenance,articles_found=bool(anchors),article_link_count=len(anchors),
                              page_title=title,error='HTML maintenance page' if maintenance else None,
                              error_category='html_maintenance' if maintenance else None)
                filename=column['unit_id']+'_'+str(report['columns'].index(column))+'_list.html'
                Path('artifacts/m1',filename).write_bytes(html)
                column['evidence_file']=filename
            except RadarError as exc:
                column.update(accessible=False,articles_found=None,error=str(exc),error_category=exc.category)
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('column',column['unit_id'],column['column_url'],column['accessible'],column.get('article_link_count'),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    run(args.limit)
