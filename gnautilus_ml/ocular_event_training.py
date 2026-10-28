"""Governed manual ocular candidate. Public spatial domains stay separate.

This is an auxiliary event classifier, not a replacement navigation JEPA or an
objective gaze estimator. No task phase or human boundary is a model input.
"""
import copy,time
from pathlib import Path
import numpy as np
from .common import digest,file_hash,atomic,read_json,within
from .navigation_contract import CHANNELS,EOG,ELECTRICAL,SCOPE
from .ocular_event_data import CLASSES,compile_annotations

SCHEMA='gnautilus.manual_ocular_training.v1'
BEST_GAZE='d67cf692c766698f888b97e225d0a76a64a299262c7c5542627412db8f56ddd3'
PUBLIC_IDS=[EOG,BEST_GAZE]

def causal_view(x,end):
    if x.ndim!=2 or x.shape[0]!=32 or not 0<end<=x.shape[1]:raise ValueError('Real 32-column causal support required')
    return x[:,max(0,end-500):end].copy()

def prefix_ends(start,end):
    if not start<end:raise ValueError('Empty event')
    return sorted(set(list(range(start+25,end+1,25))+[end]))

def event_weights(y,ids):
    w=np.zeros(len(y),dtype='float64')
    for label in np.unique(y):
        events=np.unique(ids[y==label])
        for event in events:
            at=(y==label)&(ids==event);w[at]=1/(len(events)*at.sum())
    return w/w.sum()

def metrics(y,p):
    pred=p.argmax(1);cm=np.zeros((5,5),int)
    for a,b in zip(y,pred):cm[int(a),int(b)]+=1
    recall=[float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None for i in range(5)]
    return dict(windows=len(y),class_order=CLASSES,confusion_matrix=cm.tolist(),recall=recall,
        accuracy=float(np.mean(pred==y)) if len(y) else None,
        balanced_accuracy=float(np.mean([r for r in recall if r is not None])) if len(y) else None,
        all_classes_supported=all(v is not None for v in recall))

def build_model(arrays=None,input_weights=None):
    import torch
    from torch import nn
    from .stream_jepa import Branch,transfer_branch
    class Model(nn.Module):
        def __init__(self):
            super().__init__();self.branch=Branch(32);self.head=nn.Linear(32,5)
            self.register_buffer('input_weights',torch.as_tensor(np.ones(32) if input_weights is None else np.asarray(input_weights),dtype=torch.float32).reshape(1,32,1))
            if arrays is not None:transfer_branch(self.branch,arrays,[],CHANNELS,copy_spatial=False)
        def forward(self,x,lengths):
            # Right padding never enters convolution/GRU history. The final
            # incomplete pooling group uses its real samples, not zero filler.
            z=torch.nn.functional.elu(self.branch.spatial(self.branch.temporal(torch.nn.functional.pad((x*self.input_weights)[:,None],(16,0))))).squeeze(2)
            features=[]
            for i,n in enumerate(lengths):
                k=n//4*4;parts=[]
                if k:parts.append(z[i,:,:k].reshape(16,-1,4).mean(-1))
                if k<n:parts.append(z[i,:,k:n].mean(-1,keepdim=True))
                features.append(torch.cat(parts,-1).T)
            packed=nn.utils.rnn.pack_sequence(features,enforce_sorted=False);_,h=self.branch.gru(packed)
            return self.head(h[-1])
    return Model()

def make_protocol(ds,code_root,resources,prior_seconds):
    root=Path(code_root)
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),scope=SCOPE,
        code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((root/'gnautilus_ml').glob('*.py'))},
        public_resources=resources,public_ids=PUBLIC_IDS,seeds=[17,29,41],arms=['manual_scratch','public_mixed_transfer'],
        channel_selection='train-only marked-vs-nearby contrast, top4/top8/per-direction-top2-union plus frontal8; all32 comparator ineligible for selection',
        epochs=100,freeze_shared_epochs=20,private_lr=.001,shared_lr=.00003,private_batch='all_weighted_prefixes',
        public_batch_per_domain=16,public_ce_weight=.2,public_kd_weight=.1,kd_temperature=2.,
        context_samples=500,hop_samples=25,baseline_analysis_samples=[250,500],
        target_semantics='manual ocular events only; NO_EYE is not navigation REST',
        channels=CHANNELS,quality=ELECTRICAL,normalization='private train-only equal class/event weighted centered histories; public native domains separate',
        selection='validation event macro BA, then prefix BA, then CE; earliest epoch and fixed candidate order on ties',
        fit_roles=['train'],selection_roles=['validation'],test_access='after_selection_freeze_only_previously_inspected',
        prior_elapsed_s=float(prior_seconds),max_wall_seconds=1800,total_budget_seconds=43200,
        registration='offline_arrays_only_no_service_change')

def check_protocol(p,ds,code_root):
    from .personal_study import verify_sources,episode_bounds
    if ds.get('schema')!='gnautilus.manual_ocular_dataset.v1':raise ValueError('Manual source dataset required')
    if ds['events']!=compile_annotations(ds['catalog'],ds['human_export']):raise ValueError('Manual metadata was altered after snapshot')
    parent=ds['parent'];events=ds['events']
    if (digest(parent)!=events['parent_dataset_id'] or parent['channels']!=CHANNELS or parent['sampling_rate']!=250 or
        parent.get('scope')!=SCOPE):raise ValueError('Parent physical identity differs')
    old={r['trial_id']:r for r in parent['episodes']}
    for q in events['source_trials']:
        r=old.get(q['trial_id'],{})
        if any(q[k]!=r.get(k) for k in ['stream_id','split','start','end']):raise ValueError('Original complete trial role differs')
    if set(old)!={q['trial_id'] for q in events['source_trials']}:raise ValueError('Incomplete source trial snapshot')
    if p!=make_protocol(ds,code_root,p['public_resources'],p['prior_elapsed_s']):raise ValueError('Locked manual training recipe/source differs')
    if p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>p['total_budget_seconds']:raise ValueError('Aggregate budget exceeded')
    base=Path(parent['folder']).parent.parent
    if [r['model_id'] for r in p['public_resources']]!=PUBLIC_IDS:raise ValueError('Unreviewed public tasks/teachers')
    for r in p['public_resources']:
        folder=within(base/'models'/r['model_id'],base/'models');m=read_json(folder/'manifest.json')
        if file_hash(folder/'manifest.json')!=r['manifest_sha256'] or file_hash(folder/'weights.npz')!=r['weights_sha256'] or m['weights_sha256']!=r['weights_sha256']:raise ValueError('Public weights/contract changed')
        dp=within(base/'datasets'/(m['dataset_sha256']+'.json'),base/'datasets')
        if file_hash(dp)!=r['dataset_file_sha256'] or digest(read_json(dp))!=m['dataset_sha256']:raise ValueError('Public data identity changed')
        if m['channels']!=r['channels'] or m['class_labels']!=r['class_labels'] or m['input_contract']!=r['input_contract']:raise ValueError('Public native ontology differs')
    episode_bounds(parent['episodes']);verify_sources(parent['folder'],parent['source_receipt']);return True

def prepare_private(ds,role):
    from .ocular_boundary_audit import load_contextual_episodes
    from .quality import inspect_signal
    rows={r['id']:r for r in load_contextual_episodes(ds['parent'],role,pre=500,post=0)}
    views=[];audit=[];segments=ds['events']['segments'];stored={}
    for s in segments:
        if s['split']!=role:continue
        row=rows[s['trial_id']];m=row['metadata'];start=s['start']-m['context_start'];end=s['end']-m['context_start']
        lo=max(0,start-500);raw=row['x'][:,lo:end].copy();stored[s['segment_id']]=raw
        for at in prefix_ends(start,end):
            x=causal_view(row['x'],at);q=inspect_signal(x,CHANNELS,250,ELECTRICAL)
            item=dict(segment_id=s['segment_id'],trial_id=s['trial_id'],split=role,stream_id=s['stream_id'],phase=s['phase'],
                label=CLASSES.index(s['class_label']),target_start=s['start'],target_end=s['end'],source_start=m['context_start']+at-x.shape[1],
                source_end=m['context_start']+at,available_history_samples=x.shape[1],quality_valid=q['electrical_valid'],quality_reasons=q['reasons'],
                possibly_right_censored=s['possibly_right_censored'])
            audit.append(item)
            if q['electrical_valid']:views.append(dict(metadata=item,x=x))
    return views,audit,stored

def centered(x):
    # All baseline values have already arrived by the prediction endpoint.
    return (x-x[:,:min(25,x.shape[1])].mean(-1,keepdims=True)).astype('float32')

def tensor_views(views,mean,scale,device):
    import torch
    lengths=[v['x'].shape[1] for v in views];x=np.zeros((len(views),32,max(lengths)),dtype='float32')
    for i,v in enumerate(views):x[i,:,:lengths[i]]=(centered(v['x'])-mean)/scale
    return torch.as_tensor(x,device=device),lengths

def evaluate(model,views,mean,scale,device):
    import torch
    model.eval();probs=[]
    with torch.inference_mode():
        for at in range(0,len(views),64):
            x,n=tensor_views(views[at:at+64],mean,scale,device);probs.extend(model(x,n).softmax(-1).cpu().numpy())
    p=np.asarray(probs);y=np.asarray([v['metadata']['label'] for v in views]);ids=np.asarray([v['metadata']['segment_id'] for v in views]);eventp=[];eventy=[];eventids=[]
    for sid in dict.fromkeys(ids):
        take=ids==sid;eventp.append(p[take].mean(0));eventy.append(int(y[take][0]));eventids.append(str(sid))
    result=dict(prefix=metrics(y,p),event=metrics(np.asarray(eventy),np.asarray(eventp)),
        ce=float(-np.mean(np.log(np.maximum(p[np.arange(len(y)),y],1e-8)))),event_ids=eventids,
        event_scores=np.asarray(eventp).tolist(),event_truth=eventy)
    return result,p

def native_domains(base,resources,branch,device,cache=None):
    import torch
    from .artifacts import load_artifact
    from .public_windows import public_windows
    from .governance import record_group
    from .jepa_training import DomainStudent
    result=[]
    for resource in resources:
        if cache is not None and resource['model_id'] in cache:
            d=cache[resource['model_id']]
            result.append(dict(d,student=DomainStudent(branch,d['teacher'],copy_parameters=d['teacher'].algorithm=='cnn_gru_baseline')))
            continue
        teacher,m=load_artifact(Path(base)/'models'/resource['model_id']);ds=read_json(Path(base)/'datasets'/(m['dataset_sha256']+'.json'));xx=[];yy=[];ids=[]
        for r in ds['records']:
            if r['role']!='train':continue
            record_group(r);x,y,wi=public_windows(r,m['window_s'],.1);xx.append(x);yy.append(y);ids.extend(wi)
        x=np.concatenate(xx);y=np.concatenate(yy);target=teacher.predict(x)['probabilities']
        z=teacher._neural_input(x) if teacher.algorithm=='cnn_gru_baseline' else x.copy()
        mean=z.mean((0,2),keepdims=True);scale=np.maximum(z.std((0,2),keepdims=True),1e-5)
        if teacher.algorithm=='cnn_gru_baseline':mean=teacher.arrays['input_mean'];scale=teacher.arrays['input_scale']
        student=DomainStudent(branch,teacher,copy_parameters=teacher.algorithm=='cnn_gru_baseline')
        result.append(dict(id=resource['model_id'],student=student,x=torch.as_tensor((z-mean)/scale,device=device),
            y=torch.as_tensor(np.searchsorted(teacher.classes,y),device=device),target=torch.as_tensor(target,dtype=torch.float32,device=device),
            mean=mean,scale=scale,window_ids=ids,native_labels=m['class_labels'],channels=m['channels'],fs=m['sampling_rate'],teacher=teacher))
        if cache is not None:cache[resource['model_id']]={k:v for k,v in result[-1].items() if k!='student'}
    return result

def run(base,root,p,ds,profile,out):
    import torch
    from .governance import training_authority,compute_device
    out=Path(out);started=time.monotonic();all_models=[];details=[];support={};data={};raws={}
    def check():
        if time.monotonic()-started>p['max_wall_seconds']:raise InterruptedError('Manual eye training budget exhausted')
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();torch.set_num_threads(2)
        for role in ['train','validation']:
            data[role],support[role],raws[role]=prepare_private(ds,role)
        atomic(out/'pretraining-supports.json',support)
        if not data['validation']:raise ValueError('No electrically eligible validation histories')
        if set(v['metadata']['label'] for v in data['train'])!=set(range(5)):raise ValueError('All five ocular classes need real training supports')
        # All feature and native inputs have exact, source-bound audit rows.
        train=data['train'];y=np.asarray([v['metadata']['label'] for v in train]);ids=np.asarray([v['metadata']['segment_id'] for v in train]);weights=event_weights(y,ids)
        mean=sum(w*centered(v['x']).mean(-1,keepdims=True) for w,v in zip(weights,train));var=sum(w*((centered(v['x'])-mean)**2).mean(-1,keepdims=True) for w,v in zip(weights,train));scale=np.maximum(np.sqrt(var),1e-3)
        tx,lengths=tensor_views(train,mean,scale,device);ty=torch.as_tensor(y,device=device);tw=torch.as_tensor(weights,dtype=torch.float32,device=device)
        atomic(out/'normalization.json',dict(fit_role='train',mean=mean.tolist(),scale=scale.tolist(),class_event_weights=weights.tolist(),
            fit_segment_ids=[v['metadata']['segment_id'] for v in train]))
        with np.load(Path(base)/'models'/EOG/'weights.npz',allow_pickle=False) as z:initializer={k:z[k].copy() for k in z.files}
        from .ocular_channel_selection import channel_candidates
        channel_plan=channel_candidates([dict(s,x=raws['train'][s['segment_id']],event_start=raws['train'][s['segment_id']].shape[1]-(s['end']-s['start'])) for s in ds['events']['segments'] if s['split']=='train' and s['class_label']!='NO_EYE'])
        atomic(out/'channel-selection.json',channel_plan)
        native_receipts={};native_cache={}
        for mask,arm in [(m,a) for m in channel_plan['candidates'] for a in p['arms']]:
            for seed in p['seeds']:
                check();torch.manual_seed(seed);rng=np.random.default_rng(seed);model=build_model(initializer if arm=='public_mixed_transfer' else None,mask['input_weights']).to(device)
                domains=native_domains(base,p['public_resources'],model.branch,device,native_cache) if arm=='public_mixed_transfer' else []
                shared=list(model.branch.temporal.parameters())+list(model.branch.gru.parameters());shared_ids={id(v) for v in shared}
                local=[v for v in model.parameters() if id(v) not in shared_ids]
                for d in domains:local.extend(v for v in d['student'].parameters() if id(v) not in shared_ids)
                opt=torch.optim.AdamW([dict(params=local,lr=p['private_lr']),dict(params=shared,lr=p['shared_lr'] if domains else p['private_lr'])],weight_decay=.01)
                candidate_started=time.monotonic();best=None;best_key=None;history=[];native_count={d['id']:0 for d in domains};grad_sum={d['id']:0. for d in domains};private_grad=0.;sampling=[]
                for epoch in range(p['epochs']):
                    check();model.train()
                    for v in shared:v.requires_grad_(not domains or epoch>=p['freeze_shared_epochs'])
                    opt.zero_grad();logits=model(tx,lengths);private=(torch.nn.functional.cross_entropy(logits,ty,reduction='none')*tw).sum();loss=private;native_loss={}
                    for d in domains:
                        # Equal native-class sampling; private event/class mass
                        # is independent of public corpus size or overlap.
                        sy=d['y'].cpu().numpy();chosen=[int(rng.choice(np.flatnonzero(sy==int(k)))) for k in rng.choice(np.unique(sy),p['public_batch_per_domain'])];index=torch.as_tensor(chosen,device=device)
                        sampling.append(dict(epoch=epoch+1,domain=d['id'],native_training_indices=chosen))
                        z=d['student'](d['x'][index]);ce=torch.nn.functional.cross_entropy(z,d['y'][index]);t=p['kd_temperature']
                        kd=torch.nn.functional.kl_div((z/t).log_softmax(-1),(d['target'][index].clamp_min(1e-8).log()/t).softmax(-1),reduction='batchmean')*t*t
                        part=p['public_ce_weight']*ce+p['public_kd_weight']*kd;loss=loss+part;native_loss[d['id']]=float(part.detach());native_count[d['id']]+=len(chosen)
                        grads=torch.autograd.grad(part,[v for v in shared if v.requires_grad],retain_graph=True,allow_unused=True) if any(v.requires_grad for v in shared) else []
                        grad_sum[d['id']]+=float(torch.sqrt(sum((g.detach()**2).sum() for g in grads if g is not None))) if grads else 0.
                    g=torch.autograd.grad(private,local[:len([v for v in model.parameters() if id(v) not in shared_ids])],retain_graph=True,allow_unused=True);private_grad+=float(torch.sqrt(sum((v.detach()**2).sum() for v in g if v is not None)))
                    loss.backward();torch.nn.utils.clip_grad_norm_(shared+local,5.);opt.step()
                    dev,_=evaluate(model,data['validation'],mean,scale,device);key=(dev['event']['balanced_accuracy'],dev['prefix']['balanced_accuracy'],-dev['ce'])
                    item=dict(epoch=epoch+1,loss=float(loss.detach()),private_loss=float(private.detach()),native_losses=native_loss,validation_event_ba=key[0],validation_prefix_ba=key[1]);history.append(item)
                    if best_key is None or key>best_key:best_key=key;best=copy.deepcopy(model.state_dict());best_epoch=epoch+1
                    if epoch==0 or (epoch+1)%20==0:
                        cp=out/f'{mask["name"]}-{arm}-{seed}-epoch{epoch+1:03d}.pt';torch.save(dict(model=model.state_dict(),optimizer=opt.state_dict(),public_adapters=[d['student'].state_dict() for d in domains],epoch=epoch+1,protocol_id=digest(p),torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state(),numpy_rng=rng.bit_generator.state,best_model=best,best_epoch=best_epoch,best_key=best_key),cp)
                        atomic(cp.with_suffix('.json'),dict(file=cp.name,sha256=file_hash(cp),epoch=epoch+1));atomic(out/'progress.json',dict(phase='training',montage=mask['name'],arm=arm,seed=seed,epoch=epoch+1,elapsed_s=time.monotonic()-started))
                model.load_state_dict(best)
                name=f'{mask["name"]}-{arm}-{seed}';arrays={'net__'+k:v.detach().cpu().numpy() for k,v in model.state_dict().items()};arrays.update(input_mean=mean,input_scale=scale)
                np.savez(out/(name+'.npz'),**arrays);train_metrics,_=evaluate(model,train,mean,scale,device);dev,_=evaluate(model,data['validation'],mean,scale,device)
                importance=np.sqrt(np.sum(model.branch.spatial.weight.detach().cpu().numpy()**2,axis=(0,1,3)))*np.asarray(mask['input_weights'])
                entry=dict(candidate=name,montage=mask,learned_spatial_magnitude=importance.tolist(),arm=arm,seed=seed,best_epoch=best_epoch,training=train_metrics,validation=dev,history=history,
                    native_samples_used=native_count,native_updates_per_domain=p['epochs'] if domains else 0,private_prefixes_used=p['epochs']*len(train),
                    elapsed_s=time.monotonic()-candidate_started,native_shared_gradient_norm_sum=grad_sum,private_gradient_norm_sum=private_grad,weights_sha256=file_hash(out/(name+'.npz')))
                details.append(entry)
                if mask['name']!='all32_reference':all_models.append((best_key,name,model))
                atomic(out/(name+'-history.json'),entry)
                atomic(out/(name+'-native-sampling.json'),sampling)
                for d in domains:native_receipts[d['id']]=dict(training_windows=len(d['y']),window_ids=d['window_ids'],channels=d['channels'],native_labels=d['native_labels'],sampling_rate=d['fs'],unit_alignment='not asserted; separate domain normalization and spatial adapter')
                print(name,best_key,flush=True)
        # Commit selection before any test EEG decoding. Historical inspection
        # is explicit, even though this run keeps optimization roles separate.
        chosen=max(all_models,key=lambda row:row[0]);atomic(out/'selection-freeze.json',dict(candidate=chosen[1],key=chosen[0],protocol_id=digest(p),test_decoded=False,weights_sha256=file_hash(out/(chosen[1]+'.npz'))))
        data['test'],support['test'],raws['test']=prepare_private(ds,'test')
        if not data['test']:raise ValueError('No electrically eligible test histories')
        test,pred=evaluate(chosen[2],data['test'],mean,scale,device)
        np.savez(out/'selected-test-predictions.npz',probabilities=pred)
        # Preserve variable-length real segments, including partial tails.
        for role in ['train','validation','test']:np.savez(out/('manual-segments-'+role+'.npz'),**raws[role])
        atomic(out/'segment-storage.json',dict(schema='gnautilus.manual_ocular_segment_storage.v1',channels=CHANNELS,sampling_rate=250,roles={role:[dict(segment_id=sid,
            stream_id=next(s['stream_id'] for s in ds['events']['segments'] if s['segment_id']==sid),
            source_start=next(s['end'] for s in ds['events']['segments'] if s['segment_id']==sid)-x.shape[1],
            source_end=next(s['end'] for s in ds['events']['segments'] if s['segment_id']==sid),shape=list(x.shape)) for sid,x in values.items()] for role,values in raws.items()}))
        atomic(out/'supports.json',support);atomic(out/'public-training-supports.json',native_receipts)
        report=dict(schema='gnautilus.manual_ocular_result.v1',protocol_id=digest(p),selected=chosen[1],test=test,candidates=details,
            input_counts=ds['events']['counts'],supports={role:dict(prefixes=len(data[role]),proposed=len(support[role]),qc_rejected=sum(not r['quality_valid'] for r in support[role]),events=len(raws[role])) for role in data},
            elapsed_s=time.monotonic()-started,scope=SCOPE,previous_test_inspected=True,automatic_labels_used=False,model_registry_changed=False,
            limitations=['Only 7 independent training direction events and one no-eye baseline trial.',
                'Training NO_EYE currently from official software; source/class confound remains.',
                'Human-boundary-conditioned event averages are descriptive, not autonomous continuous detection acceptance.',
                'No negative blink annotations; no blink-specific safety/effectiveness claim.',
                'Finite causal auxiliary classifier; JEPA backbone and action model unchanged.'])
        atomic(out/'report.json',report);check();return report

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for k in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();p=read_json(a.protocol);ds=read_json(a.dataset)
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve():raise ValueError('Checked protocol environment required')
    if Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Output must retain checked protocol')
    run(a.root,a.code_root,p,ds,read_json(a.profile),a.output)
if __name__=='__main__':main()
