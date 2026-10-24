"""Private SSH-only compute link, including per-socket Windows VPN bypass."""
import hashlib,http.client,io,json,os,socket,struct,subprocess,sys,threading,time
from pathlib import Path
import paramiko
from .common import read_json,digest,file_hash
from .credentials import load_credential,save_credential,forget_credential


class TunnelSocket:
    """Socket file-reference semantics for an SSH channel used by http.client.

    HTTP/1.0 closes the connection object after headers. A real socket retains
    makefile readers; Channel.close does not. Keep the channel until both close.
    """
    def __init__(self,channel):self.channel=channel;self.references=0;self.close_requested=False
    def makefile(self,mode='rb',buffering=None):
        if mode!='rb' or self.close_requested:raise ValueError('Only an open binary HTTP reader is supported')
        self.references+=1;return io.BufferedReader(socket.SocketIO(self,mode))
    def recv_into(self,buffer,nbytes=0):
        data=self.channel.recv(min(len(buffer),nbytes) if nbytes else len(buffer));buffer[:len(data)]=data;return len(data)
    def sendall(self,data):return self.channel.sendall(data)
    def settimeout(self,value):return self.channel.settimeout(value)
    def gettimeout(self):return self.channel.gettimeout()
    def close(self):
        self.close_requested=True
        if not self.references:self.channel.close()
    def _decref_socketios(self):
        self.references-=1
        if self.close_requested and not self.references:self.channel.close()

def physical_interface(profile):
    if profile.get('bind_address'):return profile['bind_address'],profile.get('interface_index')
    if sys.platform!='win32' or profile.get('network_mode')!='physical_interface':return None,None
    # Hotspot adapters have addresses but no upstream gateway; do not treat them
    # as competing routes to private compute when the collector uses the hotspot.
    command="Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -and $_.InterfaceAlias -notmatch 'Clash|TUN|Loopback|WSL|vEthernet'} | ForEach-Object { $cfg=$_; $_.IPv4Address | Where-Object {$_.IPAddress -notmatch '^(127\\.|169\\.254\\.|198\\.18\\.)'} | Select-Object IPAddress,@{N='InterfaceIndex';E={$cfg.InterfaceIndex}} } | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ConnectionError('Cannot inspect physical network interfaces')
    rows=json.loads(result.stdout or '[]');rows=rows if isinstance(rows,list) else [rows]
    if not rows:raise ConnectionError('No physical IPv4 interface is available')
    if len(rows)>1:raise ConnectionError('Multiple physical interfaces: select bind_address/interface_index in compute profile')
    return rows[0]['IPAddress'],rows[0]['InterfaceIndex']

class HostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self,expected):self.expected=expected
    def missing_host_key(self,client,hostname,key):
        if hashlib.sha256(key.asbytes()).hexdigest()!=self.expected:raise paramiko.SSHException('Private compute server host key changed')


class ComputeIdentityError(ValueError):pass


def read_service_token(client,path,timeout=10):
    """Bound subsystem handshake as well as reads: closing transport wakes both."""
    deadline=threading.Timer(timeout,client.close);deadline.daemon=True;deadline.start()
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(timeout)
            with sftp.open(path,'r') as stream:value=stream.read(512).decode().strip()
        if not value:raise ValueError('Private compute service token is empty')
        return value
    finally:deadline.cancel()

class ComputeBridge:
    def __init__(self,root,event=lambda *a:None):
        self.root=Path(root);cfg=self.root/'configs/gnautilus_compute.json'
        if not cfg.exists() and (self.root/'configs/gnautilus_compute.example.json').exists():cfg=self.root/'configs/gnautilus_compute.example.json'
        self.profile=read_json(cfg);self.event=event;self.client=None;self.password=load_credential(self.profile);self.token=None;self.lock=threading.RLock();self.auto_connect_suspended=False;self.connection_epoch=0
        self.status=dict(ssh_connected=False,worker_connected=False,connecting=False,host=self.profile['host'],ssh_port=self.profile['port'],last_error=None,last_health=None,last_health_time=None,runtime=None,roundtrip_ms=None)
        self.status.update(credential_saved=bool(self.password),auto_connect=True,connection_state='waiting_retry' if self.password else 'credential_required')
    def connect(self,password=None,*,expected_epoch=None):
        with self.lock:
            if expected_epoch is not None and (expected_epoch!=self.connection_epoch or self.auto_connect_suspended):return False
            if self.status['connecting']:return False
            self.status['connecting']=True
            self.connection_epoch+=1;epoch=self.connection_epoch
            self.auto_connect_suspended=False;self.status.update(connection_state='connecting',last_error=None)
        password=password or self.password or load_credential(self.profile)
        c=None;s=None
        try:
            self.disconnect(quiet=True);self.password=password
            if not password:raise ValueError('SSH credential required; configure the local Windows credential vault')
            p=self.profile;s=socket.socket();s.settimeout(12);s.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            address,index=physical_interface(p)
            if address:s.bind((address,0))
            if sys.platform=='win32' and index:s.setsockopt(socket.IPPROTO_IP,31,struct.pack('!I',int(index)))
            s.connect((p['host'],p['port']));c=paramiko.SSHClient();c.set_missing_host_key_policy(HostKey(p['hostkey_sha256']))
            c.connect(p['host'],port=p['port'],username=p['username'],password=password,sock=s,look_for_keys=False,allow_agent=False,timeout=12,banner_timeout=12,auth_timeout=12)
            c.get_transport().set_keepalive(10)
            with self.lock:
                if epoch!=self.connection_epoch:raise ConnectionAbortedError('Connection superseded by operator disconnect')
                self.client=c;self.status.update(ssh_connected=True,bind_address=address,last_error=None)
            try:save_credential(p,password);self.status['credential_saved']=True
            except Exception:self.status['credential_saved']=False
            token=read_service_token(c,p['root']+'/service.token')
            with self.lock:
                if epoch!=self.connection_epoch:raise ConnectionAbortedError('Connection superseded by operator disconnect')
                self.token=token
            health=self.call('GET','/health');self.status.update(worker_connected=True,last_health=health,last_health_time=time.monotonic(),runtime=health.get('python'))
            if health.get('gpu_uuid')!=p.get('gpu_uuid') or health.get('service')!='private_gnautilus_ml':raise ValueError('Private compute service identity or GPU UUID mismatch')
            with self.lock:
                if epoch!=self.connection_epoch:raise ConnectionAbortedError('Connection superseded by operator disconnect')
                self.status.update(connection_state='ready',auto_connect=True)
            self.event('新计算服务器已连接，主机身份与模型服务已核验','good')
            return True
        except Exception as error:
            if c:c.close()
            elif s:s.close()
            if epoch!=self.connection_epoch:return False
            self.client=None;self.token=None
            invalid=isinstance(error,paramiko.AuthenticationException) or 'host key changed' in str(error) or 'service identity or GPU UUID mismatch' in str(error)
            if invalid:
                self.password=None;self.auto_connect_suspended=True
                if isinstance(error,paramiko.AuthenticationException):forget_credential(self.profile);self.status['credential_saved']=False
            self.status.update(ssh_connected=False,worker_connected=False,last_error=str(error),connection_state='authentication_failed' if invalid else 'waiting_retry',auto_connect=not self.auto_connect_suspended)
            self.event('新计算服务器连接失败：'+str(error),'error')
            return False
        finally:self.status['connecting']=False
    def command(self,code,timeout=30):
        if not self.client:raise ConnectionError('Connect the private compute server first')
        _,out,err=self.client.exec_command("python3 - <<'EEGML_ADMIN'\n"+code+"\nEEGML_ADMIN",timeout=timeout);body=out.read().decode();errors=err.read().decode()
        if out.channel.recv_exit_status():raise RuntimeError(errors[-1500:])
        return json.loads(body)
    def call(self,method,path,payload=None,timeout=10):
        client=self.client
        if not client or not client.get_transport().is_active() or not self.token:
            self.status.update(ssh_connected=False,worker_connected=False);raise ConnectionError('Private compute link is disconnected')
        conn=http.client.HTTPConnection('127.0.0.1',self.profile['service_port'],timeout=timeout)
        t=time.monotonic()
        response=None
        try:
            conn.sock=TunnelSocket(client.get_transport().open_channel('direct-tcpip',('127.0.0.1',self.profile['service_port']),('127.0.0.1',0),timeout=timeout));conn.sock.settimeout(timeout)
            conn.request(method,path,body=json.dumps(payload,allow_nan=False).encode() if payload is not None else None,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            response=conn.getresponse();length=response.length
            if length is None or length>16*1024*1024:raise ValueError('Compute response length missing or exceeds limit')
            body=response.read()
            value=json.loads(body)
            if response.status!=200:raise ValueError(value.get('error','Compute request failed'))
            if client is not self.client:raise ConnectionAbortedError('Late reply from replaced compute connection')
            if path in ['/health','/snapshot'] and (value.get('gpu_uuid')!=self.profile.get('gpu_uuid') or value.get('service')!='private_gnautilus_ml'):
                raise ComputeIdentityError('Private compute service identity or GPU UUID mismatch')
            self.status.update(worker_connected=True,last_health_time=time.monotonic(),last_error=None,roundtrip_ms=(time.monotonic()-t)*1000)
            if path in ['/health','/snapshot']:self.status['last_health']={k:value[k] for k in ['project','schema','version','host','gpu_uuid','python','service','device','gpus'] if k in value}
            return value
        except ComputeIdentityError as error:
            self.disconnect(quiet=True);self.auto_connect_suspended=True;self.status.update(auto_connect=False,last_error=str(error),connection_state='identity_mismatch');raise
        except ValueError as error:
            if client is self.client:self.status.update(last_error=str(error))
            raise
        except Exception as error:
            if client is self.client:self.status.update(ssh_connected=False,worker_connected=False,last_error=str(error),connection_state='waiting_retry')
            raise
        finally:
            if response:response.close()
            conn.close()
    def request(self,method,path,payload=None):return self.call(method,path,payload)
    def snapshot(self):
        value=dict(self.status);last=value.get('last_health_time')
        value['health_age_ms']=(time.monotonic()-last)*1000 if last is not None else None
        return value
    def disconnect(self,quiet=False):
        with self.lock:
            if not quiet:self.connection_epoch+=1
            if self.client:self.client.close()
            self.client=None;self.token=None;self.status.update(ssh_connected=False,worker_connected=False)
            if not quiet:
                self.password=None;self.auto_connect_suspended=True;self.status.update(auto_connect=False,connection_state='disconnected_by_operator')
    def upload_record(self,path):
        from .data import read_native
        p=Path(path);record=read_native(p);key=record['source_sha256'];remote=self.profile['root']+'/data/'+key
        with self.client.open_sftp() as f:
            try:f.mkdir(remote,0o700)
            except OSError:pass
            for name in ['descriptor.json','raw.f32','blocks.jsonl','events.jsonl','closed.json']:
                target=remote+'/'+name;part=target+'.upload'
                f.put(str(p/name),part)
                try:f.posix_rename(part,target)
                except OSError:f.rename(part,target)
                f.chmod(target,0o600)
        return key
    def upload_artifact(self,path):
        p=Path(path);meta=read_json(p/'manifest.json');key=meta['model_id']
        identity=dict(meta);identity.pop('model_id',None)
        if digest(identity)!=key or file_hash(p/'weights.npz')!=meta['weights_sha256']:raise ValueError('Artifact hash mismatch')
        remote=self.profile['root']+'/data/'+key
        with self.client.open_sftp() as f:
            try:f.mkdir(remote,0o700)
            except OSError:pass
            for name in ['manifest.json','weights.npz']:f.put(str(p/name),remote+'/'+name);f.chmod(remote+'/'+name,0o600)
        return key
    def upload_numeric_mat(self,path):
        p=Path(path)
        if p.suffix.lower()!='.mat' or p.stat().st_size>64*1024**2:raise ValueError('A numeric MAT below 64MiB is required')
        key=file_hash(p);remote=self.profile['root']+'/data/'+key
        with self.client.open_sftp() as f:
            try:f.mkdir(remote,0o700)
            except OSError:pass
            f.put(str(p),remote+'/numeric.mat');f.chmod(remote+'/numeric.mat',0o600)
        return key
