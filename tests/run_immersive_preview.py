"""Isolated real-page replay/soak harness. Never connects to EEG hardware or real VR."""
import argparse,collections,copy,io,json,os,sys,threading,time
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from gnautilus_ml.common import atomic,read_json,file_hash
from gnautilus_ml.ssh import ComputeBridge
from hub.immersive import ImmersiveWorkspace


def run(args):
    folder=Path(args.output);receipt=read_json(folder/'deployment.json');replay=read_json(folder/'replay.json')
    assert file_hash(folder/'replay-signals.npz')==replay['signals_sha256']
    with np.load(folder/'replay-signals.npz',allow_pickle=False) as z:signals=[z['trial'+str(i)].copy() for i in range(len(replay['trials']))]
    b=ComputeBridge(ROOT);profile=dict(b.profile)
    if not b.connect():raise RuntimeError(b.status['last_error'])
    validation=profile['root']+'/validation/immersive-'+receipt['release'][:12];worker=validation+'/preview_worker.py'
    with b.client.open_sftp() as s:s.put(str(ROOT/'tests/immersive_replay_worker.py'),worker)
    code='import json,os,subprocess\nfrom pathlib import Path\nb=Path('+repr(profile['root'])+');v=Path('+repr(validation)+')\nenv=dict(os.environ,CUDA_VISIBLE_DEVICES='+repr(profile['gpu_uuid'])+',PYTHONPATH=str(b/"releases"/'+repr(receipt['release'])+'),OMP_NUM_THREADS="2",MKL_NUM_THREADS="2")\nf=(v/"preview-worker.log").open("ab",buffering=0)\np=subprocess.Popen([str(b/".venv/bin/python"),'+repr(worker)+',"--base",str(b),"--validation",str(v),"--model",'+repr(receipt['model_id'])+',"--port","18872"],cwd=v,env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=f,start_new_session=True)\nprint(json.dumps({"pid":p.pid,"port":18872}))'
    process=b.command(code);atomic(folder/'preview-worker.json',process);b.profile=dict(profile,service_port=18872)
    for _ in range(80):
        try:
            health=b.call('GET','/status');assert health['service']=='immersive_replay_validation' and health['pid']==process['pid'] and health['gpu_uuid']==profile['gpu_uuid']
            # Startup polling can mark the SSH link disconnected before the test
            # worker binds its port. Restore only after its pinned identity and
            # the actual SSH transport have both been verified.
            assert b.client.get_transport().is_active()
            b.status['ssh_connected']=True
            break
        except Exception:time.sleep(.25)
    else:raise RuntimeError('Isolated replay worker did not become ready')
    meta=health['model'];identity=meta['input_contract']['signal_identity'];d=dict(schema='gnautilus.stream.v1',stream_id='f'*32,source_kind='recording_replay',sampling_rate=250,
        device_serial=identity['device_serial'],reference=identity.get('reference'),ground=identity.get('ground'),layout_verified=True,
        configuration=meta['input_contract']['filter_configuration'],channels=[dict(name=name,kind='EEG',physical_index=i,unit='uV') for i,name in enumerate(meta['channels'])])
    h=SimpleNamespace(lock=threading.RLock(),started=time.monotonic(),last_packet=None,epoch=1,sample_index=0,raw=collections.deque(maxlen=1250),recorder=None,stop_event=threading.Event(),
        acquisition=SimpleNamespace(snapshot=lambda:dict(active=False)))
    state=dict(enabled=False,result=None,returned=0.,trial=0,offset=0,frames=0,errors=[],timings=[],faults=[],requests=collections.Counter(),stopping=False)
    peer=dict(receiver_id='replay-view',device_id='preview-only',role='unity',online=True,telemetry_age_ms=0,applied_seq=1,sent_seq=1,
        telemetry=dict(actual_speed=0.,actual_yaw_rate=0.,x=0.,z=0.,yaw=0.,state='STOP',mode='LIVE',reason='SOFTWARE_REPLAY_NO_REAL_HEADSET',app_version='SOFTWARE REPLAY'))
    h.gnautilus_runtime=SimpleNamespace(descriptor=d,fresh=lambda:h.last_packet is not None and 0<=time.monotonic()-h.last_packet<.25,gap_count=0,last_gap=None,active_transport='isolated_record_replay')
    h.ml=SimpleNamespace(cached=dict(loaded=meta['model_id'],models=[meta]),updated=0,error=None,bridge=b)
    h.vr=SimpleNamespace(snapshot=lambda:dict(ready=True,clients=[dict(peer,applied_seq=state['frames']+1,sent_seq=state['frames']+1)]))
    h.quest=SimpleNamespace(snapshot=lambda device=None:dict(enabled=True,mirror_fresh=not state['stopping'],frame_seq=int((time.monotonic()-h.started)*6),frame_age_ms=20))
    def intent():
        r=state['result'];age=(time.monotonic()-state['returned'])*1000 if state['returned'] else None
        fresh=bool(state['enabled'] and r and h.gnautilus_runtime.fresh() and (r.get('input_age_ms',1e9)+(age or 0))<250 and not r.get('warming'))
        return dict(schema='gnautilus.intent.v1',inference_enabled=state['enabled'],inference_state='running' if fresh else 'warming' if state['enabled'] else 'stopped',fresh=fresh,
            latest=r if fresh else None,control=dict(enabled=False),age_ms=age,mode='OFFLINE_REPLAY',generation=h.epoch)
    h.intent=SimpleNamespace(snapshot=intent);workspace=ImmersiveWorkspace(h)
    events=set();max_rss=0;loop_durations=[]
    def reconnect():
        b.profile=profile
        if not b.connect():raise ConnectionError(b.status.get('last_error'))
        b.profile=dict(profile,service_port=18872)
    def source():
        nonlocal max_rss
        import psutil
        while not h.stop_event.is_set():
            tick=time.monotonic();elapsed=tick-h.started;trial=state['trial'];offset=state['offset'];x=signals[trial]
            try:
                fault=next((name for at,name in [(35,'missing_block'),(65,'slow_return'),(95,'ssh_disconnect')] if elapsed>=at and name not in events),None)
                if fault:
                    events.add(fault);state['faults'].append(dict(kind=fault,seconds=elapsed))
                    if fault=='missing_block':
                        try:b.call('POST','/step',dict(trial=trial,offset=offset+50))
                        except ValueError:pass
                        else:raise AssertionError('Missing block was accepted')
                        h.epoch+=1;h.raw.clear();h.gnautilus_runtime.gap_count+=1;state.update(offset=0,result=None);continue
                    if fault=='ssh_disconnect':
                        b.client.close();h.ml.error='injected SSH disconnect';state['result']=None;h.last_packet=None;reconnect();h.epoch+=1;h.raw.clear();state.update(offset=0);continue
                if offset+25>x.shape[1]:
                    trial=(trial+1)%len(signals);offset=0;h.epoch+=1;h.raw.clear();state.update(trial=trial,offset=0,result=None);x=signals[trial]
                with h.lock:h.raw.extend(x[:,offset:offset+25].T.tolist());h.sample_index=offset+25;h.last_packet=tick
                reply=b.call('POST','/step',dict(trial=trial,offset=offset,delay_ms=600 if fault=='slow_return' else 0),timeout=3)
                r=reply['result'];latency=(time.monotonic()-tick)*1000;state['timings'].append(latency)
                r.update(model_id=meta['model_id'],source='OFFLINE_REPLAY',source_epoch=h.epoch,sample_index_end=offset+25,input_age_ms=latency,roundtrip_ms=latency,
                    worker_ms=r.get('worker_ms'),prediction=['REST','FORWARD','BACKWARD','LEFT','RIGHT'][r['navigation_decision']['direction']] if not r.get('warming') and r['navigation_decision']['status']!='UNKNOWN' else 'UNKNOWN')
                state.update(result=r,returned=time.monotonic(),offset=offset+25,frames=state['frames']+1);h.ml.updated=time.time();h.ml.error=None;workspace.observe()
                if fault=='slow_return':
                    assert latency>250 and intent()['fresh'] is False;h.epoch+=1;h.raw.clear();state.update(offset=0,result=None)
                if state['frames']%50==0:max_rss=max(max_rss,psutil.Process().memory_info().rss)
            except Exception as error:
                state['errors'].append(dict(error=str(error),seconds=elapsed));state['result']=None;h.ml.error=str(error);h.last_packet=None
                try:reconnect();h.epoch+=1;h.raw.clear();state.update(offset=0)
                except Exception:time.sleep(1)
            loop_durations.append((time.monotonic()-tick)*1000);h.stop_event.wait(max(0,.1-(time.monotonic()-tick)))
        try:b.call('POST','/shutdown',{})
        except Exception:pass
        b.disconnect()
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',18)
    def picture():
        im=Image.new('RGB',(960,540),'#0c1828');draw=ImageDraw.Draw(im)
        for i in range(0,960,60):draw.line((i,0,i,540),fill='#1c3349')
        for i in range(0,540,60):draw.line((0,i,960,i),fill='#1c3349')
        draw.text((35,35),'SOFTWARE REPLAY / SYNTHETIC VIDEO FIXTURE',font=font,fill='#efc57d')
        draw.text((35,70),'NO REAL HEADSET - NO MOTION OUTPUT',font=font,fill='#c2d1e0')
        draw.text((35,480),'FRAME '+str(int((time.monotonic()-h.started)*6))+' / 6 FPS',font=font,fill='#72dfc7')
        output=io.BytesIO();im.save(output,format='JPEG',quality=75);return output.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def send(self,code,body,mime='application/json'):
            if isinstance(body,dict):body=json.dumps(body,allow_nan=False).encode()
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers()
            try:self.wfile.write(body)
            except (OSError,ConnectionError):pass
        def do_GET(self):
            path=urlparse(self.path).path;query=parse_qs(urlparse(self.path).query);state['requests'][path]+=1
            if path=='/api/immersive/status':
                s=workspace.status('replay-view');s.update(mode='OFFLINE_REPLAY',entry_eligible=True,blockers=[],input_match=True,can_start=workspace.quality().get('valid',False));return self.send(200,s)
            if path=='/api/immersive/signal':return self.send(200,workspace.signal(int(query['epoch'][0]) if 'epoch' in query else None,int(query['after'][0]) if 'after' in query else None))
            if path=='/api/quest/frame.jpg':return self.send(200,picture(),'image/jpeg')
            if path=='/api/quest/status':return self.send(200,h.quest.snapshot())
            if path=='/api/intent/status':return self.send(200,dict(intent(),readiness=dict(can_start=True)))
            if path=='/api/status':return self.send(200,dict(source=dict(fresh=False,connected=False,mode='paused',stats={}),quality=dict(valid=False,good_channels=0,channels=[]),server=dict(worker_connected=False,last_health=None),gnautilus={},vr=h.vr.snapshot(),performance={},recording=dict(active=False),waveforms={}))
            if path=='/api/vr/status':return self.send(200,dict(source=dict(fresh=False,mode='paused'),quality=dict(valid=False),server=dict(worker_connected=False),vr=h.vr.snapshot()))
            if path.startswith('/api/'):return self.send(200,dict(active=False,enabled=False,stages=[],status='not_run',version=1,groups=[],role='development',model_id=meta['model_id']))
            name='lab.html' if path in ('/','/lab') else path.lstrip('/')
            p=ROOT/'hub/web'/name
            if p.parent!=ROOT/'hub/web' or not p.is_file():return self.send(404,dict(error='not found'))
            import mimetypes
            content=p.read_bytes()
            if name=='lab.html':content=content.replace(b'data-page="lab"',b'data-page="lab" data-device-transition="gnautilus"')
            return self.send(200,content,(mimetypes.guess_type(p)[0] or 'text/plain')+'; charset=utf-8')
        def do_POST(self):
            if self.headers.get('X-EEGHub')!='1':return self.send(403,dict(error='Local fixture UI required'))
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}');state['requests'][self.path]+=1
            if self.path=='/api/intent/start':state['enabled']=True;return self.send(200,intent())
            if self.path=='/api/intent/stop':state['enabled']=False;return self.send(200,intent())
            if self.path=='/api/intent/control':return self.send(409,dict(error='开发回放不会驱动真实设备'))
            return self.send(409,dict(error='隔离回放不修改生产模型、采集或接收器'))
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);thread=threading.Thread(target=source,daemon=True);thread.start()
    threading.Thread(target=server.serve_forever,daemon=True).start();atomic(folder/'preview-server.json',dict(url='http://127.0.0.1:'+str(args.port)+'/lab',pid=os.getpid(),mode='OFFLINE_REPLAY',real_device_acceptance=False))
    print('Isolated replay page http://127.0.0.1:'+str(args.port)+'/lab',flush=True)
    try:
        while time.monotonic()-h.started<args.seconds:time.sleep(.5)
    finally:
        state['stopping']=True;h.stop_event.set();thread.join(20);server.shutdown();server.server_close()
        summary=dict(elapsed_s=time.monotonic()-h.started,frames=state['frames'],errors=state['errors'],faults=state['faults'],max_rss_bytes=max_rss,
            requests=dict(state['requests']),source='OFFLINE_REPLAY',gpu_uuid=profile['gpu_uuid'],actual_worker_pid=process['pid'],real_device_acceptance=False,
            roundtrip_ms={k:float(np.quantile(state['timings'],q)) for k,q in [('p50',.5),('p95',.95),('p99',.99)]} if state['timings'] else {})
        atomic(folder/'soak.json',summary);print(json.dumps(summary),flush=True)
        if state['errors']:raise RuntimeError('Replay soak had unexpected errors')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='outputs/immersive-20261026');ap.add_argument('--port',type=int,default=18765);ap.add_argument('--seconds',type=int,default=660);run(ap.parse_args())
