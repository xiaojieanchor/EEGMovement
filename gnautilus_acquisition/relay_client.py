"""Pipelined cloud upload from durable originals; Hub acknowledgements remain authoritative."""
import bisect,json,secrets,threading,time
from eeg_relay.endpoint import Endpoint
from eeg_relay.protocol import encoded,pack
from .client import Replicator

class RelayReplicator(Replicator):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.cloud={};self.inflight={};self.ticket=None;self.cloud_bytes=0;self.live_scan_seq=0;self.backfill_scan_seq=0
        self.endpoint=Endpoint(self.enrollment,self.message,on_state=self.connection_state,lanes=('control','data','backfill'),on_stage=lambda lane,stage,message:self.on_event('公网·'+lane,'INFO',stage+' · '+message))
        self.state.update(transport='public_relay',cloud_blocks=0,hub_online=False,proxy_mode=self.enrollment.get('proxy_mode','auto'))
    def connection_state(self,state):
        with self.lock:
            self.state.update(connected=state['connected'],error=state.get('error'),path=state.get('path'));self.ticket=None;self.inflight.clear();self.cloud.clear()
        self.on_event('公网','INFO' if state['connected'] else 'WARN','已通过TLS与设备认证；等待本机中枢' if state['connected'] else '公网连接中断，本地原始日志保留：'+str(state.get('error') or '重连中'))
    def message(self,lane,h,body):
        if h.get('notice'):
            if h['notice']=='update_available':self.state['update_revision']=h.get('revision')
            elif h['notice']=='cloud_durable' and h.get('stream')==self.journal.descriptor['stream_id']:
                with self.lock:
                    seq=h['block_seq'];self.cloud[seq]=time.monotonic();self.inflight.pop(seq,None);self.state['cloud_blocks']=len(self.cloud)
            elif h['notice']=='cache_full':
                with self.lock:self.state['error']=h.get('message');self.inflight.clear()
                self.on_event('云端保存','ERROR',h.get('message','缓存满；原始数据仍保存在本机'))
            return
        if h['from']!=self.enrollment['hub_id']:raise ValueError('Unexpected Hub identity')
        data=json.loads(body)
        if h['kind']=='hub_reject' and h['stream']==self.journal.descriptor['stream_id']:
            self.state.update(fatal_error='中枢拒收并隔离：'+str(data.get('error','数据身份不一致')),error=str(data.get('error')))
            self.on_event('中枢保存','ERROR',self.state['fatal_error']);self.stop_event.set();return
        if h['kind'] in ('hub_status','hub_ack'):
            if data.get('ticket'):self.ticket=data['ticket']
            if h['kind']=='hub_ack' and h['stream']==self.journal.descriptor['stream_id']:
                with self.lock:
                    self.state['durable_seq']=max(self.state['durable_seq'],data['durable_seq']);self.acked.add(data['ack_seq'])
                    self.state['live_accepted']+=bool(data.get('live_accepted'));self.inflight.pop(data['ack_seq'],None)
                    self.acked={n for n in self.acked if n>self.state['durable_seq']}
            self.last_hub=time.monotonic();self.state['hub_online']=True;self._feedback(data)
        elif h['kind']=='clock_reply':
            data.update(t3=time.monotonic()*1000,phase='report');self.endpoint.send('clock',encoded(data),self.enrollment['hub_id'],self.enrollment['secret'])
    def _run(self):
        while not self.stop_event.is_set():
            self._run_attempt()
            if self.stop_event.wait(1):break
            self.endpoint=Endpoint(self.enrollment,self.message,on_state=self.connection_state,lanes=('control','data','backfill'),on_stage=lambda lane,stage,message:self.on_event('公网·'+lane,'INFO',stage+' · '+message))
    def _run_attempt(self):
        self.last_hub=0;self.endpoint.start();last_health=last_clock=0
        lane_stop=threading.Event();lane_error=[]
        def sender(function):
            try:
                while not lane_stop.wait(.005):
                    if self.endpoint.snapshot()['connected']:function()
            except Exception as error:lane_error.append(error);lane_stop.set()
        senders=[threading.Thread(target=sender,args=(fn,),daemon=True,name='EEG '+name) for name,fn in
                 [('live sender',self._send_next_live),('backfill sender',self._send_next_backfill)]]
        for thread in senders:thread.start()
        try:
            while not self.stop_event.wait(.01):
                if lane_error:raise lane_error[0]
                now=time.monotonic();self.state['hub_online']=now-self.last_hub<1
                if self.state.get('fatal_error'):break
                if not self.endpoint.snapshot()['connected']:continue
                if now-last_health>=.5:
                    health=self.health_provider() if self.health_provider else dict(sdk_state='backfill' if self.backfill_only else 'receiving',disk_state='durable')
                    self.endpoint.send('eeg_status',encoded(health),self.enrollment['hub_id'],self.enrollment['secret']);last_health=now
                if now-last_clock>=5:
                    self.endpoint.send('clock',encoded(dict(t0=now*1000)),self.enrollment['hub_id'],self.enrollment['secret']);last_clock=now
        except Exception as error:
            if not self.stop_event.is_set():
                self.state['error']=str(error)[:240];self.on_event('公网','WARN',str(error)[:240]+'；本地数据保留，稍后重连')
        finally:
            lane_stop.set();self.endpoint.stop()
            for thread in senders:thread.join(6)
            if any(t.is_alive() for t in senders):self.state.update(fatal_error='发送线程未结束，保留本地记录后停止',error='发送线程未结束');self.stop_event.set()
    def _next_pending(self,live):
        now=time.monotonic();stamp=time.monotonic_ns()
        with self.lock:
            if live and (self.backfill_only or not self.ticket):return None
            self.inflight={n:t for n,t in self.inflight.items() if now-t<3}
            if len(self.inflight)>=(64 if live else 32):return None
            cursor=max(self.state['durable_seq'],self.live_scan_seq if live else self.backfill_scan_seq)
            with self.journal.lock:
                numbers=self.journal.sequences;at=bisect.bisect_right(numbers,cursor)
                if not live and at==len(numbers):
                    at=bisect.bisect_right(numbers,self.state['durable_seq'])
                # Work per poll stays bounded even after hours of recording.
                candidates=[(n,self.journal.records[n]['header']['read_monotonic_ns']) for n in numbers[at:at+64]]
            for n,read in candidates:
                if live:self.live_scan_seq=n
                else:self.backfill_scan_seq=n
                age=max(0,(stamp-read)/1e6);fresh=age<150 and not self.backfill_only
                if n<=self.state['durable_seq'] or n in self.acked or n in self.inflight or now-self.cloud.get(n,-1e9)<=60:continue
                if live:
                    if not fresh or not self.ticket or n<=self.last_live_seq:continue
                elif fresh:continue
                self.inflight[n]=now
                if live:self.last_live_seq=n
                return n
        return None
    def _send_block(self,seq,live):
        if seq is None:return False
        try:
            header,raw=self.journal.read(seq);age=max(0,(time.monotonic_ns()-header['read_monotonic_ns'])/1e6)
            # Data that aged during local I/O must return later as history.
            if live and age>=150:
                with self.lock:self.inflight.pop(seq,None)
                return False
            header.update(mode='live' if live else 'backfill',ticket=self.ticket or '',source_age_ms=age)
            self.endpoint.send('eeg_block',pack(dict(descriptor=self.journal.descriptor,block=header),raw),self.enrollment['hub_id'],self.enrollment['secret'],
                lane='data' if live else 'backfill',stream=header['stream_id'],epoch=header['epoch'],block_seq=seq)
            with self.lock:self.state['sent_blocks']+=1
            return True
        except Exception:
            with self.lock:self.inflight.pop(seq,None)
            raise
    def _send_next_live(self):return self._send_block(self._next_pending(True),True)
    def _send_next_backfill(self):return self._send_block(self._next_pending(False),False)
    def snapshot(self):
        result=super().snapshot();fresh=time.monotonic()-getattr(self,'last_hub',0)<1
        result.update(hub_online=fresh,hub_state_fresh=fresh)
        if not fresh and isinstance(result.get('hub'),dict):result['hub']=dict(result['hub'],source_fresh=False,phase='unknown',label=None,events_saved=None,vr_online=None,model_state='未知／回包过期',motion_state='未知／回包过期')
        return result
    def stop(self):
        self.stop_event.set();self.endpoint.stop()
        if hasattr(self,'thread'):self.thread.join(5)

def probe_relay(enrollment,on_event=None):
    from eeg_relay.network import Connection
    from eeg_relay.protocol import seal,verify
    began=time.monotonic();callback=on_event or (lambda *a:None);nonce=secrets.token_hex(16)
    c=Connection(enrollment,on_stage=lambda a,b:callback('校验','INFO',a+' · '+b)).connect()
    try:
        body=encoded(dict(probe=nonce,sdk_state='diagnostic',disk_state='not_started'));h=seal(enrollment['workspace'],enrollment['device_id'],enrollment['hub_id'],c.boot,1,'eeg_status',body,enrollment['secret']);c.send(h,body)
        end=time.monotonic()+5
        while time.monotonic()<end:
            h,body=c.receive(max(.1,end-time.monotonic()))
            if h.get('notice'):continue
            if not verify(h,body,enrollment['secret']) or h.get('from')!=enrollment['hub_id']:raise ValueError('中枢端到端签名未通过')
            reply=json.loads(body)
            if reply.get('probe')==nonce:
                callback('校验','INFO','公网转发和本机中枢回包均已验证；没有启动设备')
                return dict(connected=True,elapsed_ms=round((time.monotonic()-began)*1000,1),hub=reply.get('hub_state',{}),path=c.path)
        raise TimeoutError('公网可达，但本机中枢没有确认；请检查中枢是否运行并连接公网')
    finally:c.close()
