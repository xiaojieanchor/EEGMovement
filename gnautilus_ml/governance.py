"""The only real-data fit authority is a checked, hash-bound remote invocation."""
import contextvars,os
from contextlib import contextmanager
from pathlib import Path
from .common import digest,file_hash,read_json

_AUTH=contextvars.ContextVar('gnautilus_training_authority',default=None)
NEW_HOST='172.25.56.83'
NEW_GPU='GPU-b6968de0-6905-0f58-1171-d6a3d62cd44b'

def record_group(row):
    if row.get('reader','gnautilus')=='gnautilus':
        group=(row.get('subject'),row.get('date'),row.get('wearing_id'))
        if any(v is None or v=='' for v in group):raise ValueError('Unknown subject/date/wearing cannot be split')
        return ('gnautilus',)+group
    meta=row.get('public_metadata',{});g=meta.get('grouping',{})
    if meta.get('admission')!='approved' or not meta.get('license_receipt') or not meta.get('source_version'):
        raise ValueError('Public source lacks reviewed admission/license/version')
    if g.get('level') not in ['subject','native_session','native_date'] or not g.get('native_id') or not meta.get('source_session_verified') or not row.get('subject'):
        raise ValueError('Verified public subject/session grouping required; do not invent a wearing date')
    if not row.get('prepared_windows'):raise ValueError('Public training requires a source-bound signal QC/window receipt')
    return (row['reader'],str(row['subject']),g['level'],str(g['native_id']))

def check_runtime(profile):
    if profile.get('host')!=NEW_HOST or profile.get('gpu_uuid')!=NEW_GPU:raise RuntimeError('Unverified compute host/GPU profile')
    if os.environ.get('CUDA_VISIBLE_DEVICES')!=profile['gpu_uuid']:raise RuntimeError('CUDA visibility differs from pinned server profile')
    import torch
    if not torch.cuda.is_available() or torch.cuda.device_count()!=1:raise RuntimeError('Pinned CUDA device is unavailable')
    import subprocess
    result=subprocess.run(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],capture_output=True,text=True,check=True,timeout=10)
    if profile['gpu_uuid'] not in result.stdout.splitlines():raise RuntimeError('Physical GPU identity missing')
    return 'cuda:0'

def check_protocol(protocol,dataset,code_root):
    if protocol.get('schema')!='gnautilus.training_protocol.v1':raise ValueError('Locked new-device protocol required')
    if protocol.get('dataset_sha256')!=digest(dataset):raise ValueError('Dataset differs from locked protocol')
    from .algorithms import ALGORITHMS
    if protocol.get('algorithm') not in ALGORITHMS:raise ValueError('Unknown algorithm')
    if not protocol.get('code_sha256'):raise ValueError('Source identity missing')
    for relative,expected in protocol['code_sha256'].items():
        p=(Path(code_root)/relative).resolve()
        if not p.is_relative_to(Path(code_root).resolve()) or file_hash(p)!=expected:raise ValueError('Training source changed: '+relative)
    seen={};roles=set();sources={}
    for row in dataset.get('records',[]):
        group=record_group(row)
        role=row.get('role');roles.add(role)
        if role not in ['train','development','test']:raise ValueError('Explicit split role required')
        if group in seen and seen[group]!=role:raise ValueError('Date/wearing crosses dataset roles')
        seen[group]=role
        source=row.get('source_sha256')
        if source in sources and sources[source]!=role:raise ValueError('Same source crosses dataset roles')
        if source in sources:raise ValueError('Duplicate source in combined dataset')
        sources[source]=role
        if role=='test' and (not row.get('prospective') or row.get('previously_inspected') is not False):raise ValueError('Independent test status is not established')
    if not {'train','development'}<=roles:raise ValueError('Separate training and development wearings required')
    if not 1<=protocol.get('seed',-1)<2**31:raise ValueError('Fixed seed required')
    if not 1<=protocol.get('max_epochs',0)<=200:raise ValueError('Training budget required')
    return True

@contextmanager
def training_authority(protocol,dataset,code_root,profile,protocol_path=None):
    previous=os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL');token=None
    try:
        if protocol_path is not None:
            if digest(read_json(protocol_path))!=digest(protocol):raise ValueError('Actual locked protocol path differs')
            os.environ['EEGMOVEMENT_TRAINING_PROTOCOL']=str(Path(protocol_path).resolve())
        check_protocol(protocol,dataset,code_root);device=check_runtime(profile)
        token=_AUTH.set({'protocol':digest(protocol),'device':device})
        yield
    finally:
        if token is not None:_AUTH.reset(token)
        if previous is None:os.environ.pop('EEGMOVEMENT_TRAINING_PROTOCOL',None)
        else:os.environ['EEGMOVEMENT_TRAINING_PROTOCOL']=previous

def authorize_training():
    if _AUTH.get() is None:raise RuntimeError('Fitting requires the governed private-server training entrypoint')
def compute_device():
    authorize_training();return _AUTH.get()['device']

