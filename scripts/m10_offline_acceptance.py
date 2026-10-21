"""Synthetic loopback producer -> real HTTP consumer -> durable Markdown drafts."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from cdu_radar.analysis_models import reference_datetime
from cdu_radar.async_cli import configuration
from cdu_radar.async_contract import mock_payload
from cdu_radar.async_queue import JobQueue
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.models import Article
from cdu_radar.rss_consumer import config, poll, export_run
from cdu_radar.rss_service import make_server
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store


def run(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    now=reference_datetime('2026-10-21T12:00:00+08:00')
    raw=directory/'synthetic.sqlite3';state=directory/'jobs.sqlite3';consumer=directory/'consumer.sqlite3'
    analyzer,_=configuration(None,True)
    def add_notice(ident,title,body):
        with Store(raw) as store:
            store.sync_sources(load_sources())
            store.save_notice('computer',f'https://computer.cdu.edu.cn/info/1036/{999900+ident}.htm',
                Article(title,'2026-10-21',body),seen_at=now.isoformat())
    def analyze():
        with JobQueue(state,raw) as queue:
            queue.scan(analyzer)
            from cdu_radar.async_queue import source_snapshot
            with source_snapshot(raw) as repo:
                for ident in repo.ids():
                    notice=repo.input(ident);supplied,_=analyzer.prepare(notice);payload=mock_payload(supplied)
                    payload['importance']=dict(kind='inference',level='high',reason='合成验收建议，不是真实模型判断',quotes=[notice.content])
                    checked=analyzer.validate(payload,notice,supplied,now)
                    queue.db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=?',
                        (checked['state'],json.dumps(checked,ensure_ascii=False),ident))
    add_notice(1,'合成本科生课程调整通知','本科生须登录教务系统核对课程调整安排，请于2026年10月25日前完成确认。')
    analyze()
    controller=AutoFeedController(raw,directory/'publisher',clock=lambda:now,
        data_mode='offline_replay',analysis_state=state,analysis_analyzer=analyzer)
    server=make_server(controller,port=0)
    worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
    try:
        assert controller.refresh();worker.start()
        base=f'http://127.0.0.1:{server.server_address[1]}'
        cfg={'schema_version':1,'subscriptions':[
            {'name':'本科生综合信息','url':base+'/rss/v2/all.xml?audience=undergraduate'},
            {'name':'AI与计算机','url':base+'/rss/v2/all.xml?topic=ai&audience=undergraduate'},
            {'name':'竞赛双创精选','url':base+'/rss/v2/featured.xml?domain=competition'}]}
        (directory/'consumer-config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
        subs=config(cfg);first=poll(subs,consumer)
        assert all(s['status']=='initialized' and not s['drafts'] for s in first['subscriptions'])
        export_run(consumer,directory/'01-initialized',first['run_id'])
        add_notice(2,'合成本科生人工智能编程竞赛报名','欢迎本科生报名人工智能编程竞赛，请于2026年10月25日前提交参赛材料，完整资格请核对原文。')
        analyze();assert controller.refresh()
        before=hashlib.sha256(raw.read_bytes()).hexdigest()
        new=poll(subs,consumer)
        assert [len(s['drafts']) for s in new['subscriptions']]==[1,1,1]
        assert before==hashlib.sha256(raw.read_bytes()).hexdigest()
        export_run(consumer,directory/'02-new',new['run_id'])
        repeated=poll(subs,consumer)
        assert not any(s['drafts'] for s in repeated['subscriptions'])
        add_notice(2,'合成本科生人工智能编程竞赛报名','欢迎本科生报名人工智能编程竞赛，请于2026年10月26日前提交参赛材料，完整资格请核对原文。')
        analyze();assert controller.refresh()
        updated=poll(subs,consumer)
        assert all(s['drafts'][0]['change']=='updated' for s in updated['subscriptions'])
        export_run(consumer,directory/'03-update',updated['run_id'])
        restarted=poll(subs,consumer)
        assert not any(s['drafts'] for s in restarted['subscriptions'])
        # No hidden stale-cache fallback on an actual publisher gate failure.
        controller.reason='synthetic_failure';controller.snapshot=None
        unavailable=poll(subs,consumer)
        assert all(s['status']=='unavailable' and not s['drafts'] for s in unavailable['subscriptions'])
        export_run(consumer,directory/'04-unavailable',unavailable['run_id'])
        result=dict(mode='synthetic_loopback_only',three_directions=[s['name'] for s in new['subscriptions']],
            initialization='passed_no_backfill',new_drafts=[len(s['drafts']) for s in new['subscriptions']],
            duplicate_poll='passed_zero_drafts',guid_update='passed_three_updated_drafts',restart='passed_zero_duplicates',
            unavailable='passed_no_stale_drafts',raw_hash_preserved_during_consumer=True,
            official_site_requests=0,model_requests=0,qq_deliveries=0,listener_stopped=True)
    finally:
        server.shutdown();server.server_close();worker.join();controller.close()
    (directory/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True)
    print(json.dumps(run(parser.parse_args().directory),ensure_ascii=False,indent=2))
