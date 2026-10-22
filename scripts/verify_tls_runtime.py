"""Real TLS positive/negative check; no school, model or server deployment."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cdu_radar.http import classify_exception
from cdu_radar.tls import trusted_context


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--openssl',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cdu-tls-') as temporary:
        root=Path(temporary);key=root/'key.pem';cert=root/'cert.pem'
        subprocess.run([str(args.openssl),'req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1',
                        '-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'synthetic TLS check')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        server.socket=context.wrap_socket(server.socket,server_side=True)
        worker=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True);worker.start()
        rejected=False;category=None
        try:
            with httpx.Client(verify=trusted_context(),trust_env=False,timeout=5) as client:
                try:client.get('https://127.0.0.1:'+str(server.server_address[1]))
                except httpx.ConnectError as exc:
                    category,_=classify_exception(exc);rejected=category=='tls_certificate'
        finally:
            server.shutdown();server.server_close();worker.join(timeout=5)
        if not rejected:raise ValueError('Untrusted certificate was not rejected')
    with httpx.Client(verify=trusted_context(),trust_env=True,timeout=20,follow_redirects=False) as client:
        response=client.get('https://pypi.org/pypi/certifi/json')
        response.raise_for_status()
    result=dict(passed=True,patched_context_https_status=response.status_code,
                untrusted_certificate_rejected=rejected,error_category=category,
                temporary_key_removed=True,loopback_listener_stopped=True,
                public_metadata_requests=1,school_requests=0,model_requests=0)
    with args.output.open('x',encoding='utf-8') as handle:json.dump(result,handle,indent=2)
    print(json.dumps(result))


if __name__=='__main__':main()
