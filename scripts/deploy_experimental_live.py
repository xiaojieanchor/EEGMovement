"""Versioned idle-only private deployment of the one operator-authorized v3 copy."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.common import atomic,digest,file_hash
from gnautilus_ml.ssh import ComputeBridge
from gnautilus_ml.experimental_live import SOURCE_ID,WEIGHTS_SHA256
from scripts.navigation_jepa_admin import mkdirs


def deploy(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);b=ComputeBridge(ROOT)
    if not b.connect():raise RuntimeError(b.status['last_error'])
    base=b.profile['root'];previous=None;changed=False
    try:
        snap=b.call('GET','/snapshot')
        if snap['inference']['inference_enabled'] or any(j['state'] in ['running','queued','cancel_requested'] for j in snap['jobs']):raise ValueError('Compute must be idle')
        prior_model=snap.get('loaded') or snap.get('selected')
        previous=b.command('import json\nfrom pathlib import Path\nb=Path('+repr(base)+')\nprint(json.dumps({"release":(b/"current-release").read_text().strip()}))')['release']
        files=list((ROOT/'gnautilus_ml').glob('*.py'))+list((ROOT/'gnautilus_acquisition').glob('*.py'))
        sources={p.relative_to(ROOT).as_posix():file_hash(p) for p in files};release=digest(sources);remote=base+'/releases/'+release;validation=base+'/validation/immersive-'+release[:12]
        with b.client.open_sftp() as s:
            s.get_channel().settimeout(30)
            for f in files:
                target=remote+'/'+f.relative_to(ROOT).as_posix();mkdirs(s,str(Path(target).parent).replace('\\','/'),base);s.put(str(f),target)
            for name,value in [('compute.json',b.profile),('source.json',sources)]:
                with s.open(remote+'/'+name,'w') as stream:stream.write(json.dumps(value))
            for f in (ROOT/'tests').glob('test_*.py'):
                target=validation+'/tests/'+f.name;mkdirs(s,validation+'/tests',base);s.put(str(f),target)
            with s.open(validation+'/tests/__init__.py','w') as stream:stream.write('')
        if sources!={p.relative_to(ROOT).as_posix():file_hash(p) for p in files}:raise RuntimeError('Source changed during deployment upload')
        setup='import os,json,subprocess\nfrom pathlib import Path\nb=Path('+repr(base)+');release='+repr(release)+'\nenv=dict(os.environ,CUDA_VISIBLE_DEVICES='+repr(b.profile['gpu_uuid'])+',PYTHONPATH=str(b/"releases"/release)+":"+'+repr(validation)+',OMP_NUM_THREADS="2",MKL_NUM_THREADS="2")\n'
        code=setup+'r=subprocess.run([str(b/".venv/bin/python"),"-m","pytest","tests/test_intent_repair.py","tests/test_eye_evidence.py","tests/test_immersive_model.py","-q","--tb=short"],env=env,cwd='+repr(validation)+',capture_output=True,text=True,timeout=180)\nprint(json.dumps(dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr[-2000:])))'
        tests=b.command(code,timeout=210);atomic(out/'remote-tests.json',tests)
        if tests['returncode']:raise RuntimeError(tests['stdout']+tests['stderr'])
        # Verify the deployed metadata copy and raw-source prediction equivalence before replacing the service.
        verify=ROOT/'scripts/verify_immersive_model.py'
        with b.client.open_sftp() as s:s.put(str(verify),validation+'/verify_model.py')
        code=setup+'r=subprocess.run([str(b/".venv/bin/python"),'+repr(validation+'/verify_model.py')+',"--base",str(b),"--output",'+repr(validation)+'],env=env,cwd='+repr(validation)+',capture_output=True,text=True,timeout=180)\nprint(json.dumps(dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr[-2500:])))'
        result=b.command(code,timeout=210);atomic(out/'equivalence-process.json',result)
        if result['returncode']:raise RuntimeError(result['stdout']+result['stderr'])
        with b.client.open_sftp() as s:
            s.get(validation+'/verification.json',str(out/'model-verification.json'));s.get(validation+'/replay.json',str(out/'replay.json'));s.get(validation+'/replay-signals.npz',str(out/'replay-signals.npz'))
        verified=json.loads((out/'model-verification.json').read_text('utf-8'));mid=verified['deployment_model_id']
        # Recheck idle after potentially long verification, then switch only this project's service.
        snap=b.call('GET','/snapshot')
        if snap['inference']['inference_enabled'] or any(j['state'] in ['running','queued','cancel_requested'] for j in snap['jobs']):raise ValueError('Compute became busy; deployment not applied')
        code='import json,subprocess\nfrom pathlib import Path\np=Path('+repr(base+'/current-release')+');p.write_text('+repr(release)+')\nr=subprocess.run(["systemctl","--user","restart","eegmovement-gnautilus.service"],capture_output=True,text=True)\nif r.returncode:raise RuntimeError(r.stderr)\nprint(json.dumps({"release":'+repr(release)+'}))'
        changed=True;b.command(code,timeout=30)
        for _ in range(80):
            try:
                b.call('GET','/health');break
            except Exception:time.sleep(.25)
        else:raise RuntimeError('Versioned service did not become ready')
        # Registry administration is private pinned SSH; no public relay can access it.
        code=setup+'import sys\nsys.path.insert(0,str(b/"releases"/release))\nfrom gnautilus_ml.registry import Registry\nfrom gnautilus_ml.common import read_json\np=b/"models"/'+repr(mid)+'\nRegistry(b/"state.sqlite").register(p,read_json(p/"manifest.json"))\nprint(json.dumps({"registered":'+repr(mid)+'}))'
        b.command(code)
        b.call('POST','/models/activate',dict(id=mid,weights_sha256=WEIGHTS_SHA256),timeout=30)
        snap=b.call('GET','/snapshot');assert snap['loaded']==mid and not snap['inference']['inference_enabled']
        health=b.call('GET','/health');assert health['gpu_uuid']==b.profile['gpu_uuid']
        receipt=dict(release=release,previous_release=previous,previous_model_id=prior_model,model_id=mid,source_model_id=SOURCE_ID,
            weights_sha256=WEIGHTS_SHA256,stage='experimental_live_v1',loaded=True,inference_enabled=False,
            gpu_uuid=health['gpu_uuid'],private_service_port=b.profile['service_port'],independent_test=False,real_device_acceptance=False)
        atomic(out/'deployment.json',receipt);print(json.dumps(receipt),flush=True)
    except BaseException:
        if changed and previous:
            rollback='import json,subprocess\nfrom pathlib import Path\nPath('+repr(base+'/current-release')+').write_text('+repr(previous)+')\nr=subprocess.run(["systemctl","--user","restart","eegmovement-gnautilus.service"],capture_output=True,text=True)\nprint(json.dumps({"rollback_returncode":r.returncode}))'
            atomic(out/'rollback.json',b.command(rollback,timeout=30))
            for _ in range(60):
                try:
                    b.call('GET','/health')
                    if prior_model:b.call('POST','/models/activate',dict(id=prior_model),timeout=30)
                    break
                except Exception:time.sleep(.25)
        raise
    finally:b.disconnect()

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='outputs/immersive-20261026');deploy(ap.parse_args().output)
