"""Explicit LAN-only, exact-peer read proxy. Never opens databases or local files."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import sys
import threading

CONTRACT='student-notices-v1'
PRIVATE=tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
PUBLIC_ROUTES={
    '/':'text/html','/app.css':'text/css','/app.js':'text/javascript','/favicon.svg':'image/svg+xml',
    '/api/notices':'application/json','/feed.xml':'application/rss+xml',
    '/current.xml':'application/rss+xml','/history.xml':'application/rss+xml','/health':'application/json',
}
MAX_BYTES=2*1024*1024
ITEM_FIELDS={'guid','title','source_name','source_url','source_published_date','summary','category',
             'registration_status','listing','source_checked_at','first_aggregated_at'}
TOP_FIELDS={'schema_version','items','categories','counts','refreshed_at','data_mode','valid_for_ms','poll_after_ms'}


def private_address(value):
    address=ipaddress.IPv4Address(value)
    if str(address)!=value or not any(address in network for network in PRIVATE):
        raise ValueError('Use an explicit RFC1918 IPv4 address; no wildcard, public or loopback binding')
    return str(address)


def configuration(bind,clients,port,upstream_port):
    bind=private_address(bind)
    if not clients or len(clients)>8:raise ValueError('List 1 to 8 specific permitted device IPv4 addresses')
    clients=tuple(dict.fromkeys(private_address(value) for value in clients))
    if not 1<=port<=65535 or not 1<=upstream_port<=65535:raise ValueError('Invalid port')
    return bind,clients


def upstream_read(port,path,etag=None):
    """Fixed loopback destination, no redirects, credentials, cookies or file reads."""
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        headers={'Accept':PUBLIC_ROUTES[path]}
        if etag:headers['If-None-Match']=etag
        connection.request('GET',path,headers=headers)
        response=connection.getresponse()
        body=response.read(MAX_BYTES+1)
        if len(body)>MAX_BYTES:raise ValueError('Upstream response exceeds preview limit')
        if response.getheader('X-CDU-Public-Contract')!=CONTRACT:raise ValueError('Unexpected upstream service')
        mime=(response.getheader('Content-Type') or '').split(';',1)[0]
        if response.status not in {200,304,503}:raise ValueError('Unexpected upstream status')
        if response.status==200 and mime!=PUBLIC_ROUTES[path]:raise ValueError('Unexpected upstream content')
        if response.status==304 and (path not in {'/feed.xml','/current.xml','/history.xml'} or not etag):
            raise ValueError('Unexpected conditional response')
        if response.status==200 and path=='/api/notices':
            data=json.loads(body)
            if (not isinstance(data,dict) or set(data)!=TOP_FIELDS or data.get('schema_version')!=1 or
                    not isinstance(data.get('items'),list) or any(not isinstance(i,dict) or set(i)!=ITEM_FIELDS for i in data['items'])):
                raise ValueError('Unexpected public JSON fields')
        return response.status,body,response.getheader('Content-Type'),response.getheader('ETag')
    finally:connection.close()


def make_preview_server(upstream_port, *, bind='127.0.0.1',port=0,clients=('127.0.0.1',),enable_lan=False):
    address=ipaddress.ip_address(bind)
    if not address.is_loopback:
        if not enable_lan:raise ValueError('LAN listening requires explicit enablement')
        bind,clients=configuration(bind,clients,port,upstream_port)
    elif str(address)!='127.0.0.1':raise ValueError('Local validation uses literal 127.0.0.1 only')
    allowed=frozenset(clients)
    slots=threading.BoundedSemaphore(4)
    class Handler(BaseHTTPRequestHandler):
        server_version='CDULocalPreview';sys_version=''
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass
        def reply(self,status,body,mime='text/plain; charset=utf-8',etag=None):
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store, max-age=0')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Cross-Origin-Resource-Policy','same-origin')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            if status==503:self.send_header('Retry-After','1')
            if status==405:self.send_header('Allow','GET, HEAD')
            if etag:self.send_header('ETag',etag)
            self.end_headers()
            if self.command!='HEAD' and status!=304:self.wfile.write(body)
        def serve_read(self):
            if self.client_address[0] not in allowed:self.reply(403,b'Device not permitted\n');return
            hosts=self.headers.get_all('Host',[])
            if hosts!=[self.server.expected_host]:self.reply(403,b'Use the configured literal preview address\n');return
            origin=self.headers.get('Origin')
            public_navigation=(self.path=='/' and self.headers.get('Sec-Fetch-Mode')=='navigate'
                and self.headers.get('Sec-Fetch-Dest')=='document')
            if ((origin and origin!='http://'+self.server.expected_host) or
                    self.headers.get('Sec-Fetch-Site')=='cross-site' and not public_navigation):
                self.reply(403,b'Cross-site requests are disabled\n');return
            if self.path=='/approved.xml':self.reply(410,b'Human-approved feed retired; use /feed.xml\n');return
            if self.path not in PUBLIC_ROUTES:self.reply(404,b'Not found\n');return
            if not slots.acquire(blocking=False):self.reply(503,b'Preview temporarily unavailable\n');return
            try:
                etag=self.headers.get('If-None-Match') if self.path.endswith('.xml') else None
                if etag and (len(etag)>512 or any(ord(c)<32 for c in etag)):
                    self.reply(400,b'Invalid conditional header\n');return
                try:status,body,mime,tag=upstream_read(upstream_port,self.path,etag)
                except (OSError,ValueError,TypeError,http.client.HTTPException):
                    self.reply(503,b'Preview temporarily unavailable\n');return
                if status==503:self.reply(503,b'Preview temporarily unavailable\n')
                else:self.reply(status,body,mime or PUBLIC_ROUTES[self.path],tag)
            finally:slots.release()
        def do_GET(self):
            try:self.serve_read()
            except (ConnectionError,TimeoutError):pass
        do_HEAD=do_GET
        def forbidden(self):
            try:self.reply(405,b'Read-only preview\n')
            except (ConnectionError,TimeoutError):pass
        do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=forbidden
    server=ThreadingHTTPServer((bind,port),Handler)
    server.daemon_threads=True
    server.expected_host=f'{bind}:{server.server_address[1]}'
    return server


def serve_command(args):
    server=None
    try:
        bind,clients=configuration(args.bind,args.allow_client,args.port,args.upstream_port)
        if not args.check_only and not args.enable_lan:raise ValueError('Use --check-only, or explicitly --enable-lan to listen')
        status,_,_,_=upstream_read(args.upstream_port,'/api/notices')
        if status!=200:raise ValueError('Start a healthy local serve-rss service first')
        if args.check_only:
            print(json.dumps({'configuration_valid':True,'upstream_ready':True,'listener_opened':False,
                'allowed_devices':len(clients),'public_routes':sorted(PUBLIC_ROUTES)}));return 0
        server=make_preview_server(args.upstream_port,bind=bind,port=args.port,clients=clients,enable_lan=True)
        print(json.dumps({'preview':f'http://{bind}:{args.port}/','allowed_devices':len(clients),'upstream_loopback_only':True,
            'public_access':False,'stop':'Ctrl+C'}),flush=True)
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:return 0
    except (OSError,ValueError,TypeError,http.client.HTTPException) as exc:
        print('Preview not started: '+type(exc).__name__+': '+str(exc),file=sys.stderr);return 2
    finally:
        if server:server.server_close()
