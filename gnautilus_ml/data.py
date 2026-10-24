"""Closed-journal verification and source-index-aware labelled windows."""
import json
from pathlib import Path
import numpy as np
from gnautilus_acquisition.contract import validate_descriptor,validate_block
from .common import file_hash,read_json,digest
from .quality import inspect_signal

LABELS=['REST','FORWARD','BACKWARD','LEFT','RIGHT']
MAPPING={'FORWARD':('UP','LEFT_LEG_MI'),'BACKWARD':('DOWN','RIGHT_LEG_MI'),'LEFT':('LEFT','LEFT_HAND_MI'),'RIGHT':('RIGHT','RIGHT_HAND_MI'),'REST':('CENTER','NO_CONTROL')}
NAVIGATION_MODES=['navigation_mi_gaze','navigation_mi_only']
DEFAULT_MODE='navigation_mi_gaze'
MODES=NAVIGATION_MODES+['motor_only','gaze_only','paired','no_control','interference']

def event_intervals(events,total,epoch=0):
    result=[];active=None
    for row in events:
        if row.get('kind')!='marker' or row.get('phase')=='heartbeat':continue
        at=row.get('sample_index_end')
        if type(at) is not int or not 0<=at<=total:continue
        if active:
            end=row.get('_close_index',at)
            if row.get('source_epoch',epoch)!=epoch:end=active['start']
            if end>active['start']:result.append(dict(active,end=end))
            active=None
        if row.get('source_epoch',epoch)==epoch and row.get('phase') in ['task','hold_start'] and row.get('label') in LABELS:
            active=dict(start=at,label=row['label'],mode=row.get('experiment_mode','unspecified'),gaze=row.get('gaze_instruction'),limb=row.get('limb_instruction'))
            for field,key in [('motor','motor_instruction'),('task_binding_version','task_binding_version'),('primary_source','primary_source'),('auxiliary_source','auxiliary_source')]:
                if key in row:active[field]=row[key]
    # No implicit label extension to EOF: unclosed intent is UNKNOWN.
    return result

def read_native(path):
    p=Path(path);d=validate_descriptor(read_json(p/'descriptor.json'));closed=read_json(p/'closed.json')
    if not closed.get('closed'):raise ValueError('Recording has not been closed')
    for name in ['descriptor.json','raw.f32','blocks.jsonl','events.jsonl']:
        receipt=closed.get('files',{}).get(name,{})
        if receipt.get('sha256')!=file_hash(p/name) or receipt.get('bytes')!=(p/name).stat().st_size:raise ValueError('Closed recording changed: '+name)
    blocks=[json.loads(s) for s in (p/'blocks.jsonl').read_text('utf-8').splitlines()];events=[json.loads(s) for s in (p/'events.jsonl').read_text('utf-8').splitlines()]
    chunks=[];valid=[];offset=0;previous=None;maps=[]
    with (p/'raw.f32').open('rb') as raw:
        for row in blocks:
            if row['offset']!=offset:raise ValueError('Noncontiguous raw file index')
            h=row['header'];body=raw.read(row['length']);x=validate_block(h,body,d).copy();offset+=len(body)
            local=sum(len(a) for a in chunks);source=h.get('source_sample_start',h['sample_start']);epoch=h.get('source_epoch',h['epoch'])
            maps.append((epoch,source,source+len(x),local))
            good=np.ones(len(x),dtype=bool)
            if previous is not None and (previous!=(epoch,source)):good[:]=False
            previous=(epoch,source+len(x));chunks.append(x)
            for i,c in enumerate(d['channels']):
                if c['kind']=='VALIDATION':good &= x[:,i]==1
            valid.append(good)
    if offset!=(p/'raw.f32').stat().st_size or sum(map(len,chunks))!=closed.get('samples'):raise ValueError('Sample/file count mismatch')
    x=np.concatenate(chunks) if chunks else np.empty((0,len(d['channels'])));good=np.concatenate(valid) if valid else np.empty(0,bool)
    mapped=[]
    for row in events:
        if row.get('kind')!='marker':continue
        index=row.get('sample_index_end');epoch=row.get('source_epoch',d['epoch'])
        assoc=row.get('sample_association');low=index;phase=row.get('phase')
        if assoc:
            if assoc.get('status')=='host_clock_estimate' and assoc.get('source_epoch')==epoch and assoc.get('stream_id')==d['stream_id']:
                low,index=assoc['sample_range']
            else:phase='pause' # An unresolved marker terminates support; it cannot start a task.
        found=[(source,start,end,local) for source,start,end,local in maps if source==epoch and type(index) is int and start<=index<=end]
        if not found:continue
        _,start,end,local=found[-1]
        low_map=[(a,b,c,v) for a,b,c,v in maps if a==epoch and type(low) is int and b<=low<=c]
        close_index=low_map[-1][3]+low-low_map[-1][1] if low_map else local+index-start
        mapped.append(dict(row,phase=phase,sample_index_end=local+index-start,_close_index=close_index,source_epoch=0))
    indices=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG']
    return dict(samples=x[:,indices].T,valid=good,channels=[d['channels'][i]['name'] for i in indices],sampling_rate=d['sampling_rate'],
                intervals=event_intervals(mapped,len(x)),descriptor=d,source_sha256=digest(closed['files']))

def check_record(path,attributes):
    allowed={'subject','date','wearing_id','role','experiment_mode','reference','prospective','previously_inspected','electrical_policy','notes'}
    if set(attributes)-allowed:raise ValueError('Unknown record attributes')
    r=read_native(path);d=r['descriptor'];reasons=[]
    for key in ['subject','date','wearing_id','role','experiment_mode','reference']:
        if not attributes.get(key):reasons.append('missing_'+key)
    if attributes.get('experiment_mode') not in MODES:reasons.append('unknown_experiment_mode')
    if attributes.get('experiment_mode') in NAVIGATION_MODES:
        from .tasks import task_spec
        expected=lambda v:task_spec(v['label'],attributes['experiment_mode'])
        if any(v.get('mode')!=attributes['experiment_mode'] or v.get('task_binding_version')!='navigation_mi_gaze_v3' or
               any(v.get(field)!=expected(v)[key] for field,key in [('primary_source','primary_source'),('auxiliary_source','auxiliary_source'),
                   ('motor','motor_instruction'),('gaze','gaze_instruction'),('limb','limb_instruction')]) for v in r['intervals']):
            reasons.append('navigation_task_metadata_missing_or_mismatched')
    if not d.get('layout_verified'):reasons.append('montage_not_verified')
    if not d.get('reference') or d.get('reference')!=attributes.get('reference'):reasons.append('reference_differs_or_unverified')
    if d['source_kind']!='gtec_live':reasons.append('not_real_gtec_origin')
    if not r['intervals']:reasons.append('no_closed_label_intervals')
    if not attributes.get('electrical_policy'):reasons.append('electrical_policy_not_configured')
    return dict(schema='gnautilus.dataset_record.v1',path=str(Path(path).resolve()),source_sha256=r['source_sha256'],
                channels=r['channels'],sampling_rate=r['sampling_rate'],configuration_sha256=d['configuration_sha256'],
                reference=attributes.get('reference'),recorded_reference=d.get('reference'),filter_configuration=d['configuration'],**{k:v for k,v in attributes.items() if k!='reference'},
                closed=True,training_ready=not reasons,blocking_reasons=reasons)

def windows(record,window_s=1.5,hop_s=.1):
    r=read_native(record['path'])
    if r['source_sha256']!=record['source_sha256']:raise ValueError('Training source changed after inspection')
    fs=r['sampling_rate'];n=round(window_s*fs);hop=max(1,round(hop_s*fs));x=[];y=[];ids=[]
    for interval in r['intervals']:
        for start in range(interval['start'],interval['end']-n+1,hop):
            stop=start+n;values=r['samples'][:,start:stop]
            if not r['valid'][start:stop].all():continue
            q=inspect_signal(values,r['channels'],fs,record['electrical_policy'])
            if not q['electrical_valid']:continue
            x.append(values);y.append(LABELS.index(interval['label']));ids.append(dict(source=record['source_sha256'],trial=interval['start'],start=start,end=stop,label=interval['label'],mode=interval['mode'],
                **{key:interval[key] for key in ['gaze','limb','motor','task_binding_version','primary_source','auxiliary_source'] if interval['mode'] in NAVIGATION_MODES and key in interval}))
    if not x:raise ValueError('No complete labelled electrically eligible windows')
    return np.asarray(x,dtype=np.float32),np.asarray(y),ids
