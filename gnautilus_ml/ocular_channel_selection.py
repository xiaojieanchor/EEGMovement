"""Train-only channel contrast ranking; physical columns never change meaning."""
import numpy as np
from .navigation_contract import CHANNELS

def channel_candidates(records):
    by={d:[] for d in ['UP','DOWN','LEFT','RIGHT']};supports=[]
    for row in records:
        if row['split']!='train':raise ValueError('Channel ranking may read training events only')
        if row['class_label'] not in by:continue
        x=np.asarray(row['x']);at=row['event_start']
        if x.ndim!=2 or x.shape[0]!=32 or not 25<=at<x.shape[1] or not np.isfinite(x).all():raise ValueError('Real same-trial reference and marked event required')
        before=x[:,max(0,at-250):at];event=x[:,at:];baseline=np.median(before[:,-min(25,before.shape[1]):],axis=1)
        noise=np.maximum(1.,1.4826*np.median(abs(before-np.median(before,axis=1,keepdims=True)),axis=1))
        delta=np.median(event[:,-max(1,event.shape[1]//4):],axis=1)-baseline
        excursion=np.percentile(abs(event-baseline[:,None]),90,axis=1)
        score=(abs(delta)+.5*excursion)/noise
        by[row['class_label']].append(score);supports.append(dict(segment_id=row['segment_id'],reference_samples=before.shape[1],event_samples=event.shape[1],direction=row['class_label']))
    if any(not rows for rows in by.values()):raise ValueError('All four training directions need marked signal for ranking')
    direction={d:np.median(rows,axis=0) for d,rows in by.items()};strength=np.mean(list(direction.values()),axis=0)
    rank=np.argsort(-strength,kind='stable');union=sorted(set(int(i) for s in direction.values() for i in np.argsort(-s,kind='stable')[:2]))
    candidates=[]
    for name,indices,weighted in [('frontal8',list(range(8)),False),('contrast_top4',sorted(rank[:4]),True),
                                  ('contrast_top8',sorted(rank[:8]),True),('direction_union2',union,True),('all32_reference',list(range(32)),False)]:
        w=np.zeros(32);v=strength[indices];w[indices]=np.clip(v/max(float(v.max()),1e-8),.25,1.) if weighted else 1.
        candidates.append(dict(name=name,channels=[CHANNELS[i] for i in indices],input_weights=w.tolist(),
            physical_indices_zero_based=[int(i) for i in indices],weight_semantics='fixed train contrast scaling; classifier spatial weights further learned'))
    return dict(schema='gnautilus.ocular_channel_candidates.v1',fit_role='train',channels=CHANNELS,global_ranking=[CHANNELS[i] for i in rank],
        strength=strength.tolist(),per_direction={d:dict(strength=v.tolist(),ranking=[CHANNELS[i] for i in np.argsort(-v,kind='stable')]) for d,v in direction.items()},
        supports=supports,candidates=candidates,reference_regions_labelled_no_eye=False,
        semantics='Relative marked event versus its own preceding reference; reference is not a new ground-truth no-eye label. Training only.')
