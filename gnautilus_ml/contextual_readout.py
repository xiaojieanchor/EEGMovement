"""Paired, governed repair of authentic context in ocular-assisted readouts.

No annotation proposals become targets. The previously inspected test is never
decoded by this entrypoint. Older experiment modules retain their old behavior.
"""
import time,json
from pathlib import Path
import numpy as np
from .common import read_json,atomic,digest,file_hash
from .navigation_contract import CHANNELS,SCOPE,ELECTRICAL
from .personal_study import load_episodes,verify_sources,episode_bounds
from .ocular_boundary_audit import load_contextual_episodes
from .eye_joint_probe import CausalEvidenceFeatures,fit_readout,predict_readout,verify_models,fuse_probabilities
from .governance import training_authority,compute_device

SCHEMA='gnautilus.contextual_readout_protocol.v1'

def overlaps(left,right):
    return [dict(left=a,right=b) for a in left for b in right if a['stream_id']==b['stream_id'] and a['start']<b['end'] and a['end']>b['start']]

def merge_rows(rows):
    """A role change is a real research boundary, a trial change is not."""
    if len({r['metadata']['split'] for r in rows})>1:raise ValueError('Only one source role may be merged')
    result=[]
    for sid in sorted({r['metadata']['stream_id'] for r in rows}):
        groups=[]
        for r in sorted([v for v in rows if v['metadata']['stream_id']==sid],key=lambda x:x['metadata']['context_start']):
            if groups and r['metadata']['context_start']<=max(v['metadata']['context_end'] for v in groups[-1]):groups[-1].append(r)
            else:groups.append([r])
        for group in groups:
            lo=min(r['metadata']['context_start'] for r in group);hi=max(r['metadata']['context_end'] for r in group);x=np.empty((32,hi-lo),dtype='float32');y=np.full(hi-lo,-1,dtype='int8');covered=np.zeros(hi-lo,bool);anchors=[]
            for r in group:
                m=r['metadata'];a=m['context_start']-lo;b=m['context_end']-lo;seen=covered[a:b]
                if seen.any() and not np.array_equal(x[:,a:b][:,seen],r['x'][:,seen]):raise ValueError('Overlapping source bytes changed')
                x[:,a:b]=r['x'];covered[a:b]=True;known=r['y']>=0
                conflict=known&(y[a:b]>=0)&(y[a:b]!=r['y'])
                if conflict.any():raise ValueError('Conflicting source label supports')
                y[a:b][known]=r['y'][known];anchors.append(dict(trial_id=r['id'],onset=m['start']-lo,offset=m['end']-lo))
            if not covered.all():raise ValueError('Cannot bridge a missing sample range')
            result.append(dict(id=sid+':'+str(lo)+':'+str(hi),x=x,y=y,metadata=dict(stream_id=sid,split=group[0]['metadata']['split'],context_start=lo,context_end=hi,anchors=anchors)))
    return result

def metadata_at(row,end):
    if row['y'][end-1]<0:return '','UNKNOWN'
    m=row['metadata'];anchors=m.get('anchors',[dict(trial_id=row['id'],onset=m.get('onset_in_episode',0),offset=m.get('offset_in_episode',len(row['y'])))])
    matches=[a for a in anchors if a['onset']<=end-1<a['offset']]
    if len(matches)!=1:raise ValueError('Known source label has no unique complete trial')
    a=matches[0];return a['trial_id'],'onset_0_2s' if end-1<a['onset']+500 else 'hold_after2s'

def summarize(frames,p):
    y=np.asarray([r['label'] for r in frames]);p=np.asarray(p);known=y>=0;ready=p.sum(1)>0;pred=p.argmax(1);correct=known&ready&(pred==y)
    count=np.bincount(y[known],minlength=5);ready_count=np.bincount(y[known&ready],minlength=5)
    recall=[float(correct[y==k].sum()/count[k]) if count[k] else None for k in range(5)]
    conditional=[float(correct[y==k].sum()/ready_count[k]) if ready_count[k] else None for k in range(5)]
    ce=float(np.mean(-np.log(np.maximum(p[np.arange(len(y))[known&ready],y[known&ready]],1e-8)))) if np.any(known&ready) else None
    return dict(full_labelled_updates=int(known.sum()),ready_labelled_updates=int((known&ready).sum()),unknown_updates=int((~known).sum()),full_class_support=count.tolist(),ready_class_support=ready_count.tolist(),
        balanced_accuracy_with_rejections=float(np.mean(recall)) if all(v is not None for v in recall) else None,
        balanced_accuracy_ready=float(np.mean(conditional)) if all(v is not None for v in conditional) else None,
        recall_with_rejections=recall,recall_ready=conditional,ready_ce=ce,readiness_coverage=float((known&ready).sum()/max(1,known.sum())),
        score_semantics='Uncalibrated joint-instruction score; no objective gaze truth')

def causal_average(p,frames,tau=1.):
    p=np.asarray(p);result=np.zeros_like(p);previous=None;state=None
    for i,(v,r) in enumerate(zip(p,frames)):
        key=(r['sequence_id'],r['reset'])
        if not v.sum():state=None;previous=key;continue
        state=v.copy() if key!=previous or state is None else state+(1-np.exp(-.1/tau))*(v-state)
        result[i]=state;previous=key
    return result

def context_inputs(ds,mode,role):
    if role not in ['train','validation']:raise ValueError('This repair never decodes test EEG')
    return load_episodes(ds,role) if mode=='crop_control' else merge_rows(load_contextual_episodes(ds,role,pre=2000,post=750))

def make_protocol(ds,root,models,prior):
    root=Path(root)
    return dict(schema=SCHEMA,scope=SCOPE,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((root/'gnautilus_ml').glob('*.py'))},
        model_sources=models,roles=['train','validation'],fit_roles=['train'],test_access='forbidden_even_previously_inspected',
        training_arms=['crop_control','context_repair'],evaluation_views=['crop_control','context_repair'],features=['eye_signed','all32_multiscale'],
        authentic_context_pre_samples=2000,authentic_context_post_samples=750,same_role_source_merge=True,
        readout_steps=250,readout_l2_sum=.05,seed=17,normalization='Train-only equal complete-trial mass; UNKNOWN excluded from fit',
        label_truth='Original joint task instruction only; proposed events remain unverified and unused as targets',
        eye_fusion_gain=.1,evaluation_ema_seconds=[0.,1.],warmup_samples=375,hop_samples=25,quality=ELECTRICAL,
        prior_elapsed_s=float(prior),max_wall_seconds=900,total_budget_s=43200,registration='offline_research_arrays_only',
        selection='Development descriptive comparison; no model/action selection or threshold tuning')

def check_protocol(p,ds,root):
    if p!=make_protocol(ds,root,p['model_sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>p['total_budget_s']:raise ValueError('Context repair source/recipe/budget differs')
    if ds.get('schema')!='gnautilus.personal_study.v1' or ds.get('scope')!=SCOPE or ds.get('channels')!=CHANNELS or ds.get('sampling_rate')!=250:raise ValueError('Wrong source contract')
    episode_bounds(ds['episodes']);verify_sources(ds['folder'],ds['source_receipt']);verify_models(Path(ds['folder']).parent.parent,p['model_sources']);return True

def extract(rows,model,check):
    frames=[];values=[]
    for row in rows:
        session=model.new_session();feature=CausalEvidenceFeatures();reset=0
        for end in range(25,row['x'].shape[1]+1,25):
            check();xx=row['x'][:,end-25:end];tid,phase=metadata_at(row,end);meta=row['metadata'];status='READY';f=None;o=None
            try:o=session.step(xx)
            except ValueError:session=model.new_session();feature.reset();reset+=1;status='QC_REJECTED'
            if o is not None:
                ff=feature.step(xx)
                if o['warming']:status='WARMUP'
                else:
                    if len(ff)!=1:raise ValueError('Feature and neural sample supports differ')
                    f=ff[0]
            frames.append(dict(sequence_id=row['id'],reset=reset,trial_id=tid,phase=phase,label=int(row['y'][end-1]),stream_id=meta['stream_id'],
                source_sample_end=meta['context_start']+end,status=status,physical_samples_consumed=end,original_joint_scores=o['probabilities'] if o is not None and not o['warming'] else [0.]*5))
            values.append(f)
    return frames,values

def frozen_training_supports(base):
    folder=Path(base)/'logs/jepa-9fea6135e1ed46799a1a8c819ca55396';result=[]
    for r in read_json(folder/'continuous-supports.json')['training']:
        m=r['metadata'];result.append(dict(stream_id=m['stream_id'],start=m['context_start'],end=m['context_end'],kind='supervised'))
    for line in (folder/'17-jepa_transfer-ssl.supports.jsonl').read_text().splitlines():
        for r in json.loads(line)['private_supports']:result.append(dict(stream_id=r['stream_id'],start=r['context'][0],end=r['targets'][-1][1],kind='private_SSL'))
    return result

def run(base,root,p,ds,profile,out):
    import torch
    from .joint_stream import JointStreamModel
    from .eye_joint_probe import decision_report
    started=time.monotonic();out=Path(out)
    def check():
        if time.monotonic()-started>p['max_wall_seconds']:raise InterruptedError('Context repair budget exhausted')
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();torch.set_num_threads(2);source=Path(base)/p['model_sources']['candidate']['path'];meta=read_json(source.parent/'manifest.json')
        with np.load(source,allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
        details=dict(meta['details']);details['decision_policy']=dict(details['decision_policy'],temperature=1.)
        model=JointStreamModel(arrays,details,device);prepared={};ranges={};input_receipt={};support=[]
        for mode in p['training_arms']:
            for role in p['roles']:
                rows=context_inputs(ds,mode,role);key=mode+'/'+role;ranges[key]=[dict(stream_id=r['metadata']['stream_id'],start=r['metadata']['context_start'],end=r['metadata']['context_end']) for r in rows]
                frames,features=extract(rows,model,check);prepared[key]=(frames,features);support.extend([dict(mode=mode,role=role,**f) for f in frames])
                input_receipt[key]=dict(continuous_regions=len(rows),raw_samples=sum(r['x'].shape[1] for r in rows),full_labelled_updates=sum(f['label']>=0 for f in frames),
                    ready_labelled_updates=sum(f['label']>=0 and f['status']=='READY' for f in frames),
                    early_labelled_updates=sum(f['phase']=='onset_0_2s' for f in frames),early_ready_updates=sum(f['phase']=='onset_0_2s' and f['status']=='READY' for f in frames),
                    regions=[dict(id=r['id'],metadata=r['metadata']) for r in rows])
                atomic(out/'progress.json',dict(phase='features',mode=mode,role=role,elapsed_s=time.monotonic()-started))
        if overlaps(ranges['context_repair/train'],ranges['context_repair/validation']):raise ValueError('Actual train/development raw supports overlap')
        if overlaps(ranges['context_repair/validation'],frozen_training_supports(base)):raise ValueError('New development context intersects frozen model training supports')
        atomic(out/'inputs.json',dict(views=input_receipt,actual_train_validation_overlap=0,frozen_model_train_development_overlap=0,test_decoded=False))
        candidates=[];saved={};prediction_arrays={}
        for arm in p['training_arms']:
            ff,vv=prepared[arm+'/train'];at=[i for i,r in enumerate(ff) if r['label']>=0 and r['status']=='READY'];yy=np.asarray([ff[i]['label'] for i in at]);ids=np.asarray([ff[i]['trial_id'] for i in at])
            for name in p['features']:
                column='eye' if name=='eye_signed' else 'full32';x=np.asarray([vv[i][column] for i in at]);a,h=fit_readout(x,yy,ids,p['readout_steps'],device)
                cid=arm+'__'+name;np.savez(out/(cid+'.npz'),**a);atomic(out/(cid+'-optimization.json'),h);saved[cid]=dict(path=cid+'.npz',sha256=file_hash(out/(cid+'.npz')),supervised_updates=len(at),classes=np.bincount(yy,minlength=5).tolist())
                result=dict(name=cid,training_context=arm,features=name,evaluations={})
                for view,(frames,values) in prepared.items():
                    ready=[i for i,r in enumerate(frames) if r['status']=='READY'];prob=np.zeros((len(frames),5))
                    if ready:prob[ready]=predict_readout(np.asarray([values[i][column] for i in ready]),a)
                    prediction_arrays[cid+'___'+view.replace('/','__')]=prob
                    cuts={}
                    for tau in p['evaluation_ema_seconds']:
                        pp=causal_average(prob,frames,tau) if tau else prob
                        metric=summarize(frames,pp);known=np.asarray([r['label'] for r in frames]);rows_ready=[i for i,r in enumerate(frames) if r['status']=='READY']
                        selected=[frames[i] for i in rows_ready];q=pp[rows_ready]
                        # Legacy decoder helper expects trial IDs; reset identity
                        # follows true continuous regions and QC in the repair.
                        decision_frames=selected
                        metric['decision']=decision_report(known[rows_ready],q,decision_frames,metric['full_labelled_updates'],int(np.sum(known==0))*.1)
                        phases={}
                        for phase in ['onset_0_2s','hold_after2s']:
                            pick=[i for i,r in enumerate(frames) if r['phase']==phase];phases[phase]=summarize([frames[i] for i in pick],pp[pick])
                        metric['phases']=phases;cuts[str(tau)]=metric
                    result['evaluations'][view]=cuts
                candidates.append(result);atomic(out/'progress.json',dict(phase='candidate_complete',candidate=cid,elapsed_s=time.monotonic()-started));print(cid,flush=True)
        # Same fixed bounded auxiliary coefficient; do not tune to test or dev.
        for arm in p['training_arms']:
            result=dict(name=arm+'__full32_plus_eye0.1',training_context=arm,features='convex fusion',evaluations={})
            for view,(frames,values) in prepared.items():
                eye=prediction_arrays[arm+'__eye_signed___'+view.replace('/','__')];all32=prediction_arrays[arm+'__all32_multiscale___'+view.replace('/','__')];prob=fuse_probabilities(all32,eye,p['eye_fusion_gain']);prediction_arrays[result['name']+'___'+view.replace('/','__')]=prob;cuts={}
                for tau in p['evaluation_ema_seconds']:
                    q=causal_average(prob,frames,tau) if tau else prob;cuts[str(tau)]=summarize(frames,q)
                result['evaluations'][view]=cuts
            candidates.append(result)
        np.savez(out/'predictions.npz',**prediction_arrays);atomic(out/'supports.json',support)
        report=dict(schema='gnautilus.contextual_readout_result.v1',protocol_id=digest(p),dataset_id=digest(ds),candidates=candidates,readouts=saved,elapsed_s=time.monotonic()-started,
            test_decoded=False,model_registry_changed=False,old_test_previously_inspected=True,thresholds_tuned=False,
            scope='same_wearing_internal_research; comparison uses identical evaluation view and full labelled support per view',
            limitations=['Each class has one complete training trial.','Labels are joint instructions, not eye position or blink truth.',
              'UNKNOWN pre/post enters state but is not trained as REST/direction.','Role boundary and QC may leave unavoidable warmup.',
              'Training REST official source differs from software development REST.','Frozen encoder not retrained; only the readout input supports changed.'])
        atomic(out/'report.json',report)
    verify_sources(ds['folder'],ds['source_receipt']);verify_models(base,p['model_sources'])

def main():
    import argparse,os
    a=argparse.ArgumentParser();a.add_argument('--root',required=True);a.add_argument('--code-root',required=True);a.add_argument('--protocol',required=True);a.add_argument('--dataset',required=True);a.add_argument('--profile',required=True);a.add_argument('--output',required=True);v=a.parse_args();p=read_json(v.protocol)
    if p['code_sha256'].get('gnautilus_ml/contextual_readout.py')!=file_hash(__file__) or Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(v.protocol).resolve():raise ValueError('Actual entrypoint/protocol differs')
    if Path(v.protocol).parent.resolve()!=Path(v.output).resolve():raise ValueError('Actual output must preserve checked protocol')
    run(Path(v.root),Path(v.code_root),p,read_json(v.dataset),read_json(v.profile),Path(v.output))
if __name__=='__main__':main()
