"""Paired LAN API; administration remains loopback-only. Latest JPEG stays in RAM."""
import collections,hashlib,hmac,ipaddress,json,math,re,socket,threading,time
from .session_calibration import CalibrationRequired
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def private_ipv4(value):
    try:
        a=ipaddress.IPv4Address(value)
        return any(a in ipaddress.ip_network(n) for n in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8'])
    except (ValueError,TypeError):return False

def enrolled_ipv4(value):
    """Explicitly provisioned campus addresses may be public unicast IPv4."""
    try:
        address=ipaddress.IPv4Address(value)
        return not (address.is_unspecified or address.is_multicast or address.is_link_local or value.startswith('0.')
                    or int(address)>=int(ipaddress.IPv4Address('224.0.0.0'))
                    or value.startswith('127.') and value!='127.0.0.1')
    except (ValueError,TypeError):return False

def signature(secret,method,path,device,boot,seq,body):
    message='\n'.join((method,path,device,boot,str(seq),hashlib.sha256(body).hexdigest())).encode()
    return hmac.new(secret,message,hashlib.sha256).hexdigest()

def execution_key(secret):return hmac.new(secret,b'EEGMovement UDP v1',hashlib.sha256).digest()

def lan_candidates():
    """Private interface hints only. The device must authenticate HTTP and UDP."""
    try:
        import psutil
        values=[entry.address for entries in psutil.net_if_addrs().values() for entry in entries if entry.family==socket.AF_INET]
    except (ImportError,OSError):values=[]
    return sorted({v for v in values if private_ipv4(v) and not v.startswith('127.')})[:12]

class BoundedServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(16)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

class QuestService:
    def __init__(self,hub,root,port=8766):
        self.hub,self.root,self.port=hub,Path(root),port
        self.lock=threading.RLock();self.devices={};self.replay={};self.frames={};self.stats=collections.Counter();self.server=None;self.error=None;self.collection_owner=None
        path=self.root/'hub/runtime/quest_devices.json'
        if path.exists():
            self.reload_enrollments(json.loads(path.read_text(encoding='utf-8'))['devices'])
    def reload_enrollments(self,records):
        """Validate all entries before replacing credentials; revoke removed UDP keys."""
        if not isinstance(records,list) or len(records)>4:raise ValueError('Device limit reached')
        identities=set();keys=set()
        for record in records:
            identity=record.get('device_id','');secret=bytes.fromhex(record.get('secret',''))
            if not re.fullmatch('[0-9a-f]{32}',identity) or not enrolled_ipv4(record.get('address')) or len(secret)!=32:raise ValueError('Invalid paired device')
            if identity in identities or secret in keys:raise ValueError('Pairing identities and keys must be unique')
            identities.add(identity);keys.add(secret)
        with self.lock:
            removed=set(self.devices)-identities
            for identity in removed:
                self.hub.vr.unregister_lan(identity);self.devices.pop(identity,None);self.frames.pop(identity,None)
            self.replay={key:value for key,value in self.replay.items() if key[0] in identities}
            for record in records:self.register(record)
    def register(self,record):
        if not re.fullmatch('[0-9a-f]{32}',record.get('device_id','')) or not enrolled_ipv4(record.get('address')):raise ValueError('Invalid paired device')
        secret=bytes.fromhex(record['secret'])
        if len(secret)!=32:raise ValueError('Invalid secret')
        with self.lock:
            previous=self.devices.get(record['device_id'])
            if not previous and len(self.devices)>=4:raise ValueError('Device limit reached')
            if any(other!=record['device_id'] and other_record['secret']==secret for other,other_record in self.devices.items()):raise ValueError('Paired keys must be unique')
            if previous and previous['secret']!=secret:self.hub.vr.unregister_lan(record['device_id'])
            self.hub.vr.register_lan(record['device_id'],execution_key(secret))
            self.devices[record['device_id']]=dict(address=record['address'],secret=secret,
                observed_address=previous.get('observed_address') if previous else None)
    def authenticate(self,method,path,headers,body,address):
        device,boot,seq=headers.get('X-EEG-Device',''),headers.get('X-EEG-Boot',''),headers.get('X-EEG-Seq','')
        if not re.fullmatch('[0-9a-f]{32}',boot) or not re.fullmatch('[1-9][0-9]{0,14}',seq):raise ValueError('Invalid authentication')
        with self.lock:
            record=self.devices.get(device)
            if not record:raise ValueError('Unpaired device identity')
            if not hmac.compare_digest(headers.get('X-EEG-MAC',''),signature(record['secret'],method,path,device,boot,seq,body)):raise ValueError('Invalid signature')
            key=device,boot
            if key not in self.replay and len(self.replay)>=64:raise ValueError('Boot capacity reached')
            high,seen=self.replay.get(key,(0,set()));number=int(seq)
            if number in seen or number<=high-64:raise ValueError('Replay')
            high=max(high,number);seen={n for n in seen if n>high-64};seen.add(number);self.replay[key]=high,seen
            record['observed_address']=address
        return device,boot,seq,record['secret']
    def dispatch(self,device,method,path,body):
        if method=='POST':
            from .device_transition import transition_state,retired_action,MESSAGE
            archived=transition_state(self.root)['legacy_archived']
            data=json.loads(body or b'{}') if archived and path in ['/v1/intent/control','/v1/source','/v1/intent/test'] else {}
            if retired_action(path,data,archived,getattr(self.hub,'new_device_mode',False)):
                raise ValueError(MESSAGE)
        if method=='POST' and getattr(self.hub,'model_library',None) and self.hub.model_library.switching() and path not in ['/v1/intent/stop','/v1/vr/stop','/v1/calibration/cancel']:
            raise ValueError('模型正在切换，请等待加载结果')
        if method=='GET' and path=='/v1/status':
            # Deliberately small read-only view: no raw EEG, config, credentials,
            # private pairing material or administration endpoints on the headset.
            state=self.hub.snapshot();source=state.get('source') or {};quality=state.get('quality') or {}
            server=state.get('server') or {};health=server.get('last_health') or {};result=state.get('result') or {}
            age=server.get('health_age_ms');worker_fresh=bool(server.get('worker_connected') and isinstance(age,(int,float)) and 0<=age<5000)
            peers=[p for p in (state.get('vr') or {}).get('clients',[]) if p.get('online') and p.get('role')=='unity']
            telemetry=peers[0].get('telemetry',{}) if len(peers)==1 else {}
            cached=getattr(getattr(self.hub,'preparation',None),'cached',None) or {}
            intent=self.hub.intent.snapshot() if hasattr(self.hub,'intent') else {};prediction=intent.get('latest') or {};model_health=intent.get('health') or {}
            calibration=self.hub.calibration.snapshot() if hasattr(self.hub,'calibration') else None
            control=(state.get('intent_control') or {}).copy();control['fresh']=bool(intent.get('fresh'));control['calibrated']=False;control['model_check_required']=False
            if not intent.get('fresh'):control['decision']=None
            columns=quality.get('channels',[])
            if not isinstance(columns,list) or len(columns)>256:raise ValueError('Invalid channel status')
            def accuracy(value):return value if type(value) in [int,float] and math.isfinite(value) and 0<=value<=1 else None
            training_accuracy=accuracy((model_health.get('training_fit') or {}).get('accuracy'))
            development_accuracy=accuracy((model_health.get('development') or {}).get('accuracy'))
            return dict(version=1,status_schema=3,control=control,calibration=calibration,
                source=dict(connected=bool(source.get('connected')),fresh=bool(source.get('fresh')),
                    transport=source.get('transport'),transport_label=source.get('transport_label'),
                    device=source.get('device'),sample_rate=source.get('sfreq_measured') or source.get('nominal_sfreq'),
                    sample_rate_source='host_observed' if source.get('sfreq_measured') else 'device_configuration',
                    nominal_rate=source.get('nominal_sfreq'),age_ms=source.get('age_ms'),
                    channel_count=len(columns),eeg_channel_count=sum(c.get('kind','EEG')=='EEG' for c in columns),
                    good_channels=quality.get('good_channels'),quality_valid=bool(quality.get('valid')),
                    quality_state=quality.get('state','unknown'),contact_quality_known=quality.get('contact_quality_known') is True,
                    channels=[{k:c.get(k) for k in ['id','name','kind','unit','color','quality_label','training_eligible']} for c in columns]),
                model=dict(name=((('训练数据测试 '+str((intent.get('training_test') or {}).get('label'))+' / ') if (intent.get('training_test') or {}).get('active') else '')+str(model_health.get('model_id') or result.get('mode') or 'unknown'))[:120],
                    status=('experimental_loaded' if model_health.get('weights_loaded') else 'loaded' if health.get('weights_loaded') else 'not_loaded') if worker_fresh else 'unknown',
                    note=str(prediction.get('prediction') or (intent.get('readiness') or {}).get('short_message') or (intent.get('readiness') or {}).get('message') or intent.get('rejection') or '等待有效模型输入')[:160],compute_ms=prediction.get('worker_ms'),result_fresh=bool(intent.get('fresh')),
                    active_probability=prediction.get('active_probability'),idle_probability=1-prediction['active_probability'] if prediction.get('active_probability') is not None else None,
                    result_age_ms=intent.get('age_ms'),probabilities=prediction.get('probabilities'),direction_probabilities=prediction.get('direction_probabilities'),usable_channels=prediction.get('usable_channels'),
                    output_schema=prediction.get('output_schema',1),axes_probabilities=prediction.get('axes_probabilities'),score_semantics=prediction.get('score_semantics'),calibrated=bool(prediction.get('calibrated')),labels=prediction.get('labels'),
                    inference_enabled=bool(intent.get('inference_enabled')),inference_state=intent.get('inference_state','stopped'),parameters=model_health.get('parameters'),candidate_kind=model_health.get('candidate_kind'),
                    training_accuracy=training_accuracy,development_accuracy=development_accuracy,
                    has_training_accuracy=training_accuracy is not None,has_development_accuracy=development_accuracy is not None,roundtrip_ms=prediction.get('roundtrip_ms'),
                    evidence_ms=model_health.get('evidence_ms'),hop_ms=model_health.get('hop_ms')),
                server=dict(connected=worker_fresh,device=str(health.get('device') or 'unknown')[:40],health_age_ms=age,
                    gpus=[{k:g.get(k) for k in ['index','name','used_mb','total_mb','utilization','temperature']} for g in (health.get('gpus') or [])[:4]]),
                execution=dict(known=bool(telemetry),state=telemetry.get('state','UNKNOWN'),mode=telemetry.get('mode','UNKNOWN'),
                    speed=telemetry.get('actual_speed'),yaw_rate=telemetry.get('actual_yaw_rate')),
                training=dict(formal_ready=bool(cached.get('formal_training_ready')),checked_at=cached.get('checked_at'),status='cached_readiness'),
                acquisition=dict(active=bool((state.get('acquisition') or {}).get('active'))))
        if method=='GET' and path=='/v1/pairing':
            return dict(version=1,transport='lan',host=None,port=self.hub.vr.port,hub_session=self.hub.vr.session,
                        lan_hosts=lan_candidates(),lan_http_port=self.port,prefer_lan=True)
        if method=='GET' and path=='/v1/acquisition/sync':return self.hub.acquisition.sync()
        if method=='GET' and path=='/v1/acquisition/status':return self.hub.acquisition.snapshot()
        if getattr(self.hub,'new_device_mode',False) and path.startswith('/v1/calibration/'):
            if method=='GET' and path=='/v1/calibration/status':return dict(active=False,state='not_configured',phase='idle',reason='新模型尚未配置',remaining_s=0)
            if method=='GET' and path=='/v1/calibration/history':return dict(results=[])
            if method=='POST' and path=='/v1/calibration/cancel':return dict(active=False,state='not_configured')
            raise ValueError('新设备模型与校验尚未配置')
        if method=='GET' and path=='/v1/calibration/status':return self.hub.calibration.snapshot()
        if method=='POST' and path=='/v1/calibration/start':
            data=json.loads(body or b'{}');return self.hub.calibration.start('vr',data.get('mode','directions'),data.get('repetitions',1))
        if method=='GET' and path=='/v1/calibration/history':return dict(results=self.hub.calibration.history())
        if method=='POST' and path=='/v1/calibration/present':return self.hub.calibration.present(json.loads(body))
        if method=='POST' and path=='/v1/calibration/cancel':return self.hub.calibration.cancel()
        if method=='POST' and path=='/v1/calibration/new-placement':return self.hub.calibration.invalidate(new_placement=True)
        if method=='GET' and path=='/v1/intent/status':return dict(**self.hub.intent.snapshot(),control=self.hub.intent_control.snapshot())
        if method=='POST' and path=='/v1/intent/start':return self.hub.intent.start(json.loads(body or b'{}').get('purpose','normal'))
        if method=='POST' and path=='/v1/intent/stop':return self.hub.intent.stop()
        if method=='POST' and path=='/v1/intent/control':
            data=json.loads(body)
            with self.hub.lock:
                if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
                if data.get('enabled') and not self.hub.intent.snapshot().get('inference_enabled'):raise ValueError('请先启动实时推理')
                if data.get('enabled') and getattr(self.hub,'new_device_mode',False) and not self.hub.intent.snapshot().get('fresh'):raise ValueError('推理仍在等待或预热，请在新鲜结果恢复后重新允许动作')
                return self.hub.intent_control.configure(data)
        if method=='POST' and path=='/v1/mirror':
            if not 32<=len(body)<=262144 or not body.startswith(b'\xff\xd8') or not body.endswith(b'\xff\xd9'):raise ValueError('Bounded JPEG required')
            now=time.monotonic()
            with self.lock:
                previous=self.frames.get(device)
                if previous and now-previous['time']<1/12:raise ValueError('Frame rate exceeded')
                seq=previous['seq']+1 if previous else 1;self.frames[device]=dict(jpeg=body,time=now,seq=seq)
                self.stats['frames']+=1;self.stats['bytes']+=len(body)
            return dict(saved=True,seq=seq)
        if method=='POST' and path in ['/v1/acquisition/start','/v1/acquisition/event','/v1/acquisition/stop']:
            data=json.loads(body)
            if not isinstance(data,dict):raise ValueError('Object required')
            if data.get('input_surface')=='desktop' or self.hub.acquisition.snapshot().get('active') and self.hub.acquisition.snapshot().get('input_surface')=='desktop':raise ValueError('Desktop collection is owned by the local page')
            if path.endswith('/start'):
                self.hub.vr.stop_test();result=self.hub.acquisition.start(data)
                if result.get('active'):self.collection_owner=device
                return result
            if self.hub.acquisition.snapshot().get('active') and self.collection_owner!=device:raise ValueError('Collection belongs to another paired device')
            if path.endswith('/event'):return self.hub.acquisition.event(data)
            result=self.hub.acquisition.stop_client(data)
            self.collection_owner=None
            return result
        raise LookupError('Unsupported device endpoint')
    def frame(self,device_id=None):
        with self.lock:
            frame=self.frames.get(device_id) if device_id is not None else max(self.frames.values(),key=lambda f:f['time'],default=None)
            return frame.copy() if frame and time.monotonic()-frame['time']<2 else None
    def snapshot(self,device_id=None):
        with self.lock:
            latest=self.frames.get(device_id) if device_id is not None else max(self.frames.values(),key=lambda f:f['time'],default=None);age=(time.monotonic()-latest['time'])*1000 if latest else None
            return dict(enabled=self.server is not None,port=self.port,error=self.error,paired_devices=len(self.devices),mirror_fresh=age is not None and age<2000,frame_age_ms=round(age,1) if age is not None else None,frame_seq=latest['seq'] if latest else 0,frame_bytes=len(latest['jpeg']) if latest else 0,stats=dict(self.stats),scope='App center-eye spectator preview; no system UI, audio or recording')
    def start(self):
        service=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):self.handle_request()
            def do_POST(self):self.handle_request()
            def handle_request(self):
                self.connection.settimeout(3);auth=None;status=200
                try:
                    length=int(self.headers.get('Content-Length','0'));limit=262144 if self.path=='/v1/mirror' else 8192
                    if not 0<=length<=limit or self.headers.get('Transfer-Encoding'):raise ValueError('Invalid body')
                    body=self.rfile.read(length)
                    if len(body)!=length:raise ValueError('Incomplete body')
                    auth=service.authenticate(self.command,self.path,self.headers,body,self.client_address[0])
                    result=service.dispatch(auth[0],self.command,self.path,body)
                    if self.path=='/v1/pairing':result['host']=self.connection.getsockname()[0]
                except LookupError:status,result=404,dict(error='Unknown device endpoint')
                except CalibrationRequired as error:status,result=409,dict(error=str(error),code='calibration_required')
                except (ValueError,KeyError,TypeError,UnicodeError,socket.timeout):
                    status,result=(400 if auth else 403),dict(error='Device request rejected');service.stats['rejected']+=1
                encoded=json.dumps(result,allow_nan=False,separators=(',',':')).encode()
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.send_header('Cache-Control','no-store');self.send_header('Connection','close')
                if auth:
                    device,boot,seq,secret=auth;self.send_header('X-EEG-MAC',signature(secret,str(status),'response',device,boot,seq,encoded))
                self.end_headers();self.close_connection=True
                try:self.wfile.write(encoded)
                except (OSError,socket.timeout):pass
        try:
            self.server=BoundedServer(('0.0.0.0',self.port),Handler)
            threading.Thread(target=self.server.serve_forever,daemon=True,name='Quest paired LAN API').start()
        except OSError as error:self.error=type(error).__name__
    def close(self):
        if self.server:self.server.shutdown();self.server.server_close()
