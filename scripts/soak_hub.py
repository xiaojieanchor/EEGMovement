"""Real live-link soak; stores compact diagnostic metrics, never credentials/raw EEG."""
import argparse,datetime,json,time,urllib.request
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8765/api/status'

def snapshot():
    with urllib.request.urlopen(URL,timeout=4) as r:return json.load(r)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--seconds',type=int,default=600);args=ap.parse_args()
    first=snapshot()
    if first['source']['mode']!='live':raise RuntimeError('Select real Socket input before this test')
    started_at=datetime.datetime.now().astimezone().isoformat();started=time.monotonic();rows=[];results={};violations=[];http_errors=[];last=first;progress=0
    while time.monotonic()-started<args.seconds:
        tick=time.monotonic()
        try:
            s=snapshot();last=s;src=s['source'];r=s['result'];server=s['server'];control=s['control']
            if src['mode']!='live':violations.append('Input changed away from LIVE')
            if control['enabled'] or control['state']!='STOP' or control['speed']!=0:violations.append('Unexpected control output')
            if s['result_fresh'] and r['source']!='live':violations.append('Historical source appeared current')
            if s['result_fresh'] and r:
                results[r['request_id']]={k:r[k] for k in ['request_id','sample_index_end','input_ready_to_return_ms','compute_ms','device','quality_valid']}
            rows.append(dict(elapsed_s=round(tick-started,3),fresh=src['fresh'],age_ms=src['age_ms'],hz=src['sfreq_measured'],samples=src['samples_total'],stats=src['stats_total'],connections=src['connections'],quality_valid=s['quality']['valid'],good_channels=s['quality']['good_channels'],worker_connected=server['worker_connected'],result_fresh=s['result_fresh'],returns=s['performance']['return_count'],errors=s['performance']['errors']))
        except Exception as e:http_errors.append(dict(elapsed_s=round(tick-started,2),error=type(e).__name__))
        elapsed=time.monotonic()-started
        if elapsed>=progress:
            print(json.dumps({'elapsed_s':round(elapsed),'polls':len(rows),'unique_returns':len(results),'http_errors':len(http_errors),'source_fresh':last['source']['fresh']},ensure_ascii=False),flush=True);progress+=45
        time.sleep(max(0,.2-(time.monotonic()-tick)))
    duration=time.monotonic()-started
    a=np.array([r['input_ready_to_return_ms'] for r in results.values()]);g=np.array([r['compute_ms'] for r in results.values()])
    delta={k:last['source']['stats_total'][k]-first['source']['stats_total'][k] for k in first['source']['stats_total']}
    report=dict(started_at=started_at,finished_at=datetime.datetime.now().astimezone().isoformat(),duration_s=round(duration,3),measurement='last input packet received by local hub to GPU reply accepted, sampled unique replies at 5 Hz; excludes upstream Bluetooth, 1 s evidence window, decision confirmation and VR rendering',first_source=first['source'],last_source=last['source'],packet_delta=delta,new_connections=last['source']['connections']-first['source']['connections'],sample_delta=last['source']['samples_total']-first['source']['samples_total'],observed_samples_per_second=round((last['source']['samples_total']-first['source']['samples_total'])/duration,3),unique_observed_returns=len(results),accepted_return_delta=last['performance']['return_count']-first['performance']['return_count'],processing_error_delta=last['performance']['errors']-first['performance']['errors'],response_drop_delta={k:v-first['performance'].get('response_drops',{}).get(k,0) for k,v in last['performance'].get('response_drops',{}).items()},polls=len(rows),source_fresh_fraction=round(sum(r['fresh'] for r in rows)/max(1,len(rows)),5),worker_connected_fraction=round(sum(r['worker_connected'] for r in rows)/max(1,len(rows)),5),fresh_return_fraction=round(sum(r['result_fresh'] for r in rows)/max(1,len(rows)),5),return_ms={str(p):round(float(np.percentile(a,p)),3) for p in [50,95,99,100]} if len(a) else {},gpu_ms={str(p):round(float(np.percentile(g,p)),3) for p in [50,95,99,100]} if len(g) else {},software_quality_pass_polls=sum(r['quality_valid'] for r in rows),control='STOP throughout every observed poll; no calibrated decoder or VR receiver',http_errors=http_errors,violations=sorted(set(violations)),server=last['server'],rows=rows,returns=list(results.values()))
    out=ROOT/('outputs/live_soak_'+str(args.seconds)+'s.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['rows','returns','first_source','last_source','server']},ensure_ascii=False,indent=2),flush=True)
    if violations or http_errors or not len(a):raise SystemExit(1)
