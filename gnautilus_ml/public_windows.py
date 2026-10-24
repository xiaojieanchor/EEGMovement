"""Converts admitted original public tasks to finite windows without navigation relabeling."""
import numpy as np
from pathlib import Path
from .common import file_hash
from .readers import read_public

def public_windows(record,window_s,hop_s):
    if record.get('prepared_windows'):
        from .public_study import load_prepared
        return load_prepared(record,window_s,hop_s)
    if file_hash(record['path'])!=record['source_sha256']:raise ValueError('Public source changed')
    metadata=record['public_metadata'];r=read_public(record['reader'],record['path'],metadata);fs=r['sampling_rate'];n=round(window_s*fs);hop=max(1,round(hop_s*fs));spans=[]
    if 'windows' in r:
        x=r['windows'];column=metadata['class_column'];target=np.asarray(r['native_targets'])[:,column];mapping=metadata['native_class_map']
        if x.shape[2]!=n:raise ValueError('EEGEyeNet supplied epoch duration differs from model history')
        y=np.asarray([mapping[str(v)] for v in target]);return x,y,[{'source':record['source_sha256'],'trial':i} for i in range(len(y))]
    if 'native_labels' in r and len(r['native_labels'])==r['samples'].shape[1]:
        y=r['native_labels'];start=0
        for end in range(1,len(y)+1):
            if end==len(y) or y[end]!=y[start]:
                if y[start]!='UNKNOWN':spans.append((start,end,y[start]))
                start=end
    elif 'events' in r:
        for event in r['events']:
            if event['label']=='UNKNOWN':continue
            seconds=event['duration'] or metadata.get('task_duration_s')
            if seconds is None:raise ValueError('Original event duration must be explicitly established')
            spans.append((event['sample'],event['sample']+round(seconds*fs),event['label']))
    elif 'native_onsets' in r:
        duration=round(metadata['task_duration_s']*fs);mapping=metadata['native_class_map']
        for at,label in zip(r['native_onsets'],r['native_labels']):spans.append((int(at)-1,int(at)-1+duration,mapping[str(int(label))]))
    elif r.get('native_markers') is not None:
        marker=r['native_markers'];mapping=metadata['native_class_map'];duration=round(metadata['task_duration_s']*fs)
        for at,label in zip(np.ravel(marker['pos']),np.ravel(marker['y'])):spans.append((int(at)-1,int(at)-1+duration,mapping[str(int(label))]))
    else:raise ValueError('No supervised native labels; continuous evaluation truth is a separate source')
    selected=metadata.get('included_native_classes');x=[];labels=[];ids=[]
    for start,end,label in spans:
        if selected and label not in selected:continue
        for at in range(start,min(end,r['samples'].shape[1])-n+1,hop):
            values=r['samples'][:,at:at+n]
            if not np.isfinite(values).all():continue
            x.append(values);labels.append(label);ids.append({'source':record['source_sha256'],'trial':start,'start':at,'end':at+n,'native_label':label})
    if not x:raise ValueError('No native trials support this window length; HaLT requires at most its one-second task duration')
    return np.asarray(x,dtype=np.float32),np.asarray(labels),ids
