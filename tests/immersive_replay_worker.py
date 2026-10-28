"""Private test-only raw-record replay worker. No VR, fitting, or production routes."""
import argparse,hmac,json,os,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import numpy as np
from gnautilus_ml.common import read_json,file_hash
from gnautilus_ml.artifacts import load_artifact
from gnautilus_ml.governance import check_runtime

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base',required=True);ap.add_argument('--validation',required=True);ap.add_argument('--model',required=True);ap.add_argument('--port',type=int,default=18872);args=ap.parse_args()
    import gnautilus_ml,torch
    base=Path(args.base);folder=Path(args.validation);profile=read_json(Path(gnautilus_ml.__file__).resolve().parents[1]/'compute.json');check_runtime(profile);torch.set_num_threads(2)
    info=read_json(folder/'replay.json');assert file_hash(folder/'replay-signals.npz')==info['signals_sha256']
    with np.load(folder/'replay-signals.npz',allow_pickle=False) as f:signals={k:f[k].copy() for k in f.files}
    model,meta=load_artifact(base/'models'/args.model,device='cuda:0');token=(base/'service.token').read_text().strip()
    state=dict(trial=None,offset=0,session=None,frames=0);lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):self.respond(False)
        def do_POST(self):self.respond(True)
        def respond(self,post):
            status=200
            try:
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):raise PermissionError('Authentication required')
                data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}') if post else {}
                if self.path=='/status':out=dict(service='immersive_replay_validation',gpu_uuid=profile['gpu_uuid'],pid=os.getpid(),model=meta,frames=state['frames'],source='OFFLINE_REPLAY')
                elif post and self.path=='/step':
                    with lock:
                        trial=int(data['trial']);offset=int(data['offset']);x=signals['trial'+str(trial)]
                        if offset==0:state.update(trial=trial,offset=0,session=model.new_session())
                        if state['trial']!=trial or state['offset']!=offset:state.update(trial=None,session=None);raise ValueError('sample_gap_or_reorder: validation state reset')
                        if offset<0 or offset+25>x.shape[1]:raise ValueError('Replay sample support exceeded')
                        delay=data.get('delay_ms',0)
                        if type(delay) is not int or not 0<=delay<=800:raise ValueError('Invalid test delay')
                        if delay:time.sleep(delay/1000)
                        try:r=state['session'].step(x[:,offset:offset+25])
                        except ValueError as error:
                            state['session']=model.new_session();r=dict(warming=True,eligible=False,quality=dict(electrical_valid=False),navigation_decision=dict(status='UNKNOWN',direction=0,reason='quality_reset'),error=str(error))
                        state['offset']=offset+25;state['frames']+=1;out=dict(result=r,source='OFFLINE_REPLAY',motion_enabled=False)
                elif post and self.path=='/shutdown':out=dict(stopping=True);threading.Thread(target=server.shutdown,daemon=True).start()
                else:status=404;out=dict(error='No production or motion route in validation worker')
            except PermissionError as error:status=403;out=dict(error=str(error))
            except (ValueError,KeyError,TypeError) as error:status=400;out=dict(error=str(error))
            body=json.dumps(out,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    try:server.serve_forever(.1)
    finally:server.server_close()

if __name__=='__main__':main()
