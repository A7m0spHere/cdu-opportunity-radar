"""All preview servers in this test bind loopback; no LAN socket is opened."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading

import pytest

from cdu_radar import lan_preview as module
from cdu_radar.cli import main
from cdu_radar.auto_publish import failed
from cdu_radar.lan_preview import configuration,make_preview_server,upstream_read
from test_auto_publish import auto_service,URL,NOW,add
from test_rss_service import loopback_only,request


@contextmanager
def running(server):
    worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);worker.start()
    try:yield server
    finally:server.shutdown();server.server_close();worker.join(3);assert not worker.is_alive()


@pytest.fixture
def preview(auto_service):
    store,controller,upstream,point,mono=auto_service
    server=make_preview_server(upstream.server_address[1])
    with running(server):yield store,controller,upstream,server,point,mono


@pytest.mark.parametrize('bind',[
    '0.0.0.0','127.0.0.1','8.8.8.8','169.254.1.2','198.18.0.1','192.0.2.1','224.0.0.1','::1','::','localhost',
])
def test_cli_configuration_rejects_wildcard_public_and_non_rfc1918(bind):
    with pytest.raises(ValueError):configuration(bind,['192.168.1.50'],8780,8765)


@pytest.mark.parametrize('clients',[[],['8.8.8.8'],['192.168.1.0/24'],['127.0.0.1'],['::1'],['192.168.1.50']*9])
def test_only_specific_limited_private_device_addresses(clients):
    with pytest.raises(ValueError):configuration('192.168.1.10',clients,8780,8765)


def test_valid_configuration_does_not_listen(monkeypatch):
    monkeypatch.setattr(module,'ThreadingHTTPServer',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unexpected binding')))
    assert configuration('192.168.1.10',['192.168.1.50'],8780,8765)==('192.168.1.10',('192.168.1.50',))
    with pytest.raises(ValueError,match='enablement'):
        make_preview_server(8765,bind='192.168.1.10',port=8780,clients=['192.168.1.50'])


def test_public_page_api_and_feed_match_upstream(preview):
    _,_,upstream,server,_,_=preview
    for path in module.PUBLIC_ROUTES:
        status,h,body=request(server,path)
        original=request(upstream,path)
        expected=503 if path in {'/rss/v1/featured.xml','/rss/v2/featured.xml'} else 200
        assert status==original[0]==expected and h['Content-Type']==original[1]['Content-Type']
        if status==503:assert b'<rss' not in body and 'ETag' not in h
        elif path!='/api/notices':assert body==original[2]
        else:assert json.loads(body)['items']==json.loads(original[2])['items']
        assert h['Cache-Control']=='no-store, max-age=0'
        assert 'Access-Control-Allow-Origin' not in h and "frame-ancestors 'none'" in h['Content-Security-Policy']
        assert request(server,path,method='HEAD')[2]==b''
    assert request(server,'/approved.xml')[0]==410
    tag=request(server,'/feed.xml')[1]['ETag']
    assert request(server,'/feed.xml',headers={'If-None-Match':tag})[0]==304
    assert request(server,'/api/notices',headers={'If-None-Match':'*'})[0]==200


@pytest.mark.parametrize('path',[
    '/notices.sqlite3','/data','/logs','/sources.json','/owner.json','/publisher.lock','/.env','/admin',
    '/api/notices?file=../notices.sqlite3','/app.js?x=1','/%2e%2e/notices.sqlite3','/../sources.json',
    'http://example.invalid/api/notices','/api/notices/','/approved.xml?x=1',
])
def test_route_whitelist_has_no_file_or_url_passthrough(preview,path):
    server=preview[3]
    assert request(server,path)[0]==(403 if path.startswith('http://') else 404)


@pytest.mark.parametrize('method',['POST','PUT','PATCH','DELETE','OPTIONS'])
def test_no_write_methods(preview,method):
    assert request(preview[3],'/api/notices',method=method)[0]==405


@pytest.mark.parametrize('headers',[{'Host':'example.invalid'},{'Host':'127.0.0.1'},
    {'Origin':'https://example.invalid'},{'Sec-Fetch-Site':'cross-site'}])
def test_literal_host_and_same_origin_only(preview,headers):
    assert request(preview[3],'/api/notices',headers=headers)[0]==403


def test_external_link_can_navigate_to_public_shell_but_not_fetch_api(preview):
    headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
    assert request(preview[3],'/',headers=headers)[0]==200
    assert request(preview[3],'/api/notices',headers=headers)[0]==403


def test_devices_not_on_allowlist_get_no_data(auto_service):
    upstream=auto_service[2]
    with running(make_preview_server(upstream.server_address[1],clients=('192.168.1.50',))) as server:
        status,_,body=request(server,'/api/notices')
        assert status==403 and URL.encode() not in body


@pytest.mark.parametrize('fault',['withdrawn','parse_failed','unavailable','stale','export_error'])
def test_proxy_preserves_fail_closed_and_recovers(preview,fault,monkeypatch):
    store,controller,upstream,server,point,mono=preview
    tag=request(server,'/feed.xml')[1]['ETag']
    if fault in {'withdrawn','parse_failed','unavailable'}:
        with store.db:failed(store.db,URL,fault,'isolated fault',NOW.isoformat())
    elif fault=='stale':mono[0]=31
    else:
        from cdu_radar import auto_service as origin
        monkeypatch.setattr(origin,'atomic_write',lambda *a:(_ for _ in ()).throw(OSError('isolated export failure')))
        assert not controller.refresh()
    for path in ('/feed.xml','/api/notices'):
        status,_,body=request(server,path,headers={'If-None-Match':tag})
        assert status==503 and URL.encode() not in body
    if fault!='export_error':
        assert controller.refresh()
        assert request(server,'/api/notices')[0]==200


def test_preview_forwards_v2_filters_to_fixed_loopback(preview):
    server=preview[3]
    status,headers,body=request(server,'/api/notices/v2?source=computer&type=competition')
    payload=json.loads(body)
    assert status==200 and payload['schema_version']==2 and payload['counts']['all']==1
    assert payload['items'][0]['publisher_id']=='computer'
    assert request(server,'/api/notices/v2?source=not-a-publisher')[0]==400
    assert request(server,'/api/notices/v2?file=../notices.sqlite3')[0]==400


@pytest.mark.parametrize('location',['classification','options','top'])
def test_preview_v2_rejects_private_fields_even_in_nested_metadata(preview,monkeypatch,location):
    store,controller,upstream,server,point,mono=preview
    original=controller.response_snapshot
    def injected(snapshot,path):
        result=original(snapshot,path)
        if path.startswith('/api/notices/v2'):
            data=json.loads(result.body)
            if location=='classification':data['items'][0]['classification']['content']='PRIVATE-SYNTHETIC-BODY'
            elif location=='options':data['filter_options']['sources'][0]['db']='PRIVATE-SYNTHETIC-PATH'
            else:data['db']='PRIVATE-SYNTHETIC-PATH'
            return replace(result,body=json.dumps(data).encode())
        return result
    monkeypatch.setattr(controller,'response_snapshot',injected)
    status,_,body=request(server,'/api/notices/v2')
    assert status==503 and b'PRIVATE-SYNTHETIC' not in body


def test_upstream_replaced_or_missing_contract_is_not_exposed(loopback_only):
    class PrivateService(BaseHTTPRequestHandler):
        def do_GET(self):
            body=b'PRIVATE configuration must not escape'
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    with running(ThreadingHTTPServer(('127.0.0.1',0),PrivateService)) as origin:
        with running(make_preview_server(origin.server_address[1])) as server:
            status,_,body=request(server,'/')
            assert status==503 and b'PRIVATE' not in body


def test_no_browser_credentials_forwarded(loopback_only):
    seen=[]
    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers));body=b'<html>Public shell only</html>'
            self.send_response(200);self.send_header('X-CDU-Public-Contract',module.CONTRACT)
            self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
        def log_message(self,*a):pass
    with running(ThreadingHTTPServer(('127.0.0.1',0),Origin)) as origin:
        with running(make_preview_server(origin.server_address[1])) as server:
            assert request(server,'/',headers={'Cookie':'test-only','Authorization':'Bearer synthetic-test'})[0]==200
    assert 'Cookie' not in seen[0] and 'Authorization' not in seen[0]
    assert seen[0]['Host']==f'127.0.0.1:{origin.server_address[1]}'


def test_check_only_and_missing_enable_flag_never_bind(auto_service,monkeypatch,capsys):
    port=auto_service[2].server_address[1]
    monkeypatch.setattr(module,'make_preview_server',lambda *a,**k:(_ for _ in ()).throw(AssertionError('LAN listener must not open')))
    args=['serve-lan-preview','--bind','192.168.1.10','--allow-client','192.168.1.50','--upstream-port',str(port)]
    assert main(args)==2
    capsys.readouterr()
    assert main(args+['--check-only'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['listener_opened'] is False and result['upstream_ready'] is True


def test_m3a_and_auto_listeners_still_reject_lan(auto_service):
    from cdu_radar.rss_service import make_server
    controller=auto_service[1]
    for bind in ('0.0.0.0','192.168.1.10'):
        with pytest.raises(ValueError):make_server(controller,bind,0)


@pytest.mark.parametrize('body',[
    b'{"schema_version":1,"items":[],"private_db_path":"must-not-escape"}',
    b'x'*(module.MAX_BYTES+1),
],ids=['private-fields','oversized'])
def test_unexpected_json_fields_or_oversized_body_are_not_forwarded(loopback_only,body):
    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('X-CDU-Public-Contract',module.CONTRACT);self.end_headers()
            try:self.wfile.write(body)
            except ConnectionError:pass
        def log_message(self,*a):pass
    with running(ThreadingHTTPServer(('127.0.0.1',0),Origin)) as origin:
        with running(make_preview_server(origin.server_address[1])) as server:
            status,_,content=request(server,'/api/notices')
            assert status==503 and b'must-not-escape' not in content
