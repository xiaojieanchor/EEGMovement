"""Bounded versioned frames; device samples remain unscaled float32."""
import copy
import hashlib
import json
import math
import re
import struct
import time
import numpy as np

MAX_FRAME=4*1024*1024
MAX_JSON=64*1024
HEX32=re.compile(r'^[0-9a-f]{32}$')
KINDS={'EEG':'uV','ACC':'g','COUNTER':'count','LINK':'%','BATTERY':'%','DIGITAL':'bits','VALIDATION':'bool'}


def json_bytes(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def fingerprint(value):return hashlib.sha256(json_bytes(value)).hexdigest()


def integer(value,low=0,high=2**53):
    if type(value) is not int or not low<=value<=high:raise ValueError('Invalid integer identity/range')
    return value


def validate_descriptor(value):
    if not isinstance(value,dict):raise ValueError('Stream descriptor must be an object')
    d=copy.deepcopy(value)
    if d.get('schema')!='gnautilus.stream.v1' or not HEX32.fullmatch(str(d.get('stream_id',''))):raise ValueError('Invalid stream identity')
    integer(d.get('epoch'))
    if d.get('source_kind') not in ['gtec_live','synthetic']:raise ValueError('Unknown provenance')
    if not isinstance(d.get('device_serial'),str) or not 1<=len(d['device_serial'])<=120:raise ValueError('Device serial is required')
    rate=d.get('sampling_rate')
    if type(rate) not in [int,float] or not math.isfinite(rate) or not 1<=rate<=10000:raise ValueError('Invalid sampling rate')
    channels=d.get('channels')
    if not isinstance(channels,list) or not 1<=len(channels)<=80:raise ValueError('Invalid column count')
    ids=set()
    for ch in channels:
        if not isinstance(ch,dict) or ch.get('kind') not in KINDS or ch.get('unit')!=KINDS[ch['kind']]:raise ValueError('Column kind/unit mismatch')
        for key in ['id','name']:
            if not isinstance(ch.get(key),str) or not 1<=len(ch[key])<=120:raise ValueError('Column identity is required')
        if ch['id'] in ids:raise ValueError('Duplicate column identity')
        ids.add(ch['id'])
        if ch.get('physical_index') is not None:integer(ch['physical_index'],0,255)
    if not 1<=sum(c['kind']=='EEG' for c in channels)<=64:raise ValueError('Invalid EEG count')
    if not isinstance(d.get('configuration'),dict) or d.get('configuration_sha256')!=fingerprint(d['configuration']):raise ValueError('Configuration identity mismatch')
    if type(d.get('layout_verified',False)) is not bool:raise ValueError('Invalid layout verification')
    d.setdefault('layout_verified',False)
    for key in ['reference','ground','wearing_id']:d.setdefault(key,None)
    d.setdefault('role','unassigned')
    if len(json_bytes(d))>MAX_JSON:raise ValueError('Descriptor too large')
    return d


def block_frame(descriptor,seq,sample_start,samples,mode='live',**timing):
    integer(seq,1);integer(sample_start)
    a=np.asarray(samples,dtype='<f4',order='C')
    if a.ndim!=2 or a.shape[1]!=len(descriptor['channels']) or not 1<=len(a)<=10000:raise ValueError('Samples do not match declared columns')
    if mode not in ['live','backfill']:raise ValueError('Invalid delivery mode')
    raw=a.tobytes(order='C')
    h=dict(kind='block',stream_id=descriptor['stream_id'],epoch=descriptor['epoch'],seq=seq,sample_start=sample_start,
           sample_count=len(a),columns=a.shape[1],dtype='<f4',mode=mode,sha256=hashlib.sha256(raw).hexdigest(),
           read_monotonic_ns=timing.pop('read_monotonic_ns',time.monotonic_ns()),utc_ns=timing.pop('utc_ns',time.time_ns()))
    h.update(timing)
    validate_block(h,raw,descriptor)
    return h,raw


def validate_block(h,raw,d):
    if h.get('kind')!='block' or h.get('stream_id')!=d['stream_id'] or h.get('epoch')!=d['epoch']:raise ValueError('Block stream/epoch mismatch')
    integer(h.get('seq'),1);integer(h.get('sample_start'));integer(h.get('sample_count'),1,10000)
    if h.get('columns')!=len(d['channels']) or h.get('dtype')!='<f4' or h.get('mode') not in ['live','backfill']:raise ValueError('Invalid sample layout')
    if len(raw)!=h['sample_count']*h['columns']*4 or hashlib.sha256(raw).hexdigest()!=h.get('sha256'):raise ValueError('Invalid sample bytes/hash')
    for key in ['read_monotonic_ns','utc_ns','read_completed_monotonic_ns','read_completed_utc_ns']:
        if key in h:integer(h[key],0,2**63-1)
    if 'read_completed_monotonic_ns' in h and h['read_completed_monotonic_ns']<h['read_monotonic_ns']:raise ValueError('Invalid host read interval')
    return np.frombuffer(raw,dtype='<f4').reshape(h['sample_count'],h['columns'])


def send_frame(sock,header,body=b''):
    encoded=json_bytes(header)
    if not isinstance(header,dict) or len(encoded)>MAX_JSON or len(encoded)+len(body)+4>MAX_FRAME:raise ValueError('Frame exceeds bounds')
    sock.sendall(struct.pack('!II',len(encoded)+len(body)+4,len(encoded))+encoded+body)


def _read(sock,n):
    data=bytearray()
    while len(data)<n:
        b=sock.recv(n-len(data))
        if not b:raise EOFError('Connection closed during frame')
        data.extend(b)
    return bytes(data)


def recv_frame(sock):
    total=struct.unpack('!I',_read(sock,4))[0]
    if not 6<=total<=MAX_FRAME:raise ValueError('Invalid frame size')
    size=struct.unpack('!I',_read(sock,4))[0]
    if not 2<=size<=min(total-4,MAX_JSON):raise ValueError('Invalid JSON size')
    def reject(s):raise ValueError('Nonfinite JSON')
    h=json.loads(_read(sock,size).decode('utf-8'),parse_constant=reject)
    if not isinstance(h,dict):raise ValueError('Frame header must be an object')
    return h,_read(sock,total-4-size)
