"""Read-only live Hub checks, with optional explicit in-memory SSH login.

Never creates a dataset, starts a training task or enables inference/motion.
"""
import hashlib,json,os,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];BASE='http://127.0.0.1:8765'

def get(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as r:return json.load(r)

def main():
    password=os.environ.pop('EEG_ADMIN_PASSWORD',None)
    if password:
        request=urllib.request.Request(BASE+'/api/server/connect',data=json.dumps({'password':password}).encode(),headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
        with urllib.request.urlopen(request,timeout=10) as r:r.read()
        password=None
    for _ in range(40):
        model=get('/api/models')
        if model['remote_fresh']:break
        time.sleep(.25)
    else:raise RuntimeError('Private server state is not fresh; connect it first')
    paths=['/api/status','/api/preparation','/api/training/jobs','/api/ml/capabilities','/api/intent/status','/api/models','/api/ml/datasets']
    result={p:get(p) for p in paths};state=result['/api/status']
    assert state['server']['worker_connected'] and state['server']['health_age_ms']<5000
    assert not result['/api/models']['read_only'] and not result['/api/intent/status']['inference_enabled']
    assert len(result['/api/ml/capabilities']['algorithms'])==5 and result['/api/preparation']['schema']=='gnautilus.preparation.v1'
    served={}
    for name in ['gnautilus-ml.js','intent.js','model-library.js','ui-state.js','lab.js']:
        with urllib.request.urlopen(BASE+'/'+name) as r:raw=r.read()
        assert raw==(ROOT/'hub/web'/name).read_bytes();served[name]=hashlib.sha256(raw).hexdigest()
    with urllib.request.urlopen(BASE+'/training') as r:html=r.read().decode()
    assert 'data-device-transition="gnautilus"' in html and 'trainingWorkbench' in html
    out=dict(live_apis=result,served_js_sha256=served,browser_visual_tested=False,real_eeg=False)
    (ROOT/'docs/gnautilus/ml_20261023/hub-live-check.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(dict(private_host=state['server']['host'],worker_connected=True,algorithms=5,library_read_only=False,jobs=len(result['/api/training/jobs']['jobs']),registered_models=len(model['models']),inference=False,served_assets_exact=True)))

if __name__=='__main__':main()
