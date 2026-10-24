"""Persistent idle control lane. Never opens the SDK or supplies EEG samples."""
import copy,json,secrets,threading,time
from eeg_relay.endpoint import Endpoint
from eeg_relay.protocol import encoded
from .feedback import HubFeedbackLog


class StatusLink:
    def __init__(self,enrollment,on_event=None):
        self.config=dict(enrollment);self.lock=threading.RLock();self.stop_event=threading.Event()
        self.on_event=on_event or (lambda *a:None);self.last_hub=0;self.hub={};self.health={}
        self.feedback_log=HubFeedbackLog()
        self.pending={};self.thread=None;self.endpoint=Endpoint(self.config,self.message,on_state=self.connection_state,lanes=('control',),on_stage=lambda lane,stage,message:self.on_event('中枢·连接','INFO',stage+' · '+message))
        self.state=dict(connected=False,transport='public_relay',status_only=True,error=None)
    def start(self):
        with self.lock:
            if self.thread:return self
            self.thread=threading.Thread(target=self.run,daemon=True,name='EEG idle status link');self.thread.start()
        return self
    def connection_state(self,state):
        with self.lock:
            self.state.update(state)
            if not state['connected']:self.last_hub=0
        if not self.stop_event.is_set():self.on_event('中枢','INFO' if state['connected'] else 'WARN','状态连接已认证，等待中枢回包' if state['connected'] else '状态连接中断，后台重连：'+str(state.get('error') or '连接恢复中'))
    def update_health(self,health):
        with self.lock:self.health=copy.deepcopy(health)
    def run(self):
        self.endpoint.start()
        try:
            while not self.stop_event.wait(.5):
                if not self.endpoint.snapshot()['connected']:continue
                with self.lock:health=copy.deepcopy(self.health)
                health.setdefault('sdk_state','not_started');health.setdefault('disk_state','not_recording')
                try:self.endpoint.send('eeg_status',encoded(health),self.config['hub_id'],self.config['secret'])
                except Exception as error:
                    with self.lock:self.state['error']=type(error).__name__+': '+str(error)[:160]
        finally:self.endpoint.stop()
    def message(self,lane,header,body):
        if header.get('notice'):
            if header.get('notice')=='update_available':self.state['update_revision']=header.get('revision')
            return
        if header['from']!=self.config['hub_id']:raise ValueError('Unexpected Hub identity')
        if header['kind']!='hub_status':return
        data=json.loads(body)
        if not isinstance(data.get('hub_state'),dict):raise ValueError('Invalid hub state')
        self.feedback_log.consume(data['hub_state'],self.on_event)
        with self.lock:
            self.hub=data['hub_state'];self.last_hub=time.monotonic();self.state['error']=None
            item=self.pending.get(data.get('probe'))
            if item:item['reply']=dict(data);item['event'].set()
    def probe(self,on_event=None,timeout=10):
        self.start();began=time.monotonic();nonce=secrets.token_hex(16)
        item=dict(event=threading.Event(),reply=None)
        with self.lock:self.pending[nonce]=item
        try:
            end=began+timeout
            while not self.stop_event.is_set() and time.monotonic()<end:
                if self.endpoint.snapshot()['connected']:
                    self.endpoint.send('eeg_status',encoded(dict(probe=nonce,sdk_state='not_started',disk_state='not_recording')),self.config['hub_id'],self.config['secret'])
                    if item['event'].wait(min(.5,max(0,end-time.monotonic()))):
                        if on_event:on_event('校验','INFO','持续状态连接与中枢签名回包已确认；未启动设备')
                        return dict(connected=True,elapsed_ms=round((time.monotonic()-began)*1000,1),hub=item['reply']['hub_state'],path=self.endpoint.snapshot().get('path'))
                else:self.stop_event.wait(.05)
            raise TimeoutError('状态连接未得到中枢确认；后台继续重连')
        finally:
            with self.lock:self.pending.pop(nonce,None)
    def snapshot(self):
        with self.lock:
            result=dict(self.state);age=(time.monotonic()-self.last_hub)*1000 if self.last_hub else None
            fresh=bool(result['connected'] and age is not None and age<1000)
            result.update(hub_online=fresh,hub_state_fresh=fresh,hub_age_ms=age,hub=copy.deepcopy(self.hub) if fresh else {})
            return result
    def stop(self):
        self.stop_event.set();self.endpoint.stop()
        if self.thread and self.thread is not threading.current_thread():self.thread.join(6)

