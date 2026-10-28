"""Read-only inter-ocular navigation channel audit; no model fitting or test EEG.

Scalp channel relevance is exploratory. One trial per class cannot establish
independent within-direction repeatability or a pure motor-imagery source.
"""
import collections,time
from pathlib import Path
import numpy as np
from .common import atomic,digest,read_json,file_hash
from .navigation_contract import CHANNELS,ELECTRICAL,SCOPE

LABELS=['FORWARD','BACKWARD','LEFT','RIGHT']
SCHEMA='gnautilus.navigation_channel_audit.v1'

def strict_intervals(catalog,guard=250,roles=('train','validation')):
    if catalog.get('channels')!=CHANNELS or catalog.get('sampling_rate')!=250:raise ValueError('Confirmed 32 physical columns and 250Hz required')
    if guard not in [125,250]:raise ValueError('Fixed guard sensitivity required')
    included=[];excluded=[]
    for q in catalog['trials']:
        if q['split'] not in roles:continue
        if q['label'] not in LABELS:continue
        row={k:q[k] for k in ['trial_id','split','stream_id','label']};p=q['annotation']['phases'];a=p['onset'];b=p['offset']
        if a['status']!='marked' or b['status']!='marked':
            excluded.append(dict(row,reason='two_manual_eye_boundaries_required',onset_status=a['status'],offset_status=b['status']));continue
        onset_end=a.get('sample_end');offset_start=b.get('sample_start')
        if type(onset_end) is not int or type(offset_start) is not int or not q['task_start']<onset_end<offset_start<q['task_end']:raise ValueError('Invalid human source interval')
        start=onset_end+guard;end=offset_start-guard
        if end-start<500:excluded.append(dict(row,reason='less_than_two_seconds_after_guard',start=start,end=end));continue
        included.append(dict(row,start=start,end=end,raw_filter_start=onset_end,raw_filter_end=offset_start,guard_samples=guard,
            onset_direction=a.get('direction'),offset_direction=b.get('direction'),source_truth='joint_navigation_instruction_between_manual_ocular_events'))
    return dict(included=included,excluded=excluded,guard_samples=guard)

def grouped(rows):
    groups={}
    for r in rows:groups.setdefault(r['trial_id'],[]).append(r)
    return groups

def rank_channels(rows):
    if not rows or any(r['split']!='train' for r in rows):raise ValueError('Train-only ranking required')
    groups=grouped(rows);trial_centers=[];trial_var=[];classes=[];class_trials=collections.Counter()
    for values in groups.values():
        if len({r['label'] for r in values})!=1:raise ValueError('Complete trial has conflicting direction')
        x=np.asarray([r['features'] for r in values]);trial_centers.append(x.mean(0));trial_var.append(x.var(0));label=values[0]['label'];classes.append(label);class_trials[label]+=1
    if len(set(classes))<2:raise ValueError('At least two source directions needed')
    centers=np.asarray(trial_centers);variances=np.asarray(trial_var);cls=np.asarray(classes)
    mean=centers.mean(0);scale=np.sqrt(np.mean(variances+(centers-mean)**2,axis=0));scale=np.maximum(scale,.05)
    means=[];within=[]
    for label in sorted(set(classes)):
        take=cls==label;m=centers[take].mean(0);means.append((m-mean)/scale)
        within.append(np.mean(variances[take]+(centers[take]-m)**2,axis=0)/scale**2)
    between=np.var(means,axis=0);intra=np.mean(within,axis=0);score=between.sum(1)/(intra.sum(1)+1e-6);rank=np.argsort(-score,kind='stable')
    return dict(fit_role='train',channels=CHANNELS,score=score.tolist(),ranking=[CHANNELS[i] for i in rank],between_variance=between.tolist(),within_variance=intra.tolist(),
        feature_mean=mean.tolist(),feature_scale=scale.tolist(),independent_trials=dict(class_trials),independent_same_direction_repeats=all(v>=2 for v in class_trials.values()),
        label_trial_confound=any(v<2 for v in class_trials.values()),missing_training_directions=[v for v in LABELS if v not in classes],
        semantics='Between-class log bandpower dispersion / within-class window dispersion; complete trials weighted equally. Not an independent repeatability statistic.')

def select_candidates(ranking,correlation):
    score=np.asarray(ranking['score']);rank=[int(i) for i in np.argsort(-score,kind='stable') if i>=8];decor=[]
    for i in rank:
        if all(abs(correlation[i,j])<.95 for j in decor):decor.append(i)
        if len(decor)==8:break
    central=['FC3','FC1','FCz','FC2','FC4','C3','C1','Cz','C2','C4','CP1','CP2']
    sets=[('motor24',list(range(8,32))),('train_top4',rank[:4]),('train_top8',rank[:8]),('train_top12',rank[:12]),('train_decorrelated8',decor),('central12',[CHANNELS.index(v) for v in central])]
    result=[]
    for name,ix in sets:
        ix=sorted(ix);weights=np.zeros(32)
        if ix:
            s=np.sqrt(np.maximum(score[ix],0));weights[ix]=np.clip(s/max(float(s.max()),1e-8),.25,1.) if name.startswith('train_') else 1.
        result.append(dict(name=name,channels=[CHANNELS[i] for i in ix],physical_indices_zero_based=ix,input_weights=weights.tolist(),
            selected_on='training_only',training_supported_directions=sorted(ranking['independent_trials']),four_direction_validated=False))
    return result

def _cosine(a,b):
    den=np.linalg.norm(a,axis=-1)*np.linalg.norm(b,axis=-1);return np.divide(np.sum(a*b,axis=-1),den,out=np.zeros_like(den),where=den>1e-12)

def trial_representations(rows):
    return [dict(trial_id=tid,label=values[0]['label'],split=values[0]['split'],features=np.mean([r['features'] for r in values],axis=0),
        spectrum=np.mean([r['spectrum'] for r in values],axis=0),correlation=np.mean([r['correlation'] for r in values],axis=0),windows=len(values)) for tid,values in grouped(rows).items()]

def similarity_audit(train,validation):
    if any(r['split']!='train' for r in train) or any(r['split']!='validation' for r in validation):raise ValueError('Wrong similarity roles')
    tr=trial_representations(train);va=trial_representations(validation);reference=np.mean([q['spectrum'] for q in tr],axis=0);same=[];different=[];matched=[];details=[]
    for v in va:
        own=[t for t in tr if t['label']==v['label']];other=[t for t in tr if t['label']!=v['label']]
        if not own:details.append(dict(trial_id=v['trial_id'],label=v['label'],matched=False,reason='direction_missing_from_strict_training'));continue
        a=np.mean([_cosine(t['spectrum']-reference,v['spectrum']-reference) for t in own],axis=0)
        b=np.mean([_cosine(t['spectrum']-reference,v['spectrum']-reference) for t in other],axis=0)
        same.append(a);different.append(b);matched.append(v['label']);details.append(dict(trial_id=v['trial_id'],label=v['label'],matched=True,same=a.tolist(),different=b.tolist(),margin=(a-b).tolist()))
    rows=[]
    for i,name in enumerate(CHANNELS):
        rows.append(dict(electrode=name,physical_channel=i+1,mean_same_direction_similarity=float(np.mean(same,axis=0)[i]) if same else None,
            mean_different_direction_similarity=float(np.mean(different,axis=0)[i]) if different else None,
            similarity_margin=float((np.mean(same,axis=0)-np.mean(different,axis=0))[i]) if same else None,
            matched_directions=list(matched)))
    return dict(channels=rows,matched_directions=matched,details=details,reference_role='train',
        semantics='Cosine of per-channel log PSD shape after subtracting train-only mean spectrum; positive margin means same direction more similar than other training directions.')

def structure_audit(train,validation,candidates):
    tr=trial_representations(train);va=trial_representations(validation);result=[]
    for c in candidates:
        ix=c['physical_indices_zero_based'];off=~np.eye(len(ix),dtype=bool);matches=[]
        if len(ix)<2:continue
        def vector(q):return q['correlation'][np.ix_(ix,ix)][off]
        for v in va:
            own=[t for t in tr if t['label']==v['label']];other=[t for t in tr if t['label']!=v['label']]
            if not own:continue
            a=min(float(np.mean((vector(v)-vector(t))**2)) for t in own);b=min(float(np.mean((vector(v)-vector(t))**2)) for t in other)
            matches.append(dict(label=v['label'],same_distance=a,nearest_other_distance=b,same_is_nearest=a<b))
        result.append(dict(name=c['name'],channels=c['channels'],matched_trials=matches,same_is_nearest_count=sum(v['same_is_nearest'] for v in matches),denominator=len(matches),
            semantics='Off-diagonal normalized covariance pattern mean squared distance; not a fitted classifier or independent accuracy.'))
    return result

def extract(parent,intervals,role,reference='as_recorded'):
    if role not in ['train','validation']:raise ValueError('Test EEG decoding is forbidden in original channel assessment')
    return _extract_analysis_role(parent,intervals,role,reference)

def _extract_analysis_role(parent,intervals,role,reference='as_recorded'):
    from scipy.signal import butter,sosfilt,welch
    from .personal_study import load_episodes
    from .quality import inspect_signal
    if role not in ['train','validation','test']:raise ValueError('Unknown provenance role')
    original={r['id']:r for r in load_episodes(parent,role)};result=[];supports=[];sos=butter(4,[8,30],fs=250,btype='bandpass',output='sos')
    for interval in intervals:
        if interval['split']!=role:continue
        r=original[interval['trial_id']];m=r['metadata'];a=interval['raw_filter_start']-m['context_start'];b=interval['raw_filter_end']-m['context_start'];raw=r['x'][:,a:b].astype('float64')
        if reference=='motor24_common_average':raw=raw-raw[8:].mean(0,keepdims=True)
        elif reference!='as_recorded':raise ValueError('Unreviewed rereference')
        filtered=sosfilt(sos,raw,axis=-1);starts=list(range(interval['start'],interval['end']-500+1,500))
        for start in starts:
            at=start-interval['raw_filter_start'];x=raw[:,at:at+500];z=filtered[:,at:at+500]
            # Rereference is an analysis sensitivity; electrical eligibility
            # is always judged on the same original physical observations.
            original_x=r['x'][:,start-m['context_start']:start-m['context_start']+500];q=inspect_signal(original_x,CHANNELS,250,ELECTRICAL)
            support=dict(**{k:interval[k] for k in ['trial_id','stream_id','label','split','guard_samples']},start=start,end=start+500,
                raw_filter_support_start=interval['raw_filter_start'],raw_filter_support_end=start+500,reference=reference,quality_valid=q['electrical_valid'],reasons=q['reasons'])
            supports.append(support)
            if not q['electrical_valid']:continue
            f,p=welch(z,fs=250,nperseg=500,noverlap=0,axis=-1);fr,pr=welch(x,fs=250,nperseg=500,noverlap=0,axis=-1)
            power=np.stack([p[:,(f>=8)&(f<13)].sum(1)*.5,p[:,(f>=13)&(f<=30)].sum(1)*.5],axis=1)
            features=np.log(np.maximum(power,1e-12));spectrum=np.log(np.maximum(p[:,(f>=8)&(f<=30)],1e-12))
            low=pr[:,(fr>=.5)&(fr<=4)].sum(1)*.5;high=pr[:,(fr>=8)&(fr<=30)].sum(1)*.5
            corr=np.corrcoef(z);corr=np.nan_to_num(corr);np.fill_diagonal(corr,1.)
            result.append(dict(**support,features=features,spectrum=spectrum,correlation=corr,low_frequency_ratio=low/(low+high+1e-12)))
    return result,supports

def make_protocol(parent,catalog,root):
    return dict(schema=SCHEMA,parent_dataset_id=digest(parent),catalog_sha256=digest(catalog),scope=SCOPE,
        code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},
        fit_roles=['train'],diagnostic_roles=['validation'],test_access='forbidden',guards=[250,125],references=['as_recorded','motor24_common_average'],
        sampling_rate=250,channels=CHANNELS,window_samples=500,max_windows_per_trial=None,complete_trial_equal_weight=True,quality=ELECTRICAL,gradient_updates=0,model_updates=0,max_wall_seconds=900,
        selection='training_Fisher_bandpower_and_training_redundancy_only; validation cannot change candidate sets')

def run(parent,catalog,protocol,root,profile,out):
    from .governance import check_runtime
    from .personal_study import verify_sources
    started=time.monotonic();check_runtime(profile);out=Path(out)
    if protocol!=make_protocol(parent,catalog,Path(root)):raise ValueError('Locked channel analysis source/input changed')
    if any(parent['channels'][i]!=CHANNELS[i] for i in range(32)):raise ValueError('Physical montage changed')
    source_trials={q['trial_id']:q for q in parent['episodes']}
    for q in catalog['trials']:
        r=source_trials[q['trial_id']]
        if any(q[a]!=r[b] for a,b in [('task_start','start'),('task_end','end'),('split','split'),('stream_id','stream_id'),('label','label')]):raise ValueError('Human source/trial role differs from parent')
    verify_sources(parent['folder'],parent['source_receipt']);primary=strict_intervals(catalog,250);train,ts=extract(parent,primary['included'],'train')
    if len(grouped(train))<2:raise ValueError('Insufficient electrically eligible complete training trials')
    ranking=rank_channels(train);trials=trial_representations(train);corr=np.mean([t['correlation'] for t in trials],axis=0);candidates=select_candidates(ranking,corr)
    atomic(out/'selection-freeze.json',dict(protocol_id=digest(protocol),candidates=candidates,ranking=ranking,validation_decoded=False,test_decoded=False))
    validation,vs=extract(parent,primary['included'],'validation');similarity=similarity_audit(train,validation)
    sensitivity={};all_support=dict(train=ts,validation=vs)
    for name,guard,reference in [('guard_halfsecond',125,'as_recorded'),('motor24_car',250,'motor24_common_average')]:
        intervals=strict_intervals(catalog,guard);rows,support=extract(parent,intervals['included'],'train',reference);rank=rank_channels(rows)
        old=[v for v in ranking['ranking'] if v in CHANNELS[8:]][:8];new=[v for v in rank['ranking'] if v in CHANNELS[8:]][:8]
        sensitivity[name]=dict(ranking=rank,top8_overlap=len(set(old)&set(new)),top8_jaccard=len(set(old)&set(new))/len(set(old)|set(new)),intervals=intervals)
        all_support[name]=support
    scale=np.asarray(ranking['feature_scale']);reference=np.asarray(ranking['feature_mean']);verification=[]
    for c in candidates:
        ix=c['physical_indices_zero_based'];same=[];different=[];matches=[]
        for v in trial_representations(validation):
            own=[t for t in trials if t['label']==v['label']];other=[t for t in trials if t['label']!=v['label']]
            if not own:continue
            def distance(t):return float(np.mean(((t['features'][ix]-v['features'][ix])/scale[ix])**2))
            a=min(distance(t) for t in own);b=min(distance(t) for t in other);same.append(a);different.append(b);matches.append(dict(label=v['label'],trial_id=v['trial_id'],same_distance=a,nearest_other_distance=b,same_is_nearest=a<b))
        verification.append(dict(name=c['name'],channels=c['channels'],matched_trials=matches,same_is_nearest_count=sum(q['same_is_nearest'] for q in matches),denominator=len(matches),
            mean_margin=float(np.mean(np.asarray(different)-np.asarray(same))) if same else None,semantics='diagnostic distance comparison, not classifier training/accuracy or four-direction selection'))
    channel_rows=[]
    for i,name in enumerate(CHANNELS):
        sm=similarity['channels'][i];row=dict(sm,training_separation=ranking['score'][i],train_low_frequency_ratio=float(np.mean([np.mean([q['low_frequency_ratio'][i] for q in values]) for values in grouped(train).values()])),
            max_frontal_motorband_correlation=float(max(abs(corr[i,j]) for j in range(8) if j!=i)),motor_candidate_pool=i>=8)
        channel_rows.append(row)
    representations={role:[dict(trial_id=q['trial_id'],label=q['label'],split=q['split'],windows=q['windows'],features=q['features'].tolist(),spectrum=q['spectrum'].tolist(),correlation=q['correlation'].tolist()) for q in trial_representations(rows)] for role,rows in [('train',train),('validation',validation)]}
    atomic(out/'supports.json',all_support);atomic(out/'representations.json',representations)
    arrays={role+'_'+kind:np.asarray([r[kind] for r in rows]) for role,rows in [('train',train),('validation',validation)] for kind in ['features','spectrum','correlation']};np.savez(out/'analysis-features.npz',**arrays)
    verify_sources(parent['folder'],parent['source_receipt'])
    elapsed=time.monotonic()-started
    if elapsed>protocol['max_wall_seconds']:raise InterruptedError('Diagnostic time budget exceeded')
    report=dict(schema=SCHEMA,protocol_id=digest(protocol),intervals=primary,ranking=ranking,similarity=similarity,channels=channel_rows,
        candidates=candidates,candidate_verification=verification,joint_structure_verification=structure_audit(train,validation,candidates),sensitivity=sensitivity,trial_support={role:[dict(trial_id=t['trial_id'],label=t['label'],windows=t['windows']) for t in trial_representations(rows)] for role,rows in [('train',train),('validation',validation)]},
        source_files_verified=len(parent['source_receipt']['files']),elapsed_s=elapsed,channels_physical=CHANNELS,test_decoded=False,gradient_updates=0,model_updates=0,
        limitations=['Joint imagery/gaze task, no pure MI source ground truth.', 'One strict training trial per observed class, labels confounded with trial identity.',
         'BACKWARD missing strict training and RIGHT missing strict validation.', 'Cross-trial same-direction checks only FORWARD and LEFT; cannot conclude four-way optimal channels.'])
    atomic(out/'report.json',report);return report

def main():
    import argparse
    ap=argparse.ArgumentParser()
    for k in ['parent','catalog','protocol','code-root','profile','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();r=run(read_json(a.parent),read_json(a.catalog),read_json(a.protocol),Path(a.code_root),read_json(a.profile),Path(a.output))
    print('navigation_channel_audit_complete',r['elapsed_s'])
if __name__=='__main__':main()
