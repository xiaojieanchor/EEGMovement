"""Authenticated, outbound-client TLS ingest independent of the retired EEG protocol."""
import datetime
import hashlib
import hmac
import ipaddress
import json
import math
import os
import secrets
import socket
import ssl
import threading
import time
from pathlib import Path
from gnautilus_acquisition.contract import json_bytes,validate_descriptor,validate_block,send_frame,recv_frame,HEX32
from gnautilus_acquisition.storage import StreamJournal,atomic_json


def auth_mac(secret,challenge,agent,boot):
    return hmac.new(bytes.fromhex(secret),json_bytes([challenge,agent,boot]),hashlib.sha256).hexdigest()


def provision(root,host,port=8767,agent_id=None):
    """Local operator provisioning. Returns credentials only to the local caller."""
    ip=ipaddress.IPv4Address(host)
    if ip.is_unspecified or ip.is_multicast:raise ValueError('Explicit unicast hub address required')
    base=Path(root)/'hub/runtime/gnautilus';base.mkdir(parents=True,exist_ok=True)
    cert,key=base/'server.crt',base/'server.key'
    if not cert.exists() or not key.exists():
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        private=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'EEGMovement local ingest')])
        now=datetime.datetime.now(datetime.timezone.utc)
        certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1825))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]),critical=False).sign(private,hashes.SHA256()))
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));os.chmod(key,0o600)
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    agents=base/'agents.json';records=json.loads(agents.read_text('utf-8')) if agents.exists() else {}
    agent_id=agent_id or next(iter(records),None) or secrets.token_hex(16)
    if not HEX32.fullmatch(agent_id):raise ValueError('Invalid agent identity')
    if agent_id not in records and len(records)>=4:raise ValueError('Four enrolled agents maximum')
    secret=records.setdefault(agent_id,secrets.token_hex(32));atomic_json(agents,records);os.chmod(agents,0o600)
    digest=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text('ascii'))).hexdigest()
    enrollment=dict(schema='gnautilus.enrollment.v1',host=str(ip),port=port,agent_id=agent_id,secret=secret,certificate_sha256=digest)
    output=base/'enrollments'/f'{agent_id}.json';atomic_json(output,enrollment);os.chmod(output,0o600)
    return enrollment


class IngestService:
    def __init__(self,root,on_block,*,on_state=None,on_descriptor=None,on_capture_event=None,get_hub_state=None,host='0.0.0.0',port=8767,freshness_ms=250):
        self.root=Path(root);self.base=self.root/'hub/runtime/gnautilus';self.host,self.port=host,port
        self.on_block,self.on_state,self.on_descriptor=on_block,on_state or (lambda s:None),on_descriptor or (lambda d:None)
        self.on_capture_event=on_capture_event or (lambda agent,event:dict(ok=False,error='Capture event handler not configured'))
        self.get_hub_state=get_hub_state or (lambda:dict(model_state='not_configured',recording_active=False,label=None,phase='idle',vr_online=0))
        self.freshness_ms=freshness_ms;self.lock=threading.RLock();self.stop=threading.Event();self.journals={};self.connections={};self.clients=set();self.threads=[]
        self.selected_agent=None;self.status=dict(enabled=False,connected=False,state='not_provisioned',error=None,received_blocks=0,durable_blocks=0,backfill_blocks=0,expired_blocks=0)
        self.last_live=None;self.live_cursors={};self.slots=threading.BoundedSemaphore(8);self.sock=None

    def start(self):
        if not all((self.base/n).exists() for n in ['server.crt','server.key','agents.json']):return
        self.context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);self.context.minimum_version=ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(str(self.base/'server.crt'),str(self.base/'server.key'))
        self.sock=socket.socket();self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);self.sock.bind((self.host,self.port));self.port=self.sock.getsockname()[1]
        self.sock.listen(8);self.sock.settimeout(.2);self.status.update(enabled=True,state='waiting_agent',port=self.port)
        self.thread=threading.Thread(target=self._accept,daemon=True,name='g.Nautilus TLS ingest');self.thread.start()

    def _accept(self):
        while not self.stop.is_set():
            try:raw,address=self.sock.accept()
            except socket.timeout:continue
            except OSError:break
            if not self.slots.acquire(False):raw.close();continue
            t=threading.Thread(target=self._handle,args=(raw,address),daemon=True);self.threads.append(t);t.start()

    def _handle(self,raw,address):
        agent=None;generation=secrets.token_hex(16);s=None;descriptor=None;authenticated=False
        try:
            raw.settimeout(3);raw.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            s=self.context.wrap_socket(raw,server_side=True)
            with self.lock:self.clients.add(s)
            challenge=secrets.token_hex(32);send_frame(s,dict(kind='challenge',challenge=challenge,schema='gnautilus.ingest.v1'))
            hello,body=recv_frame(s);agent=hello.get('agent_id');boot=hello.get('boot_id')
            records=json.loads((self.base/'agents.json').read_text('utf-8'))
            if body or hello.get('kind')!='auth' or agent not in records or not HEX32.fullmatch(str(boot or '')):raise ValueError('Pairing identity rejected')
            if not hmac.compare_digest(str(hello.get('mac','')),auth_mac(records[agent],challenge,agent,boot)):raise ValueError('Pairing signature rejected')
            with self.lock:
                if agent in self.connections:raise ValueError('Agent already connected')
                self.connections[agent]=generation
                authenticated=True
                if self.selected_agent is None:self.selected_agent=agent
                self.status.update(connected=True,state='authenticated',error=None,agent_id=agent)
            send_frame(s,dict(kind='auth_ok'));s.settimeout(5)
            ticket=secrets.token_hex(16);issued=time.monotonic()
            while not self.stop.is_set():
                h,body=recv_frame(s);kind=h.get('kind')
                if kind=='descriptor':
                    if body:raise ValueError('Descriptor body unexpected')
                    d=validate_descriptor(h.get('descriptor'))
                    with self.lock:
                        identity=(agent,d['stream_id'])
                        j=self.journals.get(identity)
                        if j is None:
                            data_root=self.root/'data/gnautilus'
                            existing=list(data_root.glob(f'*/streams/{agent}/{d["stream_id"]}/descriptor.json'))
                            if len(existing)>1:raise ValueError('Multiple dated journals for one stream identity; manual audit required')
                            path=existing[0].parent if existing else data_root/datetime.date.today().isoformat()/'streams'/agent/d['stream_id']
                            if not path.resolve().is_relative_to(data_root.resolve()):raise ValueError('Journal path escapes data root')
                            j=StreamJournal(path,d);self.journals[identity]=j
                        elif json_bytes(j.descriptor)!=json_bytes(d):raise ValueError('Stream descriptor changed')
                        descriptor=d;self.status.update(descriptor=d,state='waiting_samples')
                    if agent==self.selected_agent:self.on_descriptor(d)
                    ticket=secrets.token_hex(16);issued=time.monotonic()
                    send_frame(s,dict(kind='descriptor_ok',durable_seq=j.durable_seq,ticket=ticket,hub_state=self.get_hub_state()))
                elif kind=='block':
                    if descriptor is None:raise ValueError('Descriptor must precede samples')
                    x=validate_block(h,body,descriptor)
                    source_age=h.get('source_age_ms',float('inf'))
                    result=j.append(h,body)
                    # fsync can outlive the freshness budget. Judge only after
                    # the durable commit, then carry the remaining age into the
                    # runtime rather than restarting a full 250ms lease.
                    now=time.monotonic();elapsed=(now-issued)*1000
                    live=(h['mode']=='live' and h.get('ticket')==ticket and type(source_age) in [int,float]
                          and math.isfinite(source_age) and 0<=source_age and source_age+elapsed<self.freshness_ms)
                    with self.lock:
                        self.status['received_blocks']+=1;self.status['durable_blocks']+=not result['duplicate']
                        self.status['backfill_blocks']+=h['mode']=='backfill';self.status['expired_blocks']+=h['mode']=='live' and not live
                    with self.lock:
                        cursor=self.live_cursors.get((agent,descriptor['stream_id']),(-1,-1))
                        accepted=bool(live and not result['duplicate'] and agent==self.selected_agent and h['seq']>cursor[0] and h['sample_start']>=cursor[1])
                        if accepted:self.live_cursors[(agent,descriptor['stream_id'])]=(h['seq'],h['sample_start']+h['sample_count'])
                    if accepted:
                        source_mark=issued-source_age/1000
                        self.on_block(descriptor,dict(h,hub_source_monotonic_ns=int(source_mark*1e9)),x);self.last_live=source_mark
                        with self.lock:self.status.update(state='receiving',last_seq=h['seq'],last_sample_end=h['sample_start']+h['sample_count'])
                    ticket=secrets.token_hex(16);issued=time.monotonic()
                    send_frame(s,dict(kind='ack',ack_seq=h['seq'],live_accepted=accepted,ticket=ticket,hub_state=self.get_hub_state(),**result))
                elif kind=='status':
                    if body:raise ValueError('Status body unexpected')
                    # Bound the sender's health object; credentials never enter status.
                    safe={k:h.get(k) for k in ['sdk_state','queue_blocks','disk_state','error','stream_id']}
                    with self.lock:self.status['agent_health']=safe
                    ticket=secrets.token_hex(16);issued=time.monotonic();send_frame(s,dict(kind='status_ok',ticket=ticket,hub_state=self.get_hub_state()))
                elif kind=='capture_event':
                    if body:raise ValueError('Capture event body unexpected')
                    ack=self.on_capture_event(agent,h.get('event') or {})
                    ticket=secrets.token_hex(16);issued=time.monotonic()
                    send_frame(s,dict(kind='capture_ack',ticket=ticket,hub_state=self.get_hub_state(),capture_ack=ack))
                elif kind=='close':
                    send_frame(s,dict(kind='closed',durable_seq=j.durable_seq if descriptor else 0));break
                else:raise ValueError('Unsupported message')
        except (OSError,EOFError,ValueError,KeyError,TypeError) as error:
            with self.lock:self.status.update(error=f'{type(error).__name__}: {error}'[:240])
        finally:
            with self.lock:
                released=bool(authenticated and self.connections.get(agent)==generation)
                if released:self.connections.pop(agent,None)
                self.status['connected']=bool(self.connections)
                if not self.status['connected']:self.status['state']='disconnected'
                if s:self.clients.discard(s)
            if released and agent==self.selected_agent:
                try:self.on_state(dict(state='disconnected',agent_id=agent))
                except Exception as error:
                    with self.lock:self.status['error']='State callback failed: '+str(error)[:180]
            try:(s or raw).close()
            except OSError:pass
            self.slots.release()

    def ingest_external(self,agent,d,h,body,source_mark=None):
        """Same durable journal for outbound relay; only fresh ticketed data may publish."""
        d=validate_descriptor(d);x=validate_block(h,body,d)
        if not HEX32.fullmatch(str(agent)):raise ValueError('Invalid agent identity')
        with self.lock:
            identity=agent,d['stream_id'];j=self.journals.get(identity)
            if j is None:
                data_root=self.root/'data/gnautilus';existing=list(data_root.glob(f'*/streams/{agent}/{d["stream_id"]}/descriptor.json'))
                if len(existing)>1:raise ValueError('Multiple dated journals for one stream')
                path=existing[0].parent if existing else data_root/datetime.date.today().isoformat()/'streams'/agent/d['stream_id']
                j=StreamJournal(path,d);self.journals[identity]=j
            if json_bytes(j.descriptor)!=json_bytes(d):raise ValueError('Stream descriptor changed')
            if self.selected_agent is None:self.selected_agent=agent
        result=j.append(h,body);now=time.monotonic()
        live=source_mark is not None and 0<=now-source_mark<self.freshness_ms/1000 and h['mode']=='live'
        with self.lock:
            cursor=self.live_cursors.get(identity,(-1,-1))
            # A durable backfill may race the data lane. Disk deduplication must
            # not consume the once-only opportunity to publish its fresh copy.
            accepted=bool(live and agent==self.selected_agent and h['seq']>cursor[0] and h['sample_start']>=cursor[1])
            self.status['received_blocks']+=1;self.status['durable_blocks']+=not result['duplicate'];self.status['backfill_blocks']+=not live
            self.status['expired_blocks']+=h['mode']=='live' and not live
            if accepted:self.live_cursors[identity]=h['seq'],h['sample_start']+h['sample_count']
        if accepted:
            self.on_descriptor(d)
            self.on_block(d,dict(h,hub_source_monotonic_ns=int(source_mark*1e9)),x)
            with self.lock:
                self.last_live=source_mark;self.status.update(enabled=True,connected=True,state='receiving',descriptor=d,last_seq=h['seq'],last_sample_end=h['sample_start']+h['sample_count'],agent_id=agent,transport='public_relay')
        return dict(result,live_accepted=accepted)

    def snapshot(self):
        with self.lock:
            state=json.loads(json.dumps(self.status));age=(time.monotonic()-self.last_live)*1000 if self.last_live else None
            state.update(fresh=bool(state['connected'] and age is not None and age<self.freshness_ms),age_ms=round(age,2) if age is not None else None,
                         freshness_ms=self.freshness_ms,contact_quality_known=False,model_available=False,hardware_verified=False)
            return state

    def close(self):
        self.stop.set()
        if self.sock:self.sock.close()
        with self.lock:clients=list(self.clients)
        for s in clients:
            try:s.shutdown(socket.SHUT_RDWR);s.close()
            except OSError:pass
        if hasattr(self,'thread'):self.thread.join(1)
        for t in self.threads:t.join(1)
        for j in self.journals.values():
            try:j.close()
            except OSError as error:self.status['error']='Journal close failed: '+str(error)[:180]
