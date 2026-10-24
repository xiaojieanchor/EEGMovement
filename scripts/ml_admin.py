"""Deploy only the new isolated compute service. Password is read from stdin."""
import argparse,getpass,hashlib,json,os,secrets,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.ssh import ComputeBridge
from gnautilus_ml.common import read_json,file_hash,digest

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--deploy',action='store_true');ap.add_argument('--probe',action='store_true');args=ap.parse_args()
    bridge=ComputeBridge(ROOT);password=os.environ.pop('EEG_ADMIN_PASSWORD',None) or getpass.getpass('Server password: ')
    # Establish pinned SSH without assuming a service has already been installed.
    bridge.connect(password)
    if not bridge.client:raise RuntimeError(bridge.status.get('last_error'))
    probe=bridge.command("import json,subprocess,importlib.util\nfrom pathlib import Path\npaths=[str(p) for p in Path('/home/sysu/miniconda3/envs').glob('*/bin/python')]\nrows=[]\nfor py in paths:\n r=subprocess.run([py,'-c',\"import json,importlib.util;print(json.dumps({m:bool(importlib.util.find_spec(m)) for m in ['numpy','scipy','torch','mne'] }))\"],capture_output=True,text=True,timeout=20);rows.append({'python':py,'modules':r.stdout.strip(),'error':r.stderr[-200:]})\nprint(json.dumps({'runtimes':rows,'gpus':subprocess.check_output(['nvidia-smi','--query-gpu=uuid,memory.used','--format=csv,noheader'],text=True)}))",timeout=90)
    print(json.dumps(probe,ensure_ascii=False))
    if not args.deploy:return
    candidates=[r['python'] for r in probe['runtimes'] if r['modules'] and all(json.loads(r['modules']).get(m) for m in ['numpy','scipy','torch'])]
    if not candidates:raise RuntimeError('No existing compatible runtime; no environment was modified')
    py=candidates[0];p=bridge.profile;base=p['root']
    if bridge.status.get('worker_connected'):
        live=bridge.call('GET','/snapshot')
        if live.get('inference',{}).get('inference_enabled') or any(j['state'] in ['queued','waiting_resource','preparing','running','evaluating','exporting','registering'] for j in live.get('jobs',[])):raise RuntimeError('Compute service has inference or active/queued tasks; deployment must wait for idle')
    setup="""import json,subprocess
from pathlib import Path
base=Path(BASE);base.mkdir(parents=True,exist_ok=True);venv=base/'.venv';py=PY
if not (venv/'bin/python').exists():subprocess.run([py,'-m','venv','--system-site-packages',str(venv)],check=True,timeout=45)
check=subprocess.run([str(venv/'bin/python'),'-c','import mne,h5py,paramiko'],capture_output=True,text=True)
if check.returncode:
 result=subprocess.run([str(venv/'bin/python'),'-m','pip','install','mne>=1.6,<2','h5py>=3.10,<4','pytest>=8,<9','paramiko>=3.5,<5'],capture_output=True,text=True,timeout=180)
 if result.returncode:raise RuntimeError(result.stderr[-2000:])
print(json.dumps({'isolated_python':str(venv/'bin/python')}))
""".replace('BASE',repr(base)).replace('py=PY','py='+repr(py))
    setup_result=bridge.command(setup,timeout=240);py=setup_result['isolated_python'];print(json.dumps(setup_result))
    files=list((ROOT/'gnautilus_ml').glob('*.py'))+list((ROOT/'gnautilus_acquisition').glob('*.py'))
    source={str(f.relative_to(ROOT)).replace('\\','/'):file_hash(f) for f in files};release=digest(source);folder=base+'/releases/'+release
    with bridge.client.open_sftp() as s:
        for path in [base,base+'/releases',folder,folder+'/gnautilus_ml',folder+'/gnautilus_acquisition',base+'/data',base+'/models',base+'/datasets',base+'/protocols',base+'/logs']:
            try:s.mkdir(path,0o700)
            except OSError:
                s.stat(path)
        for f in files:s.put(str(f),folder+'/'+str(f.relative_to(ROOT)).replace('\\','/'))
        try:
            with s.open(base+'/service.token') as f:token=f.read().decode().strip()
        except FileNotFoundError:
            token=secrets.token_urlsafe(32)
            with s.open(base+'/service.token','w') as f:f.write(token)
            s.chmod(base+'/service.token',0o600)
        with s.open(folder+'/compute.json','w') as f:f.write(json.dumps(p))
        with s.open(folder+'/source.json','w') as f:f.write(json.dumps(source))
    code="""import json,os,subprocess,time,signal
from pathlib import Path
p=PROFILE;base=Path(p['root']);release=RELEASE;py=PY
unit='eegmovement-gnautilus.service';manager=subprocess.run(['systemctl','--user','show-environment'],capture_output=True,text=True)
if manager.returncode:raise RuntimeError('User service manager unavailable; deployment not changed')
pidfile=base/'service.json';old=json.loads(pidfile.read_text()) if pidfile.exists() else {};pid=old.get('pid')
subprocess.run(['systemctl','--user','stop',unit],capture_output=True)
if pid and Path(f'/proc/{pid}/cmdline').exists():
 cmd=Path(f'/proc/{pid}/cmdline').read_bytes().decode(errors='replace');owned='gnautilus_ml.worker' in cmd and str(base) in cmd
 if owned:
  os.kill(pid,signal.SIGTERM)
  for _ in range(30):
   if not Path(f'/proc/{pid}').exists():break
   time.sleep(.1)
launch="import json,os,sys\\nfrom pathlib import Path\\nb=Path(__file__).resolve().parent\\nr=(b/'current-release').read_text().strip()\\nf=b/'releases'/r\\np=json.loads((f/'compute.json').read_text())\\ne=dict(os.environ,CUDA_VISIBLE_DEVICES=p['gpu_uuid'],PYTHONPATH=str(f),OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')\\ns=b/'service.json.tmp'\\ns.write_text(json.dumps({'pid':os.getpid(),'release':r,'gpu_uuid':p['gpu_uuid']}))\\nos.replace(s,b/'service.json')\\nos.chdir(f)\\nos.execve(sys.executable,[sys.executable,'-u','-m','gnautilus_ml.worker','--root',str(b),'--release',r],e)\\n"
(base/'launch.py').write_text(launch)
pointer=base/'current-release';prior=pointer.read_text() if pointer.exists() else old.get('release','')
(base/'current-release.tmp').write_text(release);os.replace(base/'current-release.tmp',pointer)
folder=Path.home()/'.config/systemd/user';folder.mkdir(parents=True,exist_ok=True)
text='[Unit]\\nDescription=EEGMovement private g.Nautilus compute\\nAfter=network.target\\n[Service]\\nType=simple\\nExecStart='+py+' '+str(base/'launch.py')+'\\nRestart=always\\nRestartSec=3\\nTimeoutStopSec=20\\nUMask=0077\\nNoNewPrivileges=yes\\nStandardOutput=append:'+str(base/'logs/service.log')+'\\nStandardError=inherit\\n[Install]\\nWantedBy=default.target\\n'
(folder/unit).write_text(text)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
try:subprocess.run(['systemctl','--user','enable','--now',unit],check=True,capture_output=True)
except Exception:
 if prior:pointer.write_text(prior);subprocess.run(['systemctl','--user','restart',unit],capture_output=True)
 raise
print(json.dumps({'supervisor':unit,'release_directory':str(base/'releases'/release),'restart_policy':'always','previous_release':prior}))
""".replace('PROFILE',repr(p)).replace('RELEASE',repr(release)).replace('py=PY','py='+repr(py))
    print(json.dumps(bridge.command(code),ensure_ascii=False));bridge.token=token
    for attempt in range(160):
        try:print(json.dumps(bridge.call('GET','/health'),ensure_ascii=False));break
        except Exception:
            if attempt==159:raise
            time.sleep(.25)
    bridge.disconnect()
if __name__=='__main__':main()
