"""Actual cached v3 results -> public JSON/RSS; no model or source requests."""
import argparse
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cdu_radar.async_cli import configuration
from cdu_radar.review_api import validate_public_v4
from scripts.m10_local_acceptance import serving, rows
from scripts.m10_probe import reader
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--state-db',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args();args.directory.mkdir(parents=True,exist_ok=False)
    analyzer,_=configuration(args.config,False)
    analyzer.provider.complete=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Public HTTP attempted a model call'))
    before=dict(notices=rows(args.db,'notices'),publications=rows(args.db,'auto_publications'),attempts=rows(args.state_db,'attempts'))
    state=args.directory/'publisher';results={};identities={}
    with serving(args.db,state,jobs=args.state_db,analyzer=analyzer) as (_,base):
        with httpx.Client(timeout=10,trust_env=False) as client:
            for audience in ('all','undergraduate'):
                for selection in ('all','featured'):
                    query=f'?audience={audience}'
                    api=client.get(base+'/api/notices/v4'+query+'&selection='+selection)
                    rss=client.get(base+'/rss/v2/'+selection+'.xml'+query)
                    assert api.status_code==200
                    payload=api.json();validate_public_v4(payload)
                    guids=sorted(item['guid'] for item in payload['items'])
                    comparable=rss.status_code==200
                    if comparable:
                        root=ET.fromstring(rss.content);entries=root.findall('./channel/item')
                        assert guids==sorted(item.findtext('guid') for item in entries)
                        identities[audience+'_'+selection]=sorted((item.findtext('guid'),item.findtext('pubDate')) for item in entries)
                    else:assert selection=='featured' and rss.status_code==503
                    key=audience+'_'+selection
                    results[key]=dict(json_status=api.status_code,rss_status=rss.status_code,items=len(guids),
                        classification_llm_reviewed=sum(item['classification']['method']=='llm_reviewed' for item in payload['items']),
                        consistent=comparable,coverage=payload['featured']['coverage'])
                    (args.directory/(key+'.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
                    (args.directory/(key+'.xml')).write_bytes(rss.content)
    first=rows(state/'subscriptions.sqlite3','first_featured')
    with serving(args.db,state,jobs=args.state_db,analyzer=analyzer) as (_,base):
        with httpx.Client(timeout=10,trust_env=False) as client:
            for key,old in identities.items():
                audience,selection=key.split('_')
                response=client.get(base+'/rss/v2/'+selection+'.xml?audience='+audience)
                assert response.status_code==200
                current=sorted((item.findtext('guid'),item.findtext('pubDate')) for item in ET.fromstring(response.content).findall('./channel/item'))
                assert current==old
    assert before['notices']==rows(args.db,'notices') and before['publications']==rows(args.db,'auto_publications')
    assert before['attempts']==rows(args.state_db,'attempts') and first==rows(state/'subscriptions.sqlite3','first_featured')
    result=dict(pipeline=analyzer.version,projections=results,raw_notices_preserved=True,usage_ledger_preserved=True,
                guid_pubdate_preserved=True,first_featured_preserved=True,model_requests=0,official_requests=0,listeners_stopped=True)
    (args.directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
