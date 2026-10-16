"""New synthetic-only dataset, benchmark and optional loopback browser fixture.

Private control-file modes exist only in this owned test script, never HTTP.
No model transport, official-page requests or original user dataset writes.
"""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import threading
import time

from cdu_radar.analysis_models import reference_datetime
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_cli import configuration
from cdu_radar.async_contract import mock_payload, validate
from cdu_radar.async_queue import JobQueue
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.featured_benchmark import export_dataset, import_labels, evaluate_dataset, COLUMNS
from cdu_radar.models import Article
from cdu_radar.rss_service import make_server
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store

NOW=reference_datetime('2026-10-16T19:00:00+08:00')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args(argv);directory=args.directory.resolve();directory.mkdir(parents=True,exist_ok=False)
    fixture=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/featured_cases.json').read_text(encoding='utf-8'))
    assert fixture['origin']=='synthetic_offline_only';cases=fixture['cases']
    raw=directory/'synthetic.sqlite3';state=directory/'analysis.sqlite3';a,_=configuration(None,True)
    def proposal(n,level):
        s,_=a.prepare(n);p=mock_payload(s)
        p['importance']={'kind':'inference','level':level,'reason':'合成模型建议，用于软件验收，不是真实模型效果','quotes':[n.content]}
        return validate(p,n,s,a,NOW)
    with Store(raw) as store:
        store.sync_sources(load_sources())
        for i,r in enumerate(cases,1):
            host='bs' if r['publisher']=='business' else 'computer'
            url=f'https://{host}.cdu.edu.cn/info/1036/{990000+i}.htm'
            store.save_notice(r['publisher'],url,Article(r['title'],r['published'],r['content']),seen_at=NOW.isoformat())
        store.save_notice('business','https://computer.cdu.edu.cn/info/1036/990001.htm',
            Article(cases[0]['title'],cases[0]['published'],cases[0]['content']),seen_at=NOW.isoformat())
        repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=store.db
        results={i:proposal(repo.input(i),'low' if r['model_level'] in {'queued','failed'} else r['model_level']) for i,r in enumerate(cases,1)}
        unknown_results={}
        for i in range(1,len(cases)+1):
            n=repo.input(i);s,_=a.prepare(n)
            unknown_results[i]=validate(mock_payload(s),n,s,a,NOW)
    with JobQueue(state,raw) as queue:
        queue.scan(a)
        for i,r in enumerate(cases,1):
            status=r['model_level'] if r['model_level'] in {'queued','failed'} else results[i]['state']
            queue.db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=?',(status,json.dumps(results[i],ensure_ascii=False),i))
        baseline=[dict(r) for r in queue.db.execute('SELECT notice_id,state,result FROM jobs ORDER BY id')]
    dataset=directory/'benchmark';export_dataset(raw,dataset,reference_time=NOW)
    with (dataset/'labels.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    for row in rows:
        r=cases[int(row['sample_id'])-1]
        row.update(objective_featured=str(r['truth']).lower(),importance='high' if r['truth'] else 'low',
            criterion_codes=r['criterion'],evidence_quote=r['content'] if r['truth'] else '',
            personal_relevance_note='独立于客观重要性，未设置个人偏好',notes='合成边界标签，不是真实人工真值',
            label_origin='synthetic',confirmed_guideline='true')
    with (dataset/'synthetic-labels.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)
    labels=directory/'synthetic-labels.sqlite3'
    import_labels(dataset/'dataset.json',dataset/'synthetic-labels.csv',labels,labeler='synthetic-software-fixture',allow_synthetic=True)
    report=evaluate_dataset(dataset/'dataset.json',raw,labels_db=labels,state_db=state,analyzer=a,split='all',include_synthetic=True)
    assert report['summary']['full_benchmark_metrics']['confusion_matrix']=={'tp':3,'fp':0,'fn':2,'tn':2}
    default=evaluate_dataset(dataset/'dataset.json',raw,labels_db=labels,state_db=state,analyzer=a,split='all')
    assert default['summary']['valid_labels']==0
    (directory/'synthetic-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'mode':'synthetic_offline_acceptance','counts':report['summary'],
        'actual_model_requests':0,'school_requests':0,'human_truth_count':0},ensure_ascii=False),flush=True)
    if not args.serve:return 0
    controller=AutoFeedController(raw,directory/'http-state',data_mode='offline_replay',analysis_state=state,
        analysis_analyzer=a,clock=lambda:NOW,max_age=30)
    server=make_server(controller,port=args.port);controller.start(.5)
    worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True);worker.start()
    (directory/'preview.json').write_text(json.dumps({'mode':'synthetic_offline_browser','port':server.server_address[1]}),encoding='utf-8')
    current=None
    try:
        while not (directory/'stop').exists():
            mode=(directory/'mode').read_text().strip() if (directory/'mode').exists() else 'mixed'
            if mode!=current and mode in {'mixed','ready','pending','unknown','degraded','source_fault','withdrawn'}:
                controller.db=directory/'intentionally-missing.sqlite3' if mode=='source_fault' else raw
                with sqlite3.connect(raw) as db:db.execute("UPDATE notice_health SET status=? WHERE notice_id=1",('withdrawn' if mode=='withdrawn' else 'valid',))
                with sqlite3.connect(state) as db:
                    for b in baseline:
                        ident=b['notice_id'];status=b['state'];payload=b['result']
                        if mode=='degraded':status='failed'
                        if mode=='pending':status='queued'
                        if mode=='ready' and ident in {6,7}:status=results[ident]['state'];payload=json.dumps(results[ident],ensure_ascii=False)
                        if mode=='unknown':
                            checked=unknown_results[ident]
                            status=checked['state'];payload=json.dumps(checked,ensure_ascii=False)
                        db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=?',(status,payload,ident))
                controller.wakeup.set();current=mode
            time.sleep(.1)
    finally:
        server.shutdown();server.server_close();worker.join(3);controller.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
