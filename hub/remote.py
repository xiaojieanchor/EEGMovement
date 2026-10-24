import socket,time,json,hashlib,secrets,shlex,http.client,threading,ipaddress
from pathlib import Path
import paramiko
from training.gpu_placement import project_gpu_environment

def ssh_bind_address(config):
    """Keep project SSH on the EEG LAN interface when a TUN owns default routes.

    An explicit address remains an override. Following source_host avoids a
    second stale IP literal when the acquisition endpoint changes networks.
    """
    if config.get('ssh_bind_address'):return config['ssh_bind_address']
    if not config.get('ssh_bind_to_source'):return None
    address=ipaddress.IPv4Address(config.get('source_host',''))
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address in ipaddress.IPv4Network('198.18.0.0/15'):raise ValueError('SSH需要真实采集网卡的局域网IPv4地址；请检查source_host')
    return str(address)

class PinnedKey(paramiko.MissingHostKeyPolicy):
    def __init__(self,expected):self.expected=expected
    def missing_host_key(self,client,hostname,key):
        actual=hashlib.sha256(key.asbytes()).hexdigest()
        if not self.expected or actual!=self.expected:raise paramiko.SSHException('Server host key differs from verified fingerprint')

class ChannelHTTP(http.client.HTTPConnection):
    def __init__(self,transport,port):super().__init__('127.0.0.1',port,timeout=4);self.transport=transport
    def connect(self):
        self.sock=self.transport.open_channel('direct-tcpip',('127.0.0.1',self.port),('127.0.0.1',0),timeout=4);self.sock.settimeout(4)

class RemoteBridge:
    def __init__(self,config,root,event):
        self.config=config;self.root=root;self.event=event;self.client=None;self.connection=None;self.token=None;self.password=None;self.rpc_lock=threading.Lock();self.connect_lock=threading.Lock();self.closed=False
        self.status={'ssh_connected':False,'worker_connected':False,'connecting':False,'host':config['server_host'],'ssh_port':config['server_port'],'last_error':None,'last_health':None,'last_health_time':None,'roundtrip_ms':None,'runtime':None,'deployment':None}
    def command(self,code,timeout=60):
        _,out,err=self.client.exec_command("python3 - <<'EEGHUB_PY'\n"+code+"\nEEGHUB_PY",timeout=timeout)
        body=out.read().decode();errors=err.read().decode();exitcode=out.channel.recv_exit_status()
        if exitcode:raise RuntimeError('Remote setup failed: '+errors[-500:])
        return json.loads(body)
    def connect(self,password):
        if not self.connect_lock.acquire(blocking=False):return
        try:
            self.disconnect(quiet=True);self.status.update(connecting=True,last_error=None);self.password=password
            c=paramiko.SSHClient();c.load_system_host_keys();c.set_missing_host_key_policy(PinnedKey(self.config.get('ssh_hostkey_sha256')))
            sock=socket.socket();sock.settimeout(12)
            # EEG windows and small replies should not wait for TCP's Nagle coalescing.
            sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            bind=ssh_bind_address(self.config)
            if bind:sock.bind((bind,0))
            sock.connect((self.config['server_host'],self.config['server_port']))
            c.connect(self.config['server_host'],port=self.config['server_port'],username=self.config['server_user'],password=password,sock=sock,look_for_keys=False,allow_agent=False,timeout=12,banner_timeout=12,auth_timeout=15)
            c.get_transport().set_keepalive(15);self.client=c;self.status['ssh_connected']=True
            self.event('服务器SSH已连接，核验独立工作进程','info')
            self.deploy()
            health=self.request('GET','/health');self.status.update(worker_connected=True,last_health=health,last_health_time=time.monotonic())
            self.event('GPU处理服务就绪：'+health['device'],'good')
        except Exception as e:
            if isinstance(e,paramiko.AuthenticationException):self.password=None
            active=bool(self.client and self.client.get_transport() and self.client.get_transport().is_active())
            self.status.update(ssh_connected=active,worker_connected=False,last_error=str(e)[:300]);self.event('服务器连接失败：'+str(e)[:150],'error')
        finally:self.status['connecting']=False;self.connect_lock.release()
    def deploy(self):
        base=self.config['remote_directory'];port=self.config['remote_service_port'];gpu=0;gpu_env=project_gpu_environment(self.config)
        if base!='/home/sysu/EEGMovement':raise ValueError('Unexpected remote project directory')
        sftp=self.client.open_sftp()
        for path in [base,base+'/server',base+'/models',base+'/data',base+'/logs',base+'/run']:
            try:sftp.stat(path)
            except FileNotFoundError:sftp.mkdir(path,mode=0o700)
        worker=self.root/'server/worker.py';digest=hashlib.sha256(worker.read_bytes()).hexdigest()
        sftp.put(str(worker),base+'/server/worker.py');sftp.chmod(base+'/server/worker.py',0o600)
        tokenfile=base+'/run/service.token'
        try:
            with sftp.open(tokenfile,'r') as f:self.token=f.read().decode().strip()
        except FileNotFoundError:
            self.token=secrets.token_urlsafe(32)
            with sftp.open(tokenfile,'w') as f:f.write(self.token)
            sftp.chmod(tokenfile,0o600)
        sftp.close()
        # Runtime is reused read-only; this command never installs into an existing environment.
        code=r'''import json,os,sys,subprocess,socket,time,signal,hashlib
from pathlib import Path
base=Path(BASE);port=PORT;gpu=GPU;expected=EXPECTED;pinned_env=PINNED_ENV
py_candidates=[Path('/home/sysu/anaconda3/envs/py310/bin/python'),Path('/home/sysu/anaconda3/envs/nnunet/bin/python'),Path('/home/sysu/anaconda3/bin/python')]
chosen=None
for py in py_candidates:
 if py.exists():
  result=subprocess.run([str(py),'-c',"import importlib.util;print(int(all(importlib.util.find_spec(m) is not None for m in ['numpy','torch'])))"],capture_output=True,text=True,timeout=15)
  if result.stdout.strip()=='1':chosen=str(py);break
if not chosen:raise RuntimeError('No installed torch+numpy runtime found; existing environments were not modified')
pidfile=base/'run/worker.pid';manifest=base/'run/worker.json';path=base/'server/worker.py';alive=False
if pidfile.exists():
 try:
  pid=int(pidfile.read_text());cmd=Path(f'/proc/{pid}/cmdline').read_bytes().decode(errors='replace');alive=str(path) in cmd
 except (ValueError,FileNotFoundError,ProcessLookupError):alive=False
if alive:
 old=json.loads(manifest.read_text()) if manifest.exists() else {}
 actual_env=dict(v.split(b'=',1)for v in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')if b'='in v)
 if old.get('sha256')!=expected or actual_env.get(b'CUDA_VISIBLE_DEVICES',b'').decode()!=pinned_env['CUDA_VISIBLE_DEVICES']:
  os.kill(pid,signal.SIGTERM)
  for _ in range(20):
   try:os.kill(pid,0);time.sleep(.1)
   except ProcessLookupError:break
  alive=False
if not alive:
 test=socket.socket();test.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
 try:test.bind(('127.0.0.1',port))
 except OSError:raise RuntimeError('Dedicated EEG port is in use; no unrelated service was stopped')
 finally:test.close()
 log=open(base/'logs/worker.log','ab',buffering=0)
 p=subprocess.Popen([chosen,'-u',str(path),'--port',str(port),'--gpu',str(gpu),'--token-file',str(base/'run/service.token')],cwd=str(base),stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,**pinned_env))
 pid=p.pid;pidfile.write_text(str(pid));manifest.write_text(json.dumps({'pid':pid,'sha256':expected,'python':chosen,'project':'EEGMovement','physical_gpu_uuid':pinned_env['CUDA_VISIBLE_DEVICES']}));os.chmod(pidfile,0o600);os.chmod(manifest,0o600)
print(json.dumps({'pid':pid,'python':chosen,'directory':str(base),'service_port':port,'sha256':expected,'reused':alive}))'''
        code=code.replace('BASE',repr(base)).replace('PORT',str(port)).replace('GPU',str(gpu)).replace('EXPECTED',repr(digest)).replace('PINNED_ENV',repr(gpu_env))
        self.status['deployment']=self.command(code);self.status['runtime']=self.status['deployment']['python']
        error=None
        # First Torch/CUDA import on the shared machine may take tens of seconds.
        for _ in range(160):
            try:
                h=self.request('GET','/health')
                if h.get('project')=='EEGMovement':return
            except Exception as e:error=e
            time.sleep(.25)
        raise RuntimeError('GPU worker not ready: '+str(error))
    def request(self,method,path,payload=None):
        with self.rpc_lock:
            if not self.client or not self.client.get_transport() or not self.client.get_transport().is_active():
                self.status.update(ssh_connected=False,worker_connected=False)
                raise ConnectionError('SSH disconnected')
            if self.connection is None:self.connection=ChannelHTTP(self.client.get_transport(),self.config['remote_service_port'])
            b=json.dumps(payload,allow_nan=False).encode() if payload is not None else None
            t=time.perf_counter()
            try:
                self.connection.request(method,path,body=b,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
                r=self.connection.getresponse();body=r.read()
                if r.status!=200:raise RuntimeError(f'Worker HTTP{r.status}')
                result=json.loads(body);self.status['roundtrip_ms']=round((time.perf_counter()-t)*1000,2);return result
            except Exception:
                self.connection.close();self.connection=None;raise
    def disconnect(self,quiet=False):
        # Only close this project's transport. Remote loopback worker may be reused.
        with self.rpc_lock:
            if self.connection:
                try:self.connection.close()
                except Exception:pass
            if self.client:self.client.close()
            self.connection=None;self.client=None;self.token=None
            self.status.update(ssh_connected=False,worker_connected=False)
        if not quiet:self.password=None;self.event('服务器连接已关闭','info')
