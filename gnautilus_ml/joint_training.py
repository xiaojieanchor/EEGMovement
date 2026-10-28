"""Locked, train/validation-only repair pilot. No held-out test forward pass."""
import copy,math,time
from pathlib import Path
import numpy as np
import torch
from .common import atomic,digest,file_hash,read_json,within
from .navigation_contract import CHANNELS,FRONTAL,OPENBMI,EOG,DEFAULT_POLICY,ELECTRICAL,SCOPE,ALGORITHM
from .personal_study import load_episodes,unlabelled_regions,reuse_inventory,episode_bounds,verify_sources,PHYSIONET
from .stream_jepa import arrays_from,transfer_branch,detach_state
from .joint_stream import JointJEPA,JointPreprocessor,JointStreamModel,joint_jepa_loss,RUNTIME
from .jepa_training import prepared,normalization,native_domains,native_teacher_ids,ssl_fit,evaluate,neural_segments,gradient_evidence,checkpoint,restore_torch_rng
from .governance import training_authority,compute_device
from .data import LABELS


def freeze_public_resources(root):
    root=Path(root);inventory=reuse_inventory(root);teachers={}
    for row in inventory:row['manifest_sha256']=file_hash(root/'models'/row['model_id']/'manifest.json')
    for mid in [OPENBMI,EOG,PHYSIONET]:
        teachers[mid]=[]
        for model_id in native_teacher_ids(root,mid,inventory):
            path=root/'models'/model_id;m=read_json(path/'manifest.json');data=root/'datasets'/(m['dataset_sha256']+'.json')
            if digest(read_json(data))!=m['dataset_sha256']:raise ValueError('Public source dataset identity differs')
            teachers[mid].append(dict(model_id=model_id,weights_sha256=m['weights_sha256'],dataset_sha256=m['dataset_sha256'],manifest_sha256=file_hash(path/'manifest.json'),dataset_file_sha256=file_hash(data)))
    resources=dict(inventory=inventory,teachers=teachers);verify_public_resources(root,resources);return resources


def verify_public_resources(root,resources):
    root=Path(root)
    if set(resources.get('teachers',{}))!={OPENBMI,EOG,PHYSIONET} or not resources.get('inventory'):raise ValueError('Frozen public resources required')
    for row in resources['inventory']:
        path=within(root/'models'/row['model_id'],root/'models')
        if file_hash(path/'manifest.json')!=row['manifest_sha256'] or file_hash(path/'weights.npz')!=row['weights_sha256']:raise ValueError('Frozen public artifact changed')
    for mid in resources['teachers']:native_teacher_ids(root,mid,resources['inventory'],resources['teachers'])
    return True


def make_joint_protocol(dataset,code_root,options,budget,public_resources=None):
    steps=int(options.get('ssl_steps',300));epochs=int(options.get('max_epochs',100))
    wall=min(7200,int(43200-budget['prior_elapsed_s']-budget['reserve_s']))
    p=dict(schema='gnautilus.training_protocol.v4',architecture='shared_full32_joint_v2',scope=SCOPE,algorithm=ALGORITHM,
        dataset_sha256=digest(dataset),code_sha256={p.relative_to(code_root).as_posix():file_hash(p) for p in sorted((Path(code_root)/'gnautilus_ml').glob('*.py'))},
        seeds=[17],seed=17,candidate_matrix=[[17,'jepa_transfer'],[17,'transfer_no_jepa']],ssl_steps=steps,max_epochs=epochs,max_wall_seconds=wall,
        target_supports=[[25,150],[150,275]],context_max_samples=2000,hop_samples=25,reuse_ids=[OPENBMI,EOG,PHYSIONET],
        head_lr=.003,backbone_lr=.0003,freeze_rounds=5,private_sequence_passes_per_epoch=1,whole_trial_state_carry=True,
        optimizer_update='after_all_class_balanced_trials',bptt_samples=2000,normalization_roles=['train'],selection_roles=['validation'],
        test_access='not_in_pilot',registration='offline_research_candidate',normalization='training_label_class_balanced_static',
        selection='validation raw BA tie earliest; no useful execution policy is explicitly reported',
        auxiliary_supervision='instruction-anchored first 2s and explicit REST only; class balance within actual weak support; no objective gaze claim',
        continuous_context_samples=1000,aggregate_budget=dict(budget),probe_steps=100,private_label_truth='joint instructed navigation; UNKNOWN remains -1',
        public_resources=copy.deepcopy(public_resources if public_resources is not None else options.get('public_resources',{})),ssl_quality='causal_electrical_reset_segments_before_all_private_draws')
    if not 1<=steps<=300 or not 1<=epochs<=100 or wall<60 or options.get('reuse_ssl_job'):raise ValueError('Repair pilot budget/identity differs')
    return p


def prior_budget(root):
    from .registry import Registry
    root=Path(root);rows=[]
    for job in Registry(root/'state.sqlite').jobs():
        if job['spec'].get('algorithm')!=ALGORITHM:continue
        if job['state'] not in ['completed','failed','cancelled','interrupted']:raise ValueError('Compute must have no active study before locking a pilot')
        path=root/'logs'/('jepa-'+job['id'])/'progress.json'
        rows.append(dict(job_id=job['id'],elapsed_s=float(read_json(path).get('elapsed_s',0.)) if path.exists() else 0.,progress_sha256=file_hash(path) if path.exists() else None))
    # Source-bound supporting experiments are outside the model-library jobs.
    # Include duplicate/negative runs too; a lost timing receipt uses its full
    # locked upper bound, never a fabricated zero cost.
    for path in sorted((root/'logs').glob('eye-joint-probe-*/report.json')):
        value=read_json(path);elapsed=float(value['elapsed_s'])
        rows.append(dict(job_id='support:'+path.parent.name,elapsed_s=elapsed,progress_sha256=file_hash(path),receipt_path=path.relative_to(root).as_posix()))
    for protocol in sorted((root/'logs').glob('eye-weight-probe-*/protocol.json')):
        result=protocol.parent/'report.json';value=read_json(result) if result.exists() else {};elapsed=value.get('elapsed_s')
        receipt=result if result.exists() else protocol
        rows.append(dict(job_id='support:'+protocol.parent.name,elapsed_s=float(elapsed) if elapsed is not None else float(read_json(protocol)['max_wall_seconds']),progress_sha256=file_hash(receipt),receipt_path=receipt.relative_to(root).as_posix(),timing='actual' if elapsed is not None else 'locked_upper_bound'))
    for protocol in sorted((root/'logs').glob('context-readout-repair-*/protocol.json')):
        result=protocol.parent/'report.json';value=read_json(result) if result.exists() else {};elapsed=value.get('elapsed_s');receipt=result if result.exists() else protocol
        rows.append(dict(job_id='support:'+protocol.parent.name,elapsed_s=float(elapsed) if elapsed is not None else float(read_json(protocol)['max_wall_seconds']),progress_sha256=file_hash(receipt),receipt_path=receipt.relative_to(root).as_posix(),timing='actual' if elapsed is not None else 'locked_upper_bound'))
    return dict(prior_elapsed_s=sum(r['elapsed_s'] for r in rows),reserve_s=600.,jobs=rows)


def check_joint_protocol(p,dataset,code_root):
    if p.get('ssl_reuse') or p.get('test_access')!='not_in_pilot':raise ValueError('Pilot cannot reuse old SSL or access held-out test')
    expected=make_joint_protocol(dataset,code_root,p,p['aggregate_budget'])
    if p!=expected:raise ValueError('Joint pilot source/recipe/budget differs from locked protocol')
    if dataset.get('schema')!='gnautilus.personal_study.v1' or dataset.get('scope')!=SCOPE or dataset.get('channels')!=CHANNELS or dataset.get('sampling_rate')!=250:raise ValueError('Explicit internal same-wearing physical montage required')
    if p['aggregate_budget']['prior_elapsed_s']<0 or p['aggregate_budget']['reserve_s']<600:raise ValueError('Aggregate budget receipt incomplete')
    if not p['code_sha256']:raise ValueError('No source identity')
    episode_bounds(dataset['episodes']);verify_sources(dataset['folder'],dataset['source_receipt'])
    verify_public_resources(Path(dataset['folder']).parent.parent,p['public_resources'])
    return True


def initialize_joint(root,device):
    net=JointJEPA().to(device);audit={}
    for name,mid,positions in [('motor',OPENBMI,CHANNELS),('eye',EOG,FRONTAL)]:
        path=Path(root)/'models'/mid;meta=read_json(path/'manifest.json')
        if file_hash(path/'weights.npz')!=meta['weights_sha256']:raise ValueError('Initializer hash changed')
        with np.load(path/'weights.npz',allow_pickle=False) as z:a={k:z[k].copy() for k in z.files}
        copied=transfer_branch(getattr(net,name),a,meta['channels'],positions,copy_spatial=name=='motor')
        if name=='eye':net.native_eye_head.load_state_dict({k:torch.as_tensor(a['net__head.'+k].copy(),device=device) for k in ['weight','bias']})
        audit[name]=dict(model_id=mid,weights_sha256=meta['weights_sha256'],copied=copied,source_channels=meta['channels'],destination_channels=positions,
                        montage16=positions[15] if name=='motor' else None,montage19=positions[18] if name=='motor' else None)
    return net,audit


def training_spans(episodes,stream,segment,context=1000):
    """Merge same-role raw supports; stop at held-out contexts and real gaps."""
    sa,sb=segment;blocked=[(r['context_start'],r['context_end']) for r in episodes if r['stream_id']==stream and r['split']!='train']
    spans=[]
    for r in episodes:
        if r['stream_id']!=stream or r['split']!='train' or not sa<=r['context_start']<r['context_end']<=sb:continue
        lo=max(sa,r['context_start']-context);hi=min(sb,r['context_end']+context)
        for a,b in blocked:
            if b<=r['context_start']:lo=max(lo,b)
            elif a>=r['context_end']:hi=min(hi,a)
            else:raise ValueError('Training trial overlaps held-out raw samples')
        spans.append([lo,hi])
    merged=[]
    for lo,hi in sorted(spans):
        if merged and lo<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],hi)
        else:merged.append([lo,hi])
    return merged


def continuous_training_rows(dataset):
    base=load_episodes(dataset,'train');out=[];folder=Path(dataset['folder'])/'.metadata/canonical'
    for stream in sorted({r['metadata']['stream_id'] for r in base}):
        path=folder/stream
        source_rows=[r for r in base if r['metadata']['stream_id']==stream]
        if not path.exists() and all(r['metadata'].get('contract_id','').startswith('official_') for r in source_rows):
            # Official HDF5 was admitted through the immutable episode NPZ, not
            # through the native software's canonical-stream export. Do not
            # invent unavailable surrounding samples or a native descriptor.
            for row in source_rows:
                m=dict(row['metadata'],context_extension='official_immutable_episode_only')
                m['anchors']=[dict(trial_id=row['id'],onset=m['onset_in_episode'],offset=m['offset_in_episode'])]
                out.append(dict(row,metadata=m))
            continue
        d=read_json(path/'descriptor.original.json');indices=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG']
        if len(indices)!=32 or d['sampling_rate']!=250:raise ValueError('Continuous input identity changed')
        raw=np.load(path/'samples.npy',mmap_mode='r',allow_pickle=False);coverage=np.load(path/'coverage.npy',mmap_mode='r',allow_pickle=False);conflict=np.load(path/'conflict_mask.npy',mmap_mode='r',allow_pickle=False)
        for segment in read_json(path/'segments.json')['segments']:
            for a,b in training_spans(dataset['episodes'],stream,segment):
                if not coverage[a:b].all() or conflict[a:b].any():raise ValueError('Continuous training support has missing/conflicting source samples')
                x=np.asarray(raw[a:b][:,indices].T,dtype='float32').copy();y=np.full(b-a,-1,dtype='int8');anchors=[]
                for row in base:
                    m=row['metadata'];lo=max(a,m['context_start']);hi=min(b,m['context_end'])
                    if m['stream_id']!=stream or lo>=hi:continue
                    y[lo-a:hi-a]=row['y'][lo-m['context_start']:hi-m['context_start']];anchors.append(dict(trial_id=row['id'],onset=m['context_start']+m['onset_in_episode']-a,offset=m['context_start']+m['offset_in_episode']-a))
                if anchors:out.append(dict(id=stream+':'+str(a)+':'+str(b),x=x,y=y,metadata=dict(stream_id=stream,context_start=a,context_end=b,anchors=anchors)))
    if sum(len(r['metadata']['anchors']) for r in out)!=len(base):raise ValueError('Continuous supports lost or duplicated a complete training trial')
    return out


def screen_ssl_regions(regions):
    """No sampled context, gap or future target may cross a rejected hop."""
    out=[]
    for row in regions:
        for a,b in neural_segments(row):
            if b-a<650:continue
            out.append(dict(x=row['x'][:,a:b],stream_id=row['stream_id'],start=row['start']+a,end=row['start']+b))
    return out


def joint_normalization(rows):
    moments=[]
    for label in range(5):
        blocks=[r['z'][:,r['normalization_mask']&(r['y']==label)] for r in rows];x=np.concatenate(blocks,1)
        if not x.shape[1]:raise ValueError('Missing electrically eligible training class')
        moments.append((x.mean(1),(x*x).mean(1)))
    mean=np.mean([m[0] for m in moments],0);second=np.mean([m[1] for m in moments],0)
    return mean,np.sqrt(np.maximum(second-mean*mean,1e-4))


def weak_supervision_weights(rows):
    """Balance the ACTUAL weak support, independently from whole-trial CE."""
    masks=[];counts=np.zeros(5,int)
    for row in rows:
        weak=row['y']==0
        for a in row['metadata'].get('anchors',[dict(onset=row['metadata'].get('onset_in_episode',0))]):
            weak[max(0,a['onset']):a['onset']+500]=True
        ids=row['quality_eligible_ends'];active=ids[weak[ids]]
        counts+=np.bincount(row['y'][active],minlength=5);masks.append(weak)
    if np.any(counts==0):raise ValueError('Every weak class needs real eligible support')
    return counts,masks


def adaptation_round(net,rows,mean,scale,optimizer,check):
    """Keep parameters fixed throughout all genuine continuous histories."""
    device=next(net.parameters()).device;counts=np.zeros(5,int)
    for row in rows:counts+=np.bincount(row['y'][row['quality_eligible_ends']],minlength=5)
    if np.any(counts==0):raise ValueError('Class-balanced optimization requires all training classes')
    weak_counts,masks=weak_supervision_weights(rows)
    optimizer.zero_grad(set_to_none=True);stats=dict(joint_ce=0.,shared_ce=0.,weak_eye_ce=0.,weak_eye_class_support=weak_counts.tolist(),weak_eye_weight_mass=[0.]*5,complete_training_trials=sum(len(r['metadata'].get('anchors',[r['id']])) for r in rows))
    for row,weak in zip(rows,masks):
        ends=row['quality_eligible_ends']+1
        for sa,sb in neural_segments(row):
            state=None
            for a in range(sa,sb,2000):
                check();b=min(a+2000,sb);x=(row['z'][:,a:b]-mean[:,None])/scale[:,None]
                out,state=net.sequence(torch.as_tensor(x[None],device=device),state)
                chosen=ends[(ends>a)&(ends<=b)];ix=(chosen-sa)//4-1-(a-sa)//4;keep=(ix>=0)&(ix<out['logits'].shape[1]);chosen=chosen[keep];ix=ix[keep]
                if len(chosen):
                    yy=row['y'][chosen-1];target=torch.as_tensor(yy,dtype=torch.long,device=device);weights=torch.as_tensor(1/(5*counts[yy]),device=device,dtype=torch.float32)
                    losses={k:(torch.nn.functional.cross_entropy(out[key][0,ix],target,reduction='none')*weights).sum() for k,key in [('joint_ce','logits'),('shared_ce','motor_logits')]}
                    mask=torch.as_tensor(weak[chosen-1],device=device)
                    eye_weights=torch.as_tensor(1/(5*weak_counts[yy]),device=device,dtype=torch.float32)*mask
                    losses['weak_eye_ce']=(torch.nn.functional.cross_entropy(out['eye_logits'][0,ix],target,reduction='none')*eye_weights).sum()
                    for k in range(5):stats['weak_eye_weight_mass'][k]+=float(eye_weights[target==k].sum())
                    loss=losses['joint_ce']+.1*losses['shared_ce']+.1*losses['weak_eye_ce'];loss.backward()
                    for k,v in losses.items():stats[k]+=float(v.detach())
                state=detach_state(state)
    stats['gradient_norms']=gradient_evidence(net);norm=torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
    if not torch.isfinite(norm) or not np.isfinite(stats['joint_ce']):raise ValueError('Nonfinite balanced adaptation')
    optimizer.step();stats['loss']=stats['joint_ce']+.1*stats['shared_ce']+.1*stats['weak_eye_ce'];return stats


def equal_trial_ce(frames):
    losses=[]
    for tid in sorted({f['trial_id'] for f in frames}):
        f=[r for r in frames if r['trial_id']==tid and r['label']>=0 and r['raw_prediction']>=0]
        if f:losses.append(np.mean([-np.log(max(1e-8,r['probabilities'][r['label']])) for r in f]))
    return float(np.mean(losses)) if losses else None


def choose_useful_policy(table):
    useful=[r for r in table if r['validation']['responded_trials']>0 and r['validation']['accepted_macro_recall']>0]
    if not useful:return dict(status='no_useful_policy',selected=None,reason='no candidate responds correctly to any directional trial')
    best=max(useful,key=lambda r:(-r['validation']['false_starts'],r['validation']['accepted_macro_recall'],-(r['validation']['first_response_p50_s'] or 1000)))
    return dict(status='validation_selected_research_policy',selected=best['policy'])


def calibrate_joint(model,validation):
    original=dict(model.details['decision_policy']);model.details['decision_policy']=dict(original,temperature=1.)
    _,frames=evaluate(model,validation);temps=[]
    for t in [.75,1.,1.5,2.]:
        changed=[]
        for f in frames:
            p=np.maximum(f['probabilities'],1e-8)**(1/t);p=p/p.sum();changed.append(dict(f,probabilities=p.tolist()))
        temps.append((equal_trial_ce(changed),t))
    temperature=min(temps)[1];table=[]
    for threshold in [.7,.75,.8,.85]:
        policy=dict(DEFAULT_POLICY,aligned_threshold=threshold,conservative_threshold=min(.95,threshold+.1),temperature=temperature)
        model.details['decision_policy']=policy;report,_=evaluate(model,validation);table.append(dict(policy=policy,validation=report))
    result=choose_useful_policy(table)
    model.details['decision_policy']=result['selected'] or dict(original,temperature=temperature)
    model.details['policy_selection']=result
    return dict(temperature_fits=temps,candidates=table,result=result)


def fixed_training_probe(net,rows,mean,scale,protocol,progress,check):
    clone=copy.deepcopy(net).train();device=next(net.parameters()).device;xx=[];yy=[];supports=[]
    for row in rows:
        ends=row['quality_eligible_ends']+1
        for label in range(5):
            eligible=ends[(row['y'][ends-1]==label)&(ends>=375)]
            for end in eligible[np.linspace(0,len(eligible)-1,min(8,len(eligible)),dtype=int)] if len(eligible) else []:
                xx.append((row['z'][:,end-375:end]-mean[:,None])/scale[:,None]);yy.append(label)
                supports.append(dict(stream_id=row['metadata']['stream_id'],start=row['metadata']['context_start']+int(end)-375,end=row['metadata']['context_start']+int(end),label=label))
    x=torch.as_tensor(np.stack(xx),device=device);y=torch.as_tensor(yy,device=device);optimizer=torch.optim.AdamW(clone.parameters(),lr=.003,weight_decay=0.);points=[]
    for step in range(protocol['probe_steps']):
        check();optimizer.zero_grad(set_to_none=True);out=clone.batch(x,[375]*len(x));loss=torch.nn.functional.cross_entropy(out['logits'],y)
        loss.backward();optimizer.step()
        if step%10==0 or step+1==protocol['probe_steps']:
            point=dict(step=step+1,joint_ce=float(loss.detach()),training_accuracy=float((out['logits'].argmax(-1)==y).float().mean()));points.append(point);progress(dict(phase='train_only_fit_probe',**point))
    return dict(points=points,window_supports=supports,scope='fixed training-only finite crops, diagnostic clone discarded; not validation or streaming performance')


def fit_joint(net,rows,train_eval,validation,mean,scale,protocol,progress,check,cp):
    device=next(net.parameters()).device;heads=('motor_head','eye_head','motor.spatial','eye.spatial','fusion','joint_head')
    opt=torch.optim.AdamW([dict(params=[p for n,p in net.named_parameters() if n.startswith(heads)],lr=protocol['head_lr']),dict(params=[p for n,p in net.named_parameters() if not n.startswith(heads)],lr=protocol['backbone_lr'])],weight_decay=.01)
    best=None;best_epoch=None;best_key=None;start=0
    if cp.with_suffix('.json').exists():
        r=read_json(cp.with_suffix('.json'));path=cp.parent/r['file']
        if file_hash(path)!=r['sha256']:raise ValueError('Private resume checkpoint changed')
        saved=torch.load(path,map_location=device,weights_only=False)
        if saved['protocol_sha256']!=digest(protocol):raise ValueError('Private resume protocol changed')
        net.load_state_dict(saved['model']);opt.load_state_dict(saved['optimizer']);restore_torch_rng(saved);start=saved['step']
        best=saved['extra']['best_model'];best_epoch=saved['extra']['best_epoch'];best_key=saved['extra']['best_key']
    for epoch in range(start,protocol['max_epochs']):
        for name,p in net.named_parameters():p.requires_grad_(epoch>=protocol['freeze_rounds'] or name.startswith(heads))
        net.train();stats=adaptation_round(net,rows,mean,scale,opt,check)
        if epoch==0 or (epoch+1)%5==0 or epoch+1==protocol['max_epochs']:
            model=JointStreamModel(arrays_from(net,mean,scale),dict(decision_policy=dict(DEFAULT_POLICY)),str(device))
            train,tf=evaluate(model,train_eval);val,vf=evaluate(model,validation);key=val['balanced_accuracy']
            if best_key is None or key>best_key:best_key=key;best={k:v.detach().cpu().clone() for k,v in net.state_dict().items()};best_epoch=epoch+1
            progress(dict(phase='personal_adaptation',epoch=epoch+1,**stats,training_balanced_accuracy=train['balanced_accuracy'],validation_balanced_accuracy=key,
                          training_joint_ce=equal_trial_ce(tf),validation_joint_ce=equal_trial_ce(vf),best_epoch=best_epoch,optimizer_updates=epoch+1,
                          frozen_backbone=epoch<protocol['freeze_rounds'],state_carry='continuous_allowed_raw_region',bptt_samples=2000))
            checkpoint(cp,net,opt,None,np.random.default_rng(17),epoch+1,'joint_personal',protocol,dict(best_model=best,best_epoch=best_epoch,best_key=best_key))
    net.load_state_dict(best);return best_epoch


def run_joint_pilot(runner,jid,dataset,protocol):
    from .artifacts import save_artifact,load_artifact
    root=runner.root;out=root/'logs'/('jepa-'+jid);out.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    if (out/'progress.json').exists():started-=read_json(out/'progress.json').get('elapsed_s',0.)
    context={}
    def check():
        runner._check_cancel(jid)
        if time.monotonic()-started>protocol['max_wall_seconds']:raise InterruptedError('Locked repair pilot budget exhausted')
    def progress(row):
        check();row=dict(context,**row);runner.registry.event(jid,'jepa_progress',row);atomic(out/'progress.json',dict(row,elapsed_s=time.monotonic()-started))
    with training_authority(protocol,dataset,runner.code_root,runner.profile,root/'protocols'/(digest(protocol)+'.json')):
        for previous in protocol['aggregate_budget'].get('jobs',[]):
            path=within(root/previous['receipt_path'],root) if previous.get('receipt_path') else root/'logs'/('jepa-'+previous['job_id'])/'progress.json'
            if previous['progress_sha256'] is not None and file_hash(path)!=previous['progress_sha256']:raise ValueError('Prior aggregate budget record changed')
        device=compute_device();torch.set_num_threads(2);inventory=copy.deepcopy(protocol['public_resources']['inventory']);atomic(out/'reuse-inventory.json',inventory)
        # Loading any test signal is intentionally absent from this entrypoint.
        train_eval=load_episodes(dataset,'train');validation=load_episodes(dataset,'validation');rows=prepared(continuous_training_rows(dataset),JointPreprocessor)
        mean,scale=joint_normalization(rows);regions=screen_ssl_regions(unlabelled_regions(dataset)+[dict(x=r['x'],stream_id=r['metadata']['stream_id'],start=r['metadata']['context_start'],end=r['metadata']['context_end']) for r in rows])
        atomic(out/'continuous-supports.json',dict(training=[dict(id=r['id'],metadata=r['metadata'],eligible_ends=r['quality_eligible_ends'].tolist()) for r in rows],ssl=[{k:v for k,v in r.items() if k!='x'} for r in regions],held_out_excluded=True))
        runner.registry.transition(jid,'running');candidates=[];initialization={}
        for seed,variant in protocol['candidate_matrix']:
            context.update(seed=seed,variant=variant);torch.manual_seed(seed);rng=np.random.default_rng(seed);net,init=initialize_joint(root,device);initialization[variant]=init
            if variant=='jepa_transfer' and not (out/'fit-probe.json').exists():atomic(out/'fit-probe.json',fixed_training_probe(net,rows,mean,scale,protocol,progress,check))
            domains=native_domains(root,net,inventory,device,progress,teacher_plan=protocol['public_resources']['teachers'])
            totals=ssl_fit(net,regions,mean,scale,domains,protocol,rng,progress,check,out/f'{seed}-{variant}-ssl.pt',use_jepa=variant=='jepa_transfer',preprocessor_factory=JointPreprocessor,loss_function=joint_jepa_loss);del domains
            epoch=fit_joint(net,rows,train_eval,validation,mean,scale,protocol,progress,check,out/f'{seed}-{variant}-personal.pt')
            model=JointStreamModel(arrays_from(net,mean,scale),dict(decision_policy=dict(DEFAULT_POLICY),selected_epoch=epoch,architecture='shared_full32_joint_v2',physical_channel_count=32),device)
            calibration=calibrate_joint(model,validation);train,tf=evaluate(model,train_eval);val,vf=evaluate(model,validation)
            name=f'{seed}-{variant}';np.savez(out/(name+'.npz'),**model.arrays)
            report=dict(name=name,seed=seed,variant=variant,best_epoch=epoch,training=train,validation=val,training_joint_ce=equal_trial_ce(tf),validation_joint_ce=equal_trial_ce(vf),details=model.details,calibration=calibration,domain_totals=totals,weights_sha256=file_hash(out/(name+'.npz')))
            atomic(out/(name+'.json'),report);atomic(out/(name+'.replay.json'),dict(training=tf,validation=vf));candidates.append(report);progress(dict(phase='candidate_complete',training=train,validation=val))
        atomic(out/'initialization.json',initialization);atomic(out/'reuse-inventory.json',inventory);runner.registry.transition(jid,'evaluating')
        # A pilot exports the planned JEPA arm even if its matched control wins.
        # This is an offline research artifact, not a hidden method selection.
        candidate=next(c for c in candidates if c['variant']=='jepa_transfer')
        with np.load(out/(candidate['name']+'.npz'),allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
        model=JointStreamModel(arrays,candidate['details'],device);runner.registry.transition(jid,'exporting')
        report=dict(schema='gnautilus.joint_repair_pilot.v1',candidates=candidates,test_accessed=False,independent_test=None,elapsed_s=time.monotonic()-started,
                    source_confounds=dataset['contract_review'],complete_training_trials=len(train_eval),scope=SCOPE,hardware_accepted=False,aggregate_budget=protocol['aggregate_budget'])
        atomic(out/'report.json',report)
        evidence=dict(runtime_schema=RUNTIME,dataset_title='个人导航数据集',dataset_sha256=digest(dataset),protocol_sha256=digest(protocol),job_id=jid,evaluation_stage=SCOPE,
                      runtime_accepted=False,class_labels=LABELS,electrical_policy=ELECTRICAL,metrics=dict(training=candidate['training'],development=candidate['validation'],internal_test=None,independent_test=None),
                      code_sha256=protocol['code_sha256'],pilot=True,test_accessed=False,real_hardware_acceptance=False,seed=17)
        path=save_artifact(root/'models',model,evidence);loaded,meta=load_artifact(path,device=device)
        np.testing.assert_allclose(model.predict(train_eval[0]['x'][None,:,:375])['probabilities'],loaded.predict(train_eval[0]['x'][None,:,:375])['probabilities'],atol=1e-6)
        runner.registry.transition(jid,'registering');runner.registry.register(path,meta,jid);runner.registry.transition(jid,'completed')
        atomic(out/'completed.json',dict(model_id=meta['model_id'],runtime_loaded=False,inference_enabled=False,motion_enabled=False,test_accessed=False,pilot=True))
