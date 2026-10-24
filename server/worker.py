"""Loopback-only GPU diagnostic worker, reached through authenticated SSH channels.
No trained EEG intention model or calibrated VR controller is claimed by this service.
"""
import argparse,json,os,time,threading,subprocess,hashlib
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import numpy as np
import torch

LOCK=threading.Lock();START=time.time();LAST={};REQUESTS=0;WORKER_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
torch.set_num_threads(2)

def gpu_stats():
    try:
        p=subprocess.run(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
        return [dict(index=int(a[0]),name=a[1],total_mb=float(a[2]),used_mb=float(a[3]),utilization=float(a[4]),temperature=float(a[5])) for l in p.stdout.strip().splitlines() if (a:=[s.strip() for s in l.split(',')]) and len(a)==6]
    except Exception:return []

def canonical_scores(x,fs,freqs):
    # Diagnostic CCA; no fabricated probability or learned classification head.
    n=x.shape[0];t=torch.arange(n,device=x.device,dtype=torch.float32)/fs
    a=x-x.mean(0,keepdim=True);qa=torch.linalg.qr(a,mode='reduced').Q
    scores=[]
    for f in freqs:
        y=torch.stack([fn(2*torch.pi*f*h*t) for h in [1,2,3] for fn in [torch.sin,torch.cos]],dim=1)
        qy=torch.linalg.qr(y-y.mean(0,keepdim=True),mode='reduced').Q
        scores.append(float(torch.linalg.svdvals(qa.T@qy)[0].clamp(0,1).item()))
    return scores

def process(data):
    global REQUESTS,LAST
    started=time.perf_counter()
    x=np.asarray(data['filtered'],dtype=np.float32);raw=np.asarray(data['raw'],dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=7 or len(x)>1000 or len(x)<63 or raw.shape!=x.shape or not np.isfinite(x).all() or not np.isfinite(raw).all():raise ValueError('expected finite Nx7 paired arrays, 63<=N<=1000')
    fs=int(data.get('sfreq',250))
    if fs!=250:raise ValueError('worker expects250Hz, resample explicitly for each pretrained model')
    freqs=[float(f) for f in data.get('frequencies',[8,10,12,15])]
    if len(freqs)!=4 or any(f<5 or f>30 for f in freqs):raise ValueError('invalid frequency configuration')
    with LOCK,torch.inference_mode():
        tensor=torch.as_tensor(x,device=DEVICE)
        if DEVICE.type=='cuda':torch.cuda.synchronize(DEVICE)
        a=time.perf_counter();power=torch.fft.rfft(tensor,dim=0).abs().square().mean(dim=1)
        spectrum=float(power.mean().item())
        valid=bool(data.get('quality_valid',False))
        # Worker rechecks raw amplitude independently of the gateway-provided gate.
        centered=raw-np.median(raw,axis=0)
        raw_power=np.abs(np.fft.rfft(centered,axis=0))**2;rf=np.fft.rfftfreq(len(raw),1/fs)
        line_ratio=np.sum(raw_power[(rf>=48)&(rf<=52)],axis=0)/(np.sum(raw_power[(rf>=1)&(rf<=100)],axis=0)+1e-20)
        valid=valid and bool(np.max(np.abs(centered))<=500 and np.max(np.sqrt(np.mean(centered**2,axis=0)))<=200 and np.max(line_ratio)<=.45 and np.min(np.ptp(raw,axis=0))>=.05)
        scores=canonical_scores(tensor,fs,freqs) if valid else None
        if DEVICE.type=='cuda':torch.cuda.synchronize(DEVICE)
        compute=(time.perf_counter()-a)*1000
    REQUESTS+=1
    result=dict(ok=True,request_id=data.get('request_id'),sample_index_end=data.get('sample_index_end'),source=data.get('source'),worker_version='0.1.0',mode='gpu_signal_diagnostics_and_cca_uncalibrated',device=str(DEVICE),gpu_name=torch.cuda.get_device_name(DEVICE) if DEVICE.type=='cuda' else None,compute_ms=round(compute,3),worker_total_ms=round((time.perf_counter()-started)*1000,3),quality_valid=valid,frequency_scores=scores,frequencies=freqs,spectral_energy=spectrum,intention=None,motion={'state':'STOP','speed':0,'yaw_rate':0,'enabled':False,'reason':'uncalibrated_no_vr_receiver'},model_state='没有已验收的运动意图模型；CCA只作信号诊断',server_monotonic=time.monotonic())
    LAST=result;return result

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def send_json(self,code,obj):
        b=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def authorized(self):return __import__('hmac').compare_digest(self.headers.get('Authorization',''),f'Bearer {TOKEN}')
    def do_GET(self):
        if not self.authorized():return self.send_json(401,{'error':'unauthorized'})
        if self.path!='/health':return self.send_json(404,{'error':'not found'})
        self.send_json(200,dict(ok=True,project='EEGMovement',version='0.1.0',pid=os.getpid(),uptime_s=time.time()-START,torch=torch.__version__,cuda=torch.version.cuda,device=str(DEVICE),gpus=gpu_stats(),requests=REQUESTS,weights_loaded=False,control_enabled=False,worker_sha256=WORKER_SHA))
    def do_POST(self):
        if not self.authorized():return self.send_json(401,{'error':'unauthorized'})
        n=int(self.headers.get('Content-Length',0))
        if n<=0 or n>500000:self.close_connection=True;return self.send_json(413,{'error':'request too large'})
        b=self.rfile.read(n)
        if self.path!='/process':return self.send_json(404,{'error':'not found'})
        try:self.send_json(200,process(json.loads(b)))
        except (KeyError,ValueError,TypeError) as e:self.send_json(400,{'error':str(e)[:150]})
        except Exception:self.send_json(500,{'error':'processing failed'})

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=18861);ap.add_argument('--gpu',type=int,default=0);ap.add_argument('--token-file',required=True);args=ap.parse_args()
    TOKEN=Path(args.token_file).read_text().strip()
    DEVICE=torch.device(f'cuda:{args.gpu}' if torch.cuda.is_available() else 'cpu')
    if DEVICE.type=='cuda':
        with torch.inference_mode():torch.fft.rfft(torch.zeros((250,7),device=DEVICE),dim=0);torch.cuda.synchronize(DEVICE)
    print(json.dumps({'ready':True,'listen':'127.0.0.1','port':args.port,'device':str(DEVICE)}),flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
