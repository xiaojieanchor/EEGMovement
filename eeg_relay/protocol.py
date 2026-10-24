"""Bounded binary envelopes, application signatures and explicit relay roles."""
import hashlib,hmac,json,re,struct

SCHEMA='eegmovement.relay.v1'
MAX_HEADER=65536
MAX_BODY=4*1024*1024
HEX32=re.compile(r'^[0-9a-f]{32}$')
HEX64=re.compile(r'^[0-9a-f]{64}$')
KINDS={'eeg_block','eeg_status','hub_ack','hub_reject','hub_status','rpc','rpc_reply','vr_wire','quest_event','event_ack','preview','clock','clock_reply','update_state'}
FIELDS=('workspace','from','to','boot','seq','kind','stream','epoch','block_seq','digest')

def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def pack(header,body=b''):
    raw=encoded(header)
    if not isinstance(body,bytes) or len(body)>MAX_BODY or len(raw)>MAX_HEADER:raise ValueError('Frame bounds exceeded')
    return struct.pack('!I',len(raw))+raw+body

def unpack(raw):
    if not isinstance(raw,bytes) or not 6<=len(raw)<=MAX_HEADER+MAX_BODY+4:raise ValueError('Invalid frame')
    size=struct.unpack('!I',raw[:4])[0]
    if not 2<=size<=MAX_HEADER or 4+size>len(raw):raise ValueError('Invalid header length')
    def invalid(_):raise ValueError('Nonfinite JSON')
    h=json.loads(raw[4:4+size].decode('utf-8'),parse_constant=invalid)
    if not isinstance(h,dict) or len(raw)-4-size>MAX_BODY:raise ValueError('Invalid header/body')
    return h,raw[4+size:]

def validate(h):
    if h.get('schema')!=SCHEMA or h.get('kind') not in KINDS:raise ValueError('Unknown relay protocol/kind')
    if any(not HEX32.fullmatch(str(h.get(k,''))) for k in ('workspace','from','to','boot')):raise ValueError('Invalid routing identity')
    if h.get('stream') and not HEX32.fullmatch(str(h['stream'])):raise ValueError('Invalid stream identity')
    for k,low in [('seq',1),('epoch',0),('block_seq',0)]:
        if type(h.get(k)) is not int or not low<=h[k]<=2**53:raise ValueError('Invalid sequence')
    if not HEX64.fullmatch(str(h.get('digest',''))) or not HEX64.fullmatch(str(h.get('mac',''))):raise ValueError('Invalid digest/signature')

def signature(h,secret):
    value='\n'.join(str(h[k]) for k in FIELDS).encode('utf-8')
    return hmac.new(bytes.fromhex(secret),value,hashlib.sha256).hexdigest()

def seal(workspace,source,target,boot,seq,kind,body,secret,*,stream='',epoch=0,block_seq=0):
    if len(bytes.fromhex(secret))!=32:raise ValueError('Invalid end-to-end key')
    h=dict(schema=SCHEMA,workspace=workspace,**{'from':source},to=target,boot=boot,seq=seq,kind=kind,stream=stream,epoch=epoch,block_seq=block_seq,digest=hashlib.sha256(body).hexdigest())
    h['mac']=signature(h,secret);validate(h);return h

def verify(h,body,secret):
    try:
        validate(h)
        return hmac.compare_digest(h['digest'],hashlib.sha256(body).hexdigest()) and hmac.compare_digest(h['mac'],signature(h,secret))
    except (ValueError,KeyError,TypeError):return False

def authentication(secret,challenge,workspace,device,boot,lane):
    if lane not in ('control','data','backfill','preview'):raise ValueError('Invalid lane')
    return hmac.new(bytes.fromhex(secret),'\n'.join([SCHEMA,challenge,workspace,device,boot,lane]).encode(),hashlib.sha256).hexdigest()

def stable_digest(h,body):
    if h['kind']=='eeg_block':
        try:
            inner,raw=unpack(body);block=dict(inner['block'])
            for k in ('mode','ticket','source_age_ms'):block.pop(k,None)
            return hashlib.sha256(pack(dict(descriptor=inner['descriptor'],block=block),raw)).hexdigest()
        except (ValueError,KeyError,TypeError):pass
    return hashlib.sha256(body).hexdigest()
