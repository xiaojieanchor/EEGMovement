"""Governed repair study. All-data adaptation and cross-fitted diagnostics only."""
import time,itertools
from pathlib import Path
import numpy as np
from .common import atomic,read_json,digest,file_hash,within
from .navigation_contract import CHANNELS,ELECTRICAL
from .hybrid_training import targets,scores_report
from .hybrid_navigation import NavEvidence
from .intent_repair import EyeFeatures,eye_embedding,evidence,scaled,IntentPolicy,MovementLatch,RepairedHybridModel,RUNTIME

SCHEMA='gnautilus.intent_repair_training.v1'

def balanced(y,ids):
    y=np.asarray(y);ids=np.asarray(ids);w=np.zeros(len(y));classes=np.unique(y)
    for c in classes:
        events=np.unique(ids[y==c])
        for e in events:
            at=(y==c)&(ids==e);w[at]=1/len(classes)/len(events)/at.sum()
    return w

def fit(x,y,ids,penalty,device,out,check,steps=250):
    from .governance import authorize_training
    authorize_training()
    import torch
    y=np.asarray(y);assert np.array_equal(np.unique(y),np.arange(int(y.max())+1));w=balanced(y,ids);mean=(x*w[:,None]).sum(0);scale=np.sqrt(np.maximum(((x-mean)**2*w[:,None]).sum(0),1e-6));torch.manual_seed(17)
    xx=torch.as_tensor((x-mean)/scale,dtype=torch.float32,device=device);yy=torch.as_tensor(y,device=device);ww=torch.as_tensor(w,dtype=torch.float32,device=device);head=torch.nn.Linear(x.shape[1],int(y.max())+1).to(device)
    torch.nn.init.zeros_(head.weight);torch.nn.init.zeros_(head.bias);opt=torch.optim.AdamW(head.parameters(),lr=.02,weight_decay=0);history=[]
    for step in range(steps):
        if step%50==0:check()
        opt.zero_grad();ce=(torch.nn.functional.cross_entropy(head(xx),yy,reduction='none')*ww).sum();loss=ce+penalty*head.weight.square().sum();loss.backward();norm=torch.nn.utils.clip_grad_norm_(head.parameters(),5)
        if not torch.isfinite(loss) or not torch.isfinite(norm):raise ValueError('Nonfinite repair gradient')
        opt.step()
        if step==0 or (step+1)%50==0:
            history.append(dict(step=step+1,ce=float(ce.detach()),objective=float(loss.detach()),gradient_norm=float(norm)))
            temp=Path(out).with_suffix('.tmp');torch.save(dict(step=step+1,head=head.state_dict(),optimizer=opt.state_dict(),mean=mean,scale=scale,fit_source_ids=list(map(str,np.unique(ids))),history=history),temp);temp.replace(out)
    result=dict(mean=mean.astype('float32'),scale=scale.astype('float32'),weight=head.weight.detach().cpu().numpy(),bias=head.bias.detach().cpu().numpy());atomic(Path(out).with_suffix('.json'),dict(history=history,fit_source_ids=list(map(str,np.unique(ids)))));return result

def fit_architecture(x,y,ids,architecture,penalty,device,out,check):
    result={}
    if architecture=='flat':specs=[('flat__',np.ones(len(y),bool),y)]
    else:specs=[('active__',np.ones(len(y),bool),(y>0).astype('int64')),('direction__',y>0,y[y>0]-1)]
    for prefix,take,label in specs:
        fitted=fit(x[take],label,ids[take],penalty,device,Path(str(out)+'-'+prefix+'.pt'),check);result.update({prefix+k:v for k,v in fitted.items()})
    return result

def build_cache(ds,model,check,out):
    import torch
    from .ocular_boundary_audit import load_contextual_episodes
    from .ocular_event_training import centered
    from .quality import inspect_signal
    truth,_=targets(ds['catalog']);events=ds['events']['segments'];eye_classes=['NO_EYE','UP','DOWN','LEFT','RIGHT'];frames=[];spec=[];latents=[];ocular=[];frontal=[];segments=[]
    indices=np.flatnonzero(model.eye_arrays['net__input_weights'].reshape(-1))
    for role in ['train','validation','test']:
        for row in load_contextual_episodes(ds['parent'],role,pre=2000,post=750):
            check();m=row['metadata'];tid=row['id'];manual=[v for v in events if v['trial_id']==tid];target=truth.get(tid)
            def reset():return NavEvidence(model.backbone,model.arrays['navigation_input_weights'],'cuda:0',model.arrays.get('encoder_input_weights')),EyeFeatures(indices),np.empty((32,0),dtype='float32')
            nav,eye,history=reset();reset_at=m['context_start']
            for end in range(25,row['x'].shape[1]+1,25):
                at=m['context_start']+end;hop=row['x'][:,end-25:end];history=np.concatenate((history,hop),-1)[:,-500:];q=inspect_signal(history[:,-375:],CHANNELS,250,ELECTRICAL) if history.shape[1]>=375 else dict(electrical_valid=True)
                label=target['label_index'] if target and target['start']<=at-1<target['end'] else -1
                marked=next((v for v in manual if v['start']<=at-1<v['end']),None);eye_y=eye_classes.index(marked['class_label']) if marked and m['stream_id']!='official_rest_hdf5' else -1
                f=dict(trial_id=tid,split=role,stream_id=m['stream_id'],source_end=at,context_start=reset_at,task_start=m['start'],task_end=m['end'],navigation_label=label,eye_label=eye_y,segment_id=marked['segment_id'] if marked and eye_y>=0 else '',quality_valid=q['electrical_valid'],warming=True,cache_index=-1)
                if not q['electrical_valid']:
                    f['quality_reasons']=q['reasons'];frames.append(f);nav,eye,history=reset();reset_at=at;continue
                x,h,ready=nav.step(hop);z=(centered(history)-model.eye_arrays['input_mean'])/model.eye_arrays['input_scale']
                with torch.inference_mode():hidden,_=eye_embedding(model.eye,torch.as_tensor(z[None],dtype=torch.float32,device='cuda:0'))
                ef=eye.step(hop,hidden[0].cpu().numpy());f.update(warming=not ready,cache_index=len(spec));spec.append(x);latents.append(h);ocular.append(ef);frontal.append(eye.features(hidden[0].cpu().numpy(),np.arange(8)));frames.append(f)
            atomic(out/'progress.json',dict(phase='source_bound_features',trial_id=tid,frames=len(frames)))
    atomic(out/'frames.json',frames);np.savez(out/'features.npz',spectral=np.asarray(spec),latent=np.asarray(latents),ocular=np.asarray(ocular),frontal=np.asarray(frontal));return frames,np.asarray(spec),np.asarray(latents),np.asarray(ocular),np.asarray(frontal)

def temperature(p,y,ids):
    w=balanced(y,ids)
    return min([.75,1.,1.5,2.,3.],key=lambda t:float(-(w*np.log(np.maximum(scaled(p,t)[np.arange(len(y)),y],1e-9))).sum()))

def calibrate(p,y,ids,architecture):
    if architecture=='flat':return [temperature(p,y,ids)]
    from .intent_repair import factor_scores
    active,direction=factor_scores(p);binary=np.column_stack((1-active,active));take=y>0
    return [temperature(binary,(y>0).astype('int64'),ids),temperature(direction[take],y[take]-1,ids[take])]

def rescale(p,architecture,temps):
    if architecture=='flat':return scaled(p,temps[0])
    from .intent_repair import factor_scores
    active,direction=factor_scores(p);active=scaled(np.column_stack((1-active,active)),temps[0])[:,1];direction=scaled(direction,temps[1]);return np.column_stack((1-active,active[:,None]*direction))

def crossfit(x,labels,ids,roles,architecture,penalty,device,out,check):
    from .hybrid_navigation import readout_predict
    classes=int(labels.max())+1;pred=np.zeros((len(labels),classes));arrays={}
    for role in ['train','validation','test']:
        train=(roles!=role)&(labels>=0);valid=roles==role;a=fit_architecture(x[train],labels[train],ids[train],architecture,penalty,device,out/role,check)
        pred[valid]=evidence(x[valid],a,'',architecture,[1.,1.])[0];arrays[role]=a
    return pred,arrays

def event_stats(frames,probs,segments,policy,motion=None):
    from .intent_repair import factor_scores
    active,direction=factor_scores(probs);predicted=[];latch=MovementLatch(policy);previous=None
    for f in frames:
        if previous is None or f['trial_id']!=previous['trial_id'] or not f['quality_valid']:latch=MovementLatch(policy)
        previous=f;i=f['cache_index']
        if i<0 or f['stream_id']=='official_rest_hdf5':continue
        event=latch.step(float(active[i]),direction[i],None if motion is None else motion[i])
        if event:predicted.append(dict(trial_id=f['trial_id'],source_end=f['source_end'],direction=event))
    truth=[s for s in segments if s['kind']=='manual_direction'];mapping={'UP':1,'DOWN':2,'LEFT':3,'RIGHT':4};matched=set()
    for pred in predicted:
        for s in truth:
            if s['segment_id'] not in matched and pred['trial_id']==s['trial_id'] and s['start']<=pred['source_end']<=s['end']+500 and pred['direction']==mapping[s['class_label']]:matched.add(s['segment_id']);break
    precision=len(matched)/max(1,len(predicted));recall=len(matched)/len(truth);f1=2*precision*recall/max(1e-9,precision+recall)
    return dict(matched_events=len(matched),manual_events=len(truth),triggers=len(predicted),matching_fraction=precision,event_recall=recall,f1=f1,predicted=predicted,matching_allowance_s=2,independent_test=False)

def policy_replay(frames,navp,eyep,config,eye_config,mode='actual',motion=None):
    from .intent_repair import factor_scores
    active,direction=factor_scores(navp);eyeactive,eyedir=factor_scores(eyep);policy=IntentPolicy(config);latch=MovementLatch(eye_config);previous=None;rows=[]
    for f in frames:
        if previous is None or previous['trial_id']!=f['trial_id'] or not f['quality_valid']:policy=IntentPolicy(config);latch=MovementLatch(eye_config)
        previous=f;i=f['cache_index'];event=0;decision=dict(direction=0,status='UNKNOWN',reason='electrical_quality' if i<0 else 'real_history_prewarm',cue_direction=policy.cue)
        if i>=0:
            event=latch.step(float(eyeactive[i]),eyedir[i],None if motion is None else motion[i]) if mode=='actual' else 0
            if event:policy.eye_event(event)
            if not f['warming']:decision=policy.step(float(active[i]),direction[i])
        rows.append(dict(trial_id=f['trial_id'],source_end=f['source_end'],label=f['navigation_label'],quality_valid=f['quality_valid'],warming=f['warming'],decision=decision,eye_event=event))
    return policy_metrics(rows),rows

def policy_metrics(rows):
    known=[r for r in rows if r['label']>=0];ids=np.asarray([r['trial_id'] for r in known]);y=np.asarray([r['label'] for r in known]);pred=np.asarray([r['decision']['direction'] if r['decision']['status']!='UNKNOWN' else -1 for r in known]);recalls=[];wrong=[]
    for c in range(5):
        trials=np.unique(ids[y==c]);recalls.append(float(np.mean([np.mean(pred[ids==t]==c) for t in trials])));wrong.append(float(np.mean([np.mean((pred[ids==t]>=0)&(pred[ids==t]!=c)) for t in trials])))
    false=0;rest_seconds=0;last=0;tid=None
    for row in known:
        if row['trial_id']!=tid:last=0;tid=row['trial_id']
        if row['label']==0:
            d=row['decision']['direction'];rest_seconds+=.1
            if d and d!=last:false+=1
            last=d
    report=dict(balanced_accuracy=float(np.mean(recalls)),class_recall=recalls,wrong_accepted_macro=float(np.mean(wrong)),coverage=float(np.mean(pred>=0)),known_frames=len(known),false_starts_on_software_REST=false,software_REST_seconds=rest_seconds,directional_active_frames=int((pred>0).sum()),rejection_reasons=dict(__import__('collections').Counter(r['decision'].get('reason') or 'legacy_unspecified_rejection' for r in known if r['decision']['status']=='UNKNOWN')))
    return report

def make_protocol(ds,root,sources,prior):
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},sources=sources,
        scope='full_personal_adaptation_exploratory',independent_test=False,seed=17,steps=250,penalties=[.005,.02,.05],navigation_candidates=[['joint','flat'],['joint','hierarchical'],['spectral','hierarchical']],
        eye_features='causal_signed_delta_slope_logstd_four_scales_plus_frozen_eye_GRU',eye_feature_modes=['legacy8','frontal8'],eye_labels='human_marked_or_explicit_absent_head_only_software_contract',navigation_labels='same_10_paired_directions_and_2_software_REST',motion_thresholds=[0.,1.5,2.5,4.],motion_normalization='cross_fitted_genuine_absent_head_ranges_only',
        fusion='learned_feature_fusion_no_forced_probability_average',calibration='original_role_cross_fitted_temperature_NLL',temperatures=[.75,1.,1.5,2.,3.],
        policy_selection='cross_fitted_accepted_macro_minus_half_wrong_macro; zero_observed_software_REST_false_starts_first',eye_selection='top_two_matching_F1_configs_per_mode_and_penalty_plus_ungated_reference_then_joint_policy_utility',
        prior_elapsed_s=float(prior),max_wall_seconds=1800,total_budget_seconds=43200,quality=ELECTRICAL,channels=CHANNELS,live_acceptance=False)

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Frozen human source differs')
    if digest(ds['parent'])!=ds['catalog']['dataset_id'] or ds['parent']['channels']!=CHANNELS:raise ValueError('Parent/source montage differs')
    episodes={q['trial_id']:q for q in ds['parent']['episodes']}
    if set(episodes)!={q['trial_id'] for q in ds['catalog']['trials']}:raise ValueError('Trial inventory differs')
    for q in ds['catalog']['trials']:
        if any(q[a]!=episodes[q['trial_id']][b] for a,b in [('task_start','start'),('task_end','end'),('split','split'),('stream_id','stream_id'),('label','label')]):raise ValueError('Human source role/range differs')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']):raise ValueError('Locked repair recipe/code differs')
    if p['prior_elapsed_s']<0 or p['prior_elapsed_s']+1800>43200:raise ValueError('Aggregate budget exhausted')
    base=Path(ds['parent']['folder']).parent.parent
    for source in p['sources'].values():
        if file_hash(within(base/source['path'],base))!=source['sha256']:raise ValueError('Prior artifact changed')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def run(base,root,p,ds,profile,out):
    import torch
    from .artifacts import load_artifact,save_artifact
    from .governance import training_authority,compute_device
    from .hybrid_navigation import readout_predict,GazePolicy
    out=Path(out);start=time.monotonic();updates=0
    def check():
        if time.monotonic()-start>p['max_wall_seconds']:raise InterruptedError('Repair budget exhausted')
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();torch.set_num_threads(2);oldpath=Path(base)/p['sources']['prior_manifest']['path'];old,oldmeta=load_artifact(oldpath.parent,device=device)
        frames,x,h,ef,frontal=build_cache(ds,old,check,out);eye_features={'legacy8':ef,'frontal8':frontal};valid=[f for f in frames if f['cache_index']>=0];ids=np.asarray([f['trial_id'] for f in valid]);roles=np.asarray([f['split'] for f in valid]);ny=np.asarray([f['navigation_label'] if not f['warming'] else -1 for f in valid]);ey=np.asarray([f['eye_label'] for f in valid]);eids=np.asarray([f['segment_id'] or f['trial_id'] for f in valid]);navtake=ny>=0;eyetake=ey>=0;nav_candidates=[];nav_predictions={}
        for feature,architecture in p['navigation_candidates']:
            values=np.concatenate((x,h),1) if feature=='joint' else x
            for penalty in p['penalties']:
                check();name=feature+'-'+architecture+'-'+str(penalty);folder=out/name;folder.mkdir(exist_ok=True);prob,_=crossfit(values,ny,ids,roles,architecture,penalty,device,folder,check);updates+=3*p['steps']*(1 if architecture=='flat' else 2)
                temps=calibrate(prob[navtake],ny[navtake],ids[navtake],architecture);prob=rescale(prob,architecture,temps);report=scores_report(ny[navtake],prob[navtake],ids[navtake]);item=dict(name=name,features=feature,architecture=architecture,penalty=penalty,temperatures=temps,metrics=report);nav_candidates.append(item);nav_predictions[name]=prob;atomic(out/'navigation-candidates.json',nav_candidates);atomic(out/'progress.json',dict(phase='navigation_crossfit',candidate=name,updates=updates,elapsed_s=time.monotonic()-start));print('navigation',name,report['balanced_accuracy'],flush=True)
        selected_nav=max([c for c in nav_candidates if c['features']=='joint'],key=lambda c:c['metrics']['balanced_accuracy']);eye_candidates=[];eye_predictions={}
        from .governance import authorize_training
        def motion_fit(values,take):
            authorize_training();n=8;w=balanced(ey[take],eids[take]);v=values[take,:n];mean=(v*w[:,None]).sum(0);scale=np.sqrt(np.maximum(((v-mean)**2*w[:,None]).sum(0),1e-6));return mean,scale
        motion_by_mode={}
        for mode,values in eye_features.items():
            motion_oof=np.zeros(len(ey))
            for role in ['train','validation','test']:
                mean,scale=motion_fit(values,(roles!=role)&(ey==0));take=roles==role;motion_oof[take]=np.sqrt(np.mean(((values[take,:8]-mean)/scale)**2,1))
            mean,scale=motion_fit(values,ey==0);motion_by_mode[mode]=dict(oof=motion_oof,mean=mean,scale=scale,final=np.sqrt(np.mean(((values[:,:8]-mean)/scale)**2,1)))
        for mode,penalty in itertools.product(p['eye_feature_modes'],p['penalties']):
            values=eye_features[mode];motion_oof=motion_by_mode[mode]['oof'];check();name=mode+'-ocular-'+str(penalty);folder=out/name;folder.mkdir(exist_ok=True);prob,_=crossfit(values,ey,eids,roles,'hierarchical',penalty,device,folder,check);updates+=6*p['steps'];temps=calibrate(prob[eyetake],ey[eyetake],eids[eyetake],'hierarchical');prob=rescale(prob,'hierarchical',temps);configs=[]
            for act,direc,confirm,motion_threshold in itertools.product([.55,.7,.85],[.4,.55,.7],[2,3],p['motion_thresholds']):
                config=dict(activity=act,direction=direc,margin=.1,confirm=confirm,motion_threshold=motion_threshold);stats=event_stats(frames,prob,ds['events']['segments'],config,motion_oof);configs.append(dict(config=config,stats=stats))
            chosen=max(configs,key=lambda c:(c['stats']['f1'],-c['stats']['triggers']));item=dict(name=name,feature_mode=mode,penalty=penalty,temperatures=temps,selected=chosen,candidates=configs);eye_candidates.append(item);eye_predictions[name]=prob;atomic(out/'eye-candidates.json',eye_candidates);print('eye',name,chosen['stats']['f1'],flush=True)
        npred=nav_predictions[selected_nav['name']];policies=[]
        for candidate in eye_candidates:
            pool=sorted(candidate['candidates'],key=lambda c:(c['stats']['f1'],-c['stats']['triggers']),reverse=True)[:2]
            reference=next(c for c in candidate['candidates'] if c['config']==dict(activity=.7,direction=.4,margin=.1,confirm=2,motion_threshold=0.))
            if reference not in pool:pool.append(reference)
            for option in pool:
                for act,direc,margin in itertools.product([.55,.65,.75],[.4,.5,.6],[.05,.1]):
                    check();config=dict(activity=act,direction=direc,margin=margin,eye_discount=.1,other_premium=.1,hold_discount=.05,rest_activity=.4,confirm=3,stop=2);stats,_=policy_replay(frames,npred,eye_predictions[candidate['name']],config,option['config'],motion=motion_by_mode[candidate['feature_mode']]['oof']);policies.append(dict(config=config,stats=stats,eye_name=candidate['name'],eye_option=option))
            atomic(out/'progress.json',dict(phase='joint_operating_point',eye_candidate=candidate['name'],operating_points=len(policies),updates=updates))
        chosen_policy=max(policies,key=lambda c:(c['stats']['false_starts_on_software_REST']==0,c['stats']['balanced_accuracy']-.5*c['stats']['wrong_accepted_macro'],c['eye_option']['stats']['f1'],c['stats']['coverage']));selected_eye=dict(next(c for c in eye_candidates if c['name']==chosen_policy['eye_name']),selected=chosen_policy['eye_option']);eye_config=selected_eye['selected']['config'];epred=eye_predictions[selected_eye['name']];ef=eye_features[selected_eye['feature_mode']];motion=motion_by_mode[selected_eye['feature_mode']];motion_mean,motion_scale,motion_final,motion_oof=motion['mean'],motion['scale'],motion['final'],motion['oof'];freeze=dict(navigation=selected_nav,eye=selected_eye,policy=chosen_policy,policies=policies,independent_test=False,selection_support='cross_fitted_original_roles_globally_selected_montage_previously_inspected')
        atomic(out/'selection-freeze.json',freeze);arrays={k:v.copy() for k,v in old.arrays.items() if k.startswith(('backbone__','ocular__')) or k in ['navigation_input_weights','encoder_input_weights']};values=np.concatenate((x,h),1)
        for prefix,values_,label,group,architecture,penalty in [('nav__',values[navtake],ny[navtake],ids[navtake],selected_nav['architecture'],selected_nav['penalty']),('ocular_adapter__',ef[eyetake],ey[eyetake],eids[eyetake],'hierarchical',selected_eye['penalty'])]:
            fitted=fit_architecture(values_,label,group,architecture,penalty,device,out/('final-'+prefix),check);arrays.update({prefix+k:v for k,v in fitted.items()});updates+=p['steps']*(1 if architecture=='flat' else 2)
        arrays.update(ocular_motion_mean=motion_mean.astype('float32'),ocular_motion_scale=motion_scale.astype('float32'))
        details=dict({k:v for k,v in old.details.items() if k not in ['latent_gain','rest_training_mass']},navigation_features='joint',navigation_architecture=selected_nav['architecture'],navigation_temperatures=selected_nav['temperatures'],ocular_temperatures=selected_eye['temperatures'],intent_policy=chosen_policy['config'],eye_policy=eye_config,decision_policy=dict(aligned_threshold=chosen_policy['config']['activity']-.1,conservative_threshold=chosen_policy['config']['activity']),new_training='regularized_feature_fusion_activity_direction_and_eye_adapters',fusion='learned_feature_fusion_no_fixed_probability_average',all_personal_data_adapted=True,independent_test=False)
        details['eye_feature_indices']=list(range(8)) if selected_eye['feature_mode']=='frontal8' else list(map(int,np.flatnonzero(old.eye_arrays['net__input_weights'].reshape(-1))));details['eye_feature_mode']=selected_eye['feature_mode']
        model=RepairedHybridModel(arrays,details,device=device);finalnav=evidence(values,arrays,'nav__',selected_nav['architecture'],selected_nav['temperatures'])[0];finaleye=evidence(ef,arrays,'ocular_adapter__','hierarchical',selected_eye['temperatures'])[0];finalfit=scores_report(ny[navtake],finalnav[navtake],ids[navtake]);replays={};traces={}
        for mode in ['actual','disabled']:
            replays[mode],traces[mode]=policy_replay(frames,finalnav,finaleye,chosen_policy['config'],eye_config,mode,motion_final)
        oldprob=.5*readout_predict(x,old.arrays,'spectral__')+.5*readout_predict(h,old.arrays,'latent__');baseline=[];prev=None;policy=GazePolicy()
        for f in frames:
            if prev is None or f['trial_id']!=prev['trial_id'] or not f['quality_valid']:policy=GazePolicy()
            prev=f;i=f['cache_index'];decision=policy.step(oldprob[i]) if i>=0 and not f['warming'] else dict(direction=0,status='UNKNOWN',reason='quality_or_warmup')
            baseline.append(dict(trial_id=f['trial_id'],source_end=f['source_end'],label=f['navigation_label'],decision=decision))
        replays['old_same_support']=policy_metrics(baseline)
        record=dict(runtime_schema=RUNTIME,algorithm='hybrid_navigation',dataset_title='个人导航数据集',dataset_sha256=digest(ds),parent_dataset_sha256=digest(ds['parent']),protocol_sha256=digest(p),sources=p['sources'],code_sha256=p['code_sha256'],evaluation_stage='full_personal_adaptation_exploratory',runtime_accepted=False,independent_test=False,class_labels=['REST','FORWARD','BACKWARD','LEFT','RIGHT'],input_contract=oldmeta['input_contract'],montage_attestation=oldmeta.get('montage_attestation'),electrical_policy=ELECTRICAL,metrics=dict(training=finalfit,development=selected_nav['metrics'],continuous_replay=replays['actual']),seed=17,updates=updates)
        from .hybrid_provenance import native_export_metadata
        record.update(native_export_metadata(ds['parent']))
        path=save_artifact(out/'artifacts',model,record);_,meta=load_artifact(path,device=device)
        atomic(out/'replay-frames.json',dict(actual=traces['actual'],disabled=traces['disabled'],old=baseline));np.savez(out/'predictions.npz',navigation=finalnav,eye=finaleye,navigation_oof=npred,eye_oof=epred,motion_oof=motion_oof,motion_final=motion_final)
        result=dict(schema=SCHEMA,model_id=meta['model_id'],artifact_path=str(path),weights_sha256=meta['weights_sha256'],selected_navigation=selected_nav,selected_eye=selected_eye['selected'],selected_policy=chosen_policy,final_fit=finalfit,replays=replays,final_eye_event_matching=event_stats(frames,finaleye,ds['events']['segments'],eye_config,motion_final),actual_updates=updates,elapsed_s=time.monotonic()-start,independent_test=False,live_enabled=False)
        atomic(out/'report.json',result);return result

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for key in ['root','code-root','dataset','protocol','profile','output']:ap.add_argument('--'+key,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
