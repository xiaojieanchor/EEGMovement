"""One minute real execution chain under bounded local network noise/reconnect.
No account credentials or pairing key are read by this test.
"""
import argparse,datetime,json,math,socket,threading,time,urllib.request
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];BASE='http://127.0.0.1:8765';ROLE='csharp_harness';TARGET=None
sys.path.insert(0,str(ROOT))
from scripts.execution_target import ExecutionTarget, wait_for_target

def get():
    with urllib.request.urlopen(BASE+'/api/vr/status',timeout=3) as r:return json.load(r)
def post(path,data=None):
    if path=='/api/vr/test':
        data=dict(data or {});data['control_generation']=get()['vr']['control_generation']
    req=urllib.request.Request(BASE+path,data=json.dumps(data or {}).encode(),headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
    with urllib.request.urlopen(req,timeout=3) as r:return json.load(r)
def noise():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        start=time.monotonic()
        while time.monotonic()-start<1.4:
            sock.sendto(b'not-an-authenticated-EEG-command'*8,('127.0.0.1',19771));time.sleep(.002)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/execution_soak_validation.json');parser.add_argument('--role',choices=['unity','csharp_harness'],default='csharp_harness');args=parser.parse_args();ROLE=args.role
    current_build=json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig')).get('build_id') if ROLE=='unity' else None
    initial=get();assert initial['source']['mode']=='live' and initial['source']['connected'] and initial['source']['fresh'], 'Official live Socket required'
    TARGET=wait_for_target(get,ROLE,current_build);bad_wire_before=initial['vr']['stats'].get('invalid_wire',0)
    started=time.monotonic();rows=[];events=set();errors=[];injection=None;seen_server_down=False
    try:
        while time.monotonic()-started<60:
            elapsed=time.monotonic()-started
            try:
                s=get();assert s['source']['mode']=='live', 'Source changed away from official live Socket';peer=TARGET.select(s); t=peer['telemetry'];rid=peer['receiver_id']
                if elapsed>=5 and 'forward' not in events:post('/api/vr/test',{'action':'FORWARD','duration_s':4,'receiver_id':rid});events.add('forward')
                if elapsed>=6 and 'noise' not in events:injection=threading.Thread(target=noise);injection.start();events.add('noise')
                if elapsed>=13 and 'left' not in events:post('/api/vr/test',{'action':'LEFT','duration_s':2,'receiver_id':rid});events.add('left')
                if elapsed>=20 and 'back' not in events:post('/api/vr/test',{'action':'BACKWARD','duration_s':3,'receiver_id':rid});events.add('back')
                if elapsed>=21 and 'ssh_reconnect' not in events:post('/api/server/connect');events.add('ssh_reconnect')
                seen_server_down=seen_server_down or (20<elapsed<25 and not s['server']['worker_connected'])
                rows.append(dict(elapsed_s=round(elapsed,3),source_mode=s['source']['mode'],source_fresh=s['source']['fresh'],server=s['server']['worker_connected'],online=peer['online'],mode=t['mode'],reason=t['reason'],speed=t['actual_speed'],yaw_rate=t['actual_yaw_rate'],applied_seq=peer['applied_seq'],watchdog_stops=t['watchdog_stops'],watchdog_brake_ms=t['watchdog_brake_ms'],bad_wire=s['vr']['stats'].get('invalid_wire',0),limited=s['vr']['stats'].get('rate_limited',0),ack_ms=peer['command_to_ack_ms'],input_ack_p95=s['vr']['input_to_execution_ack_p95_ms']))
            except Exception as e:errors.append(type(e).__name__+': '+str(e))
            if len(rows)%200==1:print(json.dumps({'elapsed_s':round(elapsed),'observations':len(rows),'errors':len(errors)}),flush=True)
            time.sleep(.05)
    finally:
        post('/api/vr/stop')
        if injection:injection.join(timeout=2)
    last=get();cases=[]
    def check(label,ok):cases.append(dict(label=label,passed=bool(ok)))
    check('一分钟真实执行链路持续观测',len(rows)>700 and not errors)
    check('中枢坏包注入有记录且未超速',rows[-1]['bad_wire']>bad_wire_before and all(abs(r['speed'])<=.60001 and abs(r['yaw_rate'])<=40.001 for r in rows))
    check('无效签名洪泛未造成LIVE误动',all(abs(r['speed'])<1e-6 and abs(r['yaw_rate'])<1e-6 for r in rows if r['mode']=='LIVE'))
    check('SSH短断连被观察并恢复',seen_server_down and last['server']['worker_connected'] and any(r['reason'] in ['test_invalid_or_server_stale','server_stale','eeg_stream_stale'] for r in rows if 20<r['elapsed_s']<25))
    check('故障后执行序号继续推进',rows[-1]['applied_seq']>rows[0]['applied_seq'] and rows[-1]['mode']=='LIVE' and abs(rows[-1]['speed'])<1e-6)
    values=[r['ack_ms'] for r in rows if r['ack_ms'] is not None]
    report=dict(checked_at=datetime.datetime.now().astimezone().isoformat(),status='pass' if all(c['passed'] for c in cases) else 'failed',scope='60s real '+ROLE+' pipeline with synthetic TEST motion, malformed UDP noise and explicit SSH reconnect; not Quest acceptance',build_id=peer['telemetry'].get('build_id'),duration_s=round(time.monotonic()-started,3),passed=sum(c['passed'] for c in cases),cases=cases,errors=errors,observations=len(rows),source_fresh_fraction=sum(r['source_fresh'] for r in rows)/max(1,len(rows)),ack_p95_ms=sorted(values)[math.ceil(.95*len(values))-1] if values else None,final_stats=last['vr']['stats'],watchdog_brake_ms=max(r['watchdog_brake_ms'] for r in rows),rows=rows)
    (ROOT/args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False,indent=2))
    if report['status']!='pass':raise SystemExit(1)
