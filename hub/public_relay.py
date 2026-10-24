"""Outbound public transport into local Hub operations, never an HTTP/SSH proxy."""
import base64,collections,json,math,secrets,threading,time
from pathlib import Path
from eeg_relay.endpoint import Endpoint
from eeg_relay.protocol import encoded,pack,unpack
from gnautilus_acquisition.storage import atomic_json

RPC_PATHS={'/v1/status','/v1/pairing','/v1/acquisition/sync','/v1/acquisition/status','/v1/acquisition/start','/v1/acquisition/event','/v1/acquisition/stop','/v1/intent/status','/v1/intent/start','/v1/intent/stop','/v1/intent/control'}
RPC_PATHS.update({'/v1/calibration/status','/v1/calibration/history','/v1/calibration/start','/v1/calibration/present','/v1/calibration/cancel','/v1/calibration/new-placement'})

class HubRelay:
    def __init__(self,hub,config):
        self.hub,self.config=hub,config;self.peers=config['peers'];self.lock=threading.RLock();self.tickets={};self.stop_event=threading.Event()
        self.rpc_cache=collections.OrderedDict();self.clock_pending={};self.clock_maps={};self.anchors=collections.deque(maxlen=4096)
        self.client_reports={}
        self.state=dict(enabled=True,connected=False,error=None,blocks=0,backfill_blocks=0,events_archived=0,private_proxy=False)
        self.state['onboarding']=dict(state='waiting_hub',pending_count=0)
        self.endpoint=Endpoint(config,self.message,on_state=self.connection_state,key_for=lambda identity:self.peers.get(identity,{}).get('secret'))
    def start(self):
        self.endpoint.start();self.thread=threading.Thread(target=self.tick,daemon=True,name='Hub outbound relay status');self.thread.start()
        self.onboarding_thread=threading.Thread(target=self.onboarding_tick,daemon=True,name='Automatic device roles');self.onboarding_thread.start();return self
    def onboarding_tick(self):
        from eeg_relay.onboarding import hub_call,seal_identity
        while not self.stop_event.wait(2):
            if not self.endpoint.snapshot()['connected']:continue
            try:
                report=hub_call(self.config,'list');self.state['role_states']=report['role_states'];requests=report['requests']
                feedback=self.feedback();safe=not feedback.get('recording_active',True) and feedback.get('motion_state')=='STOP'
                self.state['onboarding']=dict(state='ready' if safe else 'waiting_idle',message='按软件类型自动连接' if safe else '记录关闭、动作停止后自动连接',pending_count=len(requests),last_poll_ms=time.time()*1000)
                if not safe:continue
                for request in requests:
                    if request['occupied']:continue
                    role=request['role'];matches=[(i,p) for i,p in self.peers.items() if p['role']==role]
                    if len(matches)!=1:raise ValueError('角色配置必须恰有一项：'+role)
                    identity,peer=matches[0]
                    root=Path(getattr(self.hub,'root',Path(__file__).resolve().parents[1]))
                    path=root/'hub/runtime/relay_enrollments'/(role+'-'+identity+'.json')
                    config=json.loads(path.read_text('utf-8'))
                    if config['device_id']!=identity or config['secret']!=peer['secret']:raise ValueError('Role identity mismatch')
                    sealed=seal_identity(config,request['public_key'],request['request_id'])
                    hub_call(self.config,'complete',dict(request_id=request['request_id'],role=role,sealed=sealed))
            except Exception as error:
                self.state['role_states']={r:dict(online=None) for r in ('eeg','quest')}
                self.state['onboarding']=dict(state='error',message=str(error)[:160],pending_count=0)
    def connection_state(self,state):
        self.state.update(state)
        if not state['connected']:
            self.state['role_states']={r:dict(online=None) for r in ('eeg','quest')}
            self.state['onboarding']=dict(state='waiting_hub',message='等待中枢公网连接',pending_count=0)
        self.hub.intent_control.configure({'enabled':False})
        self.hub.gnautilus_runtime.source_lost('public_relay_reconnected' if state['connected'] else 'public_relay_disconnected')
        self.tickets.clear()
        self.hub.event('公网转发'+('已认证，动作保持停止' if state['connected'] else '断开；记录与动作已停止'),'info' if state['connected'] else 'warn')
    def send(self,identity,kind,value,*,raw=False,**fields):
        peer=self.peers[identity];body=value if raw else encoded(value)
        return self.endpoint.send(kind,body,identity,peer['secret'],**fields)
    def tick(self):
        while not self.stop_event.wait(.04):
            if not self.endpoint.snapshot()['connected']:continue
            for identity,peer in self.peers.items():
                if peer['role']!='eeg':continue
                try:
                    ticket=secrets.token_hex(16);now=time.monotonic()
                    with self.lock:
                        self.tickets[(identity,ticket)]=now
                        self.tickets={k:v for k,v in self.tickets.items() if now-v<.5}
                    self.send(identity,'hub_status',dict(ticket=ticket,hub_state=self.hub.gnautilus_runtime.feedback(),hub_monotonic_ms=now*1000))
                except Exception as error:self.state['error']=str(error)[:180]
    def message(self,lane,h,body):
        try:return self.accept_message(lane,h,body)
        except (ValueError,OSError) as error:
            if h.get('kind')!='eeg_block':raise
            import hashlib,os
            root=Path(getattr(self.hub,'root',Path(__file__).resolve().parents[1]))/'data/gnautilus/rejected_relay_blocks';root.mkdir(parents=True,exist_ok=True)
            digest=hashlib.sha256(body).hexdigest();target=root/(digest+'.bin')
            if not target.exists():
                with target.open('xb') as f:f.write(body);f.flush();os.fsync(f.fileno())
            atomic_json(root/(digest+'.json'),dict(envelope=h,reason=str(error)[:240],sha256=digest,training_eligible=False,received_utc_ns=time.time_ns()))
            self.state['error']='拒收并隔离数据块：'+str(error)[:160]
            self.send(h['from'],'hub_reject',dict(error=str(error)[:240],rejected=True,sha256=digest),stream=h['stream'],epoch=h['epoch'],block_seq=h['block_seq'])
    def accept_message(self,lane,h,body):
        if h.get('notice'):return
        identity=h['from'];peer=self.peers[identity];kind=h['kind']
        if kind=='eeg_block' and peer['role']=='eeg':
            from gnautilus_acquisition.contract import validate_descriptor,validate_block,integer
            inner,raw=unpack(body)
            if not isinstance(inner,dict) or not isinstance(inner.get('block'),dict):raise ValueError('EEG descriptor/block must be objects')
            d=validate_descriptor(inner.get('descriptor'));b=inner['block'];integer(b.get('read_monotonic_ns'),0,2**63-1)
            validate_block(b,raw,d);source_mark=None
            with self.lock:issued=self.tickets.get((identity,b.get('ticket')))
            age=b.get('source_age_ms')
            if h.get('delivery')=='live' and lane=='data' and issued is not None and type(age) in (int,float) and math.isfinite(age) and age>=0:source_mark=issued-age/1000
            if (d['stream_id'],d['epoch'],b['seq'])!=(h['stream'],h['epoch'],h['block_seq']):raise ValueError('Envelope/sample identity mismatch')
            source_mark=self.source_mark(identity,b,source_mark)
            result=self.hub.gnautilus_runtime.service.ingest_external(identity,d,b,raw,source_mark)
            self.state['blocks']+=not result['duplicate'];self.state['backfill_blocks']+=not result['live_accepted']
            if result['live_accepted']:
                mapping=self.clock_maps.get(identity)
                if mapping and time.monotonic()*1000-mapping['measured']<15000:
                    read_end=b.get('read_completed_monotonic_ns',b['read_monotonic_ns'])/1e6+mapping['offset_ms']
                    read_span=max(0,(b.get('read_completed_monotonic_ns',b['read_monotonic_ns'])-b['read_monotonic_ns'])/1e6)
                    self.anchors.append(dict(stream=d['stream_id'],epoch=self.hub.epoch,start=b['sample_start'],end=b['sample_start']+b['sample_count'],rate=d['sampling_rate'],read_end_ms=read_end,uncertainty_ms=mapping['uncertainty_ms']+read_span+1000/d['sampling_rate']))
            ack_payload=dict(result,ack_seq=b['seq'])
            if h.get('delivery')=='live' or b['seq']%16==0:
                ack_payload['hub_state']=self.hub.gnautilus_runtime.feedback()
            self.send(identity,'hub_ack',ack_payload,stream=d['stream_id'],epoch=d['epoch'],block_seq=b['seq'])
        elif kind=='eeg_status' and peer['role']=='eeg':
            try:
                if len(body)>32768:raise ValueError('Client status too large')
                data=json.loads(body)
                if not isinstance(data,dict):raise ValueError('Client status must be an object')
            except (ValueError,TypeError):
                self.state['telemetry_error']='采集端状态格式无效，已忽略；数据连接保留';return
            service=self.hub.gnautilus_runtime.service
            with service.lock:service.status.update(agent_health={k:data.get(k) for k in ('sdk_state','disk_state','queue_blocks','error')})
            if 'client_status' in data:
                from gnautilus_acquisition.telemetry import sanitize
                try:
                    report=sanitize(data['client_status'])
                    with self.lock:self.client_reports[identity]=dict(reported=report,received=time.monotonic())
                except (ValueError,TypeError):self.state['telemetry_error']='采集端状态格式无效，已忽略；数据连接保留'
            if isinstance(data.get('capture_event'),dict):
                try:ack=self.hub.gnautilus_runtime.client_capture_event(identity,data['capture_event'])
                except Exception as error:ack=dict(ok=False,error=str(error)[:240])
                ticket=secrets.token_hex(16);now=time.monotonic()
                with self.lock:
                    self.tickets[(identity,ticket)]=now
                    self.tickets={k:v for k,v in self.tickets.items() if now-v<.5}
                self.send(identity,'hub_status',dict(ticket=ticket,hub_state=self.hub.gnautilus_runtime.feedback(),capture_ack=ack,hub_monotonic_ms=now*1000))
            if data.get('probe'):
                self.send(identity,'hub_status',dict(probe=data['probe'],hub_state=self.hub.gnautilus_runtime.feedback()))
        elif kind=='eeg_event' and peer['role']=='eeg':
            try:
                if len(body)>32768:raise ValueError('Client event too large')
                data=json.loads(body)
                if not isinstance(data,dict):raise ValueError('Client event must be an object')
                ack=self.hub.gnautilus_runtime.client_capture_event(identity,data)
            except Exception as error:
                ack=dict(ok=False,error=str(error)[:240])
            ticket=secrets.token_hex(16);now=time.monotonic()
            with self.lock:
                self.tickets[(identity,ticket)]=now
                self.tickets={k:v for k,v in self.tickets.items() if now-v<.5}
            self.send(identity,'hub_status',dict(ticket=ticket,hub_state=self.hub.gnautilus_runtime.feedback(),capture_ack=ack,hub_monotonic_ms=now*1000))
        elif kind=='rpc' and peer['role']=='quest':self.rpc(identity,h,body)
        elif kind=='vr_wire' and peer['role']=='quest':
            try:response=self.hub.vr.process_datagram(body,('relay:'+identity,0))
            except (ValueError,KeyError,TypeError,UnicodeError) as error:
                self.hub.vr.stats['invalid_wire']+=1;self.state['vr_last_rejection']=str(error)[:160];return
            if response:self.send(identity,'vr_wire',response,raw=True)
        elif kind=='preview' and peer['role']=='quest':self.hub.quest.dispatch(identity,'POST','/v1/mirror',body)
        elif kind=='quest_event' and peer['role']=='quest':
            path=self.hub.root/'data/gnautilus/relay_offline_events'/identity if hasattr(self.hub,'root') else Path(__file__).resolve().parents[1]/'data/gnautilus/relay_offline_events'/identity
            path.mkdir(parents=True,exist_ok=True);cache_id=':'.join([h['workspace'],identity,h['boot'],str(h['seq'])]);name=h['boot']+'-'+str(h['seq'])+'.json'
            event=json.loads(body);event_id=event.get('event_id','')
            if len(event_id)!=32 or any(c not in '0123456789abcdef' for c in event_id):raise ValueError('Invalid offline event identity')
            target=path/(event_id+'.json');record=dict(schema='eegmovement.offline_event.v1',event=event,received_utc_ns=time.time_ns(),online=False,source_association=None,label=None,reason='delayed_event_not_applied_to_live_labels')
            if target.exists():
                if encoded(json.loads(target.read_text('utf-8'))['event'])!=encoded(event):raise ValueError('Offline event changed')
            else:atomic_json(target,record);self.state['events_archived']+=1
            self.send(identity,'event_ack',dict(cache_id=cache_id,event_id=event_id,archived=True,applied_to_live=False))
        elif kind=='clock':
            data=json.loads(body);now=time.monotonic()*1000
            if data.get('phase')=='report':
                old=self.clock_pending.pop((identity,data.get('nonce')),None)
                if old and now-old['t2']<10000 and data.get('t1')==old['t1'] and data.get('t2')==old['t2']:
                    t0,t1,t2,t3=(data[k] for k in ('t0','t1','t2','t3'));rtt=(t3-t0)-(t2-t1)
                    if all(type(v) in (int,float) and math.isfinite(v) for v in (t0,t1,t2,t3)) and 0<=rtt<=2000:
                        candidate=dict(offset_ms=((t1-t0)+(t2-t3))/2,uncertainty_ms=rtt/2,measured=now)
                        previous=self.clock_maps.get(identity)
                        if not previous or now-previous['measured']>=15000 or candidate['uncertainty_ms']<previous['uncertainty_ms']+max(1.,(now-previous['measured'])*.0001):self.clock_maps[identity]=candidate
            else:
                nonce=secrets.token_hex(16);reply=dict(nonce=nonce,t0=data['t0'],t1=now,t2=time.monotonic()*1000)
                self.clock_pending[(identity,nonce)]=reply;self.clock_pending={k:v for k,v in self.clock_pending.items() if now-v['t2']<10000}
                self.send(identity,'clock_reply',reply)
        elif kind=='update_state':self.state.setdefault('device_updates',{})[identity]=json.loads(body)
        else:raise ValueError('Unsupported device role/message')
    def feedback(self):
        return dict(self.hub.gnautilus_runtime.feedback(),relay_timing_policy='authenticated_host_interval_v1')

    def source_mark(self,identity,block,ticket_mark):
        """Tighten a valid ticket bound using the authenticated clock interval.

        Both values bound the host read from below. The larger bound avoids
        charging an old status ticket's entire round trip to a newly read block.
        The existing 250ms freshness check still runs after the durable commit.
        """
        if ticket_mark is None:return None
        mapping=self.clock_maps.get(identity);read=block.get('read_completed_monotonic_ns',block.get('read_monotonic_ns'))
        if not mapping or type(read) is not int:return ticket_mark
        now=time.monotonic();age=now*1000-mapping['measured']
        if not 0<=age<15000:return ticket_mark
        # Ten milliseconds minimum plus a conservative 100ppm drift allowance.
        margin=max(10.,age*.0001)
        lower=(read/1e6+mapping['offset_ms']-mapping['uncertainty_ms']-margin)/1000
        if not math.isfinite(lower) or lower>now:return ticket_mark
        return max(ticket_mark,lower)

    def rpc(self,identity,h,body):
        data=json.loads(body);request_id=data.get('request_id','');key=identity,request_id
        if not isinstance(request_id,str) or len(request_id)!=32:raise ValueError('Invalid device request identity')
        if data.get('path') not in RPC_PATHS or data.get('method') not in ('GET','POST'):
            return self.send(identity,'rpc_reply',dict(request_id=request_id,status=409,error='不支持此设备操作；连接保持'))
        payload=base64.b64decode(data.get('body',''),validate=True)
        if len(payload)>65536:raise ValueError('Oversized device operation')
        previous=self.rpc_cache.get(key)
        digest=__import__('hashlib').sha256(body).hexdigest()
        if previous:
            if previous[0]!=digest:raise ValueError('Retried request changed')
            return self.send(identity,'rpc_reply',previous[1])
        try:
            result=self.hub.quest.dispatch(identity,data['method'],data['path'],payload)
            if data['path']=='/v1/pairing':result.update(transport='relay',host='106.55.251.83',port=443,explicit_host=True)
            reply=dict(request_id=request_id,status=200,body=base64.b64encode(encoded(result)).decode())
        except Exception as error:reply=dict(request_id=request_id,status=409,error=str(error)[:240])
        self.rpc_cache[key]=digest,reply
        while len(self.rpc_cache)>512:self.rpc_cache.popitem(last=False)
        self.send(identity,'rpc_reply',reply)
    def snapshot(self):
        with self.lock:
            clients=[dict(device_id=identity,role='eeg',age_ms=round((time.monotonic()-row['received'])*1000,1),
                status_fresh=bool((self.state.get('connected') or row.get('lan_ws')) and time.monotonic()-row['received']<3),reported=dict(row['reported'])) for identity,row in self.client_reports.items()]
        return dict(self.state,transport='outbound_wss',endpoint=self.config['url'],clock_maps=self.clock_maps,acquisition_clients=clients)
    def associate_event(self,row):
        from eeg_relay.timing import associate
        return associate(list(self.anchors),row.get('estimated_event_hub_ms'),row.get('sync_uncertainty_ms'),row.get('source_epoch'))
    def close(self):self.stop_event.set();self.endpoint.stop()
