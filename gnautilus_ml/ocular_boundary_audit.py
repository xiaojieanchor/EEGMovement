"""Read-only ocular annotation helpers. Signal candidates are not gaze truth."""
import numpy as np
from scipy.signal import butter,sosfilt,sosfilt_zi,find_peaks

def merge_intervals(intervals):
    out=[]
    for a,b in sorted(intervals):
        if out and a<=out[-1][1]:out[-1][1]=max(out[-1][1],b)
        else:out.append([int(a),int(b)])
    return out

def context_ranges(episodes,segments,pre=1500,post=750):
    """Expand authentic supports; midpoint partitions prevent role overlap."""
    result={}
    for r in episodes:
        if r['stream_id'] not in segments:continue
        seg=next(((a,b) for a,b in segments[r['stream_id']] if a<=r['context_start']<r['context_end']<=b),None)
        if seg is None:raise ValueError('Episode crosses a real source gap')
        lo=max(seg[0],min(r['context_start'],r['start']-pre));hi=min(seg[1],max(r['context_end'],r['end']+post))
        for q in episodes:
            if q['stream_id']!=r['stream_id'] or q['split']==r['split']:continue
            if q['context_end']<=r['context_start']:lo=max(lo,(q['context_end']+r['context_start'])//2)
            elif q['context_start']>=r['context_end']:hi=min(hi,(r['context_end']+q['context_start'])//2)
            else:raise ValueError('Existing roles already overlap')
        if not lo<=r['context_start']<r['context_end']<=hi:raise ValueError('Expansion must retain the whole original episode')
        result[r['trial_id']]=[int(lo),int(hi)]
    for a in episodes:
        for b in episodes:
            if a['stream_id']==b['stream_id'] and a['split']!=b['split'] and a['trial_id'] in result and b['trial_id'] in result:
                sa,sb=result[a['trial_id']];ta,tb=result[b['trial_id']]
                if sa<tb and sb>ta:raise ValueError('Expanded source support crosses roles')
    return result

def input_status(quality_valid,warming,label):
    if not quality_valid:return 'QC_REJECTED'
    if warming:return 'WARMUP'
    return 'READY_LABEL_UNKNOWN' if label<0 else 'READY_LABELLED'

def load_contextual_episodes(dataset,role,pre=3000,post=1500):
    """Versioned diagnostic supports; do not replace locked training inputs.

    Preserve all original labels exactly, with UNKNOWN for added source samples.
    Real role boundaries and source gaps take precedence over requested duration.
    """
    from pathlib import Path
    from .common import read_json
    from .personal_study import load_episodes
    original=load_episodes(dataset,role);folder=Path(dataset['folder'])/'.metadata/canonical';segments={};descriptors={}
    for sid in {r['stream_id'] for r in dataset['episodes']}:
        p=folder/sid
        if not p.exists():continue
        segments[sid]=read_json(p/'segments.json')['segments'];descriptors[sid]=read_json(p/'descriptor.original.json')
    ranges=context_ranges(dataset['episodes'],segments,pre,post);out=[]
    for row in original:
        old=row['metadata'];sid=old['stream_id']
        if row['id'] not in ranges:
            out.append(dict(row,metadata=dict(old,context_review='immutable_official_episode_no_additional_source')));continue
        a,b=ranges[row['id']];p=folder/sid;d=descriptors[sid];ix=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG']
        if len(ix)!=32 or d['sampling_rate']!=250 or [d['channels'][i]['physical_index'] for i in ix]!=list(range(32)):raise ValueError('Physical source identity differs')
        coverage=np.load(p/'coverage.npy',mmap_mode='r',allow_pickle=False);conflict=np.load(p/'conflict_mask.npy',mmap_mode='r',allow_pickle=False)
        if not coverage[a:b].all() or conflict[a:b].any():raise ValueError('Expanded source contains gaps/conflicts')
        raw=np.load(p/'samples.npy',mmap_mode='r',allow_pickle=False);x=np.asarray(raw[a:b][:,ix].T,dtype='float32').copy();at=old['context_start']-a
        np.testing.assert_array_equal(x[:,at:at+row['x'].shape[1]],row['x'])
        y=np.full(b-a,-1,dtype='int8')
        # Adjacent same-role episodes remain separate complete labelled trials.
        # Extra context labels are UNKNOWN even if a same-role neighbour exists.
        y[at:at+len(row['y'])]=row['y'];m=dict(old,context_start=a,context_end=b,onset_in_episode=old['start']-a,offset_in_episode=old['end']-a,
            original_context_start=old['context_start'],original_context_end=old['context_end'],context_review='strict_role_authentic_source_v1',
            actual_pre_s=(old['start']-a)/250,actual_post_s=(b-old['end'])/250)
        out.append(dict(id=row['id'],x=x,y=y,metadata=m))
    return out

class FrontalFeatures:
    """Exactly the 156-feature eye contract; no non-frontal input dependency."""
    def __init__(self):
        self.sos=butter(4,8,fs=250,btype='lowpass',output='sos');self.reset()
    def reset(self):
        self.samples=0;self.pending=np.empty((8,0));self.lp=np.empty((8,0));self.zi=np.zeros((len(self.sos),8,2))
    def step(self,x):
        x=np.asarray(x,dtype=float)
        if x.ndim!=2 or x.shape[0]!=8 or not np.isfinite(x).all():self.reset();raise ValueError('Finite eight frontal inputs required')
        self.pending=np.concatenate((self.pending,x),1);out=[]
        while self.pending.shape[1]>=25:
            xx=self.pending[:,:25];self.pending=self.pending[:,25:];lo,self.zi=sosfilt(self.sos,xx,zi=self.zi,axis=-1)
            self.lp=np.concatenate((self.lp,lo),1)[:,-1500:];self.samples+=25
            if self.samples<375:continue
            features=[]
            for n in [125,375,750,1500]:
                v=self.lp[:,-n:];k=max(1,v.shape[1]//4);diff=np.stack([v[0]-v[1],v[2]-v[3],v[4]-v[5],v[6]-v[7],(v[0]+v[1]-v[6]-v[7])/2])
                for a in [v,diff]:features.extend([a.mean(1),a.std(1),a[:,-k:].mean(1)-a[:,:k].mean(1)])
            out.append(dict(sample_end=self.samples,eye=np.concatenate(features).astype('float32')))
        return out

class OcularTrace:
    """Independent causal scalp-proxy trace for annotation, not eye coordinates.

    First-value steady-state filter initialization prevents a fake start edge.
    This convention is only for descriptive proposals, not neural prewarm.
    """
    def __init__(self):
        self.sos=butter(4,8,fs=250,btype='lowpass',output='sos');self.reset()
    def reset(self):
        self.samples=0;self.zi=None;self.pending=np.empty((8,0));self.previous=None
    def step(self,x):
        x=np.asarray(x,dtype=float)
        if x.ndim!=2 or x.shape[0]!=8 or not np.isfinite(x).all():self.reset();raise ValueError('Finite frontal trace required')
        self.pending=np.concatenate((self.pending,x),1);out=[]
        while self.pending.shape[1]>=25:
            xx=self.pending[:,:25];self.pending=self.pending[:,25:]
            if self.zi is None:self.zi=sosfilt_zi(self.sos)[:,None,:]*xx[None,:,0,None]
            lp,self.zi=sosfilt(self.sos,xx,zi=self.zi,axis=-1);v=lp.mean(1)
            proxy=np.asarray([v[0]-v[1],v[2]-v[3],v[4]-v[5],v[6]-v[7],(v[0]+v[1]-v[6]-v[7])/2])
            velocity=np.zeros(5) if self.previous is None else (proxy-self.previous)/.1
            self.previous=proxy;self.samples+=25;out.append(dict(sample_end=self.samples,frontal_lp=v.tolist(),proxy=proxy.tolist(),velocity=velocity.tolist()))
        return out

def proposals(z,ends,high=6.,low=3.,merge_hops=1):
    """Frozen train-calibrated proxy score; no task or classifier labels used."""
    z=np.asarray(z);ends=np.asarray(ends);score=np.max(abs(z),axis=1)
    peaks=list(find_peaks(score,height=high,prominence=low,distance=2)[0])
    if len(score) and score[0]>=high and (len(score)==1 or score[0]>score[1]):peaks.insert(0,0)
    if len(score)>1 and score[-1]>=high and score[-1]>score[-2]:peaks.append(len(score)-1)
    result=[]
    for n,peak in enumerate(peaks):
        a=b=int(peak)
        while a>0 and score[a-1]>=low:a-=1
        while b+1<len(score) and score[b+1]>=low:b+=1
        if n:a=max(a,int(peaks[n-1]+np.argmin(score[peaks[n-1]:peak+1]))+1)
        if n+1<len(peaks):b=min(b,int(peak+np.argmin(score[peak:peaks[n+1]+1])))
        result.append(dict(sample_start=int(ends[a]-25),sample_end=int(ends[b]),peak_sample=int(ends[peak]),
            proxy_peak_z=float(score[peak]),proxy_component=int(np.argmax(abs(z[peak]))),
            annotation_status='signal_candidate_unverified',kind='ocular_or_frontal_transient',ground_truth=False))
    return result

def direction_spans(frames,score_key='eye_new_scores',threshold=.7,margin=.15):
    """Only confirmed current evidence; never backdate or extend an old gaze."""
    spans=[];candidate=-1;count=0;active=-1;last_end=None
    for row in frames:
        scores=row.get(score_key);label=-1
        if scores is not None:
            a=np.asarray(scores);order=np.sort(a)
            if a.max()>=threshold and order[-1]-order[-2]>=margin:label=int(a.argmax())
        count=count+1 if label==candidate and label>=0 else 1 if label>=0 else 0;candidate=label
        confirmed=label if count>=3 else -1
        if confirmed!=active:
            if active>=0:spans[-1]['sample_end']=int(last_end)
            if confirmed>=0:spans.append(dict(sample_start=int(row['source_sample_end']-25),sample_end=int(row['source_sample_end']),direction_code=confirmed,semantic='frontal_joint_task_direction_estimate',ground_truth=False))
            active=confirmed
        elif active>=0:spans[-1]['sample_end']=int(row['source_sample_end'])
        last_end=row['source_sample_end']
    return spans
