"""Full-data descriptive study and governed final personal readout adaptation.

All old roles enter analysis/final fit at the user's request. Grouped head
replays are exploratory, not independent tests of this globally chosen montage
or the previously privately trained backbone.
"""
import copy,time
from pathlib import Path
import numpy as np
from .common import atomic,digest,read_json,file_hash,within
from .navigation_contract import CHANNELS,ELECTRICAL,SCOPE
from .hybrid_navigation import NavEvidence,HybridNavigationModel,GazePolicy,RUNTIME,ALGORITHM,readout_predict,feature_view

SCHEMA='gnautilus.hybrid_navigation_training.v2'
LABELS=['REST','FORWARD','BACKWARD','LEFT','RIGHT']

def targets(catalog):
    from .navigation_channel_audit import strict_intervals
    strict=strict_intervals(catalog,roles=('train','validation','test'));result={r['trial_id']:dict(r,label_index=LABELS.index(r['label'])) for r in strict['included']};excluded=list(strict['excluded'])
    for q in catalog['trials']:
        if q['label']!='REST':continue
        if q['stream_id']=='official_rest_hdf5':excluded.append(dict(trial_id=q['trial_id'],reason='official_REST_source_contract_quarantined',split=q['split']));continue
        result[q['trial_id']]=dict(trial_id=q['trial_id'],label='REST',label_index=0,stream_id=q['stream_id'],split=q['split'],start=q['task_start'],end=q['task_end'],source_truth='explicit_software_navigation_REST')
    return result,excluded

def pooled_analysis(parent,catalog):
    from .navigation_channel_audit import strict_intervals,_extract_analysis_role,trial_representations,select_candidates
    intervals=strict_intervals(catalog,roles=('train','validation','test'));rows=[];supports=[]
    for role in ['train','validation','test']:
        values,ss=_extract_analysis_role(parent,intervals['included'],role);rows.extend(values);supports.extend(ss)
    trials=trial_representations(rows);labels=np.asarray([r['label'] for r in trials]);centers=np.asarray([r['features'] for r in trials]);mean=centers.mean(0)
    variance=np.asarray([np.var([v['features'] for v in rows if v['trial_id']==t['trial_id']],axis=0) for t in trials]);scale=np.sqrt(np.maximum(np.mean(variance+(centers-mean)**2,axis=0),.0025));class_centers=[];within=[]
    for label in LABELS[1:]:
        take=labels==label;c=centers[take].mean(0);class_centers.append((c-mean)/scale);within.append(np.mean(variance[take]+(centers[take]-c)**2,axis=0)/scale**2)
    score=np.var(class_centers,axis=0).sum(1)/(np.mean(within,axis=0).sum(1)+1e-6)
    reference=np.mean([t['spectrum'] for t in trials],axis=0);per_direction={};margins=[]
    from .navigation_channel_audit import _cosine
    for label in LABELS[1:]:
        same=[];other=[];own=[t for t in trials if t['label']==label]
        for t in own:
            same.append(np.mean([_cosine(t['spectrum']-reference,v['spectrum']-reference) for v in own if v['trial_id']!=t['trial_id']],axis=0))
            other.append(np.mean([_cosine(t['spectrum']-reference,v['spectrum']-reference) for v in trials if v['label']!=label],axis=0))
        a=np.mean(same,axis=0);b=np.mean(other,axis=0);margins.append(a-b);per_direction[label]=dict(independent_trials=len(own),same=a.tolist(),other=b.tolist(),margin=(a-b).tolist())
    margin=np.mean(margins,axis=0);combined=score*np.clip(margin+.25,0,1);order=np.argsort(-combined,kind='stable');corr=np.mean([t['correlation'] for t in trials],axis=0)
    candidate_rank=dict(score=combined.tolist(),independent_trials={label:int(sum(labels==label)) for label in LABELS[1:]})
    proposed=select_candidates(candidate_rank,corr);masks=[v for v in proposed if v['name'] in ['motor24','train_top8','train_top12']]
    for v in masks:v['selected_on']='all_personal_data_descriptive_analysis';v['name']=v['name'].replace('train_','pooled_')
    return dict(schema='gnautilus.full_personal_channel_analysis.v1',analysis_roles=['train','validation','test'],source_roles_preserved=True,independent_test_claim=False,
        intervals=intervals,supports=supports,independent_trials=candidate_rank['independent_trials'],channels=[dict(electrode=c,physical_channel=i+1,separation=float(score[i]),repeat_margin=float(margin[i]),combined_score=float(combined[i])) for i,c in enumerate(CHANNELS)],
        ranking=[CHANNELS[i] for i in order],per_direction=per_direction,candidates=masks,windows=len(rows))

def sample_weights(y,ids,rest_mass):
    y=np.asarray(y);ids=np.asarray(ids);w=np.zeros(len(y))
    if set(np.unique(y))!=set(range(5)):raise ValueError('All five navigation classes need genuine support')
    for c in range(5):
        group=np.unique(ids[y==c]);mass=rest_mass if c==0 else (1-rest_mass)/4
        for tid in group:
            at=(y==c)&(ids==tid);w[at]=mass/len(group)/at.sum()
    return w

def scores_report(y,p,ids):
    y=np.asarray(y);p=np.asarray(p);ids=np.asarray(ids);pred=p.argmax(1);recalls=[];trial_rows=[]
    for tid in dict.fromkeys(ids):
        at=ids==tid;label=int(y[at][0]);trial_rows.append(dict(trial_id=str(tid),label=label,frames=int(at.sum()),recall=float(np.mean(pred[at]==label))))
    for c in range(5):
        values=[r['recall'] for r in trial_rows if r['label']==c];recalls.append(float(np.mean(values)) if values else None)
    return dict(frames=len(y),trials=len(trial_rows),accuracy=float(np.mean(pred==y)),equal_trial_class_recall=recalls,
        balanced_accuracy=float(np.mean([v for v in recalls if v is not None])),trial_rows=trial_rows,class_order=LABELS)

def fit_head(x,y,ids,mass,seed,device,steps,check,checkpoint_path=None):
    from .governance import authorize_training
    authorize_training()
    import torch
    w=sample_weights(y,ids,mass);mean=(x*w[:,None]).sum(0);scale=np.sqrt(np.maximum(((x-mean)**2*w[:,None]).sum(0),1e-4));torch.manual_seed(seed)
    xx=torch.as_tensor((x-mean)/scale,dtype=torch.float32,device=device);yy=torch.as_tensor(y,device=device);ww=torch.as_tensor(w,dtype=torch.float32,device=device)
    head=torch.nn.Linear(x.shape[1],5).to(device);torch.nn.init.zeros_(head.weight);torch.nn.init.zeros_(head.bias);opt=torch.optim.AdamW(head.parameters(),lr=.02,weight_decay=0.);history=[]
    for step in range(steps):
        if step%50==0:check()
        opt.zero_grad();ce=(torch.nn.functional.cross_entropy(head(xx),yy,reduction='none')*ww).sum();loss=ce+.05*head.weight.square().sum();loss.backward();norm=torch.nn.utils.clip_grad_norm_(head.parameters(),5.)
        if not torch.isfinite(loss) or not torch.isfinite(norm):raise ValueError('Nonfinite head optimization')
        opt.step()
        if step==0 or (step+1)%50==0:
            history.append(dict(step=step+1,ce=float(ce.detach()),objective=float(loss.detach()),gradient_norm=float(norm)))
            if checkpoint_path:
                target=Path(checkpoint_path);temp=target.with_suffix('.tmp');torch.save(dict(step=step+1,seed=seed,head=head.state_dict(),optimizer=opt.state_dict(),mean=mean,scale=scale,fit_trial_ids=list(map(str,np.unique(ids))),rest_mass=mass,history=history),temp);temp.replace(target)
    return dict(mean=mean.astype('float32'),scale=scale.astype('float32'),weight=head.weight.detach().cpu().numpy(),bias=head.bias.detach().cpu().numpy()),history

def prepare_frames(parent,catalog,backbone,weights,device,encoder_mode='selected'):
    from .ocular_boundary_audit import load_contextual_episodes
    from .quality import inspect_signal
    truth,_=targets(catalog);frames=[];features=[];latent=[];source=[]
    for role in ['train','validation','test']:
        for row in load_contextual_episodes(parent,role,pre=2000,post=750):
            if row['id'] not in truth:continue
            encoder_weights=np.ones(32,dtype='float32') if encoder_mode=='full32' else weights
            target=truth[row['id']];nav=NavEvidence(backbone,weights,device,encoder_weights);history=np.empty((32,0));m=row['metadata'];reset_at=m['context_start']
            source.append(dict(trial_id=row['id'],split=role,stream_id=m['stream_id'],start=m['context_start'],end=m['context_end']))
            for end in range(25,row['x'].shape[1]+1,25):
                x=row['x'][:,end-25:end];history=np.concatenate((history,x),1)[:,-375:]
                q=inspect_signal(history,CHANNELS,250,ELECTRICAL) if history.shape[1]>=375 else dict(electrical_valid=True)
                if not q['electrical_valid']:nav=NavEvidence(backbone,weights,device,encoder_weights);history=np.empty((32,0));reset_at=m['context_start']+end;continue
                f,h,ready=nav.step(x);at=m['context_start']+end
                if ready and target['start']<=at-1<target['end']:
                    frames.append(dict(trial_id=row['id'],split=role,stream_id=m['stream_id'],source_end=at,context_start=reset_at,label=target['label_index']))
                    features.append(f);latent.append(h)
    return frames,np.asarray(features),np.asarray(latent),source

def make_protocol(ds,root,sources,prior):
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),scope='full_personal_adaptation_exploratory',all_data_channel_analysis=True,
        code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},sources=sources,
        seeds=[17,29,41],rest_masses=[.2,.35,.5],latent_gains=[0.,.25,.5],zero_latent_control_only=True,steps=250,
        feature_modes=['absolute','spatial_relative'],encoder_modes=['selected','full32'],
        folds=['train','validation','test'],fit='two_original_roles_per_head_replay_then_all_eligible_trials_final',independent_test=False,
        eye_head='frozen_manual_eye_contrast_top8_scratch17',quality=ELECTRICAL,head_l2_sum=.05,learning_rate=.02,
        selection='equal_complete_trial_class_macro_recall_then_REST_recall_then_simpler_then_fixed_order; positive_latent_gain_only',
        prior_elapsed_s=float(prior),max_wall_seconds=1800,total_budget_seconds=43200,live_acceptance='offline_until_hardware_and_new_data_evidence')

def check_protocol(p,ds,root):
    from .personal_study import verify_sources
    from .ocular_event_data import compile_annotations
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1':raise ValueError('Explicit all-data adaptation dataset required')
    if ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Manual source snapshot changed')
    if digest(ds['parent'])!=ds['catalog']['dataset_id'] or ds['parent']['channels']!=CHANNELS:raise ValueError('Parent montage/source mismatch')
    episodes={q['trial_id']:q for q in ds['parent']['episodes']}
    if set(episodes)!={q['trial_id'] for q in ds['catalog']['trials']}:raise ValueError('Human source trial inventory differs')
    for q in ds['catalog']['trials']:
        r=episodes[q['trial_id']]
        if any(q[a]!=r[b] for a,b in [('task_start','start'),('task_end','end'),('split','split'),('stream_id','stream_id'),('label','label')]):raise ValueError('Human source/trial role differs from parent')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']):raise ValueError('Locked recipe/source differs')
    if p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Approved aggregate budget exhausted')
    base=Path(ds['parent']['folder']).parent.parent
    for q in p['sources'].values():
        if file_hash(within(base/q['path'],base))!=q['sha256']:raise ValueError('Frozen prior model changed')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def run(base,root,p,ds,profile,out):
    import torch
    from .governance import training_authority,compute_device
    from .artifacts import save_artifact,load_artifact
    out=Path(out);start=time.monotonic();optimizations=0;head_receipts=[]
    def check():
        if time.monotonic()-start>p['max_wall_seconds']:raise InterruptedError('Hybrid study time budget exhausted')
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();torch.set_num_threads(2)
        def arrays(key):
            with np.load(Path(base)/p['sources'][key]['path'],allow_pickle=False) as z:return {k:z[k].copy() for k in z.files if k!='classes'}
        backbone=arrays('backbone');eye=arrays('eye');analysis=pooled_analysis(ds['parent'],ds['catalog']);atomic(out/'all-data-channel-analysis.json',analysis)
        atomic(out/'analysis-freeze.json',dict(analysis_sha256=digest(analysis),all_roles_used=True,candidates=analysis['candidates'],independent_test=False))
        prepared={};candidates=[];predictions={};supports={};cache={};cases=[]
        for mask in analysis['candidates']:
            for encoder_mode in p['encoder_modes']:
                for feature_mode in p['feature_modes']:
                    cases.append(dict(mask,name=mask['name']+'_'+encoder_mode+'_'+feature_mode,base_montage=mask['name'],encoder_mode=encoder_mode,feature_mode=feature_mode))
        for mask in cases:
            check();cache_key=(mask['base_montage'],mask['encoder_mode'])
            if cache_key not in cache:cache[cache_key]=prepare_frames(ds['parent'],ds['catalog'],backbone,mask['input_weights'],device,mask['encoder_mode'])
            frames,raw,h,source=cache[cache_key];x=feature_view(raw,len(mask['channels']),mask['feature_mode']);prepared[mask['name']]=(frames,x,h);supports[mask['name']]=dict(frames=frames,regions=source)
            y=np.asarray([v['label'] for v in frames]);ids=np.asarray([v['trial_id'] for v in frames]);roles=np.asarray([v['split'] for v in frames])
            for mass in p['rest_masses']:
                for seed in p['seeds']:
                    pp=np.zeros((len(y),5));hh=np.zeros((len(y),5))
                    for fold in p['folds']:
                        train=roles!=fold;valid=roles==fold
                        if not valid.any():raise ValueError('Missing complete-role replay fold')
                        for kind,values,result in [('spectral',x,pp),('latent',h,hh)]:
                            prefix=mask['name']+'_'+str(mass)+'_'+str(seed)+'_'+fold+'_'+kind
                            a,history=fit_head(values[train],y[train],ids[train],mass,seed,device,p['steps'],check,out/(prefix+'-checkpoint.pt'));optimizations+=p['steps']
                            np.savez(out/(prefix+'.npz'),**a);atomic(out/(prefix+'.json'),dict(history=history,fit_roles=[v for v in p['folds'] if v!=fold],score_role=fold,normalization_segment_ids=list(map(str,np.unique(ids[train]))),weights_sha256=file_hash(out/(prefix+'.npz'))))
                            logits=(values[valid]-a['mean'])/a['scale']@a['weight'].T+a['bias'];logits-=logits.max(1,keepdims=True);v=np.exp(logits);result[valid]=v/v.sum(1,keepdims=True)
                    for gain in p['latent_gains']:
                        prob=(1-gain)*pp+gain*hh;report=scores_report(y,prob,ids);name=mask['name']+'_'+str(mass)+'_'+str(seed)+'_'+str(gain)
                        item=dict(name=name,montage=mask,rest_mass=mass,seed=seed,latent_gain=gain,head_replay=report,independent_test=False);candidates.append(item);predictions[name]=prob
                    atomic(out/'progress.json',dict(phase='head_replay',montage=mask['name'],rest_mass=mass,seed=seed,actual_updates=optimizations,elapsed_s=time.monotonic()-start));print(mask['name'],mass,seed,flush=True)
        eligible=[q for q in candidates if q['latent_gain']>0]
        selected=max(eligible,key=lambda q:(q['head_replay']['balanced_accuracy'],q['head_replay']['equal_trial_class_recall'][0],-len(q['montage']['channels'])))
        atomic(out/'selection-freeze.json',dict(selected=selected,candidates=candidates,independent_test=False,all_data_features_selected=True));mask=selected['montage'];frames,x,h=prepared[mask['name']];y=np.asarray([q['label'] for q in frames]);ids=np.asarray([q['trial_id'] for q in frames]);a={}
        for kind,values in [('spectral',x),('latent',h)]:
            fitted,hist=fit_head(values,y,ids,selected['rest_mass'],selected['seed'],device,p['steps'],check,out/('final-'+kind+'-checkpoint.pt'));optimizations+=p['steps'];a.update({kind+'__'+k:v for k,v in fitted.items()});atomic(out/('final-'+kind+'-optimization.json'),hist)
        a.update({'backbone__'+k:v for k,v in backbone.items()});a.update({'ocular__'+k:v for k,v in eye.items()});a['navigation_input_weights']=np.asarray(mask['input_weights'],dtype='float32');a['latent_gain']=np.asarray(selected['latent_gain'],dtype='float32');a['encoder_input_weights']=np.ones(32,dtype='float32') if mask['encoder_mode']=='full32' else a['navigation_input_weights'].copy()
        details=dict(selected_channels=mask['channels'],feature_mode=mask['feature_mode'],encoder_mode=mask['encoder_mode'],rest_training_mass=selected['rest_mass'],latent_gain=selected['latent_gain'],eye_state_policy='bounded_eye_memory_navigation_v1',navigation_history='persistent_frozen_CNN_GRU_and_causal_multiscale_band_covariance',
            new_training='regularized_navigation_readouts_only',calibrated_probabilities=False,all_personal_data_adapted=True,independent_test=False,decision_policy=dict(aligned_threshold=.60,conservative_threshold=.85,hold_discount=.05,confirm_updates=3,stop_updates=2,temperature=1.))
        model=HybridNavigationModel(a,details,device);fitp=(1-selected['latent_gain'])*readout_predict(x,a,'spectral__')+selected['latent_gain']*readout_predict(h,a,'latent__');fit=scores_report(y,fitp,ids)
        source_manifest=read_json(Path(base)/p['sources']['backbone_manifest']['path']);evidence=dict(runtime_schema=RUNTIME,dataset_title='个人导航数据集',dataset_sha256=digest(ds),protocol_sha256=digest(p),evaluation_stage='full_personal_adaptation_exploratory',runtime_accepted=False,class_labels=LABELS,
            input_contract=source_manifest.get('input_contract'),montage_attestation=source_manifest.get('montage_attestation'),electrical_policy=ELECTRICAL,metrics=dict(training=fit,development=selected['head_replay'],independent_test=None),code_sha256=p['code_sha256'],sources=p['sources'])
        from .hybrid_provenance import native_export_metadata
        evidence.update(native_export_metadata(ds['parent']))
        path=save_artifact(out/'artifacts',model,evidence);loaded,manifest=load_artifact(path,device=device)
        atomic(out/'supports.json',supports);np.savez(out/'selected-head-replay.npz',probabilities=predictions[selected['name']],labels=y)
        result=dict(schema=SCHEMA,protocol_id=digest(p),selected=selected,candidates=candidates,final_fit=fit,actual_gradient_updates=optimizations,
            final_source_trials=len(set(ids)),excluded_supervision=targets(ds['catalog'])[1],artifact_path=str(path),model_id=manifest['model_id'],runtime_schema=RUNTIME,elapsed_s=time.monotonic()-start,live_enabled=False,independent_test=False,
            limitations=['All roles used for descriptive channel analysis and final fit; replay is not an independent test.', 'Frozen JEPA backbone previously trained on private data; head replay does not establish backbone generalization.', 'Frozen ocular head previously had poor held-out event performance; no eye-position truth.'])
        atomic(out/'report.json',result);return result

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for k in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();p=read_json(a.protocol)
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked entrypoint/output required')
    run(Path(a.root),Path(a.code_root),p,read_json(a.dataset),read_json(a.profile),Path(a.output))
if __name__=='__main__':main()
