"""Signed release manifests, resumable downloads, bounded extraction and rollback."""
import base64,hashlib,json,os,re,shutil,stat,time,urllib.request,urllib.parse,zipfile,http.client,socket,struct
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from .trust import client_context

def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def verify_manifest(raw,public_key,platform,minimum=0):
    try:
        if len(raw)>262144:raise ValueError('Oversized update manifest')
        wrapper=json.loads(raw);body=base64.b64decode(wrapper['payload'],validate=True);signature=base64.b64decode(wrapper['signature'],validate=True)
        serialization.load_pem_public_key(public_key.encode()).verify(signature,body,padding.PKCS1v15(),hashes.SHA256())
        value=json.loads(body)
        if value.get('schema')!='eegmovement.update.v1' or value.get('platform')!=platform or value.get('protocol')!=1:raise ValueError('Update platform/protocol mismatch')
        if type(value.get('release_seq')) is not int or value['release_seq']<=minimum:raise ValueError('Replayed or downgraded release')
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value.get('version','')):raise ValueError('Invalid version')
        if type(value.get('size')) is not int or not 0<value['size']<=2*1024**3 or not re.fullmatch('[0-9a-f]{64}',value.get('sha256','')):raise ValueError('Invalid artifact identity')
        if not re.fullmatch(r'releases/[A-Za-z0-9._-]+\.(zip|apk)',value.get('path','')):raise ValueError('Update URL outside release directory')
        return value
    except Exception as error:raise ValueError('更新签名／版本／包身份未通过：'+type(error).__name__) from error

def opener(config,proxy=None):
    if proxy and proxy.startswith('interface:'):
        index=int(proxy.split(':')[1]);context=client_context(config['ca_pem'])
        class HardwareHTTPS(http.client.HTTPSConnection):
            def connect(self):
                raw=socket.socket(socket.AF_INET,socket.SOCK_STREAM);raw.settimeout(self.timeout)
                try:
                    raw.setsockopt(socket.IPPROTO_IP,31,struct.pack('!I',index));raw.connect((self.host,self.port));self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
                except Exception:raw.close();raise
        class HardwareHandler(urllib.request.HTTPSHandler):
            def https_open(self,request):return self.do_open(HardwareHTTPS,request,context=context)
        return urllib.request.build_opener(urllib.request.ProxyHandler({}),HardwareHandler(context=context))
    return urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy} if proxy else {}),urllib.request.HTTPSHandler(context=client_context(config['ca_pem'])))

def fetch(config,path,limit=262144):
    from .network import paths
    errors=[]
    for _,proxy in paths(config):
        try:
            with opener(config,proxy).open(config['base_url']+path,timeout=10) as r:
                if urllib.parse.urlparse(r.geturl()).netloc!=urllib.parse.urlparse(config['base_url']).netloc:raise ValueError('Cross-host update redirect')
                data=r.read(limit+1)
                if len(data)>limit:raise ValueError('Update response too large')
                return data,proxy
        except Exception as e:errors.append(type(e).__name__)
    raise ConnectionError('更新服务连接失败：'+'/'.join(errors))

def download(config,manifest,directory,*,proxy=None,busy=lambda:False,stopped=lambda:False,on_state=lambda *a:None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);target=directory/(manifest['sha256']+Path(manifest['path']).suffix);partial=target.with_suffix(target.suffix+'.part')
    if target.exists():
        if target.stat().st_size==manifest['size'] and file_hash(target)==manifest['sha256']:return target
        target.unlink()
    existing=partial.stat().st_size if partial.exists() else 0
    if existing>manifest['size']:partial.unlink();existing=0
    if shutil.disk_usage(directory).free<(manifest['size']-existing)+512*1024**2:raise OSError('更新缓存磁盘空间不足')
    request=urllib.request.Request(config['base_url']+manifest['path'],headers={'Range':f'bytes={existing}-'} if existing else {})
    with opener(config,proxy).open(request,timeout=30) as response:
        if urllib.parse.urlparse(response.geturl()).netloc!=urllib.parse.urlparse(config['base_url']).netloc:raise ValueError('Cross-host download redirect')
        if existing and response.status!=206:existing=0
        if existing and not response.headers.get('Content-Range','').startswith(f'bytes {existing}-'):raise ValueError('Range response mismatch')
        with partial.open('ab' if existing else 'wb') as f:
            total=existing
            while True:
                if stopped():raise InterruptedError('Update stopped')
                if busy():
                    on_state('waiting_idle',total,manifest['size']);raise InterruptedError('采集／控制活跃，已暂停下载，可断点恢复')
                began=time.monotonic();data=response.read(min(65536,manifest['size']-total+1))
                if not data:break
                total+=len(data)
                if total>manifest['size']:raise ValueError('Artifact exceeds signed size')
                f.write(data);on_state('downloading',total,manifest['size']);time.sleep(max(0,len(data)/98304-(time.monotonic()-began)))
            f.flush();os.fsync(f.fileno())
    if partial.stat().st_size!=manifest['size'] or file_hash(partial)!=manifest['sha256']:raise ValueError('更新包不完整或SHA256不符')
    os.replace(partial,target);return target

def file_hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):result.update(b)
    return result.hexdigest()

def extract_version(archive,destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    if any(destination.iterdir()):raise ValueError('Version directory must be empty')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        if len(infos)>10000 or sum(i.file_size for i in infos)>2*1024**3:raise ValueError('Package extraction bound')
        names=set()
        for info in infos:
            name=info.filename.replace('\\','/');parts=name.split('/')
            if name.startswith('/') or any(p in ('..','.') or ':' in p for p in parts) or stat.S_ISLNK(info.external_attr>>16):raise ValueError('Unsafe package path')
            if parts[0]=='EEGMovement-gNautilus':parts=parts[1:]
            if not parts or not ''.join(parts):continue
            target=destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()) or str(target).lower() in names:raise ValueError('Conflicting package path')
            names.add(str(target).lower())
        for info in infos:
            parts=info.filename.replace('\\','/').split('/')
            if parts[0]=='EEGMovement-gNautilus':parts=parts[1:]
            if not parts or not ''.join(parts):continue
            target=destination.joinpath(*parts)
            if info.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
    if not (destination/'EEGMovement-gNautilus.exe').is_file():raise ValueError('Missing application executable')

def activate(root,version,directory,health):
    root=Path(root);candidate=(root/directory).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_dir():raise ValueError('Invalid version directory')
    current=root/'current.json';previous=json.loads(current.read_text('utf-8')) if current.exists() else None
    atomic(current,dict(version=version,directory=directory,previous=previous))
    try:healthy=bool(health(candidate))
    except Exception:healthy=False
    if not healthy:
        if previous:atomic(current,previous)
        else:current.unlink(missing_ok=True)
        raise RuntimeError('新版启动健康检查失败，已恢复原版本')
    return dict(version=version,directory=directory,previous=previous)
