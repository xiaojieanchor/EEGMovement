"""Observe the installed Quest app over LAN; never synthesizes motion or UI clicks."""
import argparse,datetime,hashlib,io,json,time,urllib.request
from pathlib import Path
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=3) as response:return json.load(response)

def main():
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=60);p.add_argument('--wait',type=int,default=180);args=p.parse_args()
    build=json.loads(Path('G:/Unity/android-build-status.json').read_text(encoding='utf-8-sig'));identity=build['build_id']
    deadline=time.monotonic()+args.wait;rows=[];frame=None;initial=None
    while time.monotonic()<deadline:
        vr=get('/api/vr/status')['vr'];q=get('/api/quest/status')
        peer=next((x for x in vr['clients'] if x['online'] and x['telemetry'].get('build_id')==identity and x['telemetry'].get('xr_active')),None)
        if peer and q['mirror_fresh']:initial=peer;break
        time.sleep(.5)
    if initial is None:raise RuntimeError('Wear headset with the current app foreground; no fresh current-build XR/mirror observed')
    start=time.monotonic();errors=[];first_seq=q['frame_seq']
    while time.monotonic()-start<args.seconds:
        try:
            vr=get('/api/vr/status')['vr'];q=get('/api/quest/status')
            candidates=[x for x in vr['clients'] if x['online'] and x['telemetry'].get('build_id')==identity and x['telemetry'].get('xr_active')]
            peer=candidates[0] if len(candidates)==1 else None
            rows.append(dict(elapsed_s=round(time.monotonic()-start,3),receiver_id=peer['receiver_id'] if peer else None,online=bool(peer),telemetry_age_ms=peer.get('telemetry_age_ms') if peer else None,telemetry=peer.get('telemetry') if peer else None,mirror=q))
            if q['mirror_fresh'] and (frame is None or len(rows)%10==0):
                with urllib.request.urlopen('http://127.0.0.1:8765/api/quest/frame.jpg',timeout=3) as response:frame=response.read()
        except OSError as error:errors.append(type(error).__name__)
        time.sleep(.2)
    image=Image.open(io.BytesIO(frame));pixels=np.asarray(image)
    live=[r for r in rows if r['online'] and r['telemetry_age_ms']<300]
    generations={r['receiver_id'] for r in live}
    advancing=any(len(values:=[r['telemetry']['rendered_frames'] for r in live if r['receiver_id']==rid])>=2 and max(values)>min(values) for rid in generations)
    checks=[dict(id='installed_build_XR_rendering',passed=advancing),
        dict(id='actual_mirror_jpeg',passed=image.size==(960,540) and float(pixels.std())>5),
        dict(id='LAN_frames_advance',passed=rows[-1]['mirror']['frame_seq']>first_seq),
        dict(id='safe_default_no_TEST',passed=all(not r['telemetry'] or r['telemetry']['mode']=='LIVE' and r['telemetry']['state']=='STOP' and abs(r['telemetry']['actual_speed'])<.001 and abs(r['telemetry']['actual_yaw_rate'])<.001 for r in rows)),
        dict(id='not_stale_full_window',passed=len(live)/max(1,len(rows))>=.95 and sum(r['mirror']['mirror_fresh'] for r in rows)/max(1,len(rows))>=.95),
        dict(id='no_poll_errors',passed=not errors)]
    report=dict(status='pass' if all(c['passed'] for c in checks) else 'needs_attention',checked_at=datetime.datetime.now().astimezone().isoformat(),build_id=identity,apk_sha256=build['output_sha256'],duration_s=round(time.monotonic()-start,2),cases=checks,observations=len(rows),receiver_generations_seen=len(generations),errors=errors,mirror_dimensions=list(image.size),mirror_sha256=hashlib.sha256(frame).hexdigest(),scope='Actual Quest app XR rendering, Wi-Fi command ACK and application-only spectator preview. An explicit UI reconnect/reset can change receiver ID; it is recorded and must remain the sole current-build XR peer. No EEG intention, UI ergonomic acceptance or physical display latency claim.',rows=rows)
    (ROOT/'outputs/quest_live_preview.jpg').write_bytes(frame)
    (ROOT/'outputs/quest_runtime_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False))
    if report['status']!='pass':raise SystemExit(1)
if __name__=='__main__':main()
