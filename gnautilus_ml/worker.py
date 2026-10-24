"""Loopback-only authenticated service; no shell, URL proxy or public bind."""
import argparse,hmac,json,os,signal,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from .common import read_json
from .service import ModelService
from .governance import check_runtime
from .watchdog import ProgressWatchdog

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--release',required=True);args=ap.parse_args();root=Path(args.root).resolve();release=root/'releases'/args.release
    profile=read_json(release/'compute.json');check_runtime(profile);service=ModelService(root,profile,release);token=(root/'service.token').read_text().strip()
    for job in service.registry.interrupted_candidates():service.registry.transition(job['id'],'interrupted')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def handle_request(self,method):
            status=200
            try:
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):raise PermissionError('Authentication failed')
                if method=='GET':out=service.get(self.path)
                else:
                    n=int(self.headers.get('Content-Length','0'))
                    if not 0<n<=4*1024*1024:raise ValueError('Request size outside bounds')
                    data=json.loads(self.rfile.read(n));out=service.post(self.path,data)
            except PermissionError as error:status=403;out={'error':str(error)}
            except (ValueError,KeyError,OSError,TypeError) as error:status=400;out={'error':str(error)}
            except Exception as error:status=500;out={'error':type(error).__name__+': '+str(error)}
            body=json.dumps(out,ensure_ascii=False,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):self.handle_request('GET')
        def do_POST(self):self.handle_request('POST')
    watchdog=ProgressWatchdog(root/'logs')
    class ComputeHTTPServer(ThreadingHTTPServer):
        request_queue_size=32
        def service_actions(self):watchdog.pulse()
    server=ComputeHTTPServer(('127.0.0.1',profile['service_port']),Handler);threading.Thread(target=service.runner.loop,daemon=True).start();watchdog.start()
    def stop(*a):service.runner.stopping.set();service.stop();threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:server.serve_forever(.2)
    finally:watchdog.close();service.runner.stopping.set();server.server_close()
if __name__=='__main__':main()
