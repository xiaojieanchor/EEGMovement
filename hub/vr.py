"""Authenticated UDP execution link. Unready EEG is never promoted to motion.

Receivers issue monotonic-clock tickets. Commands echo a ticket and expire relative
to its RECEIVER issue time, so delayed packets never receive a fresh lease on arrival.
"""
import base64,collections,hashlib,hmac,json,math,os,re,secrets,socket,threading,time,uuid
from pathlib import Path

MAX_PACKET=4096
HEX32=re.compile(r'^[0-9a-f]{32}$')

def encode_packet(packet,key):
    payload=json.dumps(packet,allow_nan=False,separators=(',',':')).encode()
    encoded=json.dumps({'payload':base64.b64encode(payload).decode(),'mac':hmac.new(key,payload,hashlib.sha256).hexdigest()},separators=(',',':')).encode()
    if len(encoded)>MAX_PACKET:raise ValueError('oversized packet')
    return encoded

def decode_packet(data,key):
    if not 0<len(data)<=MAX_PACKET:raise ValueError('oversized packet')
    def reject_constant(value):raise ValueError('nonfinite JSON')
    env=json.loads(data.decode('utf-8'),parse_constant=reject_constant)
    if not isinstance(env,dict) or set(env)!={'payload','mac'} or not isinstance(env['payload'],str) or not isinstance(env['mac'],str):raise ValueError('invalid envelope')
    raw=base64.b64decode(env['payload'],validate=True)
    if len(raw)>3000 or not hmac.compare_digest(env['mac'],hmac.new(key,raw,hashlib.sha256).hexdigest()):raise ValueError('invalid signature')
    packet=json.loads(raw.decode('utf-8'),parse_constant=reject_constant)
    if not isinstance(packet,dict) or type(packet.get('version')) is not int or packet['version']!=1:raise ValueError('unsupported packet')
    return packet

class VrLink:
    def __init__(self,root,provider,event,port=19771,bind_host='127.0.0.1'):
        self.root=Path(root);self.provider=provider;self.event=event;self.port=port;self.lock=threading.RLock();self.stop_event=threading.Event()
        self.key=secrets.token_bytes(32);self.session=uuid.uuid4().hex;self.sock=None;self.clients={};self.test=None;self.error=None;self.started=False;self.control_generation=secrets.randbits(48)
        self.bind_host=bind_host;self.lan_keys={};self.retired_nonces=set()
        self.stats=collections.Counter();self.ack_times=collections.deque(maxlen=600);self.fullchain_times=collections.deque(maxlen=600);self.last_test_change=0;self.quota_start=0;self.quota_count=0;self.peer_quotas={}
    def register_lan(self,identity,key):
        if not HEX32.fullmatch(str(identity)) or not isinstance(key,bytes) or len(key)!=32:raise ValueError('Enrolled identity and key required')
        with self.lock:
            if identity not in self.lan_keys and len(self.lan_keys)>=4:raise ValueError('Enrolled device limit reached')
            if any(other!=identity and hmac.compare_digest(other_key,key) for other,other_key in self.lan_keys.items()):raise ValueError('Enrolled keys must be unique')
            if identity in self.lan_keys and self.lan_keys[identity]!=key:self.unregister_lan(identity)
            self.lan_keys[identity]=key
    def unregister_lan(self,identity):
        with self.lock:
            self.lan_keys.pop(identity,None)
            for rid in [r for r,c in self.clients.items() if c.get('identity')==identity]:
                self.clients.pop(rid)
                if self.test and self.test['receiver_id']==rid:self.test=None;self.control_generation+=1
            self.retired_nonces={entry for entry in self.retired_nonces if entry[0]!=identity}
    def start(self):
        try:
            self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.sock.bind((self.bind_host,self.port));self.sock.settimeout(.2);self.port=self.sock.getsockname()[1]
            runtime=self.root/'hub/runtime';runtime.mkdir(parents=True,exist_ok=True)
            path=runtime/'vr_pairing.json';temp=runtime/('vr_pairing-'+secrets.token_hex(3)+'.tmp')
            temp.write_text(json.dumps({'version':1,'host':'127.0.0.1','port':self.port,'key':self.key.hex(),'hub_session':self.session}),encoding='utf-8');os.chmod(temp,0o600);os.replace(temp,path)
            self.started=True;threading.Thread(target=self.run,daemon=True,name='Unity execution link').start();self.event('执行链路监听127.0.0.1:'+str(self.port),'info')
        except OSError as e:self.error='执行链路端口不可用：'+type(e).__name__;self.event(self.error,'warn')
    def close(self):
        self.stop_event.set()
        if self.sock:self.sock.close()
    def rate_allows(self,address,now):
        # Unauthenticated traffic must not consume the heartbeat budget of an
        # already paired endpoint. Signature checks still run for every admitted packet.
        with self.lock:
            if now-self.quota_start>=1:self.quota_start=now;self.quota_count=0;self.peer_quotas.clear()
            known=any(c['address']==address for c in self.clients.values())
            if known:
                count=self.peer_quotas.get(address,0)+1;self.peer_quotas[address]=count
                return count<=80
            self.quota_count+=1
            return self.quota_count<=200
    @staticmethod
    def valid_telemetry(state):
        if not isinstance(state,dict) or state.get('state') not in ['STOP','MOVING'] or state.get('mode') not in ['LIVE','TEST']:raise ValueError('bad telemetry')
        numeric=['x','z','yaw','speed','yaw_rate','actual_speed','actual_yaw_rate','target_speed','target_yaw_rate','command_roundtrip_ms','command_age_ms','accepted','rejected','watchdog_stops','queue_drops','invalid_wire','received_seq','applied_seq','watchdog_brake_ms']
        result={}
        for name in numeric:
            if name in ['actual_speed','actual_yaw_rate'] and name not in state:
                result[name]=None;continue
            n=state.get(name,0)
            if type(n) not in [int,float] or not math.isfinite(n) or abs(n)>1e12:raise ValueError('invalid telemetry number')
            result[name]=n
        for name in ['role','mode','state','reason']:
            value=state.get(name,'')
            if not isinstance(value,str) or len(value)>120:raise ValueError('bad telemetry label')
            result[name]=value
        for name in ['app_version','build_id','ui_state']:
            if name in state:
                if not isinstance(state[name],str) or len(state[name])>120:raise ValueError('bad runtime label')
                result[name]=state[name]
        for name in ['frame_ms','frame_p95_ms']:
            if name in state:
                value=state[name]
                if type(value) not in [int,float] or not math.isfinite(value) or not 0<=value<=10000:raise ValueError('bad runtime timing')
                result[name]=value
        if 'rendered_frames' in state:
            if type(state['rendered_frames']) is not int or state['rendered_frames']<0:raise ValueError('bad frame count')
            result['rendered_frames']=state['rendered_frames']
        for name in ['focused','paused','xr_active']:
            if name in state:
                if type(state[name]) is not bool:raise ValueError('bad runtime flag')
                result[name]=state[name]
        result['paired']=state.get('paired') is True;result['direction']=state.get('direction',0)
        for name in ['motion_schema','linear_axis','turn_axis']:
            value=state.get(name,0)
            if type(value) is not int or (name=='motion_schema' and value not in [0,2,3]) or (name!='motion_schema' and value not in [-1,0,1]):raise ValueError('Invalid motion capability/axis')
            result[name]=value
        return result
    def run(self):
        while not self.stop_event.is_set():
            try:
                raw,address=self.sock.recvfrom(MAX_PACKET+1)
                response=self.process_datagram(raw,address)
                if response:self.sock.sendto(response,address)
            except socket.timeout:continue
            except OSError:
                if not self.stop_event.is_set():self.stats['socket_errors']+=1
            except (ValueError,KeyError,TypeError,UnicodeError):self.stats['invalid_wire']+=1
    def process_datagram(self,raw,address,readiness=None):
        """Authenticate identity before using the observed NAT endpoint for replies."""
        if not self.rate_allows(address,time.monotonic()):self.stats['rate_limited']+=1;return None
        with self.lock:
            candidates=list(self.lan_keys.items())
            if address[0]=='127.0.0.1':candidates.insert(0,('loopback',self.key))
        for identity,wire_key in candidates:
            try:p=decode_packet(raw,wire_key)
            except ValueError:continue
            # Authenticated reachability probe, without creating a receiver,
            # replacing its nonce, issuing a ticket or changing an action.
            if p.get('kind')=='route_probe':
                if not HEX32.fullmatch(str(p.get('nonce',''))):raise ValueError('Invalid route probe')
                return encode_packet(dict(version=1,kind='route_probe_reply',nonce=p['nonce'],session=self.session),wire_key)
            if readiness is None:readiness=self.provider() # No VrLink lock is held across Hub.lock.
            response=self.heartbeat(p,address,readiness,identity)
            return encode_packet(response,wire_key) if response else None
        raise ValueError('No enrolled signature matched')
    def heartbeat(self,p,address,readiness,identity='loopback'):
        if p.get('kind')!='heartbeat' or not HEX32.fullmatch(str(p.get('receiver_id',''))) or not HEX32.fullmatch(str(p.get('nonce',''))) or type(p.get('ticket')) is not int or p['ticket']<1 or p['ticket']>2**53:raise ValueError('invalid heartbeat')
        if p.get('role') not in ['unity','csharp_harness'] or type(p.get('allow_test')) is not bool:raise ValueError('invalid receiver')
        telemetry=self.valid_telemetry(p.get('telemetry'))
        now=time.monotonic();rid=p['receiver_id']
        with self.lock:
            peer=self.clients.get(rid)
            if peer and peer.get('identity','loopback')!=identity:raise ValueError('Receiver belongs to another identity')
            if (identity,rid,p['nonce']) in self.retired_nonces:raise ValueError('Retired handshake replay')
            if peer is None or peer['nonce']!=p['nonce']:
                if peer is None and identity!='loopback':
                    owned=[(r,c) for r,c in self.clients.items() if c.get('identity')==identity]
                    if any(now-c['last_seen']<.3 for _,c in owned):raise ValueError('Enrolled identity already has a live receiver')
                    for old_rid,old_peer in owned:
                        if len(self.retired_nonces)>=4096:raise ValueError('Handshake history full; re-enroll identity')
                        self.retired_nonces.add((identity,old_rid,old_peer['nonce']))
                        del self.clients[old_rid]
                        if self.test and self.test['receiver_id']==old_rid:self.test=None;self.control_generation+=1
                if peer:
                    if p.get('session'):raise ValueError('New handshake must start without a session')
                    if len(self.retired_nonces)>=4096:raise ValueError('Handshake history full; re-enroll identity')
                    self.retired_nonces.add((identity,rid,peer['nonce']))
                    if self.test and self.test['receiver_id']==rid:self.test=None;self.control_generation+=1
                if peer is None and len(self.clients)>=4:
                    stale=[k for k,c in self.clients.items() if now-c['last_seen']>10]
                    for k in stale:
                        old=self.clients[k]
                        if len(self.retired_nonces)>=4096:raise ValueError('Handshake history full; re-enroll identity')
                        self.retired_nonces.add((old.get('identity','loopback'),k,old['nonce']));del self.clients[k]
                    if len(self.clients)>=4:raise ValueError('receiver capacity reached')
                peer=dict(receiver_id=rid,identity=identity,nonce=p['nonce'],role=p['role'],allow_test=p['allow_test'],address=address,last_ticket=0,seq=0,last_seen=now,applied_seq=0,telemetry={},sent=collections.OrderedDict(),ack_ms=None,command=None)
                self.clients[rid]=peer
            if p['role']!=peer['role'] or p['allow_test']!=peer['allow_test'] or p['ticket']<=peer['last_ticket']:
                self.stats['duplicate_or_foreign_heartbeat']+=1;return None
            if address!=peer['address']:
                if identity=='loopback':self.stats['duplicate_or_foreign_heartbeat']+=1;return None
                if self.test and self.test['receiver_id']==rid:self.test=None;self.control_generation+=1
                peer['command']=dict(active=False,reason='network_rebind_required',mode='LIVE')
                self.stats['nat_rebind_requested']+=1
                return dict(version=1,kind='rebind',receiver_id=rid,nonce=p['nonce'],session=self.session,ticket=p['ticket'])
            base=dict(version=1,receiver_id=rid,nonce=p['nonce'],session=self.session,ticket=p['ticket'])
            if p.get('session')!=self.session:
                # A receiver can outlive a Hub restart. Its old applied_seq belongs
                # to the previous session, never to this Hub's new sequence space.
                # Authenticate/replay-check the handshake, then let WELCOME reset
                # the receiver before accepting any acknowledgement or telemetry.
                peer['last_ticket']=p['ticket'];peer['last_seen']=now
                peer['telemetry']={};peer['command']=None;peer['ack_ms']=None
                if self.test and self.test['receiver_id']==rid:self.test=None;self.control_generation+=1
                self.stats['session_handshakes']+=1
                return dict(base,kind='welcome')
            lease_gap=now-peer['last_seen']>=.18 and self.test and self.test['receiver_id']==rid
            if self.test and (readiness.get('acquisition_active') or not readiness['server_fresh'] or not readiness.get('source_fresh',readiness['server_fresh']) or lease_gap):
                self.test=None;self.control_generation+=1;self.stats['tests_revoked_on_stale_link']+=1
            ack=telemetry['applied_seq']
            if type(ack) is not int or ack<peer['applied_seq'] or ack>peer['seq']:raise ValueError('invalid applied acknowledgement')
            peer['last_ticket']=p['ticket'];peer['last_seen']=now
            if ack>peer['applied_seq']:
                sent=peer['sent'].get(ack)
                if sent:
                    elapsed=(now-sent['sent'])*1000;peer['ack_ms']=round(elapsed,3);self.ack_times.append(elapsed)
                    if sent['origin'] is not None and sent['mode']=='LIVE':self.fullchain_times.append((now-sent['origin'])*1000)
                peer['applied_seq']=ack
                for sequence in [n for n in peer['sent'] if n<=ack]:del peer['sent'][sequence]
            peer['telemetry']=telemetry
            test=self.test if self.test and now<self.test['until'] and self.test['receiver_id']==rid else None
            if self.test and now>=self.test['until']:self.test=None;self.control_generation+=1
            peer['seq']+=1
            command=dict(base,kind='command',seq=peer['seq'],decision_id=readiness.get('decision_id',0),epoch=readiness['epoch'],ttl_ms=180,mode='LIVE',active=False,input_valid=readiness['source_valid'],model_ready=False,direction=0,p_active=0.0,confidence=0.0,speed=0.0,yaw_rate=0.0,reason=readiness['reason'])
            if test and peer['allow_test']:
                valid=bool(test['input_valid'] and readiness['server_fresh'])
                command.update(mode='TEST',decision_id=p['ticket'],active=test['direction']!=0,input_valid=valid,model_ready=True,direction=test['direction'],p_active=test['p_active'],confidence=test['confidence'],speed=test['speed'],yaw_rate=40.0,reason='test_fixture' if valid else 'test_invalid_or_server_stale')
            elif not readiness.get('acquisition_active') and readiness.get('model_fresh'):
                decision=readiness.get('model_decision') or {}
                # One Unity receiver executes the model. Prefer the paired LAN
                # headset over a concurrently open desktop preview.
                candidates=[c for c in self.clients.values() if c['role']=='unity' and now-c['last_seen']<.3]
                candidates.sort(key=lambda c:(c['address'][0]=='127.0.0.1',c['receiver_id']))
                bound=getattr(self,'model_receiver_id',None)
                target=bound if bound is not None else candidates[0]['receiver_id'] if candidates else None
                compatible=decision.get('motion_schema')!=2 or telemetry.get('motion_schema') in [2,3]
                if decision.get('output_schema')==2:compatible=telemetry.get('motion_schema')==3
                if rid==target and decision.get('enabled') and compatible:
                    command.update(model_ready=bool(decision.get('model_ready')),active=bool(decision.get('active')),input_valid=readiness['source_valid'],
                                   direction=decision.get('direction',0),p_active=decision.get('p_active',0.),confidence=decision.get('confidence',0.),
                                   speed=decision.get('speed',0.),yaw_rate=decision.get('yaw_rate',0.),enter_active_threshold=decision.get('active_threshold',.8),enter_direction_threshold=decision.get('direction_threshold',.7),reason=decision.get('reason','model_stopped'))
                    if decision.get('motion_schema')==2:
                        for name in ['motion_schema','linear_axis','turn_axis','linear_confidence','turn_confidence','linear_mass','turn_mass','linear_strength','turn_strength','axis_mass_threshold']:command[name]=decision.get(name,0)
                        if telemetry.get('motion_schema')==3 and decision.get('decision_owner')=='hub':command['motion_schema']=3
                elif rid==target and not compatible:command['reason']='receiver_update_required'
            peer['command']={k:v for k,v in command.items() if k not in ['nonce','session','receiver_id']}
            peer['sent'][peer['seq']]={'sent':now,'origin':readiness.get('input_origin') if command['mode']=='LIVE' else None,'mode':command['mode']}
            while len(peer['sent'])>128:peer['sent'].popitem(last=False)
            self.stats['commands_sent']+=1;return command
    def set_test(self,data):
        if (self.provider() or {}).get('acquisition_active'):raise ValueError('先结束VR引导采集，再运行合成动作TEST')
        direction={'STOP':0,'FORWARD':1,'BACKWARD':2,'LEFT':3,'RIGHT':4}.get(data.get('action'))
        if direction is None:raise ValueError('请选择前进、后退、左转、右转或停止')
        duration=float(data.get('duration_s',2));speed=float(data.get('speed',.6));pa=float(data.get('p_active',.95));confidence=float(data.get('confidence',.95))
        if not all(math.isfinite(n) for n in [duration,speed,pa,confidence]) or not 0<duration<=10 or not 0<=speed<=1.2 or not 0<=pa<=1 or not 0<=confidence<=1:raise ValueError('测试参数越界')
        rid=data.get('receiver_id')
        with self.lock:
            if type(data.get('control_generation')) is not int or data['control_generation']!=self.control_generation:
                raise ValueError('控制状态已更新，请刷新接收端状态后重试；迟到的TEST已拒绝')
            active=[c for c in self.clients.values() if c['allow_test'] and time.monotonic()-c['last_seen']<.3]
            peer=next((c for c in active if c['receiver_id']==rid),None) if rid else (active[0] if len(active)==1 else None)
            if peer is None:raise ValueError('需要恰好一个已允许TEST的在线接收器，或指定receiver_id')
            self.test=dict(receiver_id=peer['receiver_id'],direction=direction,until=time.monotonic()+duration,speed=speed,p_active=pa,confidence=confidence,input_valid=data.get('input_valid',True) is True)
        self.event('链路TEST：'+data['action']+'，不是EEG解码','info')
    def stop_test(self):
        with self.lock:self.test=None;self.control_generation+=1
        self.event('合成TEST停止；模型执行状态请查看意图面板','info')
    def snapshot(self):
        def p95(values):
            if not values:return None
            a=sorted(values);return round(a[min(len(a)-1,math.ceil(len(a)*.95)-1)],3)
        now=time.monotonic()
        with self.lock:
            clients=[]
            for c in self.clients.values():
                age=(now-c['last_seen'])*1000
                transport='public_relay' if c['address'][0].startswith('relay:') else 'loopback' if c['address'][0]=='127.0.0.1' else 'lan'
                clients.append(dict(receiver_id=c['receiver_id'],device_id=c.get('identity','loopback'),observed_endpoint=c['address'][0]+':'+str(c['address'][1]),transport=transport,transport_label={'public_relay':'公网中继','loopback':'内网 · 本机','lan':'内网直连'}[transport],role=c['role'],allow_test=c['allow_test'],online=age<300,telemetry_age_ms=round(age,1),sent_seq=c['seq'],applied_seq=c['applied_seq'],command=c['command'],telemetry=c['telemetry'],command_to_ack_ms=c['ack_ms']))
            online=[c for c in clients if c['online']]
            peer=next((c for c in online if c['role']=='unity'),next((c for c in online if c['role']=='csharp_harness'),None))
            telemetry=peer['telemetry'] if peer else {}
            execution=dict(status='ok' if peer else 'offline',source=peer['role'] if peer else None,
                           telemetry_age_ms=peer['telemetry_age_ms'] if peer else None,
                           **{k:telemetry.get(k) for k in ['actual_speed','actual_yaw_rate','state','mode']})
            return dict(ready=self.started,error=self.error,endpoint='127.0.0.1:'+str(self.port),protocol=1,ttl_ms=180,control_generation=self.control_generation,clients=clients,execution=execution,test_active=bool(self.test and now<self.test['until']),test_remaining_s=round(max(0,self.test['until']-now),2) if self.test else 0,stats=dict(self.stats),command_to_ack_p95_ms=p95(self.ack_times),input_to_execution_ack_p95_ms=p95(self.fullchain_times),metric_note='ACK includes receiver application plus next heartbeat wait; conservative upper bound, not exact render latency',unity_engine_verified=None,model_control_enabled=False)

