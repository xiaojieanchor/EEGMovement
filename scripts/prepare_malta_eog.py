"""Source-bound native EOG research windows, not scalp EEG or navigation data.

The publisher supplies target gaze angles and instruction intervals, not measured
eye positions. Native amplitude units and the screen vertical sign remain
explicitly unresolved. Fits may compare unit-invariant numeric pipelines only;
these records do not satisfy a live g.Nautilus input contract.
"""
import argparse,hashlib,json,sys
from collections import Counter
from pathlib import Path
import numpy as np
from scipy.io import loadmat


def native_direction(delta):
    dx,dy=map(float,delta)
    if max(abs(dx),abs(dy))<5:return None
    if abs(dx)>=1.5*abs(dy):return 'GAZE_H_POS' if dx>0 else 'GAZE_H_NEG'
    if abs(dy)>=1.5*abs(dx):return 'GAZE_V_POS' if dy>0 else 'GAZE_V_NEG'
    return None


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);a=ap.parse_args();root=Path(a.root).resolve()
    release=root/'releases'/(root/'current-release').read_text().strip();sys.path.insert(0,str(release))
    from gnautilus_ml.common import atomic,digest,file_hash
    src=root/'data/public/malta_eog/dataset1';folder=src/'extracted/DATASET';receipt=json.loads((src/'source-receipt.json').read_text())
    if file_hash(src/'DATASET.zip')!=receipt['sha256']:raise ValueError('Original reference ZIP changed')
    policy=dict(schema='public.native_eog_qc.v1',reject_nonfinite=True,reject_exact_flat_channel=True,
        amplitude_unit='not_established_original_native_scale',absolute_amplitude_quality='unknown; no uV thresholds applied',
        contact_quality='unknown',scope='native bipolar EOG; neither scalp EEG nor independent actual-eye-position measurement')
    plan=dict(id='public-malta-eog-20261024',source_sha256=receipt['sha256'],sampling_rate=256,window_s=.75,offset_samples=-32,
        training_subjects=['S1','S2','S3','S4'],development_subjects=['S5','S6'],quality=policy,
        labels='H/V signed target-angle displacement, plus BLINK_TASK; no automatic UP/DOWN/nav mapping',
        direction_rule=dict(minimum_axis_displacement_degrees=5,dominant_axis_ratio=1.5,diagonal='unassigned'),
        intended_use='public EOG methodology only; live input units/montage/polarity require independent confirmation')
    ph=digest(plan);out=root/'studies'/('public-malta-eog-20261024-'+ph[:12]);out.mkdir(parents=True,exist_ok=True)
    cache=root/'data/public_prepared'/ph/file_hash(__file__)[:12];cache.mkdir(parents=True,exist_ok=True);atomic(out/'source-plan.json',plan)
    records=[];summaries=[]
    for sub in plan['training_subjects']+plan['development_subjects']:
        p=folder/sub;source=p/'EOG.mat';x=np.asarray(loadmat(source)['EOG'],dtype=np.float64);control=loadmat(p/'ControlSignal.mat')['ControlSignal'].ravel();targets=np.asarray(loadmat(p/'TargetGA.mat')['TargetGA'])
        if x.shape!=(2,len(control)) or targets.shape!=(600,2) or not np.isfinite(targets).all() or not np.all(targets[1::2]==0):raise ValueError('Native EOG/control/target identity differs')
        edges=np.r_[0,np.flatnonzero(np.diff(control))+1,len(control)];runs=[(int(s),int(e),int(control[s])) for s,e in zip(edges[:-1],edges[1:])]
        if [r[2] for r in runs]!=[1,2,3]*300+[30]:raise ValueError('Native 300-trial event order differs from the publisher protocol')
        windows=[];labels=[];identities=[];trials=[];counts=Counter();source_hash=file_hash(source)
        for trial in range(300):
            delta=targets[2*trial]
            for phase in range(3):
                start,end,code=runs[trial*3+phase];label='BLINK_TASK' if phase==2 else native_direction(delta if phase==0 else -delta)
                if label is None:counts['non_cardinal_or_small_displacement']+=1;continue
                counts['candidate']+=1;s=start-32;e=s+192;reason=[]
                if s<0 or e>end:reason.append('native_phase_or_record_boundary')
                elif not np.isfinite(x[:,s:e]).all():reason.append('nonfinite')
                elif np.any(np.ptp(x[:,s:e],axis=1)==0):reason.append('exact_flat_channel')
                item=dict(trial=trial*3+phase,native_label=label,start=s,end=e,eligible=not reason,reasons=reason,
                    instruction_code=code,target_gaze_displacement_degrees=None if phase==2 else (delta if phase==0 else -delta).tolist())
                trials.append(item)
                if reason:
                    counts.update(reason);continue
                sample=x[:,s:e].astype(np.float32);windows.append(sample);labels.append(label)
                identities.append(dict(source=source_hash,trial=trial*3+phase,start=s,end=e,native_label=label,source_sampling_rate=256))
        support=Counter(labels)
        if len(support)!=5 or min(support.values())<3:raise ValueError('Insufficient native direction/blink support after quality review')
        role='train' if sub in plan['training_subjects'] else 'development';key=digest(dict(plan=ph,source=file_hash(source),preparer=file_hash(__file__)))
        npz=cache/(key+'.npz');report_path=cache/(key+'.json')
        ancillary=[dict(path=str(f),sha256=file_hash(f)) for f in [p/'ControlSignal.mat',p/'TargetGA.mat',folder/'DataDescription.pdf',src/'DATASET.zip']]
        report=dict(schema='gnautilus.public_record_qc.v1',source_sha256=file_hash(source),source_path=str(source),
            preparation_code_sha256={str(Path(__file__).resolve()):file_hash(__file__)},plan_sha256=ph,reader='malta_eog',
            source_samples=x.shape[1],source_sampling_rate=256,sampling_rate=256,channels=['EOG_H','EOG_V'],native_channels=['EOG_H','EOG_V'],
            window_s=.75,role=role,subject=sub,native_session='three published runs within this source; not split across roles',native_partition=None,
            ancillary_files=ancillary,quality_policy=policy,trials=trials,retained_windows=identities,source_class_counts=dict(Counter(v['native_label'] for v in trials)),
            retained_class_counts=dict(support),coverage=len(windows)/counts['candidate'],admitted_for_pilot=True,
            gross_observations=dict(nonfinite_by_channel=np.sum(~np.isfinite(x),axis=1).tolist(),min_native=x.min(1).tolist(),max_native=x.max(1).tolist()),
            protocol_scope_counts=dict(counts),total_instruction_phases=900,actual_gaze_position='not measured; target-angle supervision',vertical_screen_sign='unresolved')
        if npz.exists() and report_path.exists():
            if json.loads(report_path.read_text())!=report:raise ValueError('Immutable EOG receipt changed')
            with np.load(npz,allow_pickle=False) as previous:
                if not np.array_equal(previous['x'],np.asarray(windows)) or not np.array_equal(previous['y'],np.asarray(labels,dtype='<U24')):raise ValueError('Immutable EOG numerical cache changed')
        else:
            with npz.open('wb') as stream:np.savez(stream,x=np.asarray(windows),y=np.asarray(labels,dtype='<U24'))
            atomic(report_path,report)
        record=dict(schema='gnautilus.dataset_record.v1',reader='malta_eog',path=str(source),source_sha256=file_hash(source),channels=report['channels'],sampling_rate=256,
            reference='published bipolar H=V3-V4 and V=V1-V2; original mastoid reference',
            filter_configuration=dict(source_bandpass_hz=[0,30],source_notch_hz=50,derived='none; finite history only',unit='original_native_scale_unresolved'),
            subject=sub,date=None,wearing_id=None,role=role,closed=True,training_ready=True,electrical_policy=policy,
            public_metadata=dict(admission='approved',license_receipt='Publisher explicitly offers research data and requests citation: '+receipt['landing_page']+'; no redistribution license asserted',source_version='Malta dataset1 / '+receipt['sha256'],grouping=dict(level='subject',native_id=sub),source_session_verified=True,source_unit='unknown_native_scale'),
            prepared_windows=dict(schema='gnautilus.public_prepared.v1',path=str(npz),sha256=file_hash(npz),report_path=str(report_path),report_sha256=file_hash(report_path)))
        records.append(record);summaries.append(dict(subject=sub,role=role,retained=len(windows),support=dict(support),counts=dict(counts),report=str(report_path)))
    dataset=dict(schema='gnautilus.dataset.v1',title='公开EOG / Malta 原生轴向与眨眼任务 / 跨参与者',records=records,
        study_plan_sha256=ph,scope='public_method_development',training_admission='native EOG numerical and protocol review; physical amplitude units unresolved',quality_summary_path=str(out/'quality-summary.json'))
    did=digest(dataset);atomic(root/'datasets'/(did+'.json'),dataset);atomic(out/'malta_eog-dataset.json',dict(id=did,dataset=dataset));atomic(out/'quality-summary.json',dict(plan=plan,records=summaries,training_started=False))
    atomic(out/'quality-completion.json',dict(completed=True,error_count=0,assessed_files=6,scope='native unit-invariant EOG research, not physical uV/contact-quality acceptance'))
    print(json.dumps(dict(study=str(out),dataset_id=did,plan_sha256=ph,records=summaries)))


if __name__=='__main__':main()
