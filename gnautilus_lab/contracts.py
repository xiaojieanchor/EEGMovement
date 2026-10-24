"""Fail-closed recording identity and public-source admission review helpers."""
from dataclasses import dataclass
from datetime import date
import math
import re

@dataclass(frozen=True)
class RecordingIdentity:
    recording_id: str
    source_sha256: str
    subject_id: str
    session_id: str
    wearing_id: str
    date: str
    role: str
    task: str
    channel_names: list[str]
    sample_rate_hz: float
    unit: str
    reference: str
    device_config_sha256: str
    provenance: str

def validate_identity(item: RecordingIdentity):
    for field in ['recording_id','subject_id','session_id','wearing_id','date','task','reference','provenance']:
        if not isinstance(getattr(item,field),str) or not getattr(item,field).strip():raise ValueError(f'Unknown {field}')
    for field in ['source_sha256','device_config_sha256']:
        if not isinstance(getattr(item,field),str) or not re.fullmatch('[0-9a-f]{64}',getattr(item,field)):raise ValueError(f'Invalid {field}')
    if item.role not in ['train','development','test','unassigned']:raise ValueError('Invalid role')
    if type(item.sample_rate_hz) not in [float,int] or not math.isfinite(item.sample_rate_hz) or item.sample_rate_hz<=0:raise ValueError('Unknown sampling rate')
    if item.unit!='uV':raise ValueError('Explicit once-scaled uV required for this internal contract')
    names=item.channel_names
    if not isinstance(names,list) or not names or any(not isinstance(n,str) or not n.strip() for n in names) or len(set(names))!=len(names):raise ValueError('Explicit unique channels required')
    date.fromisoformat(item.date)
    return item

def grouped_split(items):
    """Subject/date/wear/session/source identities cannot silently cross roles.

    Within-person dates and wears must be distinct; subject itself may span roles.
    Cross-subject validation requires the caller to additionally split subjects.
    """
    seen={k:{} for k in ['source','session','wearing','date']};result={}
    for i,item in enumerate(items):
        validate_identity(item)
        if item.role=='unassigned':raise ValueError('Unassigned source cannot enter a split')
        keys={'source':item.source_sha256,'session':(item.subject_id,item.session_id),
              'wearing':(item.subject_id,item.wearing_id),'date':(item.subject_id,item.date)}
        for name,key in keys.items():
            if key in seen[name] and seen[name][key]!=item.role:raise ValueError(f'{name} crosses data roles')
            seen[name][key]=item.role
        result.setdefault(item.role,[]).append(i)
    return result

def dataset_admission(record):
    """Review checklist, never automatic production admission or relabeling."""
    reasons=[]
    if record.get('status')!='reviewed_for_method_validation':reasons.append('quarantine_or_unreviewed_role')
    if not record.get('license'):reasons.append('license_unknown')
    if record.get('source_overlap_checked') is not True:reasons.append('source_overlap_unknown')
    if record.get('metadata_complete') is not True:reasons.append('metadata_incomplete')
    native=record.get('native_tasks');target=record.get('target_tasks')
    if not native or native!=target:reasons.append('task_semantics')
    return {'approved':not reasons,'role':'method_validation_only' if not reasons else 'held',
            'reasons':reasons,'native_tasks':native,'training_admission':False}
