"""Compile human ranges only; no EEG processing and no model-generated truth."""
import collections
from .common import digest
from .navigation_contract import CHANNELS, SCOPE

CLASSES=['NO_EYE','UP','DOWN','LEFT','RIGHT']
SCHEMA='gnautilus.manual_ocular_events.v1'

def compile_annotations(catalog,export):
    if catalog.get('channels')!=CHANNELS or catalog.get('sampling_rate')!=250:
        raise ValueError('Confirmed physical montage/rate required, including CH16=Cz/CH19=C5')
    if export.get('schema')!='gnautilus.manual_ocular_export.v1' or export.get('dataset_id')!=catalog.get('dataset_id'):
        raise ValueError('Human export identity differs')
    trials={q['trial_id']:q for q in catalog['trials']};reviews={}
    if len(trials)!=len(catalog['trials']):raise ValueError('Duplicate source trial')
    for r in export['reviews']:
        tid=r['trial_id']
        if tid in reviews or tid not in trials:raise ValueError('Duplicate/unknown manual review')
        q=trials[tid]
        if any(r.get(k)!=q.get(k) for k in ['trial_id','stream_id','split','waveform_sha256']):raise ValueError('Review source role/identity differs')
        if (r.get('dataset_id')!=catalog['dataset_id'] or r.get('sampling_rate')!=250 or
            r.get('task_range')!=[q['task_start'],q['task_end']] or type(r.get('revision')) is not int or r['revision']<1):
            raise ValueError('Invalid manual revision/task identity')
        if r!=q.get('annotation'):raise ValueError('Catalog/export revisions disagree; reload atomic snapshot')
        if set(r.get('phases',{}))!={'onset','offset'}:raise ValueError('Exactly two manual review slots required')
        reviews[tid]=r
    if set(trials)!=set(reviews):raise ValueError('Every complete trial needs a saved review')
    result=[]
    for tid,q in trials.items():
        if q['split'] not in ['train','validation','test']:raise ValueError('Unknown source role')
        a,b=q['task_start'],q['task_end'];r=reviews[tid];local=[]
        for phase,p in r['phases'].items():
            if p.get('status') not in ['pending','uncertain','absent','marked']:raise ValueError('Unknown review status')
            if p['status']!='marked':continue
            lo,hi=(a,min(a+1250,b)) if phase=='onset' else (max(a,b-1250),b)
            start,end=p.get('sample_start'),p.get('sample_end')
            if type(start) is not int or type(end) is not int or not lo<=start<end<=hi:raise ValueError('Human range outside reviewed support')
            if p.get('direction') not in CLASSES[1:] or p.get('human_confirmed') is not True:raise ValueError('Only human-confirmed ocular directions admitted')
            local.append(dict(phase=phase,start=start,end=end,class_label=p['direction'],kind='manual_direction',possibly_right_censored=end==b))
        if r['phases']['onset']['status']=='absent':
            end=min(b,a+500)
            if any(v['start']<end and v['end']>a for v in local):raise ValueError('No-eye head baseline overlaps a marked ocular event')
            local.append(dict(phase='onset',start=a,end=end,class_label='NO_EYE',kind='manual_absent_head_0_2s',possibly_right_censored=False))
        for i,v in enumerate(local):
            if any(v['start']<w['end'] and v['end']>w['start'] for w in local[:i]):raise ValueError('Conflicting manual supports')
            s=dict(v,trial_id=tid,stream_id=q['stream_id'],split=q['split'],revision=r['revision'],
                waveform_sha256=q['waveform_sha256'],navigation_rest=False,annotation_source='manual_user')
            s['segment_id']=digest(s);result.append(s)
    for i,s in enumerate(result):
        if any(s['stream_id']==v['stream_id'] and s['start']<v['end'] and s['end']>v['start'] for v in result[:i]):
            raise ValueError('Overlapping source labels or cross-role duplicate')
    counts={role:{label:sum(s['split']==role and s['class_label']==label for s in result) for label in CLASSES} for role in ['train','validation','test']}
    return dict(schema=SCHEMA,scope=SCOPE,parent_dataset_id=catalog['dataset_id'],title='个人导航数据集',sampling_rate=250,channels=list(CHANNELS),
        class_labels=list(CLASSES),segments=result,counts=counts,annotation_snapshot_sha256=digest(export),catalog_sha256=digest(catalog),
        source_trials=[dict(trial_id=q['trial_id'],stream_id=q['stream_id'],split=q['split'],start=q['task_start'],end=q['task_end']) for q in trials.values()],
        semantics='Manual ocular movement ranges only; NO_EYE is not navigation REST. UNKNOWN elsewhere. Incomplete offsets retained without completion.',
        no_eye_analysis_seconds=[1,2],automatic_direction_labels=False,split_policy='retain_complete_trial_roles',previous_test_inspected=True)
