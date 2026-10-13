"""Reproducible, certificate-verified network probes; no crawler fallback."""
import argparse
import json
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def redact_error(exc):
    # HTTPX/proxy exceptions may contain private proxy credentials/URLs.
    text = str(exc)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "[environment proxy]")
    return {"type": type(exc).__name__, "detail": text[:500]}


def diagnose(url, timeout=10, public_dns=False):
    from cdu_radar.models import RadarError, official_url
    url = official_url(url)
    host = urlsplit(url).hostname
    report = {"url": url, "checked_at": datetime.now(timezone.utc).isoformat(), "proxies": {}, "dns": [], "probes": []}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        value = os.environ.get(key)
        if value:
            parsed = urlsplit(value)
            report["proxies"][key] = {"configured": True, "scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port,
                                      "credentials_present": bool(parsed.username or parsed.password)} if "proxy" in key.lower() and key.lower() != "no_proxy" else {"configured": True}
    for family, name in [(socket.AF_INET, "ipv4"), (socket.AF_INET6, "ipv6")]:
        try:
            addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)})
            report["dns"].append({"family": name, "addresses": addresses})
            for address in addresses[:1]:
                started = time.monotonic()
                try:
                    with socket.socket(family, socket.SOCK_STREAM) as sock:
                        sock.settimeout(timeout)
                        sock.connect((address, 443))
                        with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
                            report["probes"].append({"kind": "tls", "family": name, "address": address,
                                "ok": True, "tls_version": tls.version(), "cipher": tls.cipher()[0]})
                except (OSError, ssl.SSLError) as exc:
                    report["probes"].append({"kind": "tls", "family": name, "address": address, "ok": False,
                                             **redact_error(exc), "elapsed_ms": round((time.monotonic()-started)*1000)})
                time.sleep(2)
        except OSError as exc:
            report["dns"].append({"family": name, **redact_error(exc)})
    for mode in ["environment", "direct", "direct_ipv4", "direct_ipv6"]:
        started = time.monotonic()
        kwargs = {"trust_env": mode == "environment"}
        if mode in {"direct_ipv4", "direct_ipv6"}:
            kwargs["transport"] = httpx.HTTPTransport(local_address="0.0.0.0" if mode == "direct_ipv4" else "::")
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, **kwargs) as client:
                response = client.get(url, headers={"User-Agent": "cdu-opportunity-radar/0.1 (network diagnosis; low-frequency)"})
                report["probes"].append({"kind": "http", "mode": mode, "ok": True, "status": response.status_code,
                    "redirect": response.headers.get("location"), "bytes": len(response.content), "elapsed_ms": round((time.monotonic()-started)*1000)})
                if response.status_code == 200 and "text/html" in response.headers.get("content-type", ""):
                    folder = Path("artifacts/m1")
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "innovation.html").write_bytes(response.content)
        except httpx.HTTPError as exc:
            report["probes"].append({"kind": "http", "mode": mode, "ok": False, **redact_error(exc),
                                      "elapsed_ms": round((time.monotonic()-started)*1000)})
        time.sleep(2)
    if public_dns:
        try:
            with httpx.Client(timeout=timeout,trust_env=False) as client:
                response=client.get("https://dns.google/resolve",params={"name":host,"type":"A","edns_client_subnet":"0.0.0.0/0"})
                response.raise_for_status()
                data=response.json()
            addresses=[a["data"] for a in data.get("Answer",[]) if a["type"]==1]
            report["public_dns"]={"provider":"Google Public DNS over HTTPS","status":data.get("Status"),"answers":data.get("Answer",[])}
            if addresses:
                from cdu_radar.http import Fetcher
                with Fetcher(timeout=timeout,retries=0,trust_env=False,resolve={host:addresses[0]}) as fetcher:
                    page,final=fetcher.fetch(url)
                    report["public_dns_request"]={"ok":True,"canonical_url":final,"bytes":len(page),"certificate_verification":True}
        except (httpx.HTTPError,OSError,ValueError,RadarError) as exc:
            report["public_dns_error"]=redact_error(exc)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://cxcyxy.cdu.edu.cn/jyjx/xkjs.htm")
    parser.add_argument("--output", type=Path, default=Path("data/m1-network-diagnosis.json"))
    parser.add_argument("--public-dns",action="store_true",help="Compare Google public DNS and explicitly verify its address with original Host/SNI")
    args = parser.parse_args()
    result = diagnose(args.url,public_dns=args.public_dns)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
