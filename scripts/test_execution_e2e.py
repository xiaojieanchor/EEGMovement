"""Tests the real running hub and receiver; EEG intent decoding stays disabled."""
import argparse,datetime,json,math,time,urllib.request
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];BASE='http://127.0.0.1:8765';ROLE='csharp_harness';TARGET=None
sys.path.insert(0,str(ROOT))
from scripts.execution_target import ExecutionTarget, wait_for_target, projected_displacement

def get(path='/api/vr/status'):
    with urllib.request.urlopen(BASE+path,timeout=4) as r:return json.load(r)
def post(path,data=None):
    if path=='/api/vr/test':
        data=dict(data or {});data['control_generation']=get()['vr']['control_generation']
    q=urllib.request.Request(BASE+path,data=json.dumps(data or {}).encode(),headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
    with urllib.request.urlopen(q,timeout=4) as r:return json.load(r)
def sample(seconds,allowed_mode='live'):
    rows=[];started=time.monotonic()
    while time.monotonic()-started<seconds:
        state=get();assert state['source']['mode']==allowed_mode, 'Unexpected data source mode';selected=TARGET.select(state)
        rows.append(dict(time=time.monotonic(),receiver=selected,source_mode=state['source']['mode'],source_fresh=state['source']['fresh'],server_connected=state['server']['worker_connected'],test=state['vr']['test_active']))
        time.sleep(.025)
    return rows

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/execution_e2e.json');parser.add_argument('--role',choices=['unity','csharp_harness'],default='csharp_harness');args=parser.parse_args();ROLE=args.role
    initial=get();assert initial['source']['mode']=='live' and initial['source']['connected'] and initial['source']['fresh'] and initial['server']['worker_connected'],'Real EEG/GPU link must be up for this test'
    
    if ROLE=='csharp_harness':post('/api/vr/receiver/start')
    current_build=json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig')).get('build_id') if ROLE=='unity' else None
    TARGET=wait_for_target(get,ROLE,current_build)
    deadline=time.monotonic()+5
    while TARGET.select(get())['applied_seq']<=0:
        if time.monotonic()>=deadline:raise RuntimeError('No applied handshake before deadline')
        time.sleep(.03)
    cases=[];metrics={};allrows=[]
    def check(label,ok,detail=None):
        cases.append(dict(label=label,passed=bool(ok),detail=detail));print(('PASS ' if ok else 'FAIL ')+label,flush=True)
    def speed(row):return row['receiver']['telemetry']['actual_speed']
    attempts=[]
    def motion_trial(action,seconds,condition,duration_s=3,require_uninterrupted=False):
        for attempt in range(1,5):
            deadline=time.monotonic()+8
            while True:
                full=get('/api/status')
                if full['source']['mode']=='live' and full['source']['fresh'] and full['result_fresh'] and full['result_age_ms']<100:break
                if time.monotonic()>=deadline:raise RuntimeError('No fresh GPU return for explicit test')
                time.sleep(.05)
            before_state=get();before=TARGET.select(before_state)['telemetry'];revoked_before=before_state['vr']['stats'].get('tests_revoked_on_stale_link',0);started=time.monotonic()
            post('/api/vr/test',dict(action=action,receiver_id=rid,duration_s=duration_s))
            values=sample(seconds);allrows.extend(values)
            interrupted=get()['vr']['stats'].get('tests_revoked_on_stale_link',0)>revoked_before
            passed=condition(before,values) and (not require_uninterrupted or not interrupted)
            attempts.append(dict(action=action,attempt=attempt,duration_s=duration_s,passed=passed,transport_revoked=interrupted,initial_pose={k:before[k] for k in ['x','z','yaw']}))
            if passed or not interrupted or attempt==4:return before,values,started,passed
            # A new explicit operator action is issued only after freshness returns.
            # Every revoked attempt stays in the report; the product never resumes an old TEST.
            post('/api/vr/stop');time.sleep(.15)
    def projection(before,last):
        return projected_displacement(before,last['receiver']['telemetry'])

    try:
        rows=sample(.7);allrows+=rows;check('真实EEG/GPU链路上的LIVE停止回执',bool(rows) and all(r['source_mode']=='live' and speed(r)==0 and r['receiver']['telemetry']['mode']=='LIVE' for r in rows) and rows[-1]['receiver']['applied_seq']>0)
        rid=rows[-1]['receiver']['receiver_id']
        before,rows,started,passed=motion_trial('FORWARD',1.25,lambda before,values:any(speed(r)>.01 for r in values) and projection(before,values[-1])>.1)
        moving=next((r for r in rows if speed(r)>.01),None);metrics['test_request_to_movement_ack_ms']=round((moving['time']-started)*1000,3) if moving else None
        check('TEST前进实际应用而非只发送',passed,metrics['test_request_to_movement_ack_ms'])
        started=time.monotonic();post('/api/vr/stop');rows=sample(.45);allrows+=rows;stopped=next((r for r in rows if abs(speed(r))<1e-5),None);metrics['operator_stop_ack_ms']=round((stopped['time']-started)*1000,3) if stopped else None
        check('操作停止回执',stopped is not None and metrics['operator_stop_ack_ms']<200,metrics['operator_stop_ack_ms'])
        before,rows,started,passed=motion_trial('BACKWARD',.8,lambda before,values:any(speed(r)<-.1 for r in values) and projection(before,values[-1])<-.01);check('TEST后退实际应用',passed)
        started=time.monotonic();post('/api/source',dict(mode='paused'));rows=sample(.5,allowed_mode='paused');allrows+=rows;stopped=next((r for r in rows if abs(speed(r))<1e-5 and r['receiver']['telemetry']['reason'] in ['test_invalid_or_server_stale','eeg_stream_stale','server_stale']),None);metrics['capture_pause_to_stop_ack_ms']=round((stopped['time']-started)*1000,3) if stopped else None
        check('真实采集暂停触发执行停止',stopped is not None and metrics['capture_pause_to_stop_ack_ms']<200,metrics['capture_pause_to_stop_ack_ms'])
        post('/api/source',dict(mode='live'));deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=get()
            if state['source']['fresh'] and state['server']['worker_connected'] and any(c['online'] for c in state['vr']['clients']):break
            time.sleep(.1)
        rows=sample(1.4);allrows+=rows;check('采集恢复后LIVE仍保持停止',rows[-1]['source_fresh'] and all(r['source_mode']=='live' and speed(r)==0 and r['receiver']['telemetry']['mode']=='LIVE' for r in rows))
        for action,sign in [('LEFT',-1),('RIGHT',1)]:
            before,rows,started,passed=motion_trial(action,.65,lambda before,values:any(sign*r['receiver']['telemetry']['actual_yaw_rate']>10 for r in values))
            check('TEST'+action+'实际转向回执',passed)
            post('/api/vr/stop');sample(.15)
        before,rows,started,passed=motion_trial('FORWARD',1.2,lambda before,values:any(speed(r)>.01 for r in values) and values[-1]['receiver']['telemetry']['mode']=='LIVE' and abs(speed(values[-1]))<1e-5,duration_s=.6,require_uninterrupted=True);check('测试租约到期自动回到LIVE停止',passed)
        metrics['explicit_operator_trials']=attempts
        state=get();metrics['command_to_ack_p95_ms']=state['vr']['command_to_ack_p95_ms'];metrics['input_to_stop_execution_ack_p95_ms']=state['vr']['input_to_execution_ack_p95_ms']
        check('命令与最终回执序号保持推进',rows[-1]['receiver']['applied_seq']>rows[0]['receiver']['applied_seq'])
    finally:
        post('/api/vr/stop')
        if get('/api/status')['source']['mode']!='live':post('/api/source',dict(mode='live'))
    report=dict(checked_at=datetime.datetime.now().astimezone().isoformat(),status='pass' if all(c['passed'] for c in cases) else 'failed',scope='real official Socket + A100 diagnostic + '+ROLE+' application acknowledgements; explicit TEST motion, no trained intention model',build_id=allrows[-1]['receiver']['telemetry'].get('build_id'),cases=cases,passed=sum(c['passed'] for c in cases),metrics=metrics,observations=allrows)
    (ROOT/args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(metrics,ensure_ascii=False,indent=2))
    if report['status']!='pass':raise SystemExit(1)
