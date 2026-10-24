"""Source-bound target-position research; never relabel as private navigation.

Targets are instructed screen angles, not measured gaze. Native EOG units and
vertical sign remain unresolved. Head pose is audited, never an input feature.
"""
import argparse,json,sys
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.io import loadmat


def phase_label(variant,phase,target):
    if phase==2:return 'BLINK_TASK'
    if variant=='dataset1' and phase==1:return 'CENTER_RETURN_TASK'
    dx,dy=map(float,target)
    if max(abs(dx),abs(dy))<5:return None
    if abs(dx)>=1.5*abs(dy):return 'TARGET_H_POS' if dx>0 else 'TARGET_H_NEG'
    if abs(dy)>=1.5*abs(dx):return 'TARGET_V_POS' if dy>0 else 'TARGET_V_NEG'
    return None


def stable_target_label(stream):
    """Native angle can vary with head pose; require a stable final 250ms class.

    This is label definition only. Angles and head pose are not model features.
    """
    labels=[phase_label('nonstationary',0,v) for v in np.asarray(stream)[:,-64:].T]
    return labels[0] if labels and labels[0] is not None and all(v==labels[0] for v in labels) else None


def study_name(plan_id,plan_hash,preparer_hash):
    return plan_id+'-'+plan_hash[:12]+'-'+preparer_hash[:12]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--variant',choices=['dataset1','stationary','nonstationary'],required=True);a=ap.parse_args();root=Path(a.root).resolve()
    release=root/'releases'/(root/'current-release').read_text().strip();sys.path.insert(0,str(release))
    from gnautilus_ml.common import atomic,digest,file_hash
    variant=a.variant;source=root/'data/public/malta_eog'/variant;receipt=json.loads((source/'source-receipt.json').read_text())
    zipfile=source/Path(receipt['source']).name
    if file_hash(zipfile)!=receipt['sha256']:raise ValueError('Original eye data ZIP changed')
    folder=source/'extracted'/dict(dataset1='DATASET',stationary='Dataset_Stationary',nonstationary='Dataset_NonStationary')[variant]
    reader='malta_target_'+variant;subjects=sorted(folder.glob('S*'),key=lambda p:int(p.name[1:]));n=len(subjects);train_n=dict(dataset1=4,stationary=7,nonstationary=5)[variant]
    if n!=dict(dataset1=6,stationary=10,nonstationary=8)[variant]:raise ValueError('Published subject count differs')
    policy=dict(schema='public.native_eog_qc.v1',reject_nonfinite=True,reject_exact_flat_channel=True,amplitude_unit='not_established_original_native_scale',absolute_amplitude_quality='unknown; no uV thresholds applied',contact_quality='unknown',scope='native EOG numerical research only')
    plan=dict(id='public-gaze-target-'+variant+'-20261024',source_sha256=receipt['sha256'],sampling_rate=256,window_s=1.25,offset_samples=-128,end_after_instruction_samples=192,
        training_subjects=[p.name for p in subjects[:train_n]],development_subjects=[p.name for p in subjects[train_n:]],quality=policy,
        labels='instructed native target-angle axes at output time, CENTER_RETURN_TASK only in dataset1, BLINK_TASK; not measured gaze/REST/navigation',
        direction_rule=dict(minimum_axis_angle_degrees=5,dominant_axis_ratio=1.5,diagonal_and_small='unassigned'),head_pose_input=False,
        target_stability='same native axis/sign throughout final 250ms; angles need not be constant under head movement',
        intended_use='public EOG method development; no g.Nautilus scalp/VR acceptance; holds longer than one second unsupported')
    ph=digest(plan);out=root/'studies'/study_name(plan['id'],ph,file_hash(__file__));out.mkdir(parents=True,exist_ok=True);atomic(out/'source-plan.json',plan)
    cache=root/'data/public_prepared'/ph/file_hash(__file__)[:12];cache.mkdir(parents=True,exist_ok=True);records=[];summaries=[]
    for sub in subjects:
        eog=sub/'EOG.mat';x=np.asarray(loadmat(eog)['EOG'],dtype=np.float64);c=loadmat(sub/'ControlSignal.mat')['ControlSignal'].ravel()
        trials=300 if variant=='dataset1' else 200;expected_channels=2 if variant=='dataset1' else 4
        if x.shape!=(expected_channels,len(c)) or not np.isfinite(x).all():raise ValueError('Unexpected source shape or nonfinite source')
        target=loadmat(sub/('TargetGA.mat' if variant=='dataset1' else 'Target_GA_stream.mat'))
        target=np.asarray(target['TargetGA' if variant=='dataset1' else 'Target_GA_stream'])
        if target.shape!=((600,2) if variant=='dataset1' else (2,len(c))) or not np.isfinite(target).all():raise ValueError('Invalid target stream')
        edges=np.r_[0,np.flatnonzero(np.diff(c))+1,len(c)];runs=[(int(s),int(e),int(c[s])) for s,e in zip(edges[:-1],edges[1:])]
        if [v[2] for v in runs]!=[1,2,3]*trials+[30]:raise ValueError('Source phase ordering differs')
        windows=[];labels=[];identities=[];assessments=[];counts=Counter();rawhash=file_hash(eog)
        for i,(start,end,phase) in enumerate(runs[:-1]):
            trial=i//3;phase-=1
            angle=target[2*trial] if variant=='dataset1' else target[:,start]
            label=phase_label(variant,phase,angle)
            if variant!='dataset1' and phase!=2:label=stable_target_label(target[:,start:min(end,start+192)])
            if label is None:counts['diagonal_or_small_target']+=1;continue
            counts['candidate']+=1;s=start-128;e=start+192;reasons=[]
            if s<0 or e>end:reasons.append('source_or_instruction_boundary')
            elif np.any(np.ptp(x[:,s:e],axis=1)==0):reasons.append('exact_flat_channel')
            assessments.append(dict(trial=i,start=s,end=e,native_label=label,eligible=not reasons,reasons=reasons,instruction_phase=phase+1,
                instructed_target_degrees=([0.,0.] if phase==1 else angle.tolist()) if variant=='dataset1' else target[:,min(e,end)-1].tolist()))
            if reasons:counts.update(reasons);continue
            windows.append(x[:,s:e].astype(np.float32));labels.append(label);identities.append(dict(source=rawhash,trial=i,start=s,end=e,native_label=label,source_sampling_rate=256))
        support=Counter(labels)
        if len(support)!=(6 if variant=='dataset1' else 5) or min(support.values())<3:raise ValueError('Insufficient source classes: '+str(dict(variant=variant,subject=sub.name,support=dict(support),counts=dict(counts))))
        key=digest(dict(plan=ph,source=rawhash,preparer=file_hash(__file__)));npz=cache/(key+'.npz');rp=cache/(key+'.json');role='train' if sub.name in plan['training_subjects'] else 'development'
        channels=['EOG_H','EOG_V'] if variant=='dataset1' else ['EOG_E1','EOG_E2','EOG_E3','EOG_E4']
        ancillary=[dict(path=str(p),sha256=file_hash(p)) for p in sorted(sub.glob('*.mat')) if p!=eog]+[dict(path=str(p),sha256=file_hash(p)) for p in sorted(folder.glob('*.pdf'))]+[dict(path=str(zipfile),sha256=file_hash(zipfile))]
        gross=dict(min_native=x.min(1).tolist(),max_native=x.max(1).tolist(),nonfinite_by_channel=np.sum(~np.isfinite(x),axis=1).tolist())
        if variant=='nonstationary':
            head=loadmat(sub/'Head_Pose.mat')['Head_Pose'];gross['head_yaw_pitch_roll_ptp_degrees']=np.ptp(head,axis=1).tolist()
        report=dict(schema='gnautilus.public_record_qc.v1',source_sha256=rawhash,source_path=str(eog),preparation_code_sha256={str(Path(__file__).resolve()):file_hash(__file__)},plan_sha256=ph,reader=reader,
            source_samples=x.shape[1],source_sampling_rate=256,sampling_rate=256,channels=channels,native_channels=channels,window_s=1.25,role=role,subject=sub.name,
            native_session='complete published participant source',ancillary_files=ancillary,quality_policy=policy,trials=assessments,retained_windows=identities,retained_class_counts=dict(support),
            coverage=len(windows)/counts['candidate'],admitted_for_pilot=True,gross_observations=gross,protocol_scope_counts=dict(counts),total_instruction_phases=trials*3,
            actual_gaze_position='not independently measured; native target-angle instruction only; head-motion coordinate transform not reproduced',vertical_screen_sign='unresolved',head_pose_used_as_input=False)
        if npz.exists() or rp.exists():
            if not (npz.exists() and rp.exists()) or json.loads(rp.read_text())!=report:raise ValueError('Immutable target receipt changed')
            with np.load(npz,allow_pickle=False) as old:
                if not np.array_equal(old['x'],np.asarray(windows)) or not np.array_equal(old['y'],np.asarray(labels,dtype='<U24')):raise ValueError('Immutable target windows changed')
        else:
            with npz.open('wb') as stream:np.savez(stream,x=np.asarray(windows),y=np.asarray(labels,dtype='<U24'))
            atomic(rp,report)
        records.append(dict(schema='gnautilus.dataset_record.v1',reader=reader,path=str(eog),source_sha256=rawhash,channels=channels,sampling_rate=256,
            reference='published bipolar H=V3-V4,V=V1-V2; mastoid' if variant=='dataset1' else 'published four monopolar ocular channels; mastoid R',
            filter_configuration=dict(source_bandpass_hz=[0,30],source_notch_hz=50,derived='none; finite history only',unit='original_native_scale_unresolved'),subject=sub.name,date=None,wearing_id=None,role=role,closed=True,training_ready=True,electrical_policy=policy,
            public_metadata=dict(admission='approved',license_receipt='Publisher research download and requested citation: '+receipt['landing_page']+'; no redistribution license asserted',source_version=variant+' / '+receipt['sha256'],grouping=dict(level='subject',native_id=sub.name),source_session_verified=True,source_unit='unknown_native_scale'),
            prepared_windows=dict(schema='gnautilus.public_prepared.v1',path=str(npz),sha256=file_hash(npz),report_path=str(rp),report_sha256=file_hash(rp))))
        summaries.append(dict(subject=sub.name,role=role,retained=len(windows),support=dict(support),counts=dict(counts),gross=gross,report_path=str(rp)))
    dataset=dict(schema='gnautilus.dataset.v1',title='眼动目标方向 / Malta '+variant+' / 完整参与者分组',records=records,study_plan_sha256=ph,scope='public_method_development',quality_summary_path=str(out/'quality-summary.json'))
    did=digest(dataset);atomic(root/'datasets'/(did+'.json'),dataset);atomic(out/(reader+'-dataset.json'),dict(id=did,dataset=dataset));atomic(out/'quality-summary.json',dict(plan=plan,records=summaries,training_started=False));atomic(out/'quality-completion.json',dict(completed=True,error_count=0,assessed_files=n,scope='numeric EOG/source-target review; physical amplitude quality unresolved'))
    print(json.dumps(dict(study=str(out),dataset_id=did,reader=reader,plan_sha256=ph,records=summaries),default=lambda v:v.item()))


if __name__=='__main__':main()
