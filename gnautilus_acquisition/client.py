"""Outbound pinned TLS transport. This module has no vendor SDK side effects."""
import hashlib
import hmac
import secrets
import socket
import ssl
import collections
import datetime
import queue
import threading
import time
from pathlib import Path
from .contract import block_frame,validate_descriptor
from .storage import StreamJournal
from .display import PreviewBuffer,EventLog
from .feedback import HubFeedbackLog
from .contract import json_bytes,send_frame,recv_frame


SDK_OPEN_TIMEOUT_SECONDS=15.
SDK_SAMPLE_TIMEOUT_SECONDS=5.

CAPTURE_LABELS=['REST','FORWARD','BACKWARD','LEFT','RIGHT','FORWARD_LEFT','FORWARD_RIGHT','BACKWARD_LEFT','BACKWARD_RIGHT']
CAPTURE_AXES={'REST':[0,0],'FORWARD':[1,0],'BACKWARD':[2,0],'LEFT':[0,1],'RIGHT':[0,2],'FORWARD_LEFT':[1,1],'FORWARD_RIGHT':[1,2],'BACKWARD_LEFT':[2,1],'BACKWARD_RIGHT':[2,2]}
CAPTURE_NAMES={'FORWARD':'向前','BACKWARD':'向后','LEFT':'向左','RIGHT':'向右','REST':'静止','FORWARD_LEFT':'前进左转','FORWARD_RIGHT':'前进右转','BACKWARD_LEFT':'后退左转','BACKWARD_RIGHT':'后退右转'}


def capture_task_spec(label,mode='navigation_mi_gaze'):
    eyes={'FORWARD':'UP','BACKWARD':'DOWN','LEFT':'LEFT','RIGHT':'RIGHT','REST':'CENTER',
          'FORWARD_LEFT':'UP_LEFT','FORWARD_RIGHT':'UP_RIGHT','BACKWARD_LEFT':'DOWN_LEFT','BACKWARD_RIGHT':'DOWN_RIGHT'}
    eye_cn={'UP':'向上看','DOWN':'向下看','LEFT':'向左看','RIGHT':'向右看','CENTER':'自然中央注视',
            'UP_LEFT':'向左上看','UP_RIGHT':'向右上看','DOWN_LEFT':'向左下看','DOWN_RIGHT':'向右下看'}
    motors={'FORWARD':('IMAGINE_WALK_FORWARD','向前行走'),'BACKWARD':('IMAGINE_WALK_BACKWARD','向后行走'),
            'LEFT':('IMAGINE_TURN_LEFT','向左转向'),'RIGHT':('IMAGINE_TURN_RIGHT','向右转向'),'REST':('NO_NAVIGATION_IMAGERY','停止导航想象'),
            'FORWARD_LEFT':('IMAGINE_WALK_FORWARD_LEFT','向前并向左转向'),'FORWARD_RIGHT':('IMAGINE_WALK_FORWARD_RIGHT','向前并向右转向'),
            'BACKWARD_LEFT':('IMAGINE_WALK_BACKWARD_LEFT','向后并向左转向'),'BACKWARD_RIGHT':('IMAGINE_WALK_BACKWARD_RIGHT','向后并向右转向')}
    eye='CENTER' if mode=='navigation_mi_only' else eyes.get(label,'UNKNOWN')
    motor,action=motors.get(label,('UNKNOWN','未知动作'))
    instruction=('自然中央注视，停止导航运动想象，不发出导航意图。' if label=='REST' else
        '以第一人称想象自己'+action+'，体会身体运动感觉，不只是观看移动画面；'+
        (eye_cn.get(eye,'自然中央注视')+'，眼动仅作辅助。' if mode=='navigation_mi_gaze' else '自然中央注视，不用定向眼动辅助。'))
    instruction+='不指定某只手或腿的想象映射；无需真实迈步或转身，可正常操作手柄，手柄和头动单独记录。'
    return dict(label=label,experiment_mode=mode,gaze_instruction=eye,limb_instruction='NOT_PRESCRIBED',motor_instruction=motor,
                primary_source='motor_imagery',auxiliary_source='gaze' if mode=='navigation_mi_gaze' else 'none',
                instruction=instruction,task_binding_version='navigation_mi_gaze_v3')


def source_age_ms(header,rate,now_ns=None):
    """Host read age for existing freshness gates; not a hardware timestamp."""
    now_ns=time.monotonic_ns() if now_ns is None else now_ns
    read_ns=header.get('read_completed_monotonic_ns',header['read_monotonic_ns'])
    host_age=max(0.,(now_ns-read_ns)/1e6)
    return host_age


def connect_authenticated(enrollment,timeout=3,on_stage=None):
    on_stage=on_stage or (lambda *args:None)
    required=['host','port','agent_id','secret','certificate_sha256']
    if any(k not in enrollment for k in required):raise ValueError('Incomplete pairing file')
    if len(bytes.fromhex(enrollment['secret']))!=32 or len(enrollment['certificate_sha256'])!=64:raise ValueError('Invalid pairing credential')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.check_hostname=False;context.verify_mode=ssl.CERT_NONE # Exact DER pin is mandatory below, before credentials are sent.
    raw=s=None;phase='TCP';endpoint=f"{enrollment['host']}:{enrollment['port']}"
    try:
        on_stage('TCP',f'正在连接中枢 {endpoint}，超时 {timeout:g}秒')
        raw=socket.create_connection((enrollment['host'],enrollment['port']),timeout=timeout)
        on_stage('TCP',f'中枢 {endpoint} 的目标端口已连通')
        raw.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        phase='TLS'
        s=context.wrap_socket(raw,server_hostname=enrollment['host'])
        if not hmac.compare_digest(hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest(),enrollment['certificate_sha256']):
            s.close();raise ValueError(f'[TLS] {endpoint} 证书指纹与配对文件不匹配：可能连错中枢或证书已更换，请核对并重新生成配对文件。')
        on_stage('TLS','证书指纹匹配，TLS通道已建立')
        phase='身份';on_stage('身份','正在核对中枢挑战及采集端身份')
        challenge,body=recv_frame(s)
        if body or challenge.get('kind')!='challenge' or challenge.get('schema')!='gnautilus.ingest.v1' or len(str(challenge.get('challenge','')))!=64:raise ValueError('Invalid hub challenge')
        boot=secrets.token_hex(16);agent=enrollment['agent_id']
        mac=hmac.new(bytes.fromhex(enrollment['secret']),json_bytes([challenge['challenge'],agent,boot]),hashlib.sha256).hexdigest()
        send_frame(s,dict(kind='auth',agent_id=agent,boot_id=boot,mac=mac));reply,body=recv_frame(s)
        if body or reply.get('kind')!='auth_ok':raise ValueError('Pairing not accepted')
        on_stage('身份','挑战签名已通过，中枢已确认采集端身份')
        return s,reply
    except BaseException as error:
        try:
            if s or raw:(s or raw).close()
        except OSError:pass
        if isinstance(error,TimeoutError):raise ValueError(f'[{phase}] {endpoint} 连接超时：请核对中枢已启动、配对IP、防火墙及校园网／路由可达性。') from error
        if isinstance(error,ConnectionRefusedError):raise ValueError(f'[{phase}] {endpoint} 连接被拒绝：请启动中枢并生成配对文件，确认TCP端口正在监听。') from error
        if isinstance(error,ssl.SSLError):raise ValueError(f'[TLS] {endpoint} 握手失败：请检查目的端口和配对证书，不能关闭证书校验继续连接。') from error
        if isinstance(error,EOFError):raise ValueError(f'[{phase}] {endpoint} 中枢关闭连接：配对可能已失效，或同一采集端身份正在使用，请核对后重试。') from error
        if isinstance(error,OSError):raise ValueError(f'[{phase}] {endpoint} 网络不可用（系统错误 {getattr(error,"winerror",None) or error.errno}）：请核对地址、系统防火墙及路由。') from error
        raise


def probe_hub(enrollment,on_event=None,*,status_link=None):
    if status_link is not None:return status_link.probe(on_event)
    if enrollment.get('mode')=='relay':
        from .relay_client import probe_relay
        return probe_relay(enrollment,on_event)
    if enrollment.get('mode')=='lan_ws':
        from .lan_client import probe_lan
        return probe_lan(enrollment,on_event)
    callback=on_event or (lambda *args:None);began=time.monotonic()
    s,_=connect_authenticated(enrollment,on_stage=lambda stage,message:callback('校验','INFO',stage+' · '+message))
    try:
        callback('校验','INFO','状态 · 正在请求中枢记录、标签和VR状态')
        send_frame(s,dict(kind='status',sdk_state='diagnostic',disk_state='not_started',queue_blocks=0));reply,_=recv_frame(s)
        if reply.get('kind')!='status_ok':raise ValueError('中枢状态响应不匹配')
        callback('校验','INFO','中枢状态回传通过；此测试没有访问设备或启动记录')
        return dict(connected=True,elapsed_ms=round((time.monotonic()-began)*1000,1),hub=reply.get('hub_state',{}))
    except (OSError,EOFError) as error:raise ValueError('[状态回传] 已完成TCP／TLS／身份，但中枢状态响应中断或超时，请检查中枢日志。') from error
    finally:s.close()


class Replicator:
    """Read the committed local journal; only fresh new blocks use the live lane."""
    def __new__(cls,*args,**kwargs):
        if cls is Replicator and len(args)>1 and isinstance(args[1],dict):
            if args[1].get('mode')=='relay':
                from .relay_client import RelayReplicator
                return object.__new__(RelayReplicator)
            if args[1].get('mode')=='lan_ws':
                from .lan_client import LanWsReplicator
                return object.__new__(LanWsReplicator)
        return object.__new__(cls)
    def __init__(self,journal,enrollment,backfill_only=False,on_event=None,health_provider=None,on_capture_ack=None):
        self.journal,self.enrollment,self.backfill_only=journal,enrollment,backfill_only
        self.stop_event=threading.Event();self.lock=threading.RLock();self.socket=None
        self.state=dict(connected=False,durable_seq=0,backlog=0,error=None,sent_blocks=0,live_accepted=0)
        self.last_live_seq=0;self.acked=set()
        self.feedback_log=HubFeedbackLog()
        self.health_provider=health_provider
        self.on_capture_ack=on_capture_ack or (lambda *args:None)
        self.capture_events=queue.Queue(maxsize=256)
        self.on_event=on_event or (lambda *args:None);self.last_hub_log=0;self.last_hub_phase=None;self.last_components={}
    def send_capture_event(self,payload):
        try:
            self.capture_events.put_nowait(dict(payload))
            if hasattr(self.journal,'new_data'):self.journal.new_data.set()
            return True
        except queue.Full:
            return False
    def start(self):
        self.thread=threading.Thread(target=self._run,daemon=True,name='g.Nautilus outbound TLS');self.thread.start();return self
    def snapshot(self):
        with self.lock:
            s=dict(self.state)
            with self.journal.lock:s['backlog']=sum(n>s['durable_seq'] and n not in self.acked for n in self.journal.records)
            return s
    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.socket,_=connect_authenticated(self.enrollment);s=self.socket;s.settimeout(3)
                self.on_event('中枢','INFO','TLS证书与采集端身份已通过，正在核对流描述')
                send_frame(s,dict(kind='descriptor',descriptor=self.journal.descriptor));r,_=recv_frame(s)
                if r.get('kind')!='descriptor_ok':raise ValueError('Descriptor not accepted')
                with self.lock:self.state.update(connected=True,durable_seq=r['durable_seq'],error=None)
                self._feedback(r)
                self.acked.clear();self.last_live_seq=r['durable_seq'];ticket=r['ticket'];heartbeat=time.monotonic();last_health=0
                while not self.stop_event.is_set():
                    while not self.capture_events.empty():
                        try:ev=self.capture_events.get_nowait()
                        except queue.Empty:break
                        send_frame(s,dict(kind='capture_event',event=ev));cr,_=recv_frame(s)
                        if cr.get('kind')=='capture_ack':
                            self._feedback(cr);ticket=cr['ticket'];heartbeat=time.monotonic()
                    if time.monotonic()-last_health>=1:
                        r=self._send_health(s);ticket=r['ticket'];heartbeat=last_health=time.monotonic()
                    with self.journal.lock:
                        maximum=max(self.journal.records,default=0)
                        seq=None;mode='backfill'
                        if maximum>self.last_live_seq and not self.backfill_only:
                            h,body=self.journal.read(maximum)
                            if source_age_ms(h,self.journal.descriptor['sampling_rate'])<150:
                                seq=maximum;mode='live';self.last_live_seq=seq
                        if seq is None:
                            candidate=self.state['durable_seq']+1
                            while candidate in self.acked:candidate+=1
                            if candidate in self.journal.records:seq=candidate;h,body=self.journal.read(seq)
                    if seq is None:
                        if time.monotonic()-heartbeat>=.08:
                            r=self._send_health(s);ticket=r['ticket'];heartbeat=last_health=time.monotonic()
                        self.stop_event.wait(.01);continue
                    h=dict(h,mode=mode,ticket=ticket,source_age_ms=source_age_ms(h,self.journal.descriptor['sampling_rate']))
                    send_frame(s,h,body);r,_=recv_frame(s)
                    if r.get('kind')!='ack' or r.get('ack_seq')!=seq:raise ValueError('Mismatched block acknowledgement')
                    self.acked.add(seq)
                    self._feedback(r)
                    with self.lock:
                        self.state.update(durable_seq=r['durable_seq']);self.state['sent_blocks']+=1;self.state['live_accepted']+=bool(r.get('live_accepted'))
                    self.acked={n for n in self.acked if n>r['durable_seq']};ticket=r['ticket'];heartbeat=time.monotonic()
                self._send_health(s) # Final client health precedes orderly TLS disconnect.
            except (OSError,ValueError,EOFError,KeyError) as error:
                with self.lock:self.state.update(connected=False,error=f'{type(error).__name__}: {error}'[:240])
                self.on_event('中枢','WARN','连接中断／未确认：'+str(error)[:180]+'；本地已提交记录保留')
            finally:
                if self.socket:
                    try:self.socket.close()
                    except OSError:pass
                    self.socket=None
            self.stop_event.wait(.5)
        with self.lock:self.state['connected']=False
    def _send_health(self,s):
        health=self.health_provider() if self.health_provider else dict(sdk_state='backfill' if self.backfill_only else 'closed' if self.journal.closed else 'receiving',disk_state='durable',queue_blocks=0,error=None)
        send_frame(s,dict(health,kind='status',stream_id=self.journal.descriptor['stream_id']));reply,_=recv_frame(s)
        if reply.get('kind')!='status_ok':raise ValueError('中枢健康状态回执不匹配')
        self._feedback(reply);return reply
    def _feedback(self,reply):
        if isinstance(reply.get('capture_ack'),dict):
            try:self.on_capture_ack(reply['capture_ack'])
            except Exception:pass
        hub=reply.get('hub_state')
        if not isinstance(hub,dict):return
        self.feedback_log.consume(hub,self.on_event)
        with self.lock:self.state['hub']=hub
        phase=(hub.get('recording_active'),hub.get('label'),hub.get('phase'),hub.get('vr_online'))
        now=time.monotonic()
        if phase!=self.last_hub_phase or now-self.last_hub_log>=1:
            self.last_hub_phase=phase;self.last_hub_log=now
            self.on_event('中枢·接收','INFO',f"持久化前缀 {reply.get('durable_seq',self.state['durable_seq'])} · 实时新鲜 {hub.get('source_fresh','未知')} · 源样本位置 {hub.get('sample_index_end','未知')}")
            components={
                '记录':f"主动记录 {'进行中' if hub.get('recording_active') else '未开始'} · 已保存 {hub.get('recording_samples',0)} 行",
                '标签':f"标签 {hub.get('label') or 'UNKNOWN'} · 阶段 {hub.get('phase') or '未开始'} · 事件已保存 {hub.get('events_saved','未知')}",
                'VR':f"在线身份 {hub.get('vr_online',0)} · epoch {hub.get('source_epoch','未知')}",
                '执行':f"动作 {hub.get('motion_state','未知')} · 模型 {hub.get('model_state','未配置')} · 接触质量未测量"}
            for component,message in components.items():
                if self.last_components.get(component)!=message:self.on_event('中枢·'+component,'INFO',message)
            self.last_components=components
    def stop(self):
        self.stop_event.set()
        if hasattr(self,'thread'):self.thread.join(.5)
        if self.socket:
            try:self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        if hasattr(self,'thread'):self.thread.join(4)


class Collector:
    """Supervisor drains SDK into a bounded writer queue; network reads durable files."""
    def __init__(self,settings,enrollment,storage_root,*,worker_factory=None):
        self.settings,self.enrollment,self.storage_root=settings,enrollment,Path(storage_root)
        self.worker_factory=worker_factory;self.stop_event=threading.Event();self.lock=threading.RLock();self.capture_lock=threading.RLock()
        self.state=dict(state='idle',sdk_state='not_started',error=None,descriptor=None,samples=0,blocks=0,path=None,forced_termination=False)
        self.journal=self.network=self.worker=None;self.blocks=queue.Queue(maxsize=128);self.writer_error=None
        self.preview=PreviewBuffer();self.log=EventLog();self.last_disk_log=0
        self.capture=dict(active=False,session_id=None,client_token=None,protocol='self_paced_axes_v2',experiment_mode='navigation_mi_gaze',
            allow_combos=False,labels=list(CAPTURE_LABELS[:5]),instructions={l:capture_task_spec(l)['instruction'] for l in CAPTURE_LABELS},
            held=None,held_since=None,pointer_source=None,seq=0,events_saved=0,periods_completed=0,
            hub_synced=False,hub_ack_seq=0,hub_recording=None,last_recording=None,error=None)
    def start(self):
        if hasattr(self,'thread'):raise ValueError('Collector cannot be reused')
        self.thread=threading.Thread(target=self._run,daemon=True,name='g.Nautilus supervisor');self.thread.start();return self
    def _on_capture_ack(self,ack):
        with self.capture_lock:
            if not isinstance(ack,dict):return
            if ack.get('ok'):
                self.capture['hub_synced']=True
                if ack.get('recording'):self.capture['hub_recording']=ack['recording']
                if type(ack.get('ack_seq')) is int:self.capture['hub_ack_seq']=max(self.capture['hub_ack_seq'],ack['ack_seq'])
            elif ack.get('error'):
                self.capture['error']=str(ack['error'])[:200]
    def capture_snapshot(self):
        with self.capture_lock:
            snap=dict(self.capture)
            snap['labels']=list(self.capture['labels'])
            snap['instructions']=dict(self.capture['instructions'])
            snap['held_duration_s']=round(max(0.,time.monotonic()-self.capture['held_since']),1) if self.capture['active'] and self.capture['held'] and self.capture['held_since'] else 0.0
            return snap
    def start_capture(self,*,allow_combos=False,experiment_mode='navigation_mi_gaze'):
        with self.lock,self.capture_lock:
            if not self.journal or self.journal.closed or self.state.get('state')!='receiving':
                raise ValueError('请先点击“开始接收”并确认正在接收脑电数据后，再开始自主采集打标')
            if self.capture['active']:raise ValueError('自主采集打标会话已在进行中')
            d=self.descriptor;session_id=secrets.token_hex(16);client_token=secrets.token_urlsafe(24)
            labels=list(CAPTURE_LABELS) if allow_combos else list(CAPTURE_LABELS[:5])
            instructions={l:capture_task_spec(l,experiment_mode)['instruction'] for l in CAPTURE_LABELS}
            sample_at=self.sample_start;now_ns=time.monotonic_ns();utc_ns=time.time_ns()
            session_row=dict(
                kind='self_paced_session',schema='gnautilus.session.v1',session_id=session_id,
                paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2',
                build_id='gnautilus-qt-selfpaced-v1',input_surface='acquisition_client',
                experiment_mode=experiment_mode,task_binding_version='navigation_mi_gaze_v3',
                task_guidance_version='gaze_navigation_v2',allow_combos=bool(allow_combos),labels=labels,
                stream_id=d['stream_id'],source_kind=d['source_kind'],source_epoch=d['epoch'],
                sample_index_end=sample_at,recorded_monotonic_ns=now_ns,utc_ns=utc_ns,
                hold_lease_ms=750,formal_training_ready=False,intention_verified=False,
                motor_artifact_confound=True,display_timing_verified=False,
                label_semantics='User-held button instruction, not verified intention; release is UNKNOWN, never REST',
            )
            self.journal.event(session_row)
            self.capture.update(
                active=True,session_id=session_id,client_token=client_token,
                protocol='self_paced_axes_v2',experiment_mode=experiment_mode,
                allow_combos=bool(allow_combos),labels=labels,instructions=instructions,
                held=None,held_since=None,pointer_source=None,
                seq=0,events_saved=0,periods_completed=0,
                hub_synced=False,hub_ack_seq=0,hub_recording=None,error=None
            )
            self.log.add('标签','INFO',f"开始自主采集打标 · 会话 {session_id[:8]} · {'含转向组合(9类)' if allow_combos else '四方向+静止(5类)'} · 起始样本 {sample_at}")
            if self.network:
                self.network.send_capture_event(dict(
                    op='start',session_id=session_id,client_token=client_token,
                    build_id='gnautilus-qt-selfpaced-v1',presentation_protocol='self_paced_axes_v2',
                    allow_combos=bool(allow_combos),experiment_mode=experiment_mode
                ))
            return self.capture_snapshot()
    def _emit_capture_marker(self,phase,label,pointer_source,page_active,*,hold_duration_ms=None):
        d=self.descriptor;sample_at=self.sample_start;now_ns=time.monotonic_ns();utc_ns=time.time_ns()
        self.capture['seq']+=1;seq=self.capture['seq'];mode=self.capture['experiment_mode']
        spec=capture_task_spec(label,mode) if label and phase!='hold_end' else {}
        source_instructions=dict(
            experiment_mode=mode,
            navigation_instruction=label if phase=='hold_start' else None,
            gaze_instruction=spec.get('gaze_instruction','UNKNOWN'),
            limb_instruction=spec.get('limb_instruction','UNKNOWN'),
            motor_instruction=spec.get('motor_instruction','UNKNOWN'),
            primary_source=spec.get('primary_source','UNKNOWN'),
            auxiliary_source=spec.get('auxiliary_source','UNKNOWN'),
            task_binding_version=spec.get('task_binding_version','UNKNOWN'),
        )
        event_payload=dict(
            session_id=self.capture['session_id'],client_token=self.capture['client_token'],
            seq=seq,label=label,phase=phase,sender_monotonic_ms=now_ns/1e6,
            clock_offset_ms=0.0,sync_rtt_ms=0.0,sender_frame=seq,
            xr_active=False,page_active=bool(page_active),pointer_source=pointer_source,
            client_sample_index_end=sample_at,
        )
        if hold_duration_ms is not None:event_payload['hold_duration_ms']=hold_duration_ms
        marker_row=dict(
            kind='marker',code='manual_'+phase,**event_payload,**source_instructions,
            stream_id=d['stream_id'],source_kind=d['source_kind'],source_epoch=d['epoch'],
            sample_index_end=sample_at,recorded_monotonic_ns=now_ns,utc_ns=utc_ns,
            source_fresh=True,source_mode='live',intention_verified=False,formal_training_ready=False,
            motor_artifact_confound=True,input_surface='acquisition_client',
            label_semantics='held instruction; no label on release',
            label_axes=CAPTURE_AXES.get(label) if label else None,hold_lease_ms=750,
        )
        marker_row.pop('client_token',None)
        self.journal.event(marker_row)
        self.capture['events_saved']=seq
        if self.network:
            self.network.send_capture_event(dict(
                op='event',session_id=self.capture['session_id'],
                session_info=dict(
                    session_id=self.capture['session_id'],client_token=self.capture['client_token'],
                    build_id='gnautilus-qt-selfpaced-v1',presentation_protocol='self_paced_axes_v2',
                    allow_combos=self.capture['allow_combos'],experiment_mode=mode
                ),
                event=event_payload
            ))
    def hold_start(self,label,*,pointer_source='desktop_pointer',page_active=True):
        with self.lock,self.capture_lock:
            if not self.capture['active'] or not self.journal or self.journal.closed:return self.capture_snapshot()
            if label not in self.capture['labels']:raise ValueError('当前未启用该方向标签：'+str(label))
            if pointer_source not in ('desktop_pointer','desktop_keyboard'):raise ValueError('无效的打标输入源')
            if not page_active:raise ValueError('当前窗口未激活，不能开始长按打标')
            if self.capture['held']==label:return self.capture_snapshot()
            if self.capture['held'] is not None:
                self.hold_end(self.capture['held'],pointer_source=self.capture['pointer_source'] or pointer_source,page_active=page_active)
            self.capture['held']=label;self.capture['held_since']=time.monotonic();self.capture['pointer_source']=pointer_source
            self._emit_capture_marker('hold_start',label,pointer_source,page_active)
            self.log.add('标签','INFO',f"开始长按标注 · {CAPTURE_NAMES.get(label,label)} ({label}) · 样本位置 {self.sample_start}")
            return self.capture_snapshot()
    def hold_end(self,label=None,*,pointer_source=None,page_active=True):
        with self.lock,self.capture_lock:
            if not self.capture['active'] or self.capture['held'] is None:return self.capture_snapshot()
            if label is not None and label!=self.capture['held']:return self.capture_snapshot()
            ended=self.capture['held'];src=pointer_source or self.capture['pointer_source'] or 'desktop_pointer'
            duration_ms=round(max(0.,time.monotonic()-(self.capture['held_since'] or time.monotonic()))*1000,1)
            self.capture['held']=None;self.capture['held_since']=None;self.capture['pointer_source']=None
            self.capture['periods_completed']+=1
            if self.journal and not self.journal.closed:
                self._emit_capture_marker('hold_end',ended,src,page_active,hold_duration_ms=duration_ms)
            self.log.add('标签','INFO',f"结束长按标注 · {CAPTURE_NAMES.get(ended,ended)} ({ended}) · 持续 {duration_ms/1000:.2f}s → 松开未标注(UNKNOWN) · 样本位置 {self.sample_start}")
            return self.capture_snapshot()
    def capture_heartbeat(self,*,page_active=True):
        with self.lock,self.capture_lock:
            if not self.capture['active'] or not self.journal or self.journal.closed:return self.capture_snapshot()
            if not page_active and self.capture['held'] is not None:
                self.hold_end(page_active=False)
            label=self.capture['held'];src=self.capture['pointer_source'] or 'desktop_pointer'
            self._emit_capture_marker('heartbeat',label,src,page_active)
            return self.capture_snapshot()
    def stop_capture(self,reason='completed'):
        with self.lock,self.capture_lock:
            if not self.capture['active']:return self.capture_snapshot()
            if self.capture['held'] is not None:
                self.hold_end(page_active=True)
            session_id=self.capture['session_id'];d=self.descriptor;sample_at=getattr(self,'sample_start',0)
            if self.journal and not self.journal.closed and d:
                self.journal.event(dict(
                    kind='marker',code='self_paced_end',session_id=session_id,reason=reason,
                    completed=(reason=='completed'),formal_training_ready=False,
                    stream_id=d['stream_id'],source_kind=d['source_kind'],source_epoch=d['epoch'],
                    sample_index_end=sample_at,recorded_monotonic_ns=time.monotonic_ns(),utc_ns=time.time_ns()
                ))
            if self.network:
                self.network.send_capture_event(dict(op='stop',session_id=session_id,client_token=self.capture['client_token'],reason=reason))
            self.capture['active']=False;self.capture['last_recording']=self.capture.get('hub_recording') or (session_id[:8] if session_id else None)
            self.log.add('标签','INFO',f"自主采集打标已停止并保存 · 会话 {session_id[:8]} · 完成 {self.capture['periods_completed']} 个期间／{self.capture['events_saved']} 个事件")
            return self.capture_snapshot()
    def _write(self):
        try:
            while True:
                item=self.blocks.get()
                try:
                    if item is None:return
                    h,body=item;self.journal.append(h,body)
                    with self.lock:self.state['blocks']+=1;self.state['samples']+=h['sample_count']
                    if time.monotonic()-self.last_disk_log>=1:
                        self.last_disk_log=time.monotonic();self.log.add('写盘','INFO',f"已持久化 {self.state['samples']} 行／{self.state['blocks']} 块；队列 {self.blocks.qsize()} 块")
                finally:self.blocks.task_done()
        except BaseException as e:self.writer_error=e;self.log.add('写盘','ERROR',str(e));self.stop_event.set()
    def _message(self,m):
        kind=m.get('kind')
        if kind=='descriptor':
            if self.journal:raise ValueError('SDK changed descriptor without new stream')
            d=validate_descriptor(m['descriptor'])
            path=self.storage_root/datetime.date.today().isoformat()/'streams'/d['stream_id']
            self.journal=StreamJournal(path,d);self.descriptor=d;self.next_seq=1;self.sample_start=0
            self.writer=threading.Thread(target=self._write,daemon=True,name='g.Nautilus disk writer');self.writer.start()
            self.preview.configure(d);self.log.add('采集','INFO',f"共享参与者已就绪：{d['device_serial']} · {d['sampling_rate']}Hz · {len(d['channels'])}列；配置 {d['configuration_sha256'][:12]}")
            self.network=Replicator(self.journal,self.enrollment,on_event=self.log.add,health_provider=self.health,on_capture_ack=self._on_capture_ack).start()
            with self.lock:self.state.update(state='receiving',sdk_state='participant',descriptor=d,path=str(path))
        elif kind=='block':
            if not self.journal:raise ValueError('Data before stream descriptor')
            timing={key:m[key] for key in ['read_monotonic_ns','utc_ns','read_completed_monotonic_ns','read_completed_utc_ns'] if key in m}
            h,body=block_frame(self.descriptor,self.next_seq,self.sample_start,m['samples'],**timing)
            try:self.blocks.put((h,body),timeout=.5)
            except queue.Full:raise RuntimeError('磁盘写入积压，已停止接收；不会静默丢弃')
            self.next_seq+=1;self.sample_start+=h['sample_count']
            self.preview.push(m['samples'],m['read_monotonic_ns'])
        elif kind=='error':raise RuntimeError(str(m.get('code','sdk_error'))+': '+str(m.get('message','')))
        elif kind=='state' and m.get('state')=='close_errors':raise RuntimeError('SDK资源释放失败：'+'；'.join(map(str,m.get('errors',[]))))
    def _run(self):
        try:
            if self.worker_factory is None:
                from .worker import SDKWorker
                self.worker_factory=SDKWorker
            self.worker=self.worker_factory(self.settings).start()
            self.log.add('采集','INFO','SDK隔离进程已启动；等待原厂运行中的非独占共享会话')
            with self.lock:self.state.update(state='starting',sdk_state='opening_shared_session')
            last=time.monotonic();have_descriptor=False
            while not self.stop_event.is_set():
                m=self.worker.receive(.1)
                if m:
                    self._message(m)
                    if m.get('kind')=='descriptor':have_descriptor=True
                    if m.get('kind') in ['block','descriptor']:last=time.monotonic()
                elif not self.worker.alive:raise RuntimeError('SDK工作进程已退出')
                timeout=SDK_SAMPLE_TIMEOUT_SECONDS if have_descriptor else SDK_OPEN_TIMEOUT_SECONDS
                if time.monotonic()-last>timeout:
                    raise TimeoutError(f'SDK超过{timeout:g}秒没有样本，已停止自身接收' if have_descriptor else f'SDK在{timeout:g}秒内未完成原厂共享会话初始化，已停止自身接收')
                if self.writer_error:raise self.writer_error
                if self.network and self.network.snapshot().get('fatal_error'):raise RuntimeError(self.network.snapshot()['fatal_error'])
        except BaseException as e:
            self.log.add('采集','ERROR',str(e))
            with self.lock:self.state.update(state='error',error=f'{type(e).__name__}: {e}'[:400])
        finally:
            if self.writer_error:
                with self.lock:self.state.update(state='error',error=f'写盘失败：{self.writer_error}')
            with self.lock:self.state['sdk_state']='closing'
            if self.worker:
                try:self.worker.stop(on_message=self._message)
                except BaseException as e:
                    self.log.add('采集','ERROR',f'停止SDK时：{e}')
                    with self.lock:self.state.update(state='error',error=((self.state.get('error') or '')+f' 停止SDK时：{e}').strip()[:800])
                self.state['forced_termination']=self.worker.forced_termination
                self.state['uncommitted_shutdown_messages']=sum(m.get('kind')=='block' for m in getattr(self.worker,'last_stop_messages',[]))
                if self.worker.forced_termination:
                    message='SDK隔离进程被强制结束，请核查原厂会话；不能确认正常释放'
                    self.log.add('采集','ERROR',message)
                    with self.lock:self.state.update(state='error',error=((self.state.get('error') or '')+' '+message).strip()[:800])
            with self.lock:self.state['sdk_state']='closed'
            if self.journal:
                if self.writer.is_alive():
                    try:self.blocks.put(None,timeout=1);self.writer.join(10)
                    except queue.Full:self.writer_error=RuntimeError('Disk writer queue stuck')
                if self.writer.is_alive():self.state.update(state='error',error='写盘线程未结束，文件保持未关闭以供恢复')
                else:
                    if self.capture.get('active'):
                        try:self.stop_capture(reason='completed' if self.state.get('state')!='error' else 'transport_error')
                        except BaseException:pass
                    # A brief flush opportunity; offline journals stay available for explicit recovery.
                    end=time.monotonic()+5.
                    while self.network.snapshot()['backlog'] and time.monotonic()<end:time.sleep(.02)
                    self.network.stop()
                    try:
                        self.journal.event(dict(kind='checkpoint',state=self.state['state'],error=self.state.get('error'),
                            queued_blocks=self.blocks.qsize(),forced_termination=self.state['forced_termination'],network=self.network.snapshot()))
                        self.journal.close()
                    except BaseException as e:self.writer_error=e
            with self.lock:
                if self.writer_error:self.state.update(state='error',error=f'写盘失败：{self.writer_error}')
                elif self.state['state']!='error':self.state['state']='stopped'
                self.state['sdk_state']='closed'
            self.log.add('采集','INFO','自身接收已结束；原厂设备启停仍由原厂窗口负责')
    def snapshot(self):
        with self.lock:s=dict(self.state)
        s['network']=self.network.snapshot() if self.network else dict(connected=False,durable_seq=0,backlog=0,error=None)
        s['capture']=self.capture_snapshot()
        s['queue_blocks']=self.blocks.qsize();s['supervisor_active']=hasattr(self,'thread') and self.thread.is_alive();return s
    def health(self):
        with self.lock:
            health=dict(sdk_state=self.state['sdk_state'],disk_state='failed' if self.writer_error else 'writing' if not self.blocks.empty() else 'durable',
                queue_blocks=self.blocks.qsize(),error=self.state.get('error') or (str(self.writer_error)[:400] if self.writer_error else None))
            state=dict(self.state,**health)
        from .telemetry import build
        health['client_status']=build(self.settings,state,self.network.snapshot() if self.network else {})
        return health
    def stop(self):
        self.stop_event.set()
        if hasattr(self,'thread'):self.thread.join(15)
        return self.snapshot()
