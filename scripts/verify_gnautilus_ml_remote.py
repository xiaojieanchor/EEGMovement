"""Explicit synthetic CUDA validation; artifacts stay outside the production registry."""
import json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.ssh import ComputeBridge

def main():
    bridge=ComputeBridge(ROOT);bridge.connect(os.environ.pop('EEG_ADMIN_PASSWORD'))
    profile=bridge.profile;base=profile['root'];manifest=bridge.command("import json\nfrom pathlib import Path\nprint((Path("+repr(base)+")/'service.json').read_text())")
    release=base+'/releases/'+manifest['release'];check=base+'/validation';files=['test_gnautilus_ml_v1.py','test_gnautilus_ml_service.py','test_gnautilus_ml_bridge.py','test_gnautilus_ml_e2e.py','test_gnautilus_ml_checkpoint.py','test_gnautilus_ml_import.py','test_gnautilus_transport.py']
    files.append('test_gnautilus_ml_boundaries.py')
    with bridge.client.open_sftp() as s:
        for folder in [check,check+'/tests',check+'/hub']:
            try:s.mkdir(folder,0o700)
            except OSError:pass
        with s.open(check+'/tests/__init__.py','w') as f:f.write('')
        with s.open(check+'/hub/__init__.py','w') as f:f.write('')
        s.put(str(ROOT/'hub/ml_runtime.py'),check+'/hub/ml_runtime.py')
        for name in files:
            if 'bridge' not in name:s.put(str(ROOT/'tests'/name),check+'/tests/'+name)
    code=r'''import json,os,sys,subprocess,time,tempfile
from pathlib import Path
base=Path(BASE);release=base/'releases'/RELEASE;check=base/'validation';py=base/'.venv/bin/python'
env=dict(os.environ,CUDA_VISIBLE_DEVICES=GPU,PYTHONPATH=str(release)+':'+str(check),OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
r=subprocess.run([str(py),'-m','pytest',str(check/'tests'),'-q','--tb=short'],cwd=check,env=env,capture_output=True,text=True,timeout=120)
print(json.dumps({'synthetic_pytest_exit':r.returncode,'output':r.stdout[-6000:],'stderr':r.stderr[-500:]}))
'''.replace('BASE',repr(base)).replace('RELEASE',repr(manifest['release'])).replace('GPU',repr(profile['gpu_uuid']))
    result=bridge.command(code,timeout=150);print(json.dumps(result,ensure_ascii=False));out=ROOT/'docs/gnautilus/ml_20261023/remote-tests.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if result['synthetic_pytest_exit']:raise RuntimeError('Remote synthetic validation failed')
    code=r'''import json,os,subprocess
from pathlib import Path
base=Path(BASE);release=base/'releases'/RELEASE
script="""import json,os,time,tempfile
import numpy as np,torch
from pathlib import Path
from unittest.mock import patch
from gnautilus_ml.algorithms import Classifier
from gnautilus_ml.artifacts import save_artifact,load_artifact
torch.set_num_threads(2);rng=np.random.default_rng(17);channels=['Fp1','Fp2','AF3','AF4','F7','F8','F3','F4','FC5','FC3','FC1','FCz','FC2','FC4','FC6','C5','C3','C1','Cz','C2','C4','C6','CP5','CP3','CP1','CPz','CP2','CP4','CP6','P3','Pz','P4']
x=rng.normal(size=(60,32,384)).astype('float32');y=np.tile(np.arange(5),12);g=np.repeat(np.arange(3),20);rows=[]
for algorithm in ['bandpower_lda','csp_lda','gaze_lda','hybrid','cnn_gru']:
 with patch('gnautilus_ml.algorithms.authorize_training'),patch('gnautilus_ml.algorithms.compute_device',return_value='cuda:0'):
  model=Classifier(algorithm,channels,256,max_epochs=2).fit(x,y,g)
 with tempfile.TemporaryDirectory(prefix='eeg-synthetic-') as folder:
  p=save_artifact(folder,model,{'evaluation_stage':'synthetic'});loaded,meta=load_artifact(p);np.testing.assert_allclose(model.predict(x[:2])['probabilities'],loaded.predict(x[:2])['probabilities'],atol=1e-5)
  times=[]
  for _ in range(10):
   torch.cuda.synchronize();t=time.perf_counter();loaded.predict(x[:1]);torch.cuda.synchronize();times.append((time.perf_counter()-t)*1000)
  rows.append({'algorithm':algorithm,'roundtrip_export_equal':True,'compute_p50_ms':float(np.median(times)),'compute_p95_ms':float(np.percentile(times,95))})
print(json.dumps({'kind':'synthetic_only','gpu_name':torch.cuda.get_device_name(0),'gpu_visible':os.environ['CUDA_VISIBLE_DEVICES'],'pid':os.getpid(),'results':rows,'real_eeg':False}))
"""
env=dict(os.environ,CUDA_VISIBLE_DEVICES=GPU,PYTHONPATH=str(release),OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
r=subprocess.run([str(base/'.venv/bin/python'),'-c',script],env=env,capture_output=True,text=True,timeout=90)
if r.returncode:raise RuntimeError(r.stderr[-4000:])
print(r.stdout)
'''.replace('BASE',repr(base)).replace('RELEASE',repr(manifest['release'])).replace('GPU',repr(profile['gpu_uuid']))
    result=bridge.command(code,timeout=120);print(json.dumps(result,ensure_ascii=False));(out.parent/'a100-synthetic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');bridge.disconnect()
if __name__=='__main__':main()
