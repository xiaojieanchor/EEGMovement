"""Read-only SSH audit. Password is accepted only through hidden interactive stdin."""
import getpass,paramiko,json,hashlib,socket
from pathlib import Path
root=Path(__file__).resolve().parents[1];(root/'outputs').mkdir(exist_ok=True)
client=paramiko.SSHClient();client.load_system_host_keys();client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
password=getpass.getpass('SSH password (not saved): ')
sock=socket.socket();sock.settimeout(12);sock.bind(('172.29.30.38',0));sock.connect(('172.25.56.83',3322))
client.connect('172.25.56.83',port=3322,username='sysu',password=password,sock=sock,timeout=12,auth_timeout=15,look_for_keys=False,allow_agent=False)
password=None
key=client.get_transport().get_remote_server_key();fingerprint=hashlib.sha256(key.asbytes()).hexdigest()
code='''import os,sys,json,subprocess,shutil,importlib.util,socket
from pathlib import Path
def run(args):
 try:
  p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20);return {'returncode':p.returncode,'stdout':p.stdout[:16000],'stderr':p.stderr[:1000]}
 except Exception as e:return {'error':str(e)}
d={'hostname':socket.gethostname(),'python':sys.executable,'home':str(Path.home()),'uid':os.getuid(),'gpus':run(['nvidia-smi','--query-gpu=index,name,memory.total,memory.used,utilization.gpu','--format=csv,noheader']),'gpu_processes':run(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader']),'listeners':run(['ss','-lnt']),'disk':dict(zip(['total','used','free'],shutil.disk_usage(str(Path.home())))),'target_exists':(Path.home()/'EEGMovement').exists()}
d['modules']={m:bool(importlib.util.find_spec(m)) for m in ['torch','numpy','scipy','fastapi','uvicorn']}
d['environment_candidates']=[str(p) for base in [Path.home()/'miniconda3/envs',Path.home()/'anaconda3/envs',Path('/opt/conda/envs')] if base.exists() for p in base.glob('*/bin/python')]
print(json.dumps(d,ensure_ascii=False))'''
stdin,stdout,stderr=client.exec_command("python3 - <<'PY'\n"+code+"\nPY",timeout=30)
text=stdout.read().decode();err=stderr.read().decode();exitcode=stdout.channel.recv_exit_status()
report={'ssh_connected':True,'host':'172.25.56.83','port':3322,'ssh_key_sha256_hex':fingerprint,'exit_code':exitcode,'remote':json.loads(text) if exitcode==0 else {'stderr':err}}
(root/'outputs/server_probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2));client.close()
