"""Preview or run the two-source M5-B trial in a new private baseline copy."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from cdu_radar.analysis_models import reference_datetime
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher
from cdu_radar.quality import build_report, render_markdown
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store

SOURCE_IDS = {'department-xsc-column-148','department-tuanwei-column-149'}


def write_report(root,name,report):
    (root/(name+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (root/(name+'.md')).write_text(render_markdown(report),encoding='utf-8')


def run(baseline,directory,*,execute=False):
    baseline=Path(baseline).resolve();requested_root=Path(directory);root=requested_root.resolve()
    policy={'sources':sorted(SOURCE_IDS),'pages_per_source':1,'articles_per_source':3,
            'max_http_requests':15,'timeout_seconds':10,'http_retries':0,'interval_seconds':2.5,
            'attachment_downloads':0,'model_calls':0,'baseline_modified':False}
    if not execute:
        return {'mode':'preview','policy':policy,'output_created':False}
    if not baseline.is_file() or requested_root.is_symlink() or root.exists():
        raise ValueError('Baseline must exist and output directory must be NEW')
    catalog=load_sources()
    selected=[s for s in catalog if s.id in SOURCE_IDS and s.status=='verified' and s.parser_supported and s.adapter]
    if {s.id for s in selected}!=SOURCE_IDS:
        raise ValueError('Trial sources must be implemented and verified')
    baseline_hash=hashlib.sha256(baseline.read_bytes()).hexdigest()
    # Validate the source's rollback schema without initialization/migration.
    build_report(baseline,data_mode='offline_replay')
    root.mkdir(parents=True)
    target=root/'notices.sqlite3'
    source=sqlite3.connect(baseline.as_uri()+'?mode=ro',uri=True)
    dest=sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        source.close();dest.close()
    with Store(target) as store:
        store.sync_sources(catalog)
        before_urls={r[0] for r in store.db.execute('SELECT canonical_url FROM notices')}
        with Fetcher(timeout=10,retries=0,interval=2.5,request_limit=15,event_sink=store.request_event) as fetcher:
            results=collect(store,fetcher,selected,limit=3,max_pages=1)
            repeat=collect(store,fetcher,[selected[0]],limit=1,max_pages=1)
            requests=fetcher.request_count
        new_rows=[dict(r) for r in store.db.execute('SELECT id,canonical_url,title,published_at,content_hash FROM notices')
                  if r['canonical_url'] not in before_urls]
    at=reference_datetime()
    before=build_report(baseline,as_of=at,data_mode='offline_replay')
    after=build_report(target,as_of=at,data_mode='mixed_baseline_and_live')
    write_report(root,'before',before);write_report(root,'after',after)
    final_hash=hashlib.sha256(baseline.read_bytes()).hexdigest()
    if final_hash!=baseline_hash:
        raise ValueError('Baseline changed during trial; do not claim a stable comparison')
    successful=(len(results)==len(selected) and all(r.get('success') for r in results)
        and len(repeat)==1 and repeat[0].get('success') and repeat[0].get('unchanged')==1
        and repeat[0].get('inserted')==0 and repeat[0].get('updated')==0)
    summary={'mode':'bounded_live_extension_of_historical_copy','validation_status':'passed' if successful else 'failed',
        'as_of':at.isoformat(),'policy':policy,
        'baseline_sha256_before':baseline_hash,'baseline_sha256_after':final_hash,
        'actual_http_requests':requests,'results':results,'repeat_results':repeat,
        'new_unique_originals':len(new_rows),'new_originals':new_rows,
        'snapshot_delta':{k:after['snapshot'][k]-before['snapshot'][k]
            for k in ('raw_unique_notices','qualified','participation_candidates','possible',
                      'opening_evidence_supported','registration_unknown','closed','historical','administrative')},
        'scope':'Only the two new source columns were fetched; the baseline is historical, not freshly revalidated. '
                'Candidate/opening counts are deterministic evidence buckets, not manual labels or complete eligibility.'}
    (root/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-db',required=True,type=Path)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--run',action='store_true',help='Execute bounded real official requests; default previews only')
    args=parser.parse_args(argv)
    result=run(args.baseline_db,args.directory,execute=args.run)
    print(json.dumps({k:v for k,v in result.items() if k!='new_originals'},ensure_ascii=False,indent=2))
    return 0 if result['mode']=='preview' or result['validation_status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
