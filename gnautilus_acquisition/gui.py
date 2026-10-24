"""Chinese acquisition console. Factory settings are intentionally outside this UI."""
import ipaddress
import json
import os
import queue
import re
import threading
from pathlib import Path
from .storage import atomic_json,StreamJournal


def load_enrollment(path):
    raw=os.fspath(path).strip() if isinstance(path,(str,os.PathLike)) else ''
    if len(raw)>=2 and raw[0]==raw[-1] and raw[0] in ['"',"'"]:raw=raw[1:-1].strip()
    if not raw:raise ValueError('未选择中枢配对文件：请先在本机中枢页面生成配对JSON，复制到采集电脑，再点击“选择配对文件”。尚未尝试网络连接。')
    path=Path(os.path.expandvars(raw)).expanduser()
    try:
        if path.is_dir():raise ValueError('配对路径是文件夹：请选择中枢生成的JSON文件，不能选择文件夹。')
        with path.open('rb') as source:content=source.read(8193)
    except FileNotFoundError:raise ValueError('配对文件不存在：请将中枢生成的JSON复制到这台电脑后重新选择。') from None
    except (PermissionError,OSError):raise ValueError('无法读取配对文件：请检查文件访问权限，或复制到这台电脑的桌面后重新选择。') from None
    if len(content)>8192:raise ValueError('配对文件过大：请选择中枢生成的配对JSON，不是事件日志或设备配置。')
    try:d=json.loads(content.decode('utf-8-sig'))
    except (UnicodeError,json.JSONDecodeError):raise ValueError('配对文件不是有效的UTF-8 JSON：请从中枢重新生成并复制，勿手工填写密钥。') from None
    if isinstance(d,dict) and d.get('schema')=='gnautilus.enrollment.lan.v1' and d.get('mode')=='lan_ws':
        return d
    if isinstance(d,dict) and d.get('schema')=='gnautilus.enrollment.v2' and d.get('mode')=='relay':
        from urllib.parse import urlparse
        u=urlparse(d.get('url',''))
        if u.scheme!='wss' or u.path!='/eeg/v1/ws' or not u.hostname:raise ValueError('公网配对地址无效')
        for field,n in [('device_id',32),('hub_id',32),('workspace',32),('secret',64),('auth_secret',64)]:
            if not re.fullmatch('[0-9a-f]{'+str(n)+'}',str(d.get(field,''))):raise ValueError('公网配对身份不完整')
        if not isinstance(d.get('ca_pem'),str):raise ValueError('缺少应用专用CA')
        d.update(host=u.hostname,port=u.port or 443,agent_id=d['device_id'])
        return d
    if not isinstance(d,dict) or d.get('schema','gnautilus.enrollment.v1')!='gnautilus.enrollment.v1':raise ValueError('配对文件类型不正确：请选择中枢生成的配对文件。')
    for field,n in [('agent_id',32),('secret',64),('certificate_sha256',64)]:
        if not re.fullmatch('[0-9a-f]{'+str(n)+'}',str(d.get(field,''))):raise ValueError('配对文件身份或证书不完整')
    try:ip=ipaddress.IPv4Address(d.get('host'))
    except (ValueError,TypeError):raise ValueError('配对文件的中枢IPv4无效：请在中枢页面填写采集电脑可达的IPv4并重新生成。') from None
    if ip.is_unspecified or ip.is_multicast or ip.is_link_local:raise ValueError('中枢地址无效')
    if type(d.get('port')) is not int or not 1024<=d['port']<=65535:raise ValueError('中枢端口无效')
    return d


class SettingsStore:
    FIELDS={'sdk_dir','serial','pairing_path','storage_root','layout_path','proxy_mode','proxy_url','connection_mode','lan_host'}
    def __init__(self,path):self.path=Path(path)
    def load(self):
        if not self.path.exists():return {}
        d=json.loads(self.path.read_text('utf-8'))
        if not isinstance(d,dict):raise ValueError('界面设置应为对象')
        return {k:v for k,v in d.items() if k in self.FIELDS and isinstance(v,str)}
    def save(self,value):atomic_json(self.path,{k:v for k,v in value.items() if k in self.FIELDS and isinstance(v,str)})


class AcquisitionController:
    def __init__(self,collector_factory=None):self.collector_factory=collector_factory;self.collector=None;self.running=False
    def begin(self,profile):
        if self.running:raise ValueError('正在接收，请先结束并保存')
        if isinstance(profile.get('enrollment'),dict):
            pairing=dict(profile['enrollment'])
        elif profile.get('connection_mode')=='lan_ws':
            from .lan_client import build_lan_enrollment
            pairing=build_lan_enrollment(profile.get('lan_host',''))
        else:
            pairing=load_enrollment(profile['pairing_path'])
        layout={}
        if pairing.get('mode')=='relay':pairing.update(proxy_mode=profile.get('proxy_mode','auto'),proxy_url=profile.get('proxy_url',''))
        if profile.get('layout_path'):
            p=Path(profile['layout_path'])
            if p.stat().st_size>16384:raise ValueError('电极位置文件过大')
            layout=json.loads(p.read_text('utf-8-sig'))
            if not isinstance(layout,dict):raise ValueError('电极位置文件必须为对象')
        if not Path(profile['sdk_dir']).is_dir():raise ValueError('请选择原厂Client API目录')
        if self.collector_factory is None:
            from .client import Collector
            self.collector_factory=Collector
        self.collector=self.collector_factory(dict(sdk_dir=profile['sdk_dir'],serial=profile.get('serial') or None,layout=layout),pairing,profile['storage_root'])
        self.collector.start();self.running=True
    def stop(self):
        if self.collector and self.running:
            self.collector.stop()
            if self.collector.snapshot().get('supervisor_active'):raise RuntimeError('尾块仍在关闭，请等待后重试；当前记录尚未声明关闭完成')
            self.running=False
    def start_capture(self,**kw):
        if not self.collector or not hasattr(self.collector,'start_capture'):raise ValueError('请先开始接收 EEG 数据后再启动自主打标')
        return self.collector.start_capture(**kw)
    def hold_start(self,label,**kw):
        if not self.collector or not hasattr(self.collector,'hold_start'):return {}
        return self.collector.hold_start(label,**kw)
    def hold_end(self,label=None,**kw):
        if not self.collector or not hasattr(self.collector,'hold_end'):return {}
        return self.collector.hold_end(label,**kw)
    def capture_heartbeat(self,**kw):
        if not self.collector or not hasattr(self.collector,'capture_heartbeat'):return {}
        return self.collector.capture_heartbeat(**kw)
    def stop_capture(self,reason='completed'):
        if not self.collector or not hasattr(self.collector,'stop_capture'):return {}
        return self.collector.stop_capture(reason=reason)
    def snapshot(self):return self.collector.snapshot() if self.collector else dict(state='idle',error=None,samples=0,blocks=0,network={},descriptor=None)


def run_gui(*,start_receiving=False,status_path=None):
    from .qt_gui import run
    return run(start_receiving=start_receiving,status_path=status_path)
