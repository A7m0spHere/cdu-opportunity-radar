"""Run with python -I after installing the wheel; no network or live data."""
from importlib.resources import files
from pathlib import Path
import json
import os
import subprocess
import sys
import sysconfig
import tempfile

import cdu_radar
from cdu_radar.sources import load_sources
from cdu_radar.student_page import assets
from cdu_radar.storage import Store
from cdu_radar.models import Article


def main():
    source = Path(__file__).resolve().parents[1] / 'cdu_radar'
    if Path(cdu_radar.__file__).resolve().is_relative_to(source):
        raise AssertionError('Smoke test must import the installed wheel')
    assert all(body for body, _ in assets().values())
    for name in ('sources.json','units.json','rules.json'):
        assert files('cdu_radar').joinpath(name).read_bytes()
    assert load_sources()
    with tempfile.TemporaryDirectory() as directory:
        for command in ([], ['serve-rss'], ['quality-report'], ['source-coverage'], ['analysis-queue'], ['featured-benchmark']):
            result = subprocess.run([sys.executable,'-I','-m','cdu_radar',*command,'--help'],
                                    cwd=directory,check=True,capture_output=True,text=True,encoding='utf-8')
            assert (command[0] if command else 'serve-rss') in result.stdout
            if command==['serve-rss']:
                assert '--recheck-limit' in result.stdout and '--request-budget' in result.stdout
        db=Path(directory)/'empty.sqlite3'
        with Store(db) as store:
            store.sync_sources(load_sources())
        original=db.read_bytes()
        result=subprocess.run([sys.executable,'-I','-m','cdu_radar','quality-report','--db',str(db),'--format','json'],
                              cwd=directory,check=True,capture_output=True,text=True,encoding='utf-8')
        assert json.loads(result.stdout)['snapshot']['raw_unique_notices']==0 and db.read_bytes()==original
        result=subprocess.run([sys.executable,'-I','-m','cdu_radar','source-coverage','--format','json'],
                              cwd=directory,check=True,capture_output=True,text=True,encoding='utf-8')
        coverage=json.loads(result.stdout)
        validated_ids={s.id for s in load_sources() if s.column_url and s.status=='verified'
                       and s.parser_supported and s.adapter and s.last_verified_at}
        assert 'department-jw-notices' in validated_ids
        assert {c['id'] for c in coverage['columns'] if c['collection_validated']}==validated_ids
        assert coverage['summary']['collection_validated_columns']==len(validated_ids)
        mock_db=Path(directory)/'synthetic.sqlite3'
        with Store(mock_db) as store:
            store.sync_sources(load_sources())
            store.save_notice('computer','https://computer.cdu.edu.cn/info/1/999999.htm',
                Article('合成课程调整通知','2026-10-16','这是合成的课程时间调整通知，请查看安排。'))
        mock_original=mock_db.read_bytes()
        state=Path(directory)/'m7-jobs.sqlite3'
        result=subprocess.run([sys.executable,'-I','-m','cdu_radar','analysis-queue','work',
            '--db',str(mock_db),'--state-db',str(state),'--mock'],cwd=directory,check=True,
            capture_output=True,text=True,encoding='utf-8')
        assert json.loads(result.stdout)['handled'][0]['state']=='succeeded' and mock_db.read_bytes()==mock_original
        for action in ['status','ledger']:
            before=state.read_bytes()
            result=subprocess.run([sys.executable,'-I','-m','cdu_radar','analysis-queue',action,
                '--db',str(mock_db),'--state-db',str(state)],cwd=directory,check=True,
                capture_output=True,text=True,encoding='utf-8')
            assert json.loads(result.stdout)['schema_version']==1 and state.read_bytes()==before
        from cdu_radar.analysis_models import reference_datetime
        dataset=Path(directory)/'new-featured-benchmark'
        result=subprocess.run([sys.executable,'-I','-m','cdu_radar','featured-benchmark','export',
            '--db',str(mock_db),'--directory',str(dataset),'--as-of',reference_datetime().isoformat()],
            cwd=directory,check=True,capture_output=True,text=True,encoding='utf-8')
        assert json.loads(result.stdout)['sampled']==1 and (dataset/'labels.csv').is_file()
        result=subprocess.run([sys.executable,'-I','-m','cdu_radar','featured-benchmark','evaluate',
            '--dataset',str(dataset/'dataset.json'),'--db',str(mock_db),'--split','all'],
            cwd=directory,check=True,capture_output=True,text=True,encoding='utf-8')
        assert json.loads(result.stdout)['summary']['full_benchmark_metrics'] is None
        from cdu_radar.auto_service import AutoFeedController
        from cdu_radar.featured_api import API_V3, validate_public_v3
        from cdu_radar.rss_service import make_server
        import http.client
        import threading
        controller=AutoFeedController(mock_db,Path(directory)/'http-state')
        server=make_server(controller,port=0)
        worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        try:
            assert controller.refresh();worker.start()
            connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
            try:
                connection.request('GET',API_V3+'?selection=featured')
                response=connection.getresponse();payload=json.loads(response.read())
                assert response.status==200 and payload['featured']['state']=='pending'
                validate_public_v3(payload)
            finally:connection.close()
            from xml.etree import ElementTree as ET
            for path,expected in [('/rss/v1/all.xml',200),('/rss/v1/featured.xml',503),('/rss/v1/all.xml?source=invalid',400)]:
                connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=5)
                try:
                    connection.request('GET',path)
                    response=connection.getresponse();body=response.read()
                    assert response.status==expected and response.getheader('Cache-Control')=='no-store, max-age=0'
                    if expected==200:assert ET.fromstring(body).tag=='rss'
                finally:connection.close()
        finally:
            if worker.is_alive():server.shutdown();worker.join(3)
            server.server_close();controller.close()
        executable = Path(sysconfig.get_path('scripts')) / ('cdu-radar.exe' if os.name=='nt' else 'cdu-radar')
        subprocess.run([str(executable),'--help'],cwd=directory,check=True,capture_output=True)
    print('Installed wheel: CLI, catalogs and all web assets passed; no collection or model calls.')


if __name__=='__main__':
    main()
