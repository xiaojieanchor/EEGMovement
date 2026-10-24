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
from .contract import json_bytes,send_frame,recv_frame


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
        if cls is Replicator and len(args)>1 and args[1].get('mode')=='relay':
            from .relay_client import RelayReplicator
            return object.__new__(RelayReplicator)
        return object.__new__(cls)
    def __init__(self,journal,enrollment,backfill_only=False,on_event=None,health_provider=None):
        self.journal,self.enrollment,self.backfill_only=journal,enrollment,backfill_only
        self.stop_event=threading.Event();self.lock=threading.RLock();self.socket=None
        self.state=dict(connected=False,durable_seq=0,backlog=0,error=None,sent_blocks=0,live_accepted=0)
        self.last_live_seq=0;self.acked=set()
        self.health_provider=health_provider
        self.on_event=on_event or (lambda *args:None);self.last_hub_log=0;self.last_hub_phase=None;self.last_components={}
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
                    if time.monotonic()-last_health>=1:
                        r=self._send_health(s);ticket=r['ticket'];heartbeat=last_health=time.monotonic()
                    with self.journal.lock:
                        maximum=max(self.journal.records,default=0)
                        seq=None;mode='backfill'
                        if maximum>self.last_live_seq and not self.backfill_only:
                            h,body=self.journal.read(maximum)
                            if 0<=time.monotonic_ns()-h['read_monotonic_ns']<150_000_000:
                                seq=maximum;mode='live';self.last_live_seq=seq
                        if seq is None:
                            candidate=self.state['durable_seq']+1
                            while candidate in self.acked:candidate+=1
                            if candidate in self.journal.records:seq=candidate;h,body=self.journal.read(seq)
                    if seq is None:
                        if time.monotonic()-heartbeat>=.08:
                            r=self._send_health(s);ticket=r['ticket'];heartbeat=last_health=time.monotonic()
                        self.stop_event.wait(.01);continue
                    h=dict(h,mode=mode,ticket=ticket,source_age_ms=max(0,(time.monotonic_ns()-h['read_monotonic_ns'])/1e6))
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
        hub=reply.get('hub_state')
        if not isinstance(hub,dict):return
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
        self.worker_factory=worker_factory;self.stop_event=threading.Event();self.lock=threading.RLock()
        self.state=dict(state='idle',sdk_state='not_started',error=None,descriptor=None,samples=0,blocks=0,path=None,forced_termination=False)
        self.journal=self.network=self.worker=None;self.blocks=queue.Queue(maxsize=128);self.writer_error=None
        self.preview=PreviewBuffer();self.log=EventLog();self.last_disk_log=0
    def start(self):
        if hasattr(self,'thread'):raise ValueError('Collector cannot be reused')
        self.thread=threading.Thread(target=self._run,daemon=True,name='g.Nautilus supervisor');self.thread.start();return self
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
            self.network=Replicator(self.journal,self.enrollment,on_event=self.log.add,health_provider=self.health).start()
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
            last=time.monotonic()
            while not self.stop_event.is_set():
                m=self.worker.receive(.1)
                if m:
                    self._message(m)
                    if m.get('kind') in ['block','descriptor']:last=time.monotonic()
                elif not self.worker.alive:raise RuntimeError('SDK工作进程已退出')
                if time.monotonic()-last>5:raise TimeoutError('SDK超过5秒没有样本，已停止自身接收')
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
                    # A brief flush opportunity; offline journals stay available for explicit recovery.
                    end=time.monotonic()+.3
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
