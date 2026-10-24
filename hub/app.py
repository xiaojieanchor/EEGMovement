import argparse,collections,csv,datetime,hashlib,json,mimetypes,os,secrets,socket,subprocess,threading,time,traceback,webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
import numpy as np
from .protocol import StreamParser,source_idle
from .processing import CausalFilter,Notch50Filter,quality_metrics
from .notch_repair import rechecked_quality,POLICY as QUALITY_POLICY
from training.channel_policy import disabled_channels,exclude_quality
from .remote import RemoteBridge
from .replay import session_path,read_session,list_sessions
from .vr import VrLink
from .preparation import PreparationMonitor
from .acquisition import GuidedAcquisition
from .quest import QuestService
from .channel_quality import NativeQuality
from .file_io import read_shared_json
from .training_status import training_snapshot
from .intent import IntentMonitor
from .intent_control import IntentControl
from .model_check_v4 import SessionCalibration
from .session_calibration import CalibrationRequired
from .refactor_status import RefactorObserver
from .device_transition import transition_state,retired_action,background_targets,annotate_html,MESSAGE as TRANSITION_MESSAGE

ROOT=Path(__file__).resolve().parents[1];RUNTIME=ROOT/'hub/runtime';RUNTIME.mkdir(exist_ok=True)

class Hub:
    def __init__(self,config):
        self.transition=transition_state(ROOT)
        self.new_device_mode=bool(self.transition.get('new_device_software_ready'))
        self.disabled_channels=disabled_channels(ROOT)
        config=dict(config,disabled_channels_1based=[i+1 for i in self.disabled_channels])
        self.config=config;self.lock=threading.RLock();self.events=collections.deque(maxlen=80);self.raw=collections.deque(maxlen=1250);self.filtered=collections.deque(maxlen=1250)
        self.parser=StreamParser(config['source_mode'],disabled_channels=self.disabled_channels);self.filter=CausalFilter(config['sfreq']);self.source_enabled=True;self.source_mode='live';self.socket=None;self.stop_event=threading.Event();self.sample_index=0;self.epoch=0;self.last_packet=None;self.connected=False;self.source_error=None;self.packet_times=collections.deque(maxlen=1000);self.jitter=collections.deque(maxlen=1000);self.last_arrival=None;self.source_timestamp=None;self.quality={'valid':False,'state':'warming','label':'等待数据','channels':[],'good_channels':0,'reasons':[]};self.local_process_ms=0
        self.remote=RemoteBridge(config,ROOT,self.event);self.last_result=None;self.last_result_time=None;self.last_sent=None;self.pipeline_times=collections.deque(maxlen=600);self.inference_times=collections.deque(maxlen=600);self.remote_errors=0;self.return_count=0;self.recorder=None;self.recording_name=None;self.recording_samples=0;self.record_lock=threading.Lock();self.latest_health_poll=0;self.last_quality_label=None;self.stale_seen=False;self.request_counter=0;self.replay_data=None
        self.intent=None if self.new_device_mode else IntentMonitor(self,ROOT)
        self.intent_control=IntentControl(config.get('experimental_motion_enabled',False))
        self.closed_stats={k:0 for k in self.parser.stats};self.reconnects=0;self.replay_session=None;self.reconnect_after=0
        self.acquisition=GuidedAcquisition(self)
        self.calibration=SessionCalibration(self)
        self.refactor=RefactorObserver(self,ROOT)
        from .model_library import ModelLibrary
        self.model_library=None if self.new_device_mode else ModelLibrary(self,ROOT)
        self.native_quality=NativeQuality(ROOT)
        from training.quality_continuity import QualityContinuity
        self.quality_continuity=QualityContinuity()
        self.notch50=Notch50Filter(config['sfreq']);self.notched=collections.deque(maxlen=1250);self.notch_native_quality=NativeQuality(ROOT)
        self.build_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'hub/app.py',ROOT/'hub/protocol.py',ROOT/'hub/processing.py',ROOT/'hub/remote.py',ROOT/'hub/replay.py',ROOT/'hub/vr.py',ROOT/'hub/preparation.py',ROOT/'hub/acquisition.py',ROOT/'hub/quest.py',ROOT/'hub/channel_quality.py',ROOT/'hub/file_io.py',ROOT/'hub/intent.py',ROOT/'hub/intent_control.py']}
        self.preparation=PreparationMonitor(ROOT)
        self.response_drops={'previous_epoch':0,'stale_input_or_return':0}
        self.vr=VrLink(ROOT,self.execution_readiness,self.event,bind_host='0.0.0.0' if config.get('quest_lan_enabled') else '127.0.0.1')
        self.quest=QuestService(self,ROOT,config.get('quest_lan_port',8766)) if config.get('quest_lan_enabled') else None
        self.started=time.monotonic();self.event('中枢已启动；实验模型按概率与通道质量门控，采集时停止动作','info')
        if self.transition['legacy_archived']:
            self.source_enabled=False;self.source_mode='paused'
            self.intent_control.configure({'enabled':False})
            self.event(TRANSITION_MESSAGE,'info')
        self.build_hashes['hub/notch_repair.py']=hashlib.sha256((ROOT/'hub/notch_repair.py').read_bytes()).hexdigest()
        self.build_hashes['configs/device_channels.json']=hashlib.sha256((ROOT/'configs/device_channels.json').read_bytes()).hexdigest()
        self.build_hashes['training/channel_policy.py']=hashlib.sha256((ROOT/'training/channel_policy.py').read_bytes()).hexdigest()
        if self.new_device_mode:
            from .gnautilus_runtime import GnautilusRuntime
            self.gnautilus_runtime=GnautilusRuntime(self,ROOT,port=config.get('gnautilus_port',8767))
            from gnautilus_ml.ssh import ComputeBridge
            from .ml_runtime import MLManager,MLIntent
            self.remote=ComputeBridge(ROOT,self.event)
            self.ml=MLManager(self,ROOT,self.remote);self.model_library=self.ml;self.intent=MLIntent(self,self.ml)
            self.config.update(server_host=self.remote.profile['host'],server_port=self.remote.profile['port'],server_user=self.remote.profile['username'])
        self.public_relay=None
        relay_config=ROOT/'hub/runtime/public_relay.json'
        if self.new_device_mode and relay_config.exists():
            from .public_relay import HubRelay
            self.public_relay=HubRelay(self,json.loads(relay_config.read_text('utf-8')))
    def event(self,message,level='info'):
        with self.lock:self.events.appendleft({'time':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),'message':message,'level':level})
    def start(self):
        if self.new_device_mode:
            self.gnautilus_runtime.start();self.vr.start()
            for name,target in [('model catalog',self.ml.loop),('new-device inference',self.intent.loop)]:threading.Thread(target=target,daemon=True,name=name).start()
            if self.quest:self.quest.start()
            if self.public_relay:self.public_relay.start()
            return
        targets={'acquire':self.acquire,'analyze':self.analyze,'infer':self.infer,'intent.loop':self.intent.loop,'observe_training':self.observe_training,'refactor.loop':self.refactor.loop,'model_library.loop':self.model_library.loop}
        for name in background_targets(targets,self.transition['legacy_archived']):threading.Thread(target=targets[name],daemon=True).start()
        self.vr.start()
        if self.quest:self.quest.start()
    def observe_training(self):
        from .training_observer import ensure_background_observer
        while not self.stop_event.is_set():
            if self.remote.status.get('ssh_connected') and self.remote.password:
                try:
                    pid=ensure_background_observer(ROOT,self.remote.password)
                    if pid:self.event('训练日志常驻同步已启动；独立于AI会话运行','info')
                except Exception as error:self.event('训练日志同步启动失败：'+type(error).__name__,'error')
            self.stop_event.wait(20)
    def execution_readiness(self):
        if self.new_device_mode:return self.gnautilus_runtime.readiness()
        now=time.monotonic()
        with self.lock:
            source_fresh=not self.stop_event.is_set() and self.last_packet is not None and now-self.last_packet<.1
            origin=self.last_result_time-self.last_result['input_ready_to_return_ms']/1000 if self.last_result_time and self.last_result else None
            server_fresh=bool(source_fresh and self.last_result_time and now-self.last_result_time<.15 and origin and now-origin<.25 and self.remote.status['worker_connected'] and self.remote.status['ssh_connected'])
            reason='eeg_stream_stale' if not source_fresh else 'server_stale' if not server_fresh else 'quality_invalid' if not self.quality['valid'] else 'model_not_ready'
            collecting=self.acquisition.snapshot().get('active',False) or self.calibration.snapshot()['active'] or bool(getattr(self,'model_library',None) and self.model_library.switching())
            intent=self.intent.snapshot();prediction=intent.get('latest') if intent.get('fresh') else None
            from .intent import effective_mask
            mask=effective_mask(self.quality) if 0<=now-self.quality.get('checked_monotonic',-1e9)<.35 else [False]*7
            usable=(prediction or {}).get('usable_channels',list(range(7)))
            training_test=bool((intent.get('training_test') or {}).get('active') and prediction and prediction.get('source')=='TRAINING_TEST')
            valid=bool(training_test or source_fresh and self.source_mode=='live' and prediction and usable and all(mask[i] for i in usable))
            decision=self.intent_control.update(prediction if valid else None,collecting,training_test_authorized=training_test)
            if training_test:decision=dict(decision,reason='training_data_test:'+decision['reason'])
            return dict(acquisition_active=collecting,source_fresh=bool(source_fresh),source_valid=bool(training_test or source_fresh and self.source_mode=='live' and (valid or self.quality['valid'])),server_fresh=server_fresh,epoch=self.epoch,reason=decision['reason'] if decision.get('model_ready') else reason,input_origin=decision.get('input_origin') if decision.get('model_ready') else origin if server_fresh else None,decision_id=decision.get('decision_id',0),model_fresh=bool(valid),model_decision=decision)
    def start_receiver(self):
        if any(c['online'] and c['role']=='csharp_harness' for c in self.vr.snapshot()['clients']):return
        binary=ROOT/'receiver/bin/EEGReceiver.exe'
        if not binary.exists():raise ValueError('请先运行 scripts/build_receiver.ps1 编译本机接收器')
        with (RUNTIME/'receiver.log').open('ab',buffering=0) as log:
            p=subprocess.Popen([str(binary),'--config',str(RUNTIME/'vr_pairing.json'),'--allow-test'],cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (RUNTIME/'receiver.pid').write_text(str(p.pid));self.event('已启动本机C#联调接收器；尚未运行Unity引擎','info')
    def reset_stream(self):
        with self.lock:
            for k,v in self.parser.stats.items():self.closed_stats[k]+=v
            self.epoch+=1;self.raw.clear();self.filtered.clear();self.filter.reset();self.notched.clear();self.notch50.reset();self.parser=StreamParser(self.config['source_mode'],disabled_channels=self.disabled_channels);self.packet_times.clear();self.jitter.clear();self.last_arrival=None;self.last_packet=None;self.source_timestamp=None;self.last_result=None;self.last_result_time=None;self.last_sent=None;self.quality={'valid':False,'state':'warming','label':'等待完整窗口','channels':[],'good_channels':0,'reasons':[]}
    def stop_source_socket(self):
        sock=self.socket;self.socket=None
        if sock:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            try:sock.close()
            except OSError:pass
    def set_source(self,mode,session=None):
        if mode not in ['live','replay','recording','paused']:raise ValueError('unknown source')
        self.intent.cancel_test()
        self.acquisition.stop(reason='source_changed')
        self.calibration.invalidate('采集来源已切换，请重新校准')
        selected=session_path(ROOT/'outputs/sessions',session) if mode=='recording' else None
        if selected:next(read_session(selected),None) # Validate before changing the current input.
        self.source_enabled=False;self.stop_source_socket();self.stop_recording()
        with self.lock:
            self.vr.stop_test() # Invalidates pending TEST atomically with the source epoch/mode change.
            self.source_mode=mode;self.replay_session=selected;self.connected=False;self.reset_stream();self.source_enabled=mode!='paused'
        self.event({'live':'切换到真实Socket数据','replay':'切换到公开真实EEG回放（不是当前设备）','recording':'切换到本地会话回放（不是当前设备）','paused':'采集已暂停，停止输出'}[mode],'info')
    def accept(self,samples,seq,timestamp,gap=0,discontinuity=False,source_epoch=None):
        if source_epoch is not None and source_epoch!=self.epoch:return
        t=time.perf_counter();x=np.asarray(samples,dtype=np.float64)
        with self.lock:
            if source_epoch is not None and source_epoch!=self.epoch:return
            if discontinuity:
                self.raw.clear();self.filtered.clear();self.filter.reset();self.notched.clear();self.notch50.reset();self.epoch+=1;self.last_result=None;self.last_result_time=None
                from .channel_quality import pending_quality
                self.quality=pending_quality(self.quality,'数据缺包／断点：暂停训练与动作，补齐连续窗口后复核；通道显示为上次检测')
            usable_x=x.copy()
            if self.disabled_channels:usable_x[:,list(self.disabled_channels)]=0
            y=self.filter.process(usable_x);notched=self.notch50.process(usable_x);self.notched.extend(notched.tolist());now=time.monotonic()
            if self.last_arrival is not None:self.jitter.append(abs(now-self.last_arrival-len(x)/250)*1000)
            self.last_arrival=now;self.packet_times.append((now,len(x)));self.raw.extend(x.tolist());self.filtered.extend(y.tolist());self.sample_index+=len(x);self.last_packet=now;self.source_timestamp=timestamp;self.connected=True;self.source_error=None;self.local_process_ms=(time.perf_counter()-t)*1000
            packet_end,packet_epoch,packet_source=self.sample_index,self.epoch,self.source_mode
        with self.record_lock:
            if self.recorder:
                row={'kind':'eeg','receive_monotonic':now,'received_at':datetime.datetime.now().astimezone().isoformat(),'source':packet_source,'source_epoch':packet_epoch,'timestamp_estimation':'official host timestamp; intra-packet timing estimated at250Hz, anchor relative to first/last ADC sample unverified','packet_timestamp':timestamp,'packet_seq':seq,'sample_index_end':packet_end,'gap_packets':gap,'discontinuity':discontinuity,'samples_uv':samples}
                self.recorder.write(json.dumps(row,allow_nan=False)+'\n');self.recording_samples+=len(samples)
    def acquire(self):
        backoff=.5
        while not self.stop_event.is_set():
            if not self.source_enabled or self.source_mode=='paused':self.stop_event.wait(.2);continue
            if self.source_mode in ['replay','recording']:
                try:self.replay()
                except Exception as e:self.source_error=str(e);self.stop_event.wait(1)
                continue
            try:
                sock=socket.create_connection((self.config['source_host'],self.config['source_port']),timeout=3);sock.settimeout(.25);sock.sendall(b'start');self.socket=sock;self.connected=True;self.source_error=None;self.reconnects+=1;self.event('NeuraDock Socket已连接，等待设备样本','info')
                # Each connection establishes a new sequence/filter time segment.
                self.reset_stream();connected_at=time.monotonic();last_valid_at=None
                while self.source_enabled and self.source_mode=='live' and not self.stop_event.is_set():
                    if source_idle(connected_at,last_valid_at,time.monotonic()):raise ConnectionError('Socket连续5秒没有新样本，正在重连数据订阅；请检查设备蓝牙与数据源')
                    try:data=sock.recv(65536)
                    except socket.timeout:continue
                    if not data:raise ConnectionError('设备Socket关闭')
                    for packet in self.parser.feed(data):
                        with self.lock:
                            if sock is not self.socket or not self.source_enabled or self.source_mode!='live':break
                            self.accept(packet.samples,packet.sequence,packet.timestamp,packet.gap,packet.discontinuity)
                            last_valid_at=time.monotonic();backoff=.5
                self.stop_source_socket()
            except (OSError,ConnectionError) as e:
                self.connected=False;self.source_error=str(e);self.stop_source_socket()
                if self.source_enabled and self.source_mode=='live':self.event('采集断开，自动重连中','warn');self.reset_stream();self.stop_event.wait(backoff);backoff=min(backoff*2,5)
    def replay(self):
        if self.source_mode=='recording':return self.replay_recording()
        # Official teaching release contains no confirmed task or anatomical labels.
        if self.replay_data is None:
            p=ROOT/'research/sources/repositories/Neuradock__neuradock-eeg-101/data/teaching/lesson-02/recording-01.txt';rows=[]
            with p.open(encoding='utf-8') as f:
                for line in f:
                    a=line.strip().split(',')
                    if len(a)>=9:
                        try:rows.append([float(x) for x in a[2:9]])
                        except ValueError:pass
            if not rows:raise ValueError('公开回放记录没有可用样本')
            self.replay_data=rows
        pos=0;seq=0;deadline=time.monotonic()
        while self.source_enabled and self.source_mode=='replay' and not self.stop_event.is_set():
            with self.lock:
                if not self.source_enabled or self.source_mode!='replay':return
                if pos+5>len(self.replay_data):pos=0;self.reset_stream();self.event('公开记录回放重新开始','info')
                self.accept(self.replay_data[pos:pos+5],seq,'public_recording_replay');pos+=5;seq=(seq+1)%256
            deadline+=.02;self.stop_event.wait(max(0,deadline-time.monotonic()))
    def replay_recording(self):
        path=self.replay_session
        while self.source_enabled and self.source_mode=='recording' and self.replay_session==path and not self.stop_event.is_set():
            previous=None;count=0
            for row in read_session(path):
                if not self.source_enabled or self.source_mode!='recording' or self.replay_session!=path:return
                if row['kind']=='marker':self.event('回放标记：'+str(row.get('code',''))[:80],'info');continue
                stamp=float(row.get('receive_monotonic',0));delay=max(0,stamp-previous) if previous is not None else 0;previous=stamp
                deadline=time.monotonic()+delay
                while time.monotonic()<deadline:
                    if not self.source_enabled or self.source_mode!='recording' or self.replay_session!=path:return
                    if self.stop_event.wait(min(.05,max(0,deadline-time.monotonic()))):return
                with self.lock:
                    if not self.source_enabled or self.source_mode!='recording' or self.replay_session!=path:return
                    self.accept(row['samples_uv'],row.get('packet_seq'),row.get('packet_timestamp','recording'),row.get('gap_packets',0),bool(row.get('discontinuity',row.get('gap_packets',0))))
                count+=1
            if not count:raise ValueError('会话尚无 EEG 样本')
            if self.source_mode!='recording' or self.replay_session!=path:return
            self.reset_stream();self.event('本地会话回放重新开始','info')
    def analyze(self):
        while not self.stop_event.wait(.1):
            self.acquisition.expire()
            with self.lock:x=list(self.raw)[-500:];xn=list(self.notched)[-500:];warm=self.notch50.warm_samples;epoch=self.epoch;last=self.last_packet;end=self.sample_index
            if x and self.disabled_channels:
                x=np.asarray(x,dtype=np.float64);x[:,list(self.disabled_channels)]=0;x=x.tolist()
            q=quality_metrics(x,max_peak=self.config['quality_max_peak_uv'],max_rms=self.config['quality_max_rms_uv'],line_threshold=self.config['quality_line_ratio'])
            # Electrode QC consumes raw data, not the causal display filter.
            # Its vendor window is 256 samples; do not add the unrelated 2 s
            # filter warm-up after every Bluetooth packet gap.
            if len(x)<256:q.update(valid=False,state='warming',label='等待1.024秒连续质量窗口')
            if last is None or (time.monotonic()-last)*1000>self.config['source_stale_ms']:q.update(valid=False,state='stale',label='数据已过期 / 等待采集')
            native=self.native_quality.evaluate(x[-256:],q,sample_index_end=end,source_epoch=epoch) if len(x)>=256 and q['state'] not in ['warming','stale'] else None
            if native is None:
                from .channel_quality import green_channels,pending_quality
                with self.lock:previous=self.quality.copy()
                if q['state']=='warming' and previous.get('measurement_held'):
                    q=pending_quality(previous,previous['label']);native={}
                else:native=green_channels(q,None)
                self.native_quality.eligible_since=[None]*7
            q.update(native)
            if len(x)>=256 and len(xn)==len(x) and q['state'] not in ['warming','stale']:
                post=quality_metrics(xn,max_peak=self.config['quality_max_peak_uv'],max_rms=self.config['quality_max_rms_uv'],line_threshold=self.config['quality_line_ratio'])
                filtered_native=self.notch_native_quality.evaluate(xn[-256:],post,sample_index_end=end,source_epoch=epoch)
                q=rechecked_quality(q,post,filtered_native,warm)
                q['notch50_error']=filtered_native.get('native_error')
            q.update(sample_index_start=end-min(len(x),256),sample_index_end=end,software_context_start=end-len(x),source_epoch=epoch,
                     policy=QUALITY_POLICY,checked_monotonic=time.monotonic())
            q=exclude_quality(q,self.disabled_channels)
            with self.lock:
                if epoch==self.epoch:
                    q.update(available_at_monotonic=time.monotonic(),available_sample_index_end=self.sample_index)
                    # Native work may be slow or a stream can stop mid-call. Never
                    # publish green for a frame that became stale while computing.
                    current=time.monotonic()
                    if self.last_packet is None or current-self.last_packet>self.config['source_stale_ms']/1000 or current-(last or 0)>1:
                        q.update(training_mask=[False]*7,training_good_channels=0)
                        q['channels']=[dict(c,training_eligible=False,color='gray',quality_label='状态过期') for c in q['channels']]
                    q=exclude_quality(q,self.disabled_channels)
                    q['software_valid']=q['valid']
                    q['valid']=q['valid'] and all(q['training_mask'][i] for i in range(7) if i not in self.disabled_channels)
                    q['good_channels']=sum(q['training_mask'])
                    if q['state'] not in ['warming','stale']:
                        q['state']='usable' if q['valid'] else 'invalid'
                        q['label']='厂家引擎＋本机检查：'+str(q['good_channels'])+'/'+str(7-len(self.disabled_channels))+' 路可进入训练'+(' · CH'+('/CH'.join(str(i+1) for i in self.disabled_channels))+'永久停用' if self.disabled_channels else '')
                    q,resolutions=self.quality_continuity.observe(q)
                    self.quality=q
                    self.intent.record_quality(q)
                    for resolution in resolutions:self.intent.record_quality(resolution)
                    if q['label']!=self.last_quality_label:self.last_quality_label=q['label'];self.event(q['label'],'good' if q['valid'] else 'warn')
                    with self.record_lock:
                        if self.recorder:
                            self.recorder.write(json.dumps(dict(kind='channel_quality',**q),ensure_ascii=False,allow_nan=False)+'\n')
                            for resolution in resolutions:self.recorder.write(json.dumps(resolution,ensure_ascii=False,allow_nan=False)+'\n')
    def infer(self):
        last_error_time=0
        while not self.stop_event.wait(.02):
            if self.remote.status['connecting']:continue
            if not self.remote.status['ssh_connected']:
                now=time.monotonic()
                if self.remote.password and now>self.reconnect_after:
                    self.reconnect_after=now+15
                    threading.Thread(target=self.remote.connect,args=(self.remote.password,),daemon=True).start()
                continue
            now=time.monotonic()
            if now-self.latest_health_poll>3:
                self.latest_health_poll=now
                try:
                    health=self.remote.request('GET','/health');self.remote.status.update(last_health=health,last_health_time=time.monotonic(),worker_connected=True,last_error=None)
                except Exception as e:
                    self.remote.status.update(worker_connected=False,last_error=str(e)[:180])
            with self.lock:
                end=self.sample_index;epoch=self.epoch;last=self.last_packet;q=self.quality.copy();raw=list(self.raw)[-self.config['window_samples']:];filtered=list(self.filtered)[-self.config['window_samples']:];source=self.source_mode
            if last is None or now-last>self.config['source_stale_ms']/1000 or len(raw)<self.config['window_samples']:continue
            if self.last_sent and self.last_sent[0]==epoch and end-self.last_sent[1]<self.config['hop_samples']:continue
            self.last_sent=(epoch,end);self.request_counter+=1;rid=self.request_counter
            raw=[list(row) for row in raw]
            for row in raw:
                for i in self.disabled_channels:row[i]=0.
            payload=dict(request_id=rid,sample_index_end=end,sfreq=250,raw=raw,filtered=filtered,quality_valid=q['valid'],source=source,frequencies=self.config['ssvep_frequencies'])
            t=time.monotonic()
            try:
                result=self.remote.request('POST','/process',payload);done=time.monotonic()
                with self.lock:
                    # Late responses from a previous stream/epoch or stale samples never become current.
                    if epoch!=self.epoch or self.source_mode!=source:
                        self.response_drops['previous_epoch']+=1;continue
                    if self.last_packet is None or done-self.last_packet>.25 or done-last>self.config['server_stale_ms']/1000:
                        self.response_drops['stale_input_or_return']+=1;continue
                    result['hub_roundtrip_ms']=round((done-t)*1000,2);result['input_ready_to_return_ms']=round((done-last)*1000,2);result['epoch']=epoch
                    self.last_result=result;self.last_result_time=done;self.return_count+=1;self.pipeline_times.append(result['input_ready_to_return_ms']);self.inference_times.append(result['compute_ms']);self.remote.status.update(worker_connected=True,last_error=None)
            except Exception as e:
                with self.lock:
                    self.remote_errors+=1;self.remote.status.update(worker_connected=False,last_error=str(e)[:180]);self.last_result=None;self.last_result_time=None
                if now-last_error_time>5:self.event('服务器处理未返回，输出保持停止','warn');last_error_time=now
                self.stop_event.wait(.5)
    def start_recording(self):
        if self.new_device_mode:return self.gnautilus_runtime.start_recording()
        self.intent.cancel_test()
        with self.lock,self.record_lock:
            if getattr(self,'model_library',None) and self.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
            if self.recorder:return
            d=ROOT/'outputs/sessions';d.mkdir(parents=True,exist_ok=True);name=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(2)
            self.recorder=(d/(name+'.jsonl')).open('x',encoding='utf-8');self.recording_name=name;self.recording_samples=0
            meta=dict(kind='session',started_at=datetime.datetime.now().astimezone().isoformat(),source=self.source_mode,sfreq=250,channels=self.config['channel_names'],montage_verified=False,unit='uV',mode='unlabeled_transport_diagnostic' if self.source_mode=='live' else 'historical_recording_replay',wearing_status='not_collected_by_app',sample_index_semantics='count of received samples, excludes missing samples; preserve gap_packets and discontinuity',motion_enabled=False,channel_quality_policy='neuradock_native_green_v1',quality_semantics='exact sample ranges in channel_quality rows; missing coverage is invalid; any overlapping non-green vetoes')
            meta.update(channel_quality_policy=QUALITY_POLICY,notch50=dict(frequency_hz=50,q=30,causal=True,raw_preserved=True,model_filter_applied_once=True))
            meta['disabled_channels_1based']=[i+1 for i in self.disabled_channels]
            meta['quality_resolution_policy']='native_status_continuity_200ms_v1'
            self.recorder.write(json.dumps(meta,ensure_ascii=False)+'\n')
        self.event('开始本地记录：'+name,'info')
    def stop_recording(self):
        if self.new_device_mode:return self.gnautilus_runtime.stop_recording()
        saved=False
        with self.lock,self.record_lock:
            if self.recorder:self.recorder.flush();self.recorder.close();self.recorder=None;saved=True
        # Never hold record_lock while entering event()->self.lock (acquire uses the reverse order).
        if saved:self.event('会话记录已保存','good')
    def _snapshot_core(self):
        now=time.monotonic()
        with self.lock:
            age=(now-self.last_packet)*1000 if self.last_packet else None;fresh=age is not None and age<self.config['source_stale_ms']
            times=list(self.packet_times);hz=sum(n for t,n in times if t>now-5)/max(.001,min(5,now-times[0][0])) if len(times)>1 else 0
            q=self.quality.copy()
            quality_fresh=0<=now-q.get('checked_monotonic',-1e9)<1
            if not quality_fresh:
                q.update(valid=False,state='qc_stale',label='通道质量状态未知或过期',good_channels=0,training_mask=[False]*7,training_good_channels=0,
                         channels=[dict(c,training_eligible=False,color='gray',quality_label='质量状态过期') for c in q.get('channels',[])])
            if not fresh:q.update(valid=False,state='stale',label='数据已过期 / 等待采集',good_channels=0,training_mask=[False]*7,training_good_channels=0,channels=[dict(c,valid=False,training_eligible=False,color='gray',quality_label='数据过期',reasons=['数据过期']) for c in q.get('channels',[])])
            q=exclude_quality(q,self.disabled_channels)
            total=self.parser.stats['valid_packets']+self.parser.stats['missing_packets'];loss=100*self.parser.stats['missing_packets']/total if total else 0
            rs=self.remote.status.copy();last_health_age=now-rs['last_health_time'] if rs.get('last_health_time') else None
            rs['health_age_ms']=round(last_health_age*1000,1) if last_health_age is not None else None;rs['worker_connected']=bool(rs['worker_connected'] and last_health_age is not None and last_health_age<8)
            result_age=(now-self.last_result_time)*1000 if self.last_result_time else None;result=self.last_result.copy() if self.last_result else None
            result_fresh=result is not None and result_age is not None and result_age<self.config['server_stale_ms'] and fresh and rs['worker_connected'] and rs['ssh_connected']
            traces=list(self.filtered)[-750:];raw=list(self.raw)[-750:]
            vr=self.vr.snapshot();execution=vr['execution']
            control_reasons=['尚未校准运动意图模型']+([] if execution['source'] else ['无新鲜VR接收端回执'])+([] if q['valid'] else ['当前信号质量无效'])+([] if result_fresh else ['无新鲜服务器返回'])
            snapshot=dict(version='0.2.0',build_hashes=self.build_hashes,project='EEGMovement',uptime_s=now-self.started,source=dict(mode=self.source_mode,replay_session=self.replay_session.name if self.replay_session else None,endpoint=f"{self.config['source_host']}:{self.config['source_port']}",connected=self.connected,fresh=fresh,age_ms=round(age,1) if age is not None else None,sfreq_measured=round(hz,1),nominal_sfreq=250,samples_total=self.sample_index,sequence=self.parser.last_sequence,packet_timestamp=self.source_timestamp,stats=self.parser.stats.copy(),stats_total={k:self.closed_stats[k]+v for k,v in self.parser.stats.items()},connections=self.reconnects,loss_pct=round(loss,3),jitter_p95_ms=round(float(np.percentile(self.jitter,95)),2) if self.jitter else None,error=self.source_error),quality=q,processing=dict(causal=True,bandpass='1–45 Hz',notch='50 Hz',window_ms=1000,hop_ms=100,local_ms=round(self.local_process_ms,3),filter_warm_samples=self.filter.warm_samples,channel_names=self.config['channel_names'],montage_verified=False),server=rs,result=result,result_age_ms=round(result_age,1) if result_age is not None else None,result_fresh=result_fresh,performance=dict(response_drops=self.response_drops.copy(),return_count=self.return_count,errors=self.remote_errors,measurement='last input packet received by hub to GPU response accepted; excludes evidence window, Bluetooth upstream and VR',window_returns=len(self.pipeline_times),input_return_p50_ms=round(float(np.percentile(self.pipeline_times,50)),2) if self.pipeline_times else None,input_return_p95_ms=round(float(np.percentile(self.pipeline_times,95)),2) if self.pipeline_times else None,compute_p95_ms=round(float(np.percentile(self.inference_times,95)),3) if self.inference_times else None),control=dict(enabled=False,state=execution['state'],mode=execution['mode'],speed=execution['actual_speed'],yaw_rate=execution['actual_yaw_rate'],actual_speed=execution['actual_speed'],actual_yaw_rate=execution['actual_yaw_rate'],source=execution['source'],status=execution['status'],reasons=control_reasons),vr=vr,recording=dict(active=self.recorder is not None,name=self.recording_name,samples=self.recording_samples),waveforms=dict(filtered=traces[::2],raw=raw[::2],notch50=list(self.notched)[-750:][::2],display_sfreq=125,seconds=3),events=list(self.events)[:20])

            snapshot['source']['sequence_transitions']=list(self.parser.transitions)
            snapshot['source']['continuous_samples']=len(self.raw)
            snapshot['source']['source_epoch']=self.epoch
        return snapshot
    def snapshot(self):
        snapshot=self.gnautilus_runtime.snapshot() if self.new_device_mode else self._snapshot_core()
        snapshot['project_phase']=self.transition
        snapshot['public_relay']=self.public_relay.snapshot() if self.public_relay else dict(enabled=False,connected=False)
        snapshot['acquisition']=self.acquisition.snapshot()
        snapshot['calibration']=self.calibration.snapshot()
        snapshot['vr']['unity_engine_verified']=self.preparation.engine_verified_hint(snapshot)
        snapshot['vr']['quest_verified']=False
        snapshot['quest']=self.quest.snapshot() if self.quest else dict(enabled=False,mirror_fresh=False)
        model_control=self.intent_control.snapshot()
        snapshot['intent_control']=model_control;snapshot['vr']['model_control_enabled']=model_control['enabled']
        decision=model_control.get('decision') or {}
        snapshot['control']['armed']=model_control['enabled']
        intent=self.intent.snapshot();test_fresh=intent.get('mode')=='TRAINING_TEST' and intent.get('fresh')
        snapshot['control']['enabled']=bool(model_control['enabled'] and (snapshot['source']['fresh'] or test_fresh) and decision.get('model_ready') and time.monotonic()-decision.get('input_origin',0)<.5)
        snapshot['control']['input_source']='TRAINING_TEST' if test_fresh else 'LIVE'
        snapshot['control']['reasons']=[(model_control.get('decision') or {}).get('reason','等待有效模型输入')]
        return snapshot

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def send(self,status,body,content_type='application/json; charset=utf-8'):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'");self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def allowed(self):
        host=self.headers.get('Host','').split(':')[0];origin=self.headers.get('Origin')
        return host in ['127.0.0.1','localhost'] and (not origin or urlparse(origin).netloc==self.headers.get('Host'))
    def do_GET(self):
        if not self.allowed():return self.send(403,{'error':'local origin required'})
        path=urlparse(self.path).path
        if HUB.new_device_mode and (path.startswith('/api/ml/') or path.startswith('/api/training/jobs')):
            try:return self.send(200,HUB.ml.api_get(self.path))
            except (ValueError,OSError,ConnectionError) as error:return self.send(400,{'error':str(error)})
        if path=='/api/status':return self.send(200,HUB.snapshot())
        if path=='/api/gnautilus/status':return self.send(200,HUB.gnautilus_runtime.snapshot()['gnautilus'] if HUB.new_device_mode else {'enabled':False})
        if path=='/api/relay/status':return self.send(200,HUB.public_relay.snapshot() if HUB.public_relay else {'enabled':False})
        if path=='/api/relay/enrollment':
            role=parse_qs(urlparse(self.path).query).get('role',['eeg'])[0]
            if role not in ('eeg','quest'):return self.send(400,{'error':'Invalid role'})
            matches=list((ROOT/'hub/runtime/relay_enrollments').glob(role+'-*.json'))
            if len(matches)!=1:return self.send(409,{'error':'需要在本机按设备身份选择配对文件'})
            return self.send(200,json.loads(matches[0].read_text('utf-8')))
        if path=='/api/intent/status':
            if HUB.new_device_mode:return self.send(200,HUB.intent.snapshot())
            return self.send(200,dict(**HUB.intent.snapshot(),control=HUB.intent_control.snapshot()))
        if path=='/api/calibration/status':return self.send(200,HUB.calibration.snapshot())
        if path=='/api/calibration/history':return self.send(200,dict(results=HUB.calibration.history()))
        if path=='/api/calibration/result':
            try:return self.send(200,HUB.calibration.result(parse_qs(urlparse(self.path).query).get('check_id',[''])[0]))
            except ValueError as error:return self.send(404,dict(error=str(error)))
        if path=='/api/refactor/status':return self.send(200,HUB.refactor.snapshot())
        if path=='/api/intent/test/catalog':
            try:return self.send(200,HUB.intent.clip_library.catalog())
            except ValueError as error:return self.send(503,dict(error=str(error)))
        if path=='/api/vr/panel':
            if not HUB.quest:return self.send(503,dict(error='Quest status service disabled'))
            return self.send(200,HUB.quest.dispatch(None,'GET','/v1/status',b''))
        if path=='/api/training/status':return self.send(200,HUB.ml.training() if HUB.new_device_mode else training_snapshot(ROOT))
        if path=='/api/models':
            return self.send(200,HUB.model_library.snapshot())
        if path=='/api/datasets/status':
            report=ROOT/'outputs/dataset_library_status.json'
            try:
                value=read_shared_json(report)
                if not isinstance(value,dict):raise ValueError('Dataset status must be an object')
                value['fresh']=0<=time.time()-value.get('updated_at',0)<5
            except FileNotFoundError:value=dict(state='not_started',fresh=False)
            except (OSError,ValueError,TypeError):value=dict(state='unavailable',fresh=False)
            return self.send(200,value)
        if path=='/api/preparation':
            state=HUB.snapshot() # File evidence is read only after snapshot releases acquisition lock.
            return self.send(200,HUB.ml.preparation(state) if HUB.new_device_mode else HUB.preparation.snapshot(state))
        if path=='/api/vr/status':
            state=HUB.snapshot()
            return self.send(200,dict(source={k:state['source'][k] for k in ['mode','connected','fresh','sfreq_measured','age_ms']},quality={k:state['quality'][k] for k in ['valid','good_channels']},server=dict(worker_connected=state['server']['worker_connected'],device=(state['server']['last_health'] or {}).get('device')),vr=state['vr']))
        if path=='/api/vr/validation':
            report=ROOT/'outputs/execution_validation.json'
            return self.send(200,json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'status':'not_run'})
        if path=='/api/sessions':return self.send(200,list_sessions(ROOT/'outputs/sessions'))
        if path=='/api/acquisition/status':return self.send(200,HUB.acquisition.snapshot())
        if path=='/api/acquisition/sync':return self.send(200,HUB.acquisition.sync())
        if path=='/api/quest/status':return self.send(200,HUB.quest.snapshot() if HUB.quest else dict(enabled=False,mirror_fresh=False))
        if path=='/api/quest/frame.jpg':
            frame=HUB.quest.frame() if HUB.quest else None
            return self.send(200,frame['jpeg'],'image/jpeg') if frame else self.send(503,dict(error='No fresh Quest frame'))
        if path=='/api/config':return self.send(200,{k:v for k,v in HUB.config.items() if k not in ['ssh_hostkey_sha256']})
        names={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/lab':'lab.html','/lab.js':'lab.js','/lab.css':'lab.css','/ui-state.js':'ui-state.js','/workspace.css':'workspace.css','/quest.js':'quest.js','/vr-preview':'vr-preview.html'}
        names.update({'/downloads':'downloads.html','/downloads.js':'downloads.js','/downloads.css':'downloads.css','/capture.js':'capture.js','/capture-core.js':'capture-core.js','/capture.css':'capture.css'})
        names.update({'/training':'training.html','/training.js':'training.js','/training-detail.js':'training-detail.js','/training-detail.css':'training-detail.css','/vr-training-replay.js':'vr-training-replay.js'})
        names.update({'/refactor-status.js':'refactor-status.js','/calibration.js':'calibration.js','/intent.js':'intent.js','/intent.css':'intent.css'})
        names.update({'/training-input.js':'training-input.js','/training-input-core.js':'training-input-core.js','/training-input.css':'training-input.css'})
        names.update({'/model-library.js':'model-library.js','/model-library.css':'model-library.css'})
        names.update({'/gnautilus-ml.js':'gnautilus-ml.js'})
        names.update({'/gnautilus':'index.html','/gnautilus.js':'gnautilus.js','/gnautilus.css':'gnautilus.css'})
        if path not in names:return self.send(404,{'error':'not found'})
        file=ROOT/'hub/web'/names[path];body=file.read_bytes()
        if file.suffix=='.html':body=annotate_html(body,HUB.transition['legacy_archived'])
        self.send(200,body,mimetypes.guess_type(file)[0]+'; charset=utf-8')
    def do_POST(self):
        if not self.allowed() or self.headers.get('X-EEGHub')!='1':return self.send(403,{'error':'local UI request required'})
        n=int(self.headers.get('Content-Length',0))
        if n>8192:return self.send(413,{'error':'request too large'})
        try:
            data=json.loads(self.rfile.read(n) or b'{}')
            if HUB.new_device_mode and (self.path.startswith('/api/ml/') or self.path.startswith('/api/training/jobs') or self.path=='/api/models/import'):
                return self.send(200,HUB.ml.api_post(self.path,data))
            if retired_action(self.path,data,HUB.transition['legacy_archived'],HUB.new_device_mode):
                return self.send(409,{'error':TRANSITION_MESSAGE,'code':'legacy_stack_archived'})
            if self.path=='/api/gnautilus/pair':
                if not HUB.new_device_mode:raise ValueError('g.Nautilus入口未启用')
                from .gnautilus import provision
                enrollment=provision(ROOT,data['host'],port=HUB.gnautilus_runtime.service.port)
                HUB.gnautilus_runtime.service.start() if not HUB.gnautilus_runtime.service.status['enabled'] else None
                return self.send(200,{'ok':True,'pairing_file':str(ROOT/'hub/runtime/gnautilus/enrollments'/(enrollment['agent_id']+'.json')),'port':enrollment['port']})
            if self.path=='/api/gnautilus/pause':
                HUB.gnautilus_runtime.paused=True;HUB.gnautilus_runtime.source_lost('operator_stop');return self.send(200,{'ok':True})
            if self.path=='/api/gnautilus/emergency':
                HUB.vr.stop_test();HUB.intent.stop();HUB.acquisition.stop(reason='emergency');HUB.stop_recording();return self.send(200,{'ok':True})
            if self.path=='/api/gnautilus/resume':
                HUB.gnautilus_runtime.paused=False;return self.send(200,{'ok':True})
            if self.path=='/api/models/refresh':
                return self.send(200,HUB.model_library.request_refresh())
            if self.path=='/api/models/activate':return self.send(200,HUB.model_library.activate(data))
            if getattr(HUB,'model_library',None) and HUB.model_library.switching() and self.path in ['/api/intent/start','/api/intent/control','/api/intent/test','/api/acquisition/start','/api/calibration/start','/api/record/start']:
                raise ValueError('模型正在切换，请等待加载结果')
            if self.path=='/api/calibration/start':return self.send(200,HUB.calibration.start(mode=data.get('mode','directions'),repetitions=data.get('repetitions',1)))
            if self.path=='/api/calibration/present':return self.send(200,HUB.calibration.present(data))
            if self.path=='/api/calibration/cancel':return self.send(200,HUB.calibration.cancel())
            if self.path=='/api/calibration/new-placement':return self.send(200,HUB.calibration.invalidate(new_placement=True))
            if self.path=='/api/quest/reload':
                if not HUB.quest:raise ValueError('Quest LAN is not enabled')
                records=json.loads((ROOT/'hub/runtime/quest_devices.json').read_text(encoding='utf-8'))['devices']
                if len(records)>4:raise ValueError('Paired device limit reached')
                HUB.quest.reload_enrollments(records)
                return self.send(200,{'ok':True})
            if self.path=='/api/acquisition/start':
                HUB.vr.stop_test();return self.send(200,HUB.acquisition.start(data))
            elif self.path=='/api/acquisition/event':return self.send(200,HUB.acquisition.event(data))
            elif self.path=='/api/acquisition/stop':return self.send(200,HUB.acquisition.stop_client(data))
            elif self.path=='/api/intent/replay':HUB.intent.start_replay(data.get('session'),data.get('head','heldout'))
            elif self.path=='/api/intent/live':HUB.intent.stop_replay()
            elif self.path=='/api/intent/start':return self.send(200,HUB.intent.start(data.get('purpose','normal')))
            elif self.path=='/api/intent/test':return self.send(200,HUB.intent.test_command(data))
            elif self.path=='/api/intent/stop':return self.send(200,HUB.intent.stop())
            elif self.path=='/api/intent/control':
                with HUB.lock:
                    if getattr(HUB,'model_library',None) and HUB.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
                    if data.get('enabled') and not HUB.intent.snapshot()['inference_enabled']:raise ValueError('请先启动实时推理，再允许模型动作')
                    return self.send(200,HUB.intent_control.configure(data))
            elif self.path=='/api/source':HUB.set_source(data['mode'],data.get('session'))
            elif self.path=='/api/server/connect':
                password=data.get('password','')
                if not password and not HUB.remote.password:raise ValueError('请输入SSH密码；仅保存在当前进程内存')
                if HUB.new_device_mode:HUB.intent.invalidate('private_compute_manual_reconnect');HUB.vr.stop_test()
                threading.Thread(target=HUB.remote.connect,args=(password or HUB.remote.password,),daemon=True).start()
            elif self.path=='/api/server/disconnect':
                HUB.vr.stop_test()
                if HUB.new_device_mode:HUB.intent.invalidate('private_compute_disconnected')
                HUB.remote.disconnect()
            elif self.path=='/api/vr/test':HUB.vr.set_test(data)
            elif self.path=='/api/vr/stop':HUB.intent.stop()
            elif self.path=='/api/vr/receiver/start':HUB.start_receiver()
            elif self.path=='/api/record/start':HUB.start_recording()
            elif self.path=='/api/record/stop':
                if HUB.calibration.snapshot()['active']:HUB.calibration.cancel('用户结束记录，模型校验已保存部分结果')
                HUB.acquisition.stop(reason='operator_stop');HUB.stop_recording()
            elif self.path=='/api/shutdown':
                self.send(200,{'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            elif self.path=='/api/marker':
                code=str(data.get('code','marker'))[:80];HUB.event('人工标记：'+code,'info')
                with HUB.record_lock:
                    if HUB.recorder:HUB.recorder.write(json.dumps({'kind':'marker','code':code,'monotonic':time.monotonic(),'sample_index':HUB.sample_index})+'\n')
            else:return self.send(404,{'error':'not found'})
            self.send(200,{'ok':True})
        except CalibrationRequired as e:self.send(409,{'error':str(e),'code':e.code})
        except (KeyError,ValueError,TypeError) as e:self.send(400,{'error':str(e)})

def main():
    global HUB
    ap=argparse.ArgumentParser();ap.add_argument('--connect-server',action='store_true');ap.add_argument('--open',action='store_true');args=ap.parse_args()
    config=json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8'));HUB=Hub(config);HUB.start()
    server=ThreadingHTTPServer((config['web_host'],config['web_port']),Handler);server.daemon_threads=True
    (RUNTIME/'hub.pid').write_text(str(os.getpid()));print(f"EEGMovement Hub http://127.0.0.1:{config['web_port']}",flush=True)
    if args.connect_server:
        import getpass
        password=getpass.getpass('SSH password (memory only): ');threading.Thread(target=HUB.remote.connect,args=(password,),daemon=True).start();password=None
    if args.open:webbrowser.open(f"http://127.0.0.1:{config['web_port']}")
    try:server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:pass
    finally:
        if HUB.public_relay:HUB.public_relay.close()
        if HUB.new_device_mode:HUB.gnautilus_runtime.close()
        if HUB.quest:HUB.quest.close()
        HUB.calibration.cancel('中枢关闭');HUB.acquisition.stop(reason='shutdown');HUB.stop_event.set();HUB.native_quality.close();HUB.notch_native_quality.close();HUB.stop_source_socket();HUB.stop_recording();HUB.remote.disconnect(quiet=True);HUB.vr.close();server.server_close()

if __name__=='__main__':main()
