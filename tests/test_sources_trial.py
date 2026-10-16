from pathlib import Path
import hashlib

import httpx
import pytest

from cdu_radar.http import Fetcher
from scripts import validate_sources_trial as trial

FIXTURES=Path(__file__).parent/'fixtures'/'m5b'


def test_default_trial_preview_never_creates_output_or_opens_baseline(tmp_path):
    root=tmp_path/'preview'
    result=trial.run(tmp_path/'missing.sqlite3',root)
    assert result['mode']=='preview' and result['policy']['max_http_requests']==15
    assert not root.exists()


def test_existing_output_refused(store,tmp_path):
    path=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    with pytest.raises(ValueError,match='NEW'):
        trial.run(path,tmp_path,execute=True)


def test_mock_trial_preserves_baseline_and_repeats_without_new_rows(store,tmp_path,monkeypatch):
    path=Path(store.db.execute('PRAGMA database_list').fetchone()[2]);before=hashlib.sha256(path.read_bytes()).hexdigest()
    def handler(req):
        if req.url.path=='/robots.txt':return httpx.Response(404)
        unit='xsc' if req.url.host=='xsc.cdu.edu.cn' else 'tuanwei'
        body=(FIXTURES/(unit+('_article.html' if '/info/' in req.url.path else '_list.html'))).read_bytes()
        if '/info/' in req.url.path:
            if unit=='xsc':
                day='13' if req.url.path.endswith('11473.htm') else '09' if req.url.path.endswith('11471.htm') else '08'
                body=body.replace('2026年10月08日'.encode(),f'2026年10月{day}日'.encode())
            elif not req.url.path.endswith('3213.htm'):
                body=body.replace('2026年10月12日'.encode(),'2026年09月17日'.encode())
        return httpx.Response(200,content=body)
    def factory(**kwargs):
        kwargs['interval']=0
        return Fetcher(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(trial,'Fetcher',factory)
    result=trial.run(path,tmp_path/'new-trial',execute=True)
    assert result['new_unique_originals']==6 and result['actual_http_requests']==12
    assert result['repeat_results'][0]['unchanged']==1
    assert result['baseline_sha256_before']==result['baseline_sha256_after']==before
