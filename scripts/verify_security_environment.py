"""Read-only dependency/CA preflight. No HTTP, model, key or service access."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import ssl
import sys

import certifi
import httpx

FLOOR = (2026, 10, 19)
COMPROMISED_ROOT = 'emSign ECC Root CA - G3'


def patched(version):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', version)
    return bool(match and tuple(map(int, match.groups())) >= FLOOR)


def insecure_settings(package):
    findings=[]
    for path in sorted(Path(package).rglob('*.py')):
        tree=ast.parse(path.read_text('utf-8'))
        for node in ast.walk(tree):
            bad=False
            if isinstance(node, ast.Call):
                bad=any(k.arg=='verify' and isinstance(k.value,ast.Constant) and k.value.value is False
                        for k in node.keywords)
            if isinstance(node, ast.Assign):
                bad=bad or (any(isinstance(t,ast.Attribute) and t.attr=='check_hostname' for t in node.targets)
                            and isinstance(node.value,ast.Constant) and node.value.value is False)
                bad=bad or (isinstance(node.value,ast.Attribute) and node.value.attr=='CERT_NONE')
            if isinstance(node,ast.Attribute) and node.attr=='_create_unverified_context':bad=True
            if bad:findings.append(dict(file=str(path.relative_to(package)),line=node.lineno))
    return sorted(findings,key=lambda item:(item['file'],item['line']))


def context_report(context):
    names=[value for cert in context.get_ca_certs() for rdn in cert.get('subject',())
           for field,value in rdn if field=='commonName']
    return dict(certificate_required=context.verify_mode==ssl.CERT_REQUIRED,
                hostname_checked=context.check_hostname,
                compromised_root_present=COMPROMISED_ROOT.casefold() in {name.casefold() for name in names},
                trusted_ca_count=len(context.get_ca_certs()))


def report(*, require_lock=False, lock=None):
    import cdu_radar
    from cdu_radar.tls import trusted_context
    bundle=Path(certifi.where()).resolve();package=Path(cdu_radar.__file__).resolve().parent
    clean=context_report(httpx.create_ssl_context(trust_env=False))
    effective=context_report(httpx.create_ssl_context(trust_env=True))
    application=context_report(trusted_context())
    findings=insecure_settings(package)
    versions={name:metadata.version(name) for name in ('certifi','httpx','httpcore','beautifulsoup4')}
    pins={};mismatch=[];not_installed=[]
    if require_lock:
        for line in Path(lock).read_text('utf-8').splitlines():
            text=line.split('#',1)[0].strip()
            if not text:continue
            match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)',text)
            if not match:raise ValueError('Expected exact dependency pins')
            name,wanted=match.groups()
            try:actual=metadata.version(name)
            except metadata.PackageNotFoundError:not_installed.append(name);continue
            # Package date versions may normalize leading zeros.
            normalize=lambda s:tuple(int(v) if v.isdigit() else v for v in re.split(r'[._-]',s))
            pins[name]=dict(expected=wanted,actual=actual)
            if normalize(actual)!=normalize(wanted):mismatch.append(name)
    safe_context=lambda value:value['certificate_required'] and value['hostname_checked'] and not value['compromised_root_present']
    passed=patched(versions['certifi']) and safe_context(clean) and safe_context(application) and not findings and not mismatch
    return dict(schema_version=1,observed_at=datetime.now(timezone.utc).isoformat(),passed=bool(passed),
        python=sys.version.split()[0],executable=sys.executable,platform=sys.platform,
        app_version=cdu_radar.__version__,versions=versions,certifi_patched=patched(versions['certifi']),
        minimum_certifi='2026.10.19',ca_file=str(bundle),ca_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        clean_httpx_tls=clean,effective_httpx_tls=effective,
        application_tls=application,ambient_client_safe=safe_context(effective),
        ca_environment_flags={name:bool(os.environ.get(name)) for name in ('SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE')},
        insecure_source_settings=findings,installed_lock_comparison=pins,lock_mismatches=mismatch,
        optional_pins_not_installed=not_installed,network_requests=0,model_requests=0,
        boundary='Project clients bind application_tls explicitly. Ambient HTTPX/OS trust and remote deployments are separate observations.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-lock',action='store_true')
    parser.add_argument('--lock',type=Path,default=Path(__file__).resolve().parents[1]/'requirements-lock.txt')
    parser.add_argument('--output',type=Path,required=True,help='New JSON file')
    args=parser.parse_args(argv)
    try:
        result=report(require_lock=args.require_lock,lock=args.lock)
        with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    except (ValueError,OSError,metadata.PackageNotFoundError):
        print('Security preflight unavailable; verify package, CA and new output paths.',file=sys.stderr);return 2
    print(json.dumps({key:result[key] for key in ('passed','app_version','versions','certifi_patched','lock_mismatches','network_requests')},ensure_ascii=True))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
