"""New-device buffers, observations and sessions. No legacy filter/model invocation."""
import collections
import datetime
import json
import secrets
import threading
import time
import numpy as np
from gnautilus_acquisition.contract import validate_descriptor
from gnautilus_acquisition.storage import StreamJournal
from .gnautilus import IngestService
from .lan_ws import LanHubServer,DEFAULT_LAN_WS_PORT
from gnautilus_acquisition.timing import ACTION_FRESHNESS_S,LAN_DELIVERY_GRACE_S


class SessionRecorder:
    def __init__(self,path,descriptor,hub):
        self.journal=StreamJournal(path,descriptor);self.hub=hub;self.seq=0;self.samples=0
        self.write(json.dumps(dict(kind='session',schema='gnautilus.session.v1',descriptor=descriptor,unit='uV',
            source='gtec_live',sfreq=descriptor['sampling_rate'],channels=descriptor['channels'],formal_training_ready=False,
            role='unassigned',sample_index_semantics='markers use source sample index; blocks preserve source_sample_start/source_seq; local block rows start at zero',
            started_at=datetime.datetime.now().astimezone().isoformat(),motion_enabled=False))+'\n')
    def write(self,text):
        for line in text.splitlines():
            if not line.strip():continue
            event=json.loads(line);event.setdefault('stream_id',self.journal.descriptor['stream_id']);event.setdefault('source_epoch',self.hub.epoch)
            event.setdefault('sample_index_end',self.hub.sample_index);self.journal.event(event)
            runtime=getattr(self.hub,'gnautilus_runtime',None)
            if runtime and event.get('code') in ('manual_hold_start','manual_hold_end','manual_session_stop','guided_session_stop'):
                runtime.log_feedback('标签',f"已保存 {event['code']} · {event.get('label') or 'UNKNOWN'} · 样本位置 {event['sample_index_end']}")
        return len(text)
    def append_block(self,h,x):
        self.seq+=1;header=dict(h,source_seq=h['seq'],source_sample_start=h['sample_start'],source_epoch=self.hub.epoch,
                               seq=self.seq,sample_start=self.samples)
        self.journal.append(header,np.asarray(x,dtype='<f4').tobytes());self.samples+=len(x)
    def flush(self):pass # Every event/block is durable before return.
    def close(self):return self.journal.close()


class GnautilusRuntime:
    def __init__(self,hub,root,*,port=8767,lan_ws_port=DEFAULT_LAN_WS_PORT):
        self.hub,self.root=hub,root;self.descriptor=None;self.last_seq=None;self.last_counter=None;self.received_blocks=0;self.last_close=None
        self.last_accept_monotonic=0.0
        self.active_transport=None;self.gap_count=0;self.last_gap=None;self.input_suspended=False;self.recording_requested=False;self.recovery_count=0
        self.service=IngestService(root,self.accept,on_descriptor=self.describe,on_state=self.connection_state,on_capture_event=self.client_capture_event,get_hub_state=self.feedback,port=port)
        self.lan_ws=LanHubServer(self,port=lan_ws_port)
        self.feedback_boot_id=secrets.token_hex(8);self.feedback_logs=collections.deque(maxlen=20);self.feedback_log_seq=0
        self.paused=False;self.error=None;self.last_source_loss=None;self.stop_event=threading.Event();self.reset_on_resume=False
    def start(self):
        self.service.start();self.lan_ws.start();self.thread=threading.Thread(target=self._tick,daemon=True,name='g.Nautilus lifecycle');self.thread.start()
    def execution(self):
        peers=[p for p in self.hub.vr.snapshot().get('clients',[]) if p.get('online') and p.get('role')=='unity'] if hasattr(self.hub.vr,'snapshot') else []
        telemetry=peers[0].get('telemetry',{}) if len(peers)==1 else {}
        age=peers[0].get('telemetry_age_ms') if len(peers)==1 else None
        known=bool(telemetry and isinstance(age,(int,float)) and 0<=age<1000)
        return dict(state=telemetry.get('state','UNKNOWN') if known else 'UNKNOWN',mode=telemetry.get('mode','UNKNOWN') if known else 'UNKNOWN',actual_speed=telemetry.get('actual_speed') if known else None,
                    actual_yaw_rate=telemetry.get('actual_yaw_rate') if known else None,known=known)
    def log_feedback(self,stage,message,level='INFO'):
        with self.hub.lock:
            self.feedback_log_seq+=1
            self.feedback_logs.append(dict(id=self.feedback_log_seq,time_ns=time.time_ns(),stage=stage,level=level,message=str(message)[:240]))

    def feedback(self):
        with self.hub.lock:
            acq=self.hub.acquisition.snapshot() if hasattr(self.hub,'acquisition') else {}
            peers=self.hub.vr.snapshot().get('clients',[]) if hasattr(self.hub.vr,'snapshot') else []
            return dict(schema='gnautilus.hub_feedback.v1',recording_active=self.hub.recorder is not None,recording_name=self.hub.recording_name,
                recording_samples=self.hub.recording_samples,label=acq.get('label') if acq.get('active') else None,phase=acq.get('phase','idle'),
                events_saved=acq.get('events_saved',0),source_epoch=self.hub.epoch,sample_index_end=self.hub.sample_index,
                vr_online=sum(bool(p.get('online')) for p in peers),model_state=self.hub.intent.snapshot().get('inference_state','stopped') if hasattr(self.hub,'ml') else 'not_configured',motion_state=self.execution()['state'],
                contact_quality_known=False,source_fresh=self.fresh(),hub_utc_ns=time.time_ns(),
                log_boot_id=self.feedback_boot_id,logs=list(self.feedback_logs),
                source_age_ms=round((time.monotonic()-self.hub.last_packet)*1000,2) if self.hub.last_packet is not None else None,last_source_loss=self.last_source_loss,
                active_transport=self.active_transport,input_paused=self.input_suspended,gap_count=self.gap_count,last_gap=self.last_gap,lan_listener=self.lan_ws.snapshot())
    def _tick(self):
        while not self.stop_event.wait(.1):
            try:
                self.hub.acquisition.expire()
                self.check_input_health()
            except (ValueError,OSError) as e:self.error=f'{type(e).__name__}: {e}'
    def fresh(self):
        return bool(not self.paused and self.hub.last_packet is not None and 0<=time.monotonic()-self.hub.last_packet<.25)
    def check_input_health(self):
        # A late frame disables action through fresh()/receiver leases, but is
        # not a sample discontinuity. Do not reset model state or labels here.
        if self.last_accept_monotonic and time.monotonic()-self.last_accept_monotonic>=LAN_DELIVERY_GRACE_S:
            self.source_lost('eeg_delivery_interrupted')
    def describe(self,descriptor):
        d=validate_descriptor(descriptor)
        old=self.descriptor
        changed=old is not None and (old['stream_id'],old['epoch'],old['configuration_sha256'],old['channels'])!=(d['stream_id'],d['epoch'],d['configuration_sha256'],d['channels'])
        if changed:
            self.source_lost('source_changed')
        with self.hub.lock:
            if old is None or (old['stream_id'],old['epoch'],old['configuration_sha256'],old['channels'])!=(d['stream_id'],d['epoch'],d['configuration_sha256'],d['channels']):
                if hasattr(self.hub,'ml'):self.hub.intent.invalidate('source_configuration_changed',preserve_mode=True)
                self.last_seq=self.last_counter=None;self.hub.epoch+=1;self.hub.sample_index=0;self.hub.last_packet=None
                size=max(1,int(d['sampling_rate']*5))
                self.hub.raw=collections.deque(maxlen=size);self.hub.filtered=collections.deque(maxlen=size);self.hub.notched=collections.deque(maxlen=size)
            if old is None or old['stream_id']!=d['stream_id']:
                self.log_feedback('接收',f"已接收新数据流 · {d['source_kind']} · {sum(c['kind']=='EEG' for c in d['channels'])}路EEG · 配置{d['sampling_rate']}Hz")
            self.descriptor=d
    def accept(self,descriptor,header,x):
        if self.paused or header.get('mode')!='live':return
        now=time.monotonic();source_mark=header.get('hub_source_monotonic_ns')
        observed=source_mark/1e9 if type(source_mark) is int else now
        delivery_limit=LAN_DELIVERY_GRACE_S if header.get('ingest_transport')=='lan_ws' else ACTION_FRESHNESS_S
        if not 0<=now-observed<delivery_limit:return
        if not self.descriptor or (self.descriptor['stream_id'],self.descriptor['epoch'],self.descriptor['configuration_sha256'])!=(descriptor['stream_id'],descriptor['epoch'],descriptor['configuration_sha256']):self.describe(descriptor)
        h=self.hub;d=self.descriptor
        counter_columns=[i for i,c in enumerate(d['channels']) if c['kind']=='COUNTER']
        counter_gap=False
        if counter_columns:
            values=x[:,counter_columns[0]];values=values[np.isfinite(values)]
            counter_gap=bool(len(values)!=len(x) or np.any(np.diff(values)!=1) or len(values) and self.last_counter is not None and values[0]!=self.last_counter+1)
            if len(values):self.last_counter=float(values[-1])
        gap=self.last_seq is not None and (header['seq']!=self.last_seq+1 or header['sample_start']!=h.sample_index or counter_gap)
        if gap:
            self.gap_count+=1;self.last_gap=dict(previous_seq=self.last_seq,received_seq=header['seq'],expected_sample_start=h.sample_index,
                received_sample_start=header['sample_start'],counter_gap=counter_gap,time_ns=time.time_ns(),transport=header.get('ingest_transport'))
            self.source_lost('sample_gap')
        now=time.monotonic();eeg=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG']
        validation=[i for i,c in enumerate(d['channels']) if c['kind']=='VALIDATION']
        finite=np.all(np.isfinite(x[:,eeg]),axis=0)
        device_valid=bool(np.all(x[:,validation[0]]==1)) if validation else None
        channels=[dict(name=c['name'],id=c['id'],kind=c['kind'],unit=c['unit'],color='gray',quality_label='接触质量未测量',
                       training_eligible=False,contact_quality_known=False,finite=bool(np.all(np.isfinite(x[:,i])))) for i,c in enumerate(d['channels'])]
        with h.lock:
            if self.reset_on_resume:
                h.raw.clear();h.filtered.clear();h.notched.clear();self.reset_on_resume=False
            h.raw.extend(x.tolist());h.filtered.extend(x.tolist()) # Display identity path: no second filter.
            h.sample_index=header['sample_start']+len(x);h.last_packet=observed;h.source_mode='live';h.connected=True;h.source_error=None
            h.quality=dict(valid=False,state='observed' if np.all(finite) and device_valid is not False else 'device_invalid',label='接触质量未验证；设备有效性单独记录',
                channels=channels,good_channels=0,training_mask=[False]*len(channels),policy='gnautilus_observation_v1',contact_quality_known=False,
                device_valid=device_valid,finite_eeg=bool(np.all(finite)),counter_continuity=('gap' if counter_gap else 'observed') if counter_columns else 'unknown',
                checked_monotonic=now,reasons=['electrode_contact_not_measured'],formal_training_ready=False)
            self.last_seq=header['seq'];self.received_blocks+=1;self.last_accept_monotonic=now
            self.active_transport=header.get('ingest_transport','tls_ingest')
            if self.recording_requested and h.recorder is not None:
                recorded=h.recorder.journal.descriptor
                if (recorded['stream_id'],recorded['epoch'],recorded['configuration_sha256'],recorded['channels'])!=(d['stream_id'],d['epoch'],d['configuration_sha256'],d['channels']):
                    self.stop_recording(preserve_mode=True)
            if self.recording_requested and h.recorder is None:self.start_recording(allow_recent=True)
            with h.record_lock:
                if h.recorder:
                    h.recorder.append_block(header,x);h.recording_samples+=len(x)
                    h.recorder.write(json.dumps(dict(kind='quality',stream_id=d['stream_id'],source_epoch=h.epoch,
                        sample_index_start=header['sample_start'],sample_index_end=h.sample_index,device_valid=device_valid,
                        finite_eeg=bool(np.all(finite)),counter_gap=counter_gap,contact_quality_known=False,training_mask=[False]*len(channels)))+'\n')
            if self.input_suspended:
                self.input_suspended=False
                self.recovery_count+=1
                h.intent_control.configure({'enabled':False})
                if hasattr(h,'acquisition') and hasattr(h.acquisition,'resume_input'):h.acquisition.resume_input()
    def client_capture_event(self,agent,data):
        if not isinstance(data,dict) or not hasattr(self.hub,'acquisition'):
            return dict(ok=False,error='Acquisition service unavailable')
        if self.service.selected_agent not in (None,agent):
            return dict(ok=False,error='Agent is not selected source')
        op=data.get('op')
        acq=self.hub.acquisition
        try:
            if op=='start':
                current=acq.snapshot()
                if current.get('active') and current.get('session_id')!=data.get('session_id'):
                    if current.get('input_surface')=='acquisition_client':acq.stop(reason='operator_stop')
                    else:return dict(ok=False,error='中枢已有其他采集会话正在进行')
                if not acq.snapshot().get('active'):
                    res=acq.start_manual(dict(
                        build_id=data.get('build_id','gnautilus-qt-selfpaced-v1'),
                        paradigm='self_paced_motor_imagery',
                        presentation_protocol=data.get('presentation_protocol','self_paced_axes_v2'),
                        input_surface='acquisition_client',
                        task_guidance_version='gaze_navigation_v2',
                        allow_combos=bool(data.get('allow_combos')),
                        session_id=data.get('session_id'),
                        client_token=data.get('client_token'),
                    ))
                    return dict(ok=True,op='start',session_id=res['session_id'],recording=res.get('recording'))
                return dict(ok=True,op='start',session_id=current['session_id'],recording=current.get('recording'))
            elif op=='event':
                current=acq.snapshot()
                if not current.get('active'):
                    return dict(ok=False,op='event',session_id=data.get('session_id'),session_closed=True,
                                error='中枢标签会话已关闭，请结束当前打标后重新开始；心跳不能自动重建会话')
                res=acq.manual_event(data.get('event') or {})
                return dict(ok=True,op='event',session_id=res['session_id'],ack_seq=res['ack_seq'],sample_index_end=res['sample_index_end'])
            elif op=='stop':
                current=acq.snapshot()
                if current.get('active') and current.get('session_id')==data.get('session_id'):
                    res=acq.stop(session_id=data.get('session_id'),reason=data.get('reason','completed'))
                    return dict(ok=True,op='stop',session_id=data.get('session_id'),phase=res.get('phase'))
                return dict(ok=True,op='stop',session_id=data.get('session_id'),phase='completed')
            return dict(ok=False,error='Unsupported capture operation')
        except Exception as error:
            return dict(ok=False,op=op,session_id=data.get('session_id'),error=str(error)[:240])
    def connection_state(self,state):
        if state.get('state')=='disconnected' and self.active_transport in (None,'tls_ingest'):self.source_lost('transport_error')
    def source_lost(self,reason):
        h=self.hub
        with h.lock:
            terminal=reason in ('shutdown','emergency','operator_stop','operator_pause')
            first=not self.input_suspended
            if hasattr(h,'acquisition') and h.acquisition.snapshot().get('active'):
                if terminal:h.acquisition.stop(reason=reason)
                elif hasattr(h.acquisition,'pause_input'):h.acquisition.pause_input(reason)
            elif terminal and h.recorder:self.stop_recording()
            if hasattr(h,'ml') and (h.intent.inference_enabled or h.intent.latest):
                h.intent.invalidate(reason,preserve_mode=not terminal)
            h.intent_control.configure({'enabled':False})
            if first or terminal:
                age=round((time.monotonic()-h.last_packet)*1000,2) if h.last_packet is not None else None
                self.last_source_loss=dict(reason=reason,source_age_ms=age,sample_index_end=h.sample_index,time_ns=time.time_ns())
                if not terminal:self.log_feedback('连接',f'输入暂停 · {reason} · 保留模式，等待自动恢复','WARN')
                if hasattr(h,'vr'):h.vr.stop_test()
                # Keep the last waveform visible during reconnect. It is never
                # fresh evidence; clear it when replacement samples arrive.
                self.reset_on_resume=True
                self.last_seq=self.last_counter=None
                if self.descriptor:h.epoch+=1
            self.input_suspended=True;h.last_packet=None;h.connected=False
    def start_recording(self,*,allow_recent=False):
        h=self.hub
        with h.lock,h.record_lock:
            fresh_ok=self.fresh() or (allow_recent and not self.paused and h.last_packet is not None and 0<=time.monotonic()-h.last_packet<1.5)
            if not self.descriptor or self.descriptor['source_kind']!='gtec_live' or not fresh_ok:raise ValueError('需要新鲜的真实g.Nautilus数据；模拟/回放不能启动个人采集')
            self.recording_requested=True
            if h.recorder:return
            if hasattr(h,'ml'):h.intent_control.configure({'enabled':False});h.vr.stop_test()
            name=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4)
            path=self.root/'data/gnautilus'/datetime.date.today().isoformat()/'sessions'/name
            h.recorder=SessionRecorder(path,self.descriptor,h);h.recording_name=name;h.recording_samples=0
        self.log_feedback('记录','已创建原始记录，数据与标签分开持久化')
        h.event('开始g.Nautilus原始记录：'+name,'info')
    def stop_recording(self,*,preserve_mode=False):
        if not preserve_mode:self.recording_requested=False
        h=self.hub
        with h.lock,h.record_lock:
            if h.recorder:
                recorder=h.recorder;h.recorder=None
                try:
                    self.last_close=recorder.close()
                    from gnautilus_acquisition.catalog import build_catalog
                    build_catalog(self.root/'data/gnautilus')
                    self.log_feedback('记录',f'记录已关闭并生成校验清单 · {h.recording_samples}行')
                    h.event('g.Nautilus记录已关闭并生成校验清单','good')
                except OSError as error:
                    self.error='记录关闭失败，保留已提交文件等待恢复：'+str(error);h.event(self.error,'error')
    def readiness(self):
        if hasattr(self.hub,'ml'):return self.hub.intent.readiness()
        return dict(acquisition_active=bool(self.hub.recorder),source_fresh=self.fresh(),source_valid=False,server_fresh=False,
                    epoch=self.hub.epoch,reason='new_model_not_configured',input_origin=None,decision_id=0,model_fresh=False,model_decision={})
    def model_input(self,*,require_fresh=True):
        """Future model adapter contract; observing data grants no motion eligibility."""
        with self.hub.lock:
            d=self.descriptor
            available=self.fresh() if require_fresh else bool(not self.paused and not self.input_suspended and self.hub.last_packet is not None and 0<=time.monotonic()-self.hub.last_packet<LAN_DELIVERY_GRACE_S)
            if not d or not available or d['source_kind']!='gtec_live':raise ValueError('Fresh verified-origin input required')
            return dict(schema='gnautilus.model_input.v1',stream_id=d['stream_id'],source_epoch=self.hub.epoch,
                sample_index_end=self.hub.sample_index,sampling_rate=d['sampling_rate'],channels=d['channels'],
                configuration_sha256=d['configuration_sha256'],samples=np.asarray(self.hub.raw,dtype='<f4').copy(),descriptor=dict(d),input_origin_monotonic=self.hub.last_packet,
                contact_quality_known=False,execution_eligible=False,model_identity=None)
    def snapshot(self):
        h=self.hub;d=self.descriptor;ingest=self.service.snapshot();fresh=self.fresh()
        with h.lock:
            channels=d['channels'] if d else [];fs=d['sampling_rate'] if d else None
            quality=dict(h.quality)
            if not fresh or time.monotonic()-quality.get('checked_monotonic',-1e9)>=1:
                quality.update(valid=False,training_mask=[False]*len(quality.get('channels',channels)),good_channels=0,
                    channels=[dict(c,color='gray',training_eligible=False,quality_label='质量状态未知或过期') for c in quality.get('channels',[])])
            connected=bool(h.connected and not self.input_suspended)
            return dict(version='1.0.0',project='EEGMovement',source=dict(device='g.Nautilus',mode='paused' if self.paused else 'live' if connected else 'waiting',
                delivery_state='fresh' if fresh else 'delayed' if connected else 'reconnecting',
                connected=connected,fresh=fresh,age_ms=round((time.monotonic()-h.last_packet)*1000,2) if h.last_packet else None,
                nominal_sfreq=fs,configured_sfreq=fs,sfreq_measured=None,sampling_rate_source='device_configuration',samples_total=h.sample_index,source_epoch=h.epoch,
                stream_id=d['stream_id'] if d else None,source_kind=d['source_kind'] if d else None,sequence=self.last_seq,
                continuous_samples=len(h.raw),endpoint='LAN WebSocket :'+str(self.lan_ws.port) if self.active_transport=='lan_ws' else 'TLS ingest :'+str(self.service.port),error=self.error or ingest.get('error'),stats=dict(gap_count=self.gap_count,last_gap=self.last_gap,recovery_count=self.recovery_count,input_paused=self.input_suspended,active_transport=self.active_transport)),
                quality=quality,processing=dict(causal=True,bandpass='原厂配置',notch='原厂配置',window_ms=None,hop_ms=None,
                    channel_names=[c['name'] for c in channels],montage_verified=d.get('layout_verified',False) if d else False,filter_applied_by_hub=False),
                server=h.remote.snapshot() if hasattr(h,'ml') else dict(ssh_connected=False,worker_connected=False,last_health=None,health_age_ms=None),result=None,result_age_ms=None,result_fresh=False,
                performance=dict(errors=0,return_count=0),control=dict(enabled=False,**self.execution(),speed=self.execution()['actual_speed'],yaw_rate=self.execution()['actual_yaw_rate'],reasons=['执行状态以接收端新鲜遥测为准']),
                vr=h.vr.snapshot() if hasattr(h.vr,'snapshot') else {},recording=dict(active=h.recorder is not None,name=h.recording_name,samples=h.recording_samples),
                waveforms=dict(raw=list(h.raw)[-int((fs or 1)*3):],filtered=list(h.raw)[-int((fs or 1)*3):],notch50=[],display_sfreq=fs,seconds=3),
                gnautilus=dict(ingest,last_session=self.last_close,descriptor=d,storage_root=str(self.root/'data/gnautilus'),error=self.error or ingest.get('error'),lan_listener=self.lan_ws.snapshot()),events=list(getattr(h,'events',[]))[:20])
    def close(self):
        self.stop_event.set();self.source_lost('shutdown');self.lan_ws.close();self.service.close()
        if hasattr(self,'thread'):self.thread.join(1)
