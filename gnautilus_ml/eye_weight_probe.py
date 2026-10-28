"""Paired head-only experiment isolating whole-trial vs weak-support weighting."""
from pathlib import Path
import copy,time
import numpy as np
from .common import read_json,atomic,digest,file_hash
from .navigation_contract import SCOPE,CHANNELS
from .governance import training_authority,compute_device,authorize_training
from .eye_joint_probe import verify_models,metrics,extract_rows,fuse_probabilities,decision_report
from .personal_study import load_episodes,verify_sources,episode_bounds

SCHEMA='gnautilus.eye_weight_probe_protocol.v1'

def make_protocol(ds,code_root,models,prior_seconds):
    root=Path(code_root)
    return dict(schema=SCHEMA,scope=SCOPE,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((root/'gnautilus_ml').glob('*.py'))},
        model_sources=models,prior_seconds=prior_seconds,max_wall_seconds=600,total_budget_seconds=43200,roles=['train','validation'],fit_roles=['train'],test_access='forbidden',
        arms=['legacy_whole_class_denominator','balanced_actual_weak_support'],steps=250,lr=.003,weight_decay=.01,auxiliary_scale=.1,seed=17,
        source='Same actual private eye_head weights and fixed transfer encoder for both arms',weak_labels='Explicit REST or instruction-anchored first2s only; not eye-position ground truth',
        train_support='Original extended contiguous train support with QC resets',fusion_gain=.2,registration='offline_head_weighting_diagnostic_only')

def check_protocol(p,ds,root):
    if p!=make_protocol(ds,root,p['model_sources'],p['prior_seconds']) or p['prior_seconds']<0 or p['prior_seconds']+p['max_wall_seconds']>p['total_budget_seconds']:raise ValueError('Paired weighting protocol/source/budget differs')
    if ds.get('schema')!='gnautilus.personal_study.v1' or ds.get('scope')!=SCOPE or ds.get('channels')!=CHANNELS or ds.get('sampling_rate')!=250:raise ValueError('Wrong source input')
    episode_bounds(ds['episodes']);verify_sources(ds['folder'],ds['source_receipt']);verify_models(Path(ds['folder']).parent.parent,p['model_sources']);return True

def fit_head(x,y,weak,initial,p,device,arm,check):
    authorize_training()
    import torch
    counts=np.bincount(y,minlength=5);wc=np.bincount(y[weak],minlength=5)
    if np.any(wc==0):raise ValueError('Real weak supervision missing a class')
    weights=1/(5*(counts if arm.startswith('legacy') else wc)[y[weak]])
    mass=np.bincount(y[weak],weights=weights,minlength=5)
    head=torch.nn.Linear(32,5).to(device);head.load_state_dict({k:torch.as_tensor(v.copy(),device=device) for k,v in initial.items()})
    opt=torch.optim.AdamW(head.parameters(),lr=p['lr'],weight_decay=p['weight_decay']);xx=torch.as_tensor(x[weak],device=device);yy=torch.as_tensor(y[weak],device=device,dtype=torch.long);ww=torch.as_tensor(weights,device=device,dtype=torch.float32);history=[]
    for i in range(p['steps']):
        check();opt.zero_grad(set_to_none=True);ce=torch.nn.functional.cross_entropy(head(xx),yy,reduction='none');loss=(ce*ww).sum()*p['auxiliary_scale'];loss.backward();norm=torch.nn.utils.clip_grad_norm_(head.parameters(),1.)
        if not torch.isfinite(loss) or not torch.isfinite(norm):raise ValueError('Invalid paired gradient')
        opt.step()
        if i==0 or (i+1)%50==0:history.append(dict(step=i+1,objective=float(loss.detach()),normalized_weak_ce=float((ce*ww).sum().detach()/ww.sum()),gradient_norm=float(norm)))
    return dict(weight=head.weight.detach().cpu().numpy(),bias=head.bias.detach().cpu().numpy()),dict(full_class_support=counts.tolist(),weak_class_support=wc.tolist(),class_weight_mass=mass.tolist(),rest_fraction=float(mass[0]/mass.sum()),history=history)

def run(base,root,p,ds,profile,out):
    import torch
    from .joint_stream import JointStreamModel,JointPreprocessor
    from .joint_training import continuous_training_rows,weak_supervision_weights
    from .jepa_training import prepared,neural_segments
    started=time.monotonic();out=Path(out)
    def check():
        if time.monotonic()-started>p['max_wall_seconds']:raise InterruptedError('Paired head budget exhausted')
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();torch.set_num_threads(2);source=Path(base)/p['model_sources']['candidate']['path'];m=read_json(source.parent/'manifest.json')
        with np.load(source,allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
        model=JointStreamModel(arrays,dict(m['details']),device);train=prepared(continuous_training_rows(ds),JointPreprocessor);wc,masks=weak_supervision_weights(train)
        x=[];y=[];weak=[]
        with torch.inference_mode():
            for row,mask in zip(train,masks):
                z=(row['z']-model.mean)/model.scale
                for sa,sb in neural_segments(row):
                    chosen=row['quality_eligible_ends']+1;chosen=chosen[(chosen>sa)&(chosen<=sb)];ix=(chosen-sa)//4-1
                    if not len(chosen):continue
                    latent,_=model.net.eye.sequence(torch.as_tensor(z[None,:8,sa:sb],device=device));x.extend(latent[0,ix].cpu().numpy());y.extend(row['y'][chosen-1]);weak.extend(mask[chosen-1])
        x=np.asarray(x,dtype='float32');y=np.asarray(y,dtype='int64');weak=np.asarray(weak,dtype=bool);assert np.array_equal(np.bincount(y[weak],minlength=5),wc)
        raw={role:load_episodes(ds,role) for role in p['roles']};frames={role:extract_rows(raw[role],model,check) for role in raw};result=[];predictions={};support=[]
        for role in frames:support.extend([dict(role=role,**{k:v for k,v in row.items() if k in ['trial_id','source_sample_end','reset','label','phase']}) for row in frames[role]])
        initial={k:arrays['net__eye_head.'+k] for k in ['weight','bias']}
        for arm in p['arms']:
            torch.manual_seed(17);a,training=fit_head(x,y,weak,initial,p,device,arm,check);np.savez(out/(arm+'.npz'),**a);atomic(out/(arm+'-training.json'),training)
            report=dict(arm=arm,training=training,roles={})
            for role in frames:
                f=frames[role];xx=np.asarray([v['transfer_eye_latent'] for v in f]);truth=np.asarray([v['label'] for v in f]);logits=xx@a['weight'].T+a['bias'];logits-=logits.max(1,keepdims=True);prob=np.exp(logits);prob/=prob.sum(1,keepdims=True)
                joint=np.asarray([v['original_scores'] for v in f]);full=sum(r['y'][end-1]>=0 for r in raw[role] for end in range(25,r['x'].shape[1]+1,25));rest=sum(r['y'][end-1]==0 for r in raw[role] for end in range(25,r['x'].shape[1]+1,25))*.1
                report['roles'][role]=dict(eye=metrics(truth,prob),eye_max_ge_point7=float(np.mean(prob[truth>=0].max(1)>=.7)),fusion=metrics(truth,fuse_probabilities(joint,prob,.2)),
                    decision=decision_report(truth,fuse_probabilities(joint,prob,.2),f,full,rest))
                predictions[arm+'__'+role]=prob
            result.append(report)
        np.savez(out/'predictions.npz',**predictions);atomic(out/'supports.json',support)
        atomic(out/'report.json',dict(protocol_id=digest(p),results=result,elapsed_s=time.monotonic()-started,test_accessed=False,backbone_changed=False,public_weights_changed=False,
            meaning='Paired head optimization from same private initializer; change is support normalization only. Weak task labels are not objective gaze labels.'))
    verify_sources(ds['folder'],ds['source_receipt']);verify_models(base,p['model_sources'])

def main():
    import argparse,os
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--code-root',required=True);ap.add_argument('--protocol',required=True);ap.add_argument('--dataset',required=True);ap.add_argument('--profile',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();p=read_json(a.protocol)
    if p['code_sha256'].get('gnautilus_ml/eye_weight_probe.py')!=file_hash(__file__) or Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve():raise ValueError('Actual entrypoint/protocol differs')
    if Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Output must retain protocol')
    run(Path(a.root),Path(a.code_root),p,read_json(a.dataset),read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
