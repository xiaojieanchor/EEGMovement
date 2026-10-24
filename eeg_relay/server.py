"""Whitelisted message relay; never connects to an address supplied by a peer."""
import argparse,base64,collections,hashlib,hmac,json,logging,queue,secrets,threading,time
from pathlib import Path
from websockets.sync.server import serve
from .protocol import SCHEMA,authentication,encoded,pack,unpack,validate,HEX32
from .spool import Spool,CapacityError

log=logging.getLogger('eeg_relay')
RULES={
 'eeg':{'eeg_block','eeg_status','eeg_event','clock','update_state'},
 'quest':{'rpc','quest_event','vr_wire','preview','clock','update_state'},
 'hub':{'hub_ack','hub_reject','hub_status','rpc_reply','vr_wire','clock_reply','event_ack'},
}
LANES={'eeg_block':{'data','backfill'},'preview':{'preview'},'quest_event':{'backfill'}}

class Peer:
    def __init__(self,ws,identity,record,boot,lane):
        self.ws,self.identity,self.record,self.boot,self.lane=ws,identity,record,boot,lane
        self.out=queue.Queue(maxsize=128 if lane=='data' else 64);self.closed=False;self.last_send=time.monotonic();self.sending=False
        self.thread=threading.Thread(target=self.write,daemon=True);self.thread.start()
    def enqueue(self,item):
        if self.closed:return False
        try:self.out.put_nowait(item);return True
        except queue.Full:
            if self.lane in ('preview','backfill'):return False
            self.close('bounded_queue_full');return False
    def write(self):
        while not self.closed:
            try:item=self.out.get(timeout=.2)
            except queue.Empty:continue
            try:
                self.sending=True;self.last_send=time.monotonic();self.ws.send(item);self.sending=False
            except Exception:self.close('send_failed')
    def close(self,reason):
        self.closed=True
        try:self.ws.close(1008,reason[:100])
        except Exception:pass

class RelayServer:
    def __init__(self,records,root,key,*,limit=8*1024**3,reserve=10*1024**3,ttl=86400):
        self.records=records;self.spool=Spool(root,key,limit=limit,reserve=reserve,ttl=ttl)
        self.lock=threading.RLock();self.peers={};self.stop=threading.Event();self.server=None;self.auth_failures=collections.defaultdict(collections.deque)
        self.stats=collections.Counter();self.started=time.monotonic();self.cache_error=None
        self.slots=threading.BoundedSemaphore(32);self.update_root=None;self.release_notices={}
        self.route_server=secrets.token_hex(16);self.route_generations={}
        from .onboarding import EnrollmentBroker
        self.onboarding=EnrollmentBroker(root,self.role_occupied)
        for identity,r in records.items():
            if not HEX32.fullmatch(identity) or r.get('role') not in RULES or not HEX32.fullmatch(r.get('workspace','')) or len(bytes.fromhex(r['auth_secret']))!=32:raise ValueError('Invalid device registry')
    def start(self,host='127.0.0.1',port=18767,ssl_context=None):
        # Direct TLS on Python/OpenSSL can race post-handshake TLS1.3 tickets
        # against the synchronous HTTP upgrade. Nginx TLS already disables them.
        if ssl_context is not None:ssl_context.num_tickets=0
        self.server=serve(self.handle,host,port,ssl=ssl_context,process_request=self.http,max_size=5*1024*1024,max_queue=64,compression=None,ping_interval=10,ping_timeout=15,close_timeout=1,open_timeout=5)
        threading.Thread(target=self.server.serve_forever,daemon=True,name='relay listener').start()
        threading.Thread(target=self.maintenance,daemon=True,name='relay cache replay').start();return self.server
    def http(self,connection,request):
        if request.path.startswith('/eeg/v1/enroll/'):
            return self.enroll_http(connection,request)
        if request.path=='/eeg/health':
            reply=connection.respond(200,json.dumps(dict(service='EEGMovement relay',schema=SCHEMA,status='degraded' if self.cache_error else 'ok',private_proxy=False,cache_error=self.cache_error,cache=self.spool.snapshot(),uptime_s=round(time.monotonic()-self.started))))
            reply.headers['Content-Type']='application/json';return reply
        if request.path!='/eeg/v1/ws':return connection.respond(404,'Not found')
    def role_occupied(self,role):
        with self.lock:return any(p.record['role']==role and not p.closed for p in self.peers.values())
    def enroll_http(self,connection,request):
        operation=request.path.removeprefix('/eeg/v1/enroll/');status=200
        try:
            if len({r['workspace'] for r in self.records.values()})!=1:raise ValueError('Single workspace enrollment only')
            raw=request.headers.get('X-EEG-Request','')
            if len(raw)>16384:raise ValueError('Oversized enrollment request')
            data=json.loads(base64.b64decode(raw,validate=True))
            if not isinstance(data,dict):raise ValueError('Enrollment object required')
            if operation in ('list','complete'):
                self.onboarding.authenticate(request.path,request.headers,self.records)
                if operation=='list':value=dict(requests=self.onboarding.pending(),role_states={r:dict(online=self.role_occupied(r)) for r in ('eeg','quest')})
                else:self.onboarding.complete(data.get('request_id'),data.get('role'),data.get('sealed'));value=dict(state='ready')
            elif operation in ('start','poll'):
                address=str(request.headers.get('X-Real-IP') or connection.remote_address[0]);now=time.monotonic()
                with self.onboarding.lock:
                    self.onboarding.rate={k:v for k,v in self.onboarding.rate.items() if now-v[0]<60}
                    window,count=self.onboarding.rate.get(address,(now,0))
                    if count>=180 or len(self.onboarding.rate)>=1024 and address not in self.onboarding.rate:raise ValueError('Enrollment rate exceeded')
                    self.onboarding.rate[address]=(window,count+1)
                value=self.onboarding.start(data) if operation=='start' else self.onboarding.poll(data)
            else:status=404;value=dict(state='not_found')
        except Exception as error:
            reason=str(error);status=410 if 'expired' in reason else 409 if any(k in reason for k in ('occupied','conflict')) else 400
            value=dict(state='role_occupied' if 'occupied' in reason else 'request_expired' if 'expired' in reason else 'rejected',message=reason[:160])
        reply=connection.respond(status,json.dumps(value));reply.headers['Content-Type']='application/json';reply.headers['Cache-Control']='no-store';return reply
    def handle(self,ws):
        peer=None
        if not self.slots.acquire(False):ws.close(1013,'connection_capacity');return
        try:
            address=str(ws.request.headers.get('X-Real-IP') or ws.remote_address[0]);now=time.monotonic()
            with self.lock:
                if address not in self.auth_failures and len(self.auth_failures)>=4096:
                    self.auth_failures={k:v for k,v in self.auth_failures.items() if v and now-v[-1]<60}
                    self.auth_failures=collections.defaultdict(collections.deque,self.auth_failures)
                    if len(self.auth_failures)>=4096:raise ValueError('Authentication address capacity')
                history=self.auth_failures[address]
                while history and now-history[0]>60:history.popleft()
                if len(history)>=60:raise ValueError('Authentication rate limit')
                history.append(now)
            challenge=secrets.token_hex(32);ws.send(encoded(dict(notice='challenge',schema=SCHEMA,challenge=challenge)).decode())
            auth=json.loads(ws.recv(timeout=5));identity=auth.get('device_id');r=self.records.get(identity)
            if not r or auth.get('kind')!='auth' or auth.get('workspace')!=r['workspace'] or not HEX32.fullmatch(str(auth.get('boot',''))):raise ValueError('Unknown device')
            lane=auth.get('lane');expected=authentication(r['auth_secret'],challenge,r['workspace'],identity,auth['boot'],lane)
            if not hmac.compare_digest(str(auth.get('mac','')),expected):raise ValueError('Invalid authentication')
            with self.lock:
                if (identity,lane) in self.peers:raise ValueError('Device lane already connected')
                owned=[p for (i,_),p in self.peers.items() if i==identity]
                if any(p.boot!=auth['boot'] for p in owned):raise ValueError('Device boot already connected')
                if r['role']=='hub' and any(p.record['workspace']==r['workspace'] and p.record['role']=='hub' and p.identity!=identity for p in self.peers.values()):raise ValueError('Workspace already has a Hub')
                if r['role'] in ('eeg','quest') and any(p.record['workspace']==r['workspace'] and p.record['role']==r['role'] and p.identity!=identity for p in self.peers.values()):raise ValueError('Software role already occupied')
                ws.send(encoded(dict(notice='authenticated',schema=SCHEMA,role=r['role'],route_server=self.route_server)).decode())
                generation,old_boot=self.route_generations.get(identity,(0,None))
                if old_boot!=auth['boot']:generation+=1;self.route_generations[identity]=(generation,auth['boot'])
                peer=Peer(ws,identity,r,auth['boot'],lane);peer.generation=generation;self.peers[identity,lane]=peer
            high=0;seen=set();window=time.monotonic();count=0
            for raw in ws:
                if time.monotonic()-window>=1:window=time.monotonic();count=0
                count+=1
                if count>({'control':160,'data':200,'backfill':120,'preview':3}[lane]):raise ValueError('Lane message rate exceeded')
                h,body=unpack(raw);validate(h)
                if h['from']!=identity or h['workspace']!=r['workspace'] or h['boot']!=peer.boot:raise ValueError('Foreign envelope')
                target=self.records.get(h['to'])
                if not target or target['workspace']!=r['workspace'] or h['kind'] not in RULES[r['role']]:raise ValueError('Unauthorized kind/target')
                if (r['role']=='hub')==(target['role']=='hub') or r['role']!='hub' and h['to']!=r['hub_id']:raise ValueError('Only device-Hub routing is allowed')
                if lane not in LANES.get(h['kind'],{'control'}):raise ValueError('Message on wrong lane')
                if h['seq'] in seen or h['seq']<=high-256:raise ValueError('Envelope replay')
                high=max(high,h['seq']);seen={n for n in seen if n>high-256};seen.add(h['seq'])
                if hashlib.sha256(body).hexdigest()!=h['digest']:raise ValueError('Payload digest mismatch')
                if h['kind'] in ('eeg_block','quest_event'):
                    try:receipt=self.spool.put(h,body)
                    except CapacityError as error:
                        peer.enqueue(encoded(dict(notice='cache_full',message=str(error),seq=h['seq'])).decode());continue
                    peer.enqueue(encoded(dict(notice='cloud_durable',seq=h['seq'],block_seq=h['block_seq'],stream=h['stream'],epoch=h['epoch'],**receipt)).decode())
                if h['kind']=='hub_ack':self.spool.ack(r['workspace'],h['to'],h['stream'],h['epoch'],h['block_seq'])
                elif h['kind']=='hub_reject':self.spool.quarantine(r['workspace'],h['to'],h['stream'],h['epoch'],h['block_seq'])
                elif h['kind']=='event_ack':
                    data=json.loads(body);event_id=data.get('cache_id','')
                    if not event_id.startswith(r['workspace']+':'+h['to']+':'):raise ValueError('Foreign event receipt')
                    self.spool.ack_event(event_id)
                destination='data' if h['kind']=='eeg_block' and lane=='data' else 'backfill' if h['kind'] in ('eeg_block','quest_event') else 'preview' if h['kind']=='preview' else 'control'
                with self.lock:other=self.peers.get((h['to'],destination))
                if other:other.enqueue(pack(dict(h,delivery='live' if destination!='backfill' else 'backfill',route_server=self.route_server,route_generation=peer.generation),body))
                self.stats[h['kind']]+=1
        except Exception as error:
            self.stats['rejected']+=1;log.warning('connection rejected: %s %s',type(error).__name__,str(error)[:160])
            try:ws.close(1008,'authentication/protocol rejected')
            except Exception:pass
        finally:
            if peer:
                with self.lock:
                    if self.peers.get((peer.identity,peer.lane)) is peer:self.peers.pop((peer.identity,peer.lane),None)
                peer.close('connection_closed')
            self.slots.release()
    def maintenance(self):
        last_expire=0
        while not self.stop.wait(.5):
            with self.lock:peers=list(self.peers.values())
            for peer in peers:
                if peer.lane=='control' and self.update_root and peer.record['role'] in ('eeg','quest'):
                    platform='windows-x64' if peer.record['role']=='eeg' else 'quest-arm64'
                    manifest=Path(self.update_root)/(platform+'.json')
                    if manifest.exists():
                        revision=hashlib.sha256(manifest.read_bytes()).hexdigest();key=peer.identity,peer.boot,revision
                        if self.release_notices.get(peer.identity)!=key and peer.enqueue(encoded(dict(notice='update_available',platform=platform,revision=revision)).decode()):self.release_notices[peer.identity]=key
                if peer.sending and time.monotonic()-peer.last_send>5:peer.close('send_deadline')
                if peer.record['role']=='hub' and peer.lane=='backfill':
                    try:
                        for h,body in self.spool.pending(peer.record['workspace'],peer.identity,32):
                            if not peer.enqueue(pack(dict(h,delivery='backfill'),body)):break
                        if self.spool.last_error:self.cache_error=self.spool.last_error
                    except Exception as error:self.cache_error=str(error)[:200];log.error('cache replay failed: %s',type(error).__name__)
            if time.monotonic()-last_expire>60:
                try:self.spool.expire();last_expire=time.monotonic()
                except Exception as error:self.cache_error=str(error)[:200]
    def close(self):
        self.stop.set()
        with self.lock:peers=list(self.peers.values())
        for p in peers:p.close('server_shutdown')
        if self.server:self.server.shutdown()
        time.sleep(.1);self.spool.close();self.onboarding.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);args=p.parse_args()
    config=json.loads(Path(args.config).read_text('utf-8'));key=Path(config['cache_key']).read_bytes()
    service=RelayServer(config['devices'],config['cache_root'],key,limit=config.get('limit',8*1024**3),reserve=config.get('reserve',10*1024**3),ttl=config.get('ttl',86400))
    service.update_root=Path('/srv/www/eeg-updates');service.start();logging.basicConfig(level=logging.INFO)
    try:
        while True:time.sleep(1)
    except KeyboardInterrupt:service.close()

if __name__=='__main__':main()
