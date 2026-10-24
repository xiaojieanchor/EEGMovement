import hashlib,json,ssl,tempfile,threading,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from eeg_relay.trust import create_identity
from eeg_relay.update import download,fetch

class Downloads(unittest.TestCase):
    def test_tls_range_resume_and_idle_pause(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);create_identity(root/'tls','127.0.0.1');content=b'package-fixture'*100;requests=[]
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    start=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0]);requests.append(start)
                    self.send_response(206 if start else 200);self.send_header('Content-Length',str(len(content)-start))
                    if start:self.send_header('Content-Range',f'bytes {start}-{len(content)-1}/{len(content)}')
                    self.end_headers();self.wfile.write(content[start:])
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'tls/server.pem',root/'tls/server.key');server.socket=context.wrap_socket(server.socket,server_side=True)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            cfg=dict(base_url=f'https://127.0.0.1:{server.server_port}/eeg/',ca_pem=(root/'tls/ca.pem').read_text(),proxy_mode='direct')
            m=dict(size=len(content),sha256=hashlib.sha256(content).hexdigest(),path='releases/test.zip');cache=root/'download';cache.mkdir();(cache/(m['sha256']+'.zip.part')).write_bytes(content[:17])
            try:
                p=download(cfg,m,cache);self.assertEqual(p.read_bytes(),content);self.assertEqual(requests,[17])
                p.unlink();(cache/(m['sha256']+'.zip.part')).unlink(missing_ok=True)
                with self.assertRaises(InterruptedError):download(cfg,m,cache,busy=lambda:True)
                create_identity(root/'wrong','127.0.0.1');bad=dict(cfg,ca_pem=(root/'wrong/ca.pem').read_text())
                with self.assertRaises(ConnectionError):fetch(bad,'updates/test.json')
            finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
