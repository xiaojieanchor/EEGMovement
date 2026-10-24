"""Chronological finite-history replay. UNKNOWN never contributes a REST target."""
import numpy as np
from .data import read_native,LABELS
from .quality import inspect_signal

def continuous_record(model,meta,record):
    r=read_native(record['path']);contract=meta['input_contract']
    if r['source_sha256']!=record['source_sha256'] or r['channels']!=contract['channels'] or r['sampling_rate']!=contract['sampling_rate'] or record['reference']!=contract['reference'] or r['descriptor']['configuration']!=contract['filter_configuration']:raise ValueError('Replay input differs from model contract')
    fs=r['sampling_rate'];n=round(meta['window_s']*fs);hop=max(1,round(meta['hop_s']*fs));total=r['samples'].shape[1];targets=np.full(total,-1,dtype=int)
    for span in r['intervals']:targets[span['start']:span['end']]=LABELS.index(span['label'])
    frames=[];pending=[];ids=[]
    def flush():
        if pending:
            prediction=model.predict(np.asarray(pending,dtype=np.float32))['probabilities']
            for index,p in zip(ids,prediction):frames[index]['probabilities']=p.tolist()
            pending.clear();ids.clear()
    for end in range(hop,total+1,hop):
        row={'sample_index_end':end,'target':int(targets[end-1]),'probabilities':None,'reason':None}
        if end<n:row['reason']='warmup'
        elif not r['valid'][end-n:end].all():row['reason']='source_gap_or_device_invalid'
        else:
            x=r['samples'][:,end-n:end];quality=inspect_signal(x,r['channels'],fs,meta['electrical_policy'])
            if quality['electrical_valid']:pending.append(x);ids.append(len(frames))
            else:row['reason']='electrical_reject'
        frames.append(row)
        if len(pending)>=64:flush()
    flush();supported={k:0 for k in range(5)};correct={k:0 for k in range(5)};eligible=known=false_onsets=0;false_seconds=0.;previous=False
    for row in frames:
        p=row['probabilities'];target=row['target'];pred=-1
        if p:
            active=1-p[0];directions=np.asarray(p[1:])/max(active,1e-9);pred=0 if active<.75 else int(np.argmax(directions))+1 if directions.max()>=.7 else -1
        row['decision']=LABELS[pred] if pred>=0 else 'REJECT';row['decision_policy']='fixed .75 activity / .7 conditional direction; no Hub smoothing or VR execution'
        if target<0:previous=False;continue
        known+=1;supported[target]+=1;correct[target]+=int(pred==target);eligible+=int(p is not None);false=target==0 and pred>0
        if false and not previous:false_onsets+=1
        if false:false_seconds+=hop/fs
        previous=false
    recall={LABELS[k]:correct[k]/v for k,v in supported.items() if v};nc_seconds=supported[0]*hop/fs
    summary=dict(accuracy=sum(correct.values())/known if known else None,balanced_accuracy=float(np.mean(list(recall.values()))) if recall else None,recall=recall,
        coverage=eligible/known if known else None,labelled_seconds=known*hop/fs,unknown_seconds=sum(v['target']<0 for v in frames)*hop/fs,
        no_control_seconds=nc_seconds,false_activation_onsets=false_onsets,false_activations_per_minute=false_onsets/(nc_seconds/60) if nc_seconds else None,false_motion_seconds=false_seconds,
        scoring='chronological labelled time incl. warmup/reject; UNKNOWN excluded; no physical intention or VR timing claim')
    return summary,frames
