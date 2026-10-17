"""Read-only policy checks for deployment drafts, NOT nginx -t or deployment."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.student_page import ASSETS


def validate(directory):
    root = Path(directory)
    read = lambda name: root.joinpath(name).read_text(encoding='utf-8')
    clean = lambda text: '\n'.join(line.split('#', 1)[0] for line in text.splitlines())
    main = clean(read('nginx.conf.draft'))
    routes = clean(read('gateway-server.inc'))
    upstream = clean(read('gateway-upstream.inc'))
    tls = clean(read('nginx.tls.server.draft'))
    service = clean(read('cdu-radar.service.draft'))
    checks = []

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})

    exact = re.findall(r'location\s*=\s*(\S+)\s*\{\s*include gateway-upstream\.inc;\s*\}', routes)
    expected = {*AutoFeedController.paths, *AutoFeedController.retired_paths, *ASSETS, '/health'}
    check('exact_public_route_set', len(exact) == len(set(exact)) and set(exact) == expected)
    check('unknown_paths_closed', 'location / { return 404; }' in routes)
    check('no_filesystem_mapping', not re.search(r'\b(root|alias|try_files|autoindex)\b', main + routes + upstream + tls))
    check('loopback_default', re.findall(r'\blisten\s+([^;]+);', main) == ['127.0.0.1:8870'])
    check('loopback_tls_draft', re.findall(r'\blisten\s+([^;]+);', tls) == ['127.0.0.1:9443 ssl'])
    check('explicit_host_boundary', 'map $http_host $draft_host_allowed' in main
          and 'if ($draft_host_allowed = 0) { return 403; }' in routes)
    check('tls_not_included', 'nginx.tls.server.draft' not in main)
    check('fixed_upstream_only', re.findall(r'proxy_pass\s+([^;]+);', upstream) == ['http://127.0.0.1:8765'])
    check('no_cache_or_stale', 'proxy_cache off;' in upstream and 'proxy_cache_use_stale off;' in upstream
          and not re.search(r'\b(proxy_cache_path|proxy_store|expires)\b', main + routes + upstream))
    check('no_static_error_fallback', 'error_page 502 504 =503 @upstream_unavailable;' in routes
          and 'location @upstream_unavailable { return 503 ' in routes)
    check('read_only_and_no_store', 'if ($request_method !~ ^(GET|HEAD)$) { return 405; }' in routes
          and 'add_header Cache-Control "no-store, max-age=0" always;' in routes)
    check('429_retry_after', 'limit_req_status 429;' in routes and 'map $status $retry_after' in main
          and 'add_header Retry-After $retry_after always;' in routes)
    check('bounded_connections', 'limit_conn service_connections 8;' in routes and 'worker_connections 64;' in main)
    check('no_credentials_or_forwarded_trust', 'proxy_pass_request_headers off;' in upstream
          and 'proxy_pass_request_body off;' in upstream and 'proxy_set_header If-None-Match $http_if_none_match;' in upstream
          and not re.search(r'\b(real_ip_header|set_real_ip_from)\b', main + routes + upstream)
          and set(re.findall(r'proxy_set_header\s+(\S+)', upstream)) == {'Connection', 'Host', 'Content-Length', 'If-None-Match'})
    check('includes_stay_in_draft', set(re.findall(r'\binclude\s+([^;]+);', main + routes + upstream + tls))
          == {'gateway-server.inc', 'gateway-upstream.inc'})
    check('privacy_logs', 'access_log off;' in main and 'error_log logs/error.log crit;' in main)
    check('tls_versions_and_review_paths', 'ssl_protocols TLSv1.2 TLSv1.3;' in tls and '/REVIEW_REQUIRED/' in tls)
    check('systemd_draft_offline_defaults', '--collect-interval 0' in service and '--bind 127.0.0.1' in service
          and '--send' not in service and 'ConditionPathExists=/etc/cdu-radar/ALLOW_NONPRODUCTION_RUN' in service)
    check('systemd_private_storage', 'UMask=0077' in service and 'ProtectSystem=strict' in service
          and 'ReadWritePaths=/srv/cdu-radar/private/live' in service)
    target = json.loads(read('targets.example.json'))
    check('no_assumed_server_or_domain', target['nonproduction_target'] is None and target['server_region'] is None and target['domain'] is None)
    check('no_model_or_public_authorization', target['production_authorized'] is False and target['llm_enabled'] is False
          and target['paid_model_budget'] == 0 and target['public_listener_enabled'] is False
          and target['model_credentials_included'] is False and target['collection_enabled'] is False)
    return {'kind': 'static_draft_policy_only', 'passed': all(c['passed'] for c in checks), 'checks': checks,
            'nginx_syntax_checked': False, 'services_started': False, 'deployment_performed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parents[1] / 'deploy/m9')
    args = parser.parse_args(argv)
    try:
        result = validate(args.directory)
        print(json.dumps(result, indent=2))
        return 0 if result['passed'] else 2
    except (OSError, ValueError, KeyError, TypeError):
        print('Deployment draft checks failed; inspect local drafts.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
