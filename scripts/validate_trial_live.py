"""One bounded real collection into a NEW private DB; no model or production API."""
import argparse
from collections import Counter
import json
import logging
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from cdu_radar.analysis_models import AnalysisInput, fingerprint, reference_datetime
from cdu_radar.auto_publish import AutoRepository
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher, FetchError
from cdu_radar.rule_analysis import RuleAnalyzer
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store


class BoundedFetcher(Fetcher):
    def __init__(self,**kwargs):
        super().__init__(timeout=10,retries=0,interval=2.5,**kwargs)
        self.requests=0;self.started=time.monotonic()

    def _request(self,url):
        if self.requests>=45 or time.monotonic()-self.started>360:
            raise FetchError('Controlled collection budget exhausted','budget','request',False)
        self.requests+=1
        return super()._request(url)


def run(directory):
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Choose a NEW isolated directory')
    root.mkdir(parents=True)
    logging.basicConfig(filename=root/'collection-private.log',level=logging.INFO,encoding='utf-8')
    catalog=load_sources()
    selected=[s for s in catalog if s.status=='verified' and s.parser_supported and s.adapter]
    point=reference_datetime()
    with Store(root/'notices.sqlite3') as store:
        store.sync_sources(catalog)
        with BoundedFetcher(event_sink=store.request_event) as fetcher:
            results=collect(store,fetcher,selected,limit=2,max_pages=1)
            repeat_source=next((s for s in selected if s.id=='computer' and any(r['source']==s.id and r['inserted'] for r in results)),None)
            if repeat_source is None:
                repeat_source=next((s for s in selected if any(r['source']==s.id and r['inserted'] for r in results)),None)
            repeat=collect(store,fetcher,[repeat_source],limit=1,max_pages=1) if repeat_source else []
            requests=fetcher.requests
        now=reference_datetime();plan=AutoRepository(store.db).feed_plan(now)
        analyzer=RuleAnalyzer();audit=[]
        kinds={'competition','hackathon','project_recruitment','workshop','lecture','other_activity'}
        for item in plan['items']:
            row=store.db.execute('SELECT * FROM notices WHERE id=?',(item['notice_id'],)).fetchone()
            notice=AnalysisInput(row['id'],row['content_hash'],fingerprint(dict(row)),row['title'],row['content'],
                row['canonical_url'],(item['source_name'],),row['published_at'],content_source=item['source_name'])
            result=analyzer.analyze(notice,reference_time=now)
            related=result.category in kinds and item['listing']!='history' and item['category']!='其他通知'
            # Stronger audit bucket than the product's "possible": explicit
            # opening evidence AND student audience; never infer all eligibility.
            start=item['facts']['registration_start_date']
            opening=(start and reference_datetime(start['value'])<=now) or bool(re.search(r'即日起|现开始报名|正在报名|报名已开始',row['content']))
            students=bool(item['audience'] and re.search(r'学生|本科生|专科生|研究生',item['audience']))
            confirmed=bool(related and item['registration_status']=='possible' and opening and students)
            non_opportunity=(item['listing']=='history' or result.category in {'news','administrative'} or item['registration_status']=='closed'
                or bool(re.search(r'(?:荣誉称号|推荐名单|奖学金|助学金).*公示',row['title'])))
            response=store.db.execute('SELECT status_code,error,requested_at FROM request_events WHERE url=? ORDER BY id DESC LIMIT 1',(item['source_url'],)).fetchone()
            audit.append({'notice_id':row['id'],'title':item['title'],'source':item['source_name'],'url':item['source_url'],
                'source_date':item['source_published_date'],'category':item['category'],'rule_kind':result.category,
                'registration_status':item['registration_status'],'listing':item['listing'],
                'participation_related_candidate':related,'confirmed_registration_open':confirmed,
                'expired_or_non_participation':non_opportunity,'deadline':item['facts']['registration_deadline'],
                'audience_quote':item['audience'],'link_check':dict(response) if response else None})
        counts={'latest_aggregated':len(audit),'participation_related_candidates':sum(i['participation_related_candidate'] for i in audit),
            'confirmed_registration_open':sum(i['confirmed_registration_open'] for i in audit),
            'registration_unknown':sum(i['registration_status']=='unknown' for i in audit),
            'expired_or_non_participation':sum(i['expired_or_non_participation'] for i in audit)}
        report={'mode':'bounded_real_collection','started_at':point.isoformat(),'finished_at':now.isoformat(),
            'policy':{'sources':[s.id for s in selected],'article_limit_per_source':2,'pages_per_source':1,
                'max_requests':45,'max_seconds_before_next_request':360,'timeout_seconds':10,'retries':0,'interval_seconds':2.5,
                'attachments_downloaded':0,'paid_llm_calls':0,'production_connections':0},
            'request_count':requests,'results':results,'repeat_results':repeat,
            'raw_notices':store.db.execute('SELECT count(*) FROM notices').fetchone()[0],
            'parse_failures':sum(r['parse_failures'] for r in [*results,*repeat]),
            'listings':plan['listings'],'statuses':plan['statuses'],'excluded':plan['excluded'],
            'quality_counts':counts,'groups_overlap':True,
            'count_semantics':'Latest is the bounded collected set, not all campus notices. Unknown may overlap historical/non-participation; confirmed opening is stricter than possible.',
            'classification_audit':audit,'guid_dedup_by_final_url':True}
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('request_count','raw_notices','parse_failures','quality_counts','listings','statuses','excluded')},ensure_ascii=True),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,type=Path)
    args=parser.parse_args()
    run(args.directory)
