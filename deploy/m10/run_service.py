"""Fail-closed local bootstrap; --check-only never binds or performs requests."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_security_environment import report


def settings():
    raw=Path(os.environ.get('CDU_RAW_DB','/var/lib/cdu-radar/raw.sqlite3')).resolve()
    jobs=Path(os.environ.get('CDU_ANALYSIS_DB','/var/lib/cdu-radar/jobs.sqlite3')).resolve()
    state=Path(os.environ.get('CDU_STATE_DIR','/var/lib/cdu-radar/http-state')).resolve()
    config=Path(os.environ.get('CDU_ANALYSIS_CONFIG','/etc/cdu-radar/analysis.json')).resolve()
    enabled=os.environ.get('CDU_ENABLE_LLM','0')
    if enabled not in {'0','1'}:raise ValueError('CDU_ENABLE_LLM must be 0 or 1')
    port=int(os.environ.get('CDU_PORT','8765'))
    interval=float(os.environ.get('CDU_COLLECT_INTERVAL','0'))
    if not 1024<=port<=65535 or interval!=0 and not 60<=interval<=86400:
        raise ValueError('Invalid local port or collection interval')
    if not raw.is_file():raise ValueError('Restore the raw database first')
    if state==raw or state in raw.parents or jobs==raw:
        raise ValueError('Database and publisher roles must be independent')
    args=['-m','cdu_radar','serve-rss','--db',str(raw),'--state-dir',str(state),
          '--bind','127.0.0.1','--port',str(port),'--collect-interval',str(interval)]
    configured=jobs.is_file() and config.is_file()
    if configured:
        from cdu_radar.async_cli import configuration
        from cdu_radar.async_queue import JobQueue
        analyzer,_=configuration(config,False)
        if analyzer.config.analysis_mode!='automatic_review_v3':raise ValueError('Expected automatic v3 configuration')
        with JobQueue(jobs,raw,read_only=True) as queue:queue.ensure_source()
        args+=['--analysis-state',str(jobs),'--analysis-config',str(config)]
    if enabled=='1':
        if not configured:raise ValueError('Model worker requires its restored queue and configuration')
        if not os.environ.get(analyzer.config.key_env,'').strip():raise ValueError('Model key environment is not configured')
        args+=['--analysis-worker']
    return args,dict(raw_db=str(raw),analysis_configured=configured,model_worker=enabled=='1',
                     collection_interval=interval,bind='127.0.0.1',port=port)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    try:
        security=report(require_lock=True,lock=ROOT/'requirements-lock.txt')
        if not security['passed']:raise ValueError('Dependency/CA preflight failed')
        command,summary=settings()
        if args.check_only:
            print(json.dumps(dict(passed=True,security_version=security['versions']['certifi'],**summary,
                                 listeners_opened=0,model_requests=0),ensure_ascii=True));return 0
        os.execv(sys.executable,[sys.executable,*command])
    except (ValueError,OSError):
        print('Service preflight refused; verify patched dependencies, restored paths and private configuration.',file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
