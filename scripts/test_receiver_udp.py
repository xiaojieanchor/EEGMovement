"""Real UDP fault injection into the same compiled C# core used by Unity.
Private fixture keys stay in temporary runtime files, never the exported report.
"""
import argparse,copy,datetime,json,math,os,secrets,socket,subprocess,sys,tempfile,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from hub.vr import encode_packet,decode_packet

class Fixture:
    def __init__(self,directory,unity=None):
        self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.sock.bind(('127.0.0.1',0));self.sock.settimeout(.5)
        self.key=secrets.token_bytes(32);self.session=uuid.uuid4().hex;self.seq=0;self.samples=[];self.last_command=None;self.last_heartbeat=None;self.last_send=None;self.address=None
        config=Path(directory)/'pairing.json';config.write_text(json.dumps({'version':1,'host':'127.0.0.1','port':self.sock.getsockname()[1],'key':self.key.hex(),'hub_session':self.session}),encoding='utf-8');os.chmod(config,0o600)
        if unity:
            probe=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
            args=[str(unity),'--eeg-config',str(config),'--eeg-port',str(port),'--eeg-allow-test','-screen-fullscreen','0','-logFile',str(ROOT/'hub/runtime/unity-fault.log')]
        else:
            args=[str(ROOT/'receiver/bin/EEGReceiver.exe'),'--config',str(config),'--port','0','--allow-test','--seconds','45','--telemetry',str(ROOT/'outputs/receiver_fault_trace.jsonl')]
        self.process=subprocess.Popen(args,cwd=str(ROOT),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.sock.settimeout(60 if unity else 5)
        if unity:self.phase(.1,send=False)
        self.sock.settimeout(.5)
    def send(self,p,key=None):
        self.sock.sendto(encode_packet(p,self.key if key is None else key),self.address);self.last_send=time.monotonic()
    def packet(self,hb,direction=1,**overrides):
        self.seq+=1
        return dict(version=1,kind='command',receiver_id=hb['receiver_id'],nonce=hb['nonce'],session=self.session,ticket=hb['ticket'],seq=self.seq,decision_id=self.seq,epoch=1,ttl_ms=180,mode='TEST',active=direction!=0,input_valid=True,model_ready=True,direction=direction,p_active=.95,confidence=.95,speed=.6,yaw_rate=40,**overrides)
    def phase(self,seconds,direction=1,transform=None,after=None,send=True,key=None):
        out=[];started=time.monotonic()
        while time.monotonic()-started<seconds:
            data,address=self.sock.recvfrom(5000);hb=decode_packet(data,self.key);self.address=address;self.last_heartbeat=hb
            snapshot=dict(hb['telemetry'],observer_monotonic=time.monotonic(),phase_elapsed_s=time.monotonic()-started);out.append(snapshot);self.samples.append(snapshot)
            if hb.get('session')!=self.session:
                self.send(dict(version=1,kind='welcome',receiver_id=hb['receiver_id'],nonce=hb['nonce'],session=self.session,ticket=hb['ticket']));continue
            if send:
                p=self.packet(hb,direction)
                if transform:transform(p)
                self.send(p,key);self.last_command=copy.deepcopy(p)
                if after:after(self,p)
        return out
    def close(self):
        self.process.terminate();self.process.wait(timeout=5);self.sock.close()

def still(samples):return all(abs(s['actual_speed'])<1e-5 and abs(s['actual_yaw_rate'])<1e-5 for s in samples)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--unity',type=Path);args=parser.parse_args()
    cases=[];metrics={}
    def check(label,passed,detail=None):
        cases.append(dict(label=label,passed=bool(passed),detail=detail));print(('PASS ' if passed else 'FAIL ')+label,flush=True)
    with tempfile.TemporaryDirectory(prefix='receiver-fixture-',dir=ROOT/'hub/runtime') as directory:
        f=Fixture(directory,args.unity)
        try:
            samples=f.phase(1.0);check('真实UDP握手与前进',any(s['actual_speed']>.3 for s in samples) and samples[-1]['z']>.08)
            samples=f.phase(.45,0);check('正常静止指令',still(samples[-3:]))
            sent=False
            def spike(p):
                global sent
                if sent:p['active']=False;p['direction']=0
                sent=True
            samples=f.phase(.4,transform=spike);check('单次高置信脉冲不启动',still(samples))
            samples=f.phase(.45,transform=lambda p:p.update(confidence=.6));check('低置信噪声不启动',still(samples))
            f.phase(.6)
            started=time.monotonic();samples=f.phase(.35,transform=lambda p:p.update(input_valid=False))
            stopped=next((s for s in samples if abs(s['actual_speed'])<1e-5),None);latency=(stopped['observer_monotonic']-started)*1000 if stopped else None;metrics['invalid_input_stop_ack_ms']=round(latency,3) if latency is not None else None
            check('无效输入立即停止并回执',stopped is not None and latency<200,metrics['invalid_input_stop_ack_ms'])
            f.phase(.6);started=time.monotonic();samples=f.phase(.35,transform=lambda p:p.update(model_ready=False));check('模型未就绪停止',still(samples[-3:]))
            f.phase(.6);last_sent=f.last_send;samples=f.phase(.45,send=False);brakes=[s['watchdog_brake_ms'] for s in samples if s['watchdog_stops']>0];metrics['watchdog_brake_ms']=max(brakes) if brakes else None
            # Unity applies brakes on its 20ms fixed step: a 180ms lease is observable by
            # the next physics tick (200ms), plus a 5ms measured scheduling allowance.
            # The shared native harness retains its existing strict 200ms bound.
            metrics['watchdog_bound_ms']=205 if args.unity else 200
            check('断包看门狗180ms租约制动',still(samples[-3:]) and brakes and max(brakes)<metrics['watchdog_bound_ms'],metrics['watchdog_brake_ms'])
            samples=f.phase(.65);check('断包后新鲜指令恢复',samples[-1]['actual_speed']>.2)
            before=samples[-1]['rejected']
            def reverse_dup(f,p):
                f.send(p);old=copy.deepcopy(p);old['seq']-=1;old['direction']=2;f.send(old)
            samples=f.phase(.6,after=reverse_dup);check('重复和乱序不反转',all(s['actual_speed']>=0 for s in samples) and samples[-1]['rejected']>before)
            before=samples[-1]['invalid_wire']
            def bad_signature(f,p):
                bad=copy.deepcopy(p);bad['direction']=2;f.send(bad,bytes(32))
            samples=f.phase(.5,after=bad_signature);check('坏签名不改变有效方向',all(s['actual_speed']>=0 for s in samples) and samples[-1]['invalid_wire']>before)
            f.phase(.35,0);old=copy.deepcopy(f.last_command);old['direction']=1;old['active']=True;old['seq']+=1;f.seq=max(f.seq,old['seq']);f.phase(.3,send=False);f.send(old);samples=f.phase(.3,send=False);check('延迟旧包不能重新续期',still(samples[-3:]))
            samples=f.phase(.5,transform=lambda p:p.update(session='f'*32));check('错误会话不能驱动',still(samples))
            samples=f.phase(.4,transform=lambda p:p.update(nonce='e'*32));check('错误接收随机数不能驱动',still(samples))
            samples=f.phase(.4,transform=lambda p:p.update(receiver_id='d'*32));check('错误接收对象不能驱动',still(samples))
            samples=f.phase(.4,transform=lambda p:p.update(version=2));check('不兼容协议不能驱动',still(samples))
            samples=f.phase(.4,transform=lambda p:p.update(speed=999));check('越界速度不能驱动',still(samples))
            f.phase(.4,0)
            # Authenticated burst shares ONE issued ticket. It cannot count as two observations.
            hb=f.last_heartbeat
            for _ in range(120):f.send(f.packet(hb))
            samples=f.phase(.55,send=False);check('签名突发包不能伪造连续确认',still(samples))
            for _ in range(260):f.sock.sendto(b'invalid-packet'*100,f.address)
            samples=f.phase(.65,send=False);check('坏包洪泛后保持停止',still(samples[-4:]) and samples[-1]['invalid_wire']>0)
            samples=f.phase(1.4);check('洪泛后正常链路恢复',samples[-1]['actual_speed']>.2)
            samples=f.phase(.65,3);check('左转实际回执',any(s['actual_yaw_rate']<-10 for s in samples) and abs(samples[-1]['actual_speed'])<.01)
            samples=f.phase(.65,4);check('右转平滑换向',any(s['actual_yaw_rate']>10 for s in samples))
            samples=f.phase(.5,0);check('转向后停止',still(samples[-3:]))
            if args.unity:
                f.phase(20.5)
                samples=f.phase(.8)
                check('Unity碰撞后真实位移停止且目标仍前进',abs(samples[-1]['actual_speed'])<.005 and samples[-1]['target_speed']>.3 and samples[-1]['z']<10 and samples[-1]['rendered_frames']>0)
                f.phase(.4,0)
            metrics['max_abs_speed']=max(abs(s['actual_speed']) for s in f.samples);metrics['max_abs_yaw_rate']=max(abs(s['actual_yaw_rate']) for s in f.samples);metrics['final_rejected']=f.samples[-1]['rejected'];metrics['final_invalid_wire']=f.samples[-1]['invalid_wire'];metrics['final_queue_drops']=f.samples[-1]['queue_drops']
            check('全程速度限制',metrics['max_abs_speed']<=.60001 and metrics['max_abs_yaw_rate']<=40.001)
        finally:f.close()
    report=dict(checked_at=datetime.datetime.now().astimezone().isoformat(),status='pass' if all(c['passed'] for c in cases) else 'failed',scope=('real loopback UDP into Unity Player, actual applied pose; not wearable EEG interference validation' if args.unity else 'real loopback UDP into compiled shared C# core; not Unity engine or wearable EEG interference validation'),build_id=(f.samples[-1].get('build_id') if f.samples else None),passed=sum(c['passed'] for c in cases),cases=cases,metrics=metrics)
    (ROOT/('outputs/unity_udp_validation.json' if args.unity else 'outputs/receiver_udp_validation.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(metrics,ensure_ascii=False,indent=2))
    if report['status']!='pass':raise SystemExit(1)
