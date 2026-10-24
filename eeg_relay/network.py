"""Shared outbound TLS path selection. Never changes the user's VPN or routes."""
import json,secrets,socket,threading,urllib.request,os,ipaddress,struct,subprocess,time
from urllib.parse import urlparse
from websockets.sync.client import connect
from .protocol import SCHEMA,authentication,encoded,pack,unpack
from .trust import client_context

_interfaces=(0,[])
def physical_interfaces():
    """Read active hardware adapters; route only our socket, without editing VPN/routes."""
    global _interfaces
    if os.name!='nt':return []
    if time.monotonic()-_interfaces[0]<60:return _interfaces[1]
    try:
        import ctypes as c
        from ctypes import wintypes as w
        class Adapter(c.Structure):pass
        Adapter._fields_=[('Length',w.ULONG),('IfIndex',w.DWORD),('Next',c.POINTER(Adapter)),('AdapterName',c.c_char_p),('FirstUnicastAddress',c.c_void_p),('FirstAnycastAddress',c.c_void_p),('FirstMulticastAddress',c.c_void_p),('FirstDnsServerAddress',c.c_void_p),('DnsSuffix',c.c_wchar_p),('Description',c.c_wchar_p),('FriendlyName',c.c_wchar_p),('PhysicalAddress',c.c_ubyte*8),('PhysicalAddressLength',w.DWORD),('Flags',w.DWORD),('Mtu',w.DWORD),('IfType',w.DWORD),('OperStatus',w.DWORD)]
        get=c.WinDLL('iphlpapi').GetAdaptersAddresses;get.argtypes=[w.ULONG,w.ULONG,c.c_void_p,c.POINTER(Adapter),c.POINTER(w.ULONG)];get.restype=w.ULONG
        size=w.ULONG(32768);values=[]
        for attempt in range(2):
            buffer=c.create_string_buffer(size.value);pointer=c.cast(buffer,c.POINTER(Adapter));result=get(2,0,None,pointer,c.byref(size))
            if result==111:continue
            if result:raise OSError('GetAdaptersAddresses failed')
            while pointer:
                adapter=pointer.contents;description=(adapter.Description or '').lower()
                if adapter.OperStatus==1 and adapter.IfType in (6,71) and adapter.PhysicalAddressLength>0 and not any(word in description for word in ('virtual','vpn','tap','tunnel','hyper-v','wireguard')):values.append(adapter.IfIndex)
                pointer=adapter.Next
            break
        values=values[:3]
    except Exception:values=[]
    _interfaces=time.monotonic(),values;return values

def paths(config):
    mode=config.get('proxy_mode','auto');system=urllib.request.getproxies().get('https') or urllib.request.getproxies().get('http')
    host=urlparse(config.get('url',config.get('base_url',''))).hostname
    try:is_global=ipaddress.ip_address(host).is_global
    except (ValueError,TypeError):is_global=False
    direct=[('物理网卡直连 #'+str(n),'interface:'+str(n)) for n in physical_interfaces()] if is_global else []
    direct+=[('系统路由直连',None)]
    if mode=='direct':return direct
    if mode=='system':
        if not system:raise ValueError('没有可用的系统HTTP代理')
        return [('系统代理',system)]
    if mode=='manual':
        proxy=config.get('proxy_url','')
        if urlparse(proxy).scheme not in ('http','https') or not urlparse(proxy).hostname:raise ValueError('请输入HTTP代理地址')
        return [('手动代理',proxy)]
    if mode!='auto':raise ValueError('Invalid connection mode')
    return direct+([('系统代理',system)] if system else [])

class Connection:
    def __init__(self,config,*,lane='control',boot=None,on_stage=None):
        self.config=config;self.lane=lane;self.boot=boot or secrets.token_hex(16);self.on_stage=on_stage or (lambda *a:None)
        self.ws=None;self.path=None;self.lock=threading.Lock()
    def connect(self):
        config=self.config;url=config['url']
        if urlparse(url).scheme!='wss' or urlparse(url).path!='/eeg/v1/ws':raise ValueError('Expected authenticated WSS relay endpoint')
        errors=[]
        for name,proxy in paths(config):
            raw=None
            try:
                self.on_stage('网络',name+' → '+str(urlparse(url).hostname))
                kwargs={}
                if proxy and proxy.startswith('interface:'):
                    raw=socket.socket(socket.AF_INET,socket.SOCK_STREAM);raw.settimeout(5);raw.setsockopt(socket.IPPROTO_IP,31,struct.pack('!I',int(proxy.split(':')[1])))
                    raw.connect((urlparse(url).hostname,urlparse(url).port or 443));kwargs['sock']=raw;proxy=None
                self.ws=connect(url,ssl=client_context(config['ca_pem']),proxy=proxy,open_timeout=5,close_timeout=1,ping_interval=10,ping_timeout=10,max_size=5*1024*1024,max_queue=64,compression=None,**kwargs)
                self.on_stage('TLS','专用CA、有效期与IP／主机名验证通过')
                challenge=json.loads(self.ws.recv(timeout=5))
                if challenge.get('schema')!=SCHEMA or challenge.get('notice')!='challenge':raise ValueError('Invalid relay challenge')
                auth=dict(kind='auth',workspace=config['workspace'],device_id=config['device_id'],boot=self.boot,lane=self.lane,
                    mac=authentication(config['auth_secret'],challenge['challenge'],config['workspace'],config['device_id'],self.boot,self.lane))
                self.ws.send(encoded(auth).decode());reply=json.loads(self.ws.recv(timeout=5))
                if reply.get('notice')!='authenticated':raise ValueError('Relay authentication rejected')
                self.route_server=reply.get('route_server')
                if not isinstance(self.route_server,str) or len(self.route_server)!=32:raise ValueError('Missing authenticated relay identity')
                self.path=name;self.on_stage('公网身份','工作区与设备角色已认证');return self
            except Exception as error:
                if self.ws:self.ws.close();self.ws=None
                elif raw:raw.close()
                errors.append(name+': '+type(error).__name__+' '+str(error)[:180])
        raise ConnectionError('；'.join(errors))
    def send(self,header,body=b''):
        if not self.lock.acquire(timeout=5):raise TimeoutError('Lane send queue deadline')
        timer=threading.Timer(5,self.abort);timer.daemon=True;timer.start()
        try:self.ws.send(pack(header,body))
        finally:timer.cancel();self.lock.release()
    def receive(self,timeout=None):
        raw=self.ws.recv(timeout=timeout)
        return (json.loads(raw),b'') if isinstance(raw,str) else unpack(raw)
    def close(self):
        if self.ws:
            self.abort();self.ws.close();self.ws=None
    def abort(self):
        ws=self.ws
        if ws:
            try:ws.socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
