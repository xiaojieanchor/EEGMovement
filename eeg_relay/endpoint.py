"""Reconnectable outbound lanes; no cached control can acquire a new lease."""
import secrets,threading,time
from .network import Connection
from .protocol import seal,verify

class Endpoint:
    def __init__(self,config,on_message,*,on_state=None,key_for=None,lanes=('control','data','backfill','preview')):
        self.config=config;self.on_message=on_message;self.on_state=on_state or (lambda s:None);self.key_for=key_for
        self.lanes=lanes;self.connections={};self.lock=threading.RLock();self.seq=0;self.boot=secrets.token_hex(16)
        self.stop_event=threading.Event();self.broken=threading.Event();self.state=dict(connected=False,error=None,generation=0);self.replays={};self.route_server=None
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='public relay supervisor');self.thread.start();return self
    def run(self):
        while not self.stop_event.is_set():
            self.boot=secrets.token_hex(16);self.broken.clear()
            try:
                for lane in self.lanes:
                    if self.stop_event.is_set():return
                    c=Connection(self.config,lane=lane,boot=self.boot);c.connect()
                    with self.lock:self.connections[lane]=c
                servers={c.route_server for c in self.connections.values()}
                if len(servers)!=1:raise ConnectionError('Lanes belong to different relay processes')
                with self.lock:
                    server=servers.pop()
                    if self.route_server!=server:self.replays.clear();self.route_server=server
                self.state.update(connected=True,error=None,path=self.connections['control'].path,generation=self.state['generation']+1);self.on_state(dict(self.state))
                readers=[threading.Thread(target=self.read,args=(lane,c),daemon=True) for lane,c in list(self.connections.items())]
                for reader in readers:reader.start()
                while not self.stop_event.wait(.1) and not self.broken.is_set():pass
            except Exception as error:self.state['error']=type(error).__name__+': '+str(error)[:240]
            finally:
                with self.lock:connections=list(self.connections.values());self.connections.clear()
                for c in connections:
                    try:c.close()
                    except Exception:pass
                self.state['connected']=False;self.on_state(dict(self.state))
            self.stop_event.wait(1)
    def read(self,lane,c):
        try:
            while not self.stop_event.is_set():
                try:h,body=c.receive(2)
                except TimeoutError:continue
                if h.get('notice'):
                    self.on_message(lane,h,body);continue
                if h.get('workspace')!=self.config['workspace'] or h.get('to')!=self.config['device_id']:raise ValueError('Foreign relay response')
                secret=self.key_for(h['from']) if self.key_for else self.config['secret']
                if not secret or not verify(h,body,secret):raise ValueError('End-to-end signature rejected')
                if h['kind'] not in ('eeg_block','quest_event'):
                    with self.lock:
                        server,generation=h.get('route_server'),h.get('route_generation')
                        if not isinstance(server,str) or type(generation) is not int or generation<1:raise ValueError('Missing authenticated route generation')
                        if server!=self.route_server:raise ValueError('Relay process changed; reconnect required')
                        old=self.replays.get(h['from'])
                        if old and generation<old[0]:continue
                        if old and generation==old[0] and h['boot']!=old[1]:raise ValueError('Authenticated boot conflict')
                        high,seen=(old[2],old[3]) if old and generation==old[0] else (0,set())
                        if h['seq'] in seen or h['seq']<=high-512:continue
                        high=max(high,h['seq']);seen={n for n in seen if n>high-512};seen.add(h['seq']);self.replays[h['from']]=generation,h['boot'],high,seen
                self.on_message(lane,h,body)
        except Exception as error:
            if not self.stop_event.is_set() and not self.broken.is_set():self.state['error']=type(error).__name__+': '+str(error)[:240];self.broken.set()
    def send(self,kind,body,target,secret,*,lane='control',stream='',epoch=0,block_seq=0):
        with self.lock:
            if not self.state['connected'] or lane not in self.connections:raise ConnectionError('公网连接尚未认证')
            self.seq+=1;h=seal(self.config['workspace'],self.config['device_id'],target,self.boot,self.seq,kind,body,secret,stream=stream,epoch=epoch,block_seq=block_seq)
            connection=self.connections[lane]
        connection.send(h,body);return h['seq']
    def snapshot(self):return dict(self.state)
    def stop(self):
        self.stop_event.set();self.broken.set()
        with self.lock:connections=list(self.connections.values())
        for c in connections:
            try:c.close()
            except Exception:pass
        if hasattr(self,'thread') and self.thread is not threading.current_thread():self.thread.join(5)
