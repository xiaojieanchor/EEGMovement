"""Governed real A100 study, native-task distillation and chronological evaluation."""
import copy,json,os,time
from pathlib import Path
import numpy as np
import torch
from torch import nn
from .common import atomic,digest,file_hash,read_json,within
from .navigation_contract import *
from .personal_study import load_episodes,unlabelled_regions,reuse_inventory,PHYSIONET
from .stream_jepa import CausalJEPA,Branch,Preprocessor,transfer_branch,arrays_from,StreamModel,jepa_loss,ema_update,detach_state
from .governance import training_authority,compute_device
from .data import LABELS


def prepared(rows,preprocessor_factory=Preprocessor):
    from .quality import inspect_signal
    result=[]
    for row in rows:
        x=row['x'];z=np.zeros_like(x);p=preprocessor_factory();history=np.empty((32,0),dtype='float32');fresh=0;reset=0
        reset_at=np.zeros(x.shape[1],dtype='int64');good=np.zeros(x.shape[1],bool);ends=[]
        for a in range(0,x.shape[1],25):
            b=min(a+25,x.shape[1]);history=np.concatenate((history,x[:,a:b]),1)[:,-375:]
            valid=history.shape[1]<375 or inspect_signal(history,CHANNELS,250,ELECTRICAL)['electrical_valid']
            if not valid:
                p=preprocessor_factory();history=np.empty((32,0),dtype='float32');fresh=0;reset=b
                continue
            z[:,a:b]=p.step(x[:,a:b]);fresh+=b-a;reset_at[a:b]=reset
            if fresh>=375:
                good[a:b]=True
                if row['y'][b-1]>=0:ends.append(b-1)
        if not ends:raise ValueError('No electrically eligible labelled updates in training trial '+row['id'])
        result.append(dict(row,z=z,normalization_mask=good,quality_eligible_ends=np.asarray(ends),reset_at=reset_at))
    return result


def normalization(rows):
    # Every complete training trial has equal mass, irrespective of its duration.
    mean=np.mean([r['z'][:,r['normalization_mask']].mean(1) for r in rows],0)
    second=np.mean([(r['z'][:,r['normalization_mask']]**2).mean(1) for r in rows],0)
    return mean,np.sqrt(np.maximum(second-mean**2,1e-4))


def initialize(root,device,transfer=True):
    net=CausalJEPA().to(device);sources={}
    if transfer:
        for name,mid,positions in [('motor',OPENBMI,MOTOR),('eye',EOG,FRONTAL)]:
            p=Path(root)/'models'/mid;m=read_json(p/'manifest.json')
            if file_hash(p/'weights.npz')!=m['weights_sha256']:raise ValueError('Initializer hash mismatch')
            with np.load(p/'weights.npz',allow_pickle=False) as z:a={k:z[k].copy() for k in z.files}
            copied=transfer_branch(getattr(net,name),a,m['channels'],positions,copy_spatial=name=='motor')
            if name=='eye':net.native_eye_head.load_state_dict({k:torch.as_tensor(a['net__head.'+k].copy(),device=device) for k in ['weight','bias']})
            sources[name]=dict(model_id=mid,weights_sha256=m['weights_sha256'],copied=copied,source_channels=m['channels'],destination_channels=positions,
                               equivalence='initialization only; private normalization/filtering/domain differ')
    return net,sources


class DomainStudent(nn.Module):
    def __init__(self,shared,teacher,native_head=None,copy_parameters=True):
        super().__init__();self.temporal=shared.temporal;self.gru=shared.gru
        self.spatial=nn.Conv2d(8,16,(len(teacher.channels),1),groups=8).to(next(shared.parameters()).device)
        if copy_parameters:self.spatial.load_state_dict({k:torch.as_tensor(teacher.arrays['net__spatial.'+k].copy(),device=next(shared.parameters()).device) for k in ['weight','bias']})
        self.head=native_head or nn.Linear(32,len(teacher.classes)).to(next(shared.parameters()).device)
        if copy_parameters:self.head.load_state_dict({k:torch.as_tensor(teacher.arrays['net__head.'+k].copy(),device=next(shared.parameters()).device) for k in ['weight','bias']})
    def forward(self,x):
        z=torch.nn.functional.elu(self.spatial(self.temporal(torch.nn.functional.pad(x[:,None],(16,0)))))
        z=torch.nn.functional.avg_pool2d(z,(1,4)).squeeze(2).transpose(1,2);z,_=self.gru(z)
        return self.head(z[:,-1])


def native_teacher_ids(root,mid,inventory,teacher_plan=None):
    root=Path(root);meta=read_json(root/'models'/mid/'manifest.json')
    if teacher_plan is not None:
        bindings=teacher_plan[mid];ids=[r['model_id'] for r in bindings]
        if not ids or ids[0]!=mid or len(ids)>3 or len(set(ids))!=len(ids):raise ValueError('Invalid frozen native teacher order')
        for binding in bindings:
            path=within(root/'models'/binding['model_id'],root/'models');m=read_json(path/'manifest.json')
            dataset=within(root/'datasets'/(m['dataset_sha256']+'.json'),root/'datasets')
            if (file_hash(path/'manifest.json')!=binding['manifest_sha256'] or file_hash(path/'weights.npz')!=binding['weights_sha256'] or
                m['weights_sha256']!=binding['weights_sha256'] or m['dataset_sha256']!=binding['dataset_sha256'] or file_hash(dataset)!=binding['dataset_file_sha256'] or digest(read_json(dataset))!=m['dataset_sha256']):raise ValueError('Frozen public teacher/data identity changed')
            if m.get('input_contract')!=meta.get('input_contract') or m.get('class_labels')!=meta.get('class_labels') or m.get('window_s')!=meta.get('window_s'):raise ValueError('Frozen teacher native task/input mismatch')
        return ids
    result=[mid];families={meta['algorithm']}
    for r in sorted(inventory,key=lambda row:-(row.get('development_balanced_accuracy') or -1)):
        if len(result)>=3:break
        if r['algorithm'] in families or r['use']=='retained_not_distilled':continue
        m=read_json(root/'models'/r['model_id']/'manifest.json')
        if m.get('input_contract')!=meta.get('input_contract') or m.get('class_labels')!=meta.get('class_labels') or m.get('window_s')!=meta.get('window_s'):continue
        result.append(r['model_id']);families.add(r['algorithm'])
    return result


def native_domains(root,net,inventory,device,progress,transfer=True,teacher_plan=None):
    from .artifacts import load_artifact
    from .public_windows import public_windows
    from .governance import record_group
    domains=[];models={r['model_id']:r for r in inventory}
    for mid,kind in [(OPENBMI,'motor'),(EOG,'eye'),(PHYSIONET,'motor')]:
        teacher,meta=load_artifact(Path(root)/'models'/mid)
        data=read_json(Path(root)/'datasets'/(meta['dataset_sha256']+'.json'))
        if digest(data)!=meta['dataset_sha256']:raise ValueError('Native public dataset changed')
        xx=[];yy=[];ids=[]
        for row in data['records']:
            if row['role']!='train':continue
            record_group(row) # License, source grouping and reviewed admission.
            x,y,wi=public_windows(row,meta['window_s'],.1);xx.append(x);yy.append(y);ids.extend(wi)
        x=np.concatenate(xx);y=np.concatenate(yy)
        # Domain/class-balanced sampling below; all prepared IDs remain audited.
        teachers=[(teacher,meta)]
        for model_id in native_teacher_ids(root,mid,inventory,teacher_plan)[1:]:
            r=models[model_id];child,cm=load_artifact(Path(root)/'models'/model_id);teachers.append((child,cm))
            r['use']='native_teacher';r['reason']='matching native classes, input contract and history; selected within original task'
        p=np.mean([t.predict(x)['probabilities'] for t,_ in teachers],0).astype('float32')
        z=teacher._neural_input(x) if teacher.algorithm=='cnn_gru_baseline' else x
        z=(z-teacher.arrays['input_mean'])/teacher.arrays['input_scale']
        student=DomainStudent(getattr(net,kind),teacher,net.native_eye_head if kind=='eye' else None,copy_parameters=transfer)
        domains.append(dict(id=mid,kind=kind,x=torch.as_tensor(z,device=device),y=torch.as_tensor(np.searchsorted(teacher.classes,y),device=device),
                            target=torch.as_tensor(p,device=device),student=student,teachers=[m['model_id'] for _,m in teachers]))
        progress(dict(phase='native_teacher_cache',domain=mid,windows=len(y),teachers=domains[-1]['teachers'],native_labels=meta['class_labels'],native_sampling_rate=meta['sampling_rate'],test_access=False))
        atomic(Path(root)/'logs'/('jepa-native-'+mid+'.json'),dict(training_window_ids=ids,teachers=domains[-1]['teachers'],private_navigation_relabelled=False))
    return domains


def gradient_evidence(net):
    return {name:float(torch.sqrt(sum((p.grad.detach()**2).sum() for p in getattr(net,name).parameters() if p.grad is not None))) for name in ['motor','eye']}


def checkpoint(path,net,optimizer,ema,rng,step,phase,protocol,extra=None):
    """Immutable payload then atomic receipt; never overwrite the last committed state."""
    path=Path(path);temp=path.with_suffix('.tmp')
    state=dict(model=net.state_dict(),optimizer=optimizer.state_dict(),ema=ema.state_dict() if ema else None,
               numpy_rng=rng.bit_generator.state,torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),
               step=step,phase=phase,protocol_sha256=digest(protocol),extra=extra or {})
    torch.save(state,temp)
    with temp.open('r+b') as f:f.flush();os.fsync(f.fileno())
    sha=file_hash(temp);payload=path.with_name(path.stem+'.'+sha+'.pt');os.replace(temp,payload)
    atomic(path.with_suffix('.json'),dict(file=payload.name,sha256=sha,step=step,phase=phase,protocol_sha256=digest(protocol)))


def restore_torch_rng(state):
    # map_location='cuda' also moves saved CPU ByteTensor RNG states. Both
    # generators require CPU byte storage regardless of the model's device.
    torch.set_rng_state(state['torch_rng'].cpu())
    torch.cuda.set_rng_state_all([value.cpu() for value in state['cuda_rng']])


def ssl_fit(net,regions,mean,scale,domains,protocol,rng,progress,check,cp,use_jepa=True,preprocessor_factory=Preprocessor,loss_function=jepa_loss):
    device=next(net.parameters()).device;params=list(net.parameters())
    for d in domains:params+=list(d['student'].spatial.parameters())+([] if d['kind']=='eye' else list(d['student'].head.parameters()))
    optimizer=torch.optim.AdamW(params,lr=2e-4,weight_decay=.01);target=copy.deepcopy(net).eval().requires_grad_(False)
    totals={d['id']:dict(updates=0,windows=0,gradient_norm_sum=0.,seconds=0.) for d in domains}
    start=0;receipt=cp.with_suffix('.json')
    if receipt.exists():
        r=read_json(receipt);payload=cp.parent/r['file']
        if file_hash(payload)!=r['sha256']:raise ValueError('Checkpoint commit changed')
        s=torch.load(payload,map_location=device,weights_only=False)
        if s['protocol_sha256']!=digest(protocol):raise ValueError('Resume protocol differs')
        if s['phase']=='ssl':
            net.load_state_dict(s['model']);target.load_state_dict(s['ema']);optimizer.load_state_dict(s['optimizer']);rng.bit_generator.state=s['numpy_rng']
            restore_torch_rng(s);start=s['step']
            for d in domains:
                if d['id'] in s['extra'].get('domain_states',{}):d['student'].load_state_dict(s['extra']['domain_states'][d['id']])
            totals=s['extra'].get('domain_totals',totals)
    for step in range(start,protocol['ssl_steps']):
        check();net.train();optimizer.zero_grad(set_to_none=True)
        # Independent filter initialization on every raw support. Targets never share
        # context IIR state, convolution support or GRU state.
        contexts=[];future=[[],[]];supports=[]
        n=[375,750,1250,2000][step%4]
        eligible=[r for r in regions if r['x'].shape[1]>=n+275]
        if not eligible:raise ValueError('No genuine training support for declared context length')
        for _ in range(16):
            r=eligible[int(rng.integers(len(eligible)))];x=r['x']
            a=int(rng.integers(x.shape[1]-n-275+1));end=a+n
            supports.append(dict(stream_id=r['stream_id'],context=[r['start']+a,r['start']+end],
                                 targets=[[r['start']+end+lo,r['start']+end+hi] for lo,hi in protocol['target_supports']]))
            contexts.append((preprocessor_factory().step(x[:,a:end])-mean[:,None])/scale[:,None])
            for j,(lo,hi) in enumerate(protocol['target_supports']):
                future[j].append((preprocessor_factory().step(x[:,end+lo:end+hi])-mean[:,None])/scale[:,None])
        c=torch.as_tensor(np.stack(contexts),device=device);f=[torch.as_tensor(np.stack(v),device=device) for v in future]
        loss,parts=loss_function(net,target,c,f)
        if not use_jepa:loss=loss*0
        native=loss.new_tensor(0.);domain=None;nt=time.monotonic()
        if domains:
            domain=domains[step%len(domains)];classes=torch.unique(domain['y']).tolist();ids=[]
            for k in range(16):
                at=torch.where(domain['y']==classes[k%len(classes)])[0];ids.append(int(at[int(rng.integers(len(at)))]))
            logits=domain['student'](domain['x'][ids]);temperature=2.
            soft_target=torch.softmax(torch.log(domain['target'][ids].clamp_min(1e-8))/temperature,-1)
            kd=torch.nn.functional.kl_div(torch.log_softmax(logits/temperature,-1),soft_target,reduction='batchmean')*temperature**2
            ce=torch.nn.functional.cross_entropy(logits,domain['y'][ids]);native=.5*(kd+ce);loss=loss+native
        loss.backward();grads=gradient_evidence(net);norm=torch.nn.utils.clip_grad_norm_(params,1.)
        if not torch.isfinite(loss) or (min(grads.values())<=0 if use_jepa else max(grads.values())<=0):raise ValueError('Expected native/JEPA branch nonfinite/zero gradient')
        optimizer.step();ema_update(target,net)
        with cp.with_suffix('.supports.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(step=step+1,jepa_enabled=use_jepa,private_supports=supports,native_domain=domain['id'] if domain else None,native_indices=ids if domain else []))+'\n')
        if domain:
            t=totals[domain['id']];t['updates']+=1;t['windows']+=16;t['gradient_norm_sum']+=float(norm);t['seconds']+=time.monotonic()-nt
        if (step+1)%25==0 or step==0:
            progress(dict(phase='ssl',step=step+1,total_steps=protocol['ssl_steps'],loss=float(loss.detach()),native_loss=float(native.detach()),
                          motor_jepa=float(parts['motor'].detach()),eye_jepa=float(parts['eye'].detach()),joint_jepa=float(parts['joint'].detach()) if 'joint' in parts else None,jepa_enabled=use_jepa,gradient_norms=grads,domain_totals=totals))
        if (step+1)%100==0 or step+1==protocol['ssl_steps']:
            checkpoint(cp,net,optimizer,target,rng,step+1,'ssl',protocol,
                       dict(domain_states={d['id']:d['student'].state_dict() for d in domains},domain_totals=totals))
    return totals


def sample_supervised(rows,mean,scale,rng,finite=False):
    contexts=[];labels=[]
    for k in range(20):
        row=rows[k%len(rows)];valid=row['quality_eligible_ends']
        end=int(valid[int(rng.integers(len(valid)))])+1;start=max(int(row['reset_at'][end-1]),end-(375 if finite else 2000))
        # Left align by cropping each example independently below; no fake history.
        contexts.append((row['z'][:,start:end]-mean[:,None])/scale[:,None]);labels.append(int(row['y'][end-1]))
    return contexts,labels


def neural_segments(row):
    """The exact live QC reset boundaries, with no fabricated input samples."""
    from .quality import inspect_signal
    x=row['x'];history=np.empty((32,0),dtype='float32');start=0;segments=[]
    for a in range(0,x.shape[1],25):
        b=min(a+25,x.shape[1]);history=np.concatenate((history,x[:,a:b]),1)[:,-375:]
        if history.shape[1]>=375 and not inspect_signal(history,CHANNELS,250,ELECTRICAL)['electrical_valid']:
            if a>start:segments.append((start,a))
            start=b;history=np.empty((32,0),dtype='float32')
    if x.shape[1]>start:segments.append((start,x.shape[1]))
    return segments


def supervised_trial_backward(net,row,mean,scale,device,finite=False):
    """One class-balanced trial loss; hidden state is detached, never reset at 8s."""
    ends=row['quality_eligible_ends']+1;den=max(1,len(ends));total=0.
    if finite:
        for begin in range(0,len(ends),64):
            chosen=ends[begin:begin+64]
            x=np.stack([(Preprocessor().step(row['x'][:,e-375:e])-mean[:,None])/scale[:,None] for e in chosen])
            outputs=net.batch(torch.as_tensor(x,device=device),[375]*len(x));targets=torch.tensor(row['y'][chosen-1],dtype=torch.long,device=device)
            loss=(torch.nn.functional.cross_entropy(outputs['logits'],targets,reduction='sum')+.5*torch.nn.functional.cross_entropy(outputs['motor_logits'],targets,reduction='sum')+.2*torch.nn.functional.cross_entropy(outputs['eye_logits'],targets,reduction='sum'))/den
            loss.backward();total+=float(loss.detach())
        return total
    for sa,sb in neural_segments(row):
        state=None
        for a in range(sa,sb,2000):
            b=min(a+2000,sb);x=(row['z'][:,a:b]-mean[:,None])/scale[:,None]
            outputs,state=net.sequence(torch.as_tensor(x[None],device=device),state)
            chosen=ends[(ends>a)&(ends<=b)];indices=(chosen-sa)//4-1-(a-sa)//4
            keep=(indices>=0)&(indices<outputs['logits'].shape[1]);chosen=chosen[keep];indices=indices[keep]
            if len(chosen):
                targets=torch.tensor(row['y'][chosen-1],dtype=torch.long,device=device)
                loss=(torch.nn.functional.cross_entropy(outputs['logits'][0,indices],targets,reduction='sum')+.5*torch.nn.functional.cross_entropy(outputs['motor_logits'][0,indices],targets,reduction='sum')+.2*torch.nn.functional.cross_entropy(outputs['eye_logits'][0,indices],targets,reduction='sum'))/den
                loss.backward();total+=float(loss.detach())
            state=detach_state(state)
    return total


def finetune(net,rows,validation,mean,scale,protocol,rng,progress,check,cp,finite=False):
    device=next(net.parameters()).device;best=None;best_key=None;best_epoch=None
    head_names=('motor_head','eye_head','eye.spatial')
    head=[p for n,p in net.named_parameters() if n.startswith(head_names)]
    backbone=[p for n,p in net.named_parameters() if not n.startswith(head_names)]
    anchor={n:p.detach().clone() for n,p in net.named_parameters() if not n.startswith(head_names)}
    optimizer=torch.optim.AdamW([dict(params=head,lr=1e-3),dict(params=backbone,lr=3e-5)],weight_decay=.02)
    start=0
    if cp.with_suffix('.json').exists():
        receipt=read_json(cp.with_suffix('.json'));payload=cp.parent/receipt['file']
        if file_hash(payload)!=receipt['sha256']:raise ValueError('Personal checkpoint changed')
        saved=torch.load(payload,map_location=device,weights_only=False)
        if saved['protocol_sha256']!=digest(protocol) or saved['phase']!='personal':raise ValueError('Personal resume identity differs')
        net.load_state_dict(saved['model']);optimizer.load_state_dict(saved['optimizer']);rng.bit_generator.state=saved['numpy_rng'];start=saved['step']
        restore_torch_rng(saved)
        best=saved['extra']['best_model'];best_epoch=saved['extra']['best_epoch'];best_key=saved['extra']['best_key']
        anchor=saved['extra']['anchor']
    for epoch in range(start,protocol['max_epochs']):
        check();frozen=epoch<10
        for name,p in net.named_parameters():p.requires_grad_(not frozen or name.startswith(head_names))
        net.train();loss_sum=0.
        for i in rng.permutation(len(rows)):
            check();optimizer.zero_grad(set_to_none=True)
            loss=supervised_trial_backward(net,rows[int(i)],mean,scale,device,finite)
            if not frozen:
                reg=.001*sum((p-anchor[n]).square().mean() for n,p in net.named_parameters() if n in anchor);reg.backward();loss+=float(reg.detach())
            norm=torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
            if not np.isfinite(loss) or not torch.isfinite(norm):raise ValueError('Nonfinite personal supervised update')
            optimizer.step();loss_sum+=loss
        if (epoch+1)%5==0 or epoch==0 or epoch+1==protocol['max_epochs']:
            model=StreamModel(arrays_from(net,mean,scale),dict(decision_policy=DEFAULT_POLICY),device=str(device))
            report,_=evaluate(model,validation,finite=finite)
            key=report['balanced_accuracy']
            if best_key is None or key>best_key:best_key=key;best={k:v.detach().cpu().clone() for k,v in net.state_dict().items()};best_epoch=epoch+1
            progress(dict(phase='personal_adaptation',epoch=epoch+1,loss=loss_sum/len(rows),frozen_backbone=frozen,complete_training_trials=len(rows),
                          state_carry='whole_trial' if not finite else 'finite_baseline',bptt_samples=2000,
                          head_learning_rate=.001,backbone_learning_rate=.00003,validation_balanced_accuracy=key,best_epoch=best_epoch))
            checkpoint(cp,net,optimizer,None,rng,epoch+1,'personal',protocol,dict(best_model=best,best_epoch=best_epoch,best_key=best_key,anchor=anchor))
    net.load_state_dict(best)
    return best_epoch


def evaluate(model,rows,disable_eye=False,finite=False):
    from .quality import inspect_signal
    frames=[];conf=np.zeros((5,5),int);accepted=np.zeros((5,5),int);den=np.zeros(5,int);raw_den=np.zeros(5,int);latencies=[];false_starts=0;rest_seconds=0.;previous=0;accepted_count=0;quality_rejected=0;stop_latencies=[]
    phases={k:dict(updates=0,correct_execution=0) for k in ['0_1s','1_3s','3_5s','after_5s']}
    for row in rows:
        session=model.new_session();previous=0;first=None;held=0;held_total=0;was_active=False;stop=None
        for end in range(25,row['x'].shape[1]+1,25):
            start=end-25
            history=np.concatenate((session.raw_history,row['x'][:,start:end]),1)[:,-375:]
            quality_ok=history.shape[1]<375 or inspect_signal(history,CHANNELS,250,ELECTRICAL)['electrical_valid']
            if not quality_ok:
                session=model.new_session();quality_rejected+=1
                o=dict(probabilities=[.2]*5,motor_scores=[.2]*5,auxiliary_status='UNKNOWN',navigation_decision=dict(direction=0,status='UNKNOWN'),warming=True)
            elif finite:
                # Finite-history encoders still have a causal decision history.
                # Resetting confirmation on every window would make the baseline
                # structurally unable to start any command.
                old=session;session=model.new_session();session.decoder=old.decoder
                session.eye_direction=old.eye_direction;session.eye_count=old.eye_count;session.return_count=old.return_count
                o=session.step(row['x'][:,max(0,end-375):end],disable_eye)
            else:o=session.step(row['x'][:,start:end],disable_eye)
            y=int(row['y'][end-1]);decision=o['navigation_decision']['direction'] if not o['warming'] else 0
            output_accepted=not o['warming'] and o['navigation_decision']['status']!='UNKNOWN'
            if y>=0:
                den[y]+=1;accepted_count+=output_accepted
                if not o['warming']:raw=int(np.argmax(o['probabilities']));conf[y,raw]+=1;raw_den[y]+=1
                if output_accepted:accepted[y,decision]+=1
                if y>0:
                    held_total+=1;held+=decision==y
                    if decision:was_active=True
                    elapsed=(end-row['metadata']['onset_in_episode'])/250
                    phase='0_1s' if elapsed<=1 else '1_3s' if elapsed<=3 else '3_5s' if elapsed<=5 else 'after_5s'
                    phases[phase]['updates']+=1;phases[phase]['correct_execution']+=decision==y
                    if first is None and decision==y:first=(end-row['metadata']['onset_in_episode'])/250
                if y==0:rest_seconds+=.1;false_starts+=bool(decision and decision!=previous)
            if end>=row['metadata']['offset_in_episode'] and was_active and stop is None and decision==0:stop=(end-row['metadata']['offset_in_episode'])/250
            frames.append(dict(trial_id=row['id'],source_sample_end=row['metadata']['context_start']+end,time_in_episode_s=end/250,label=y,
                               raw_prediction=int(np.argmax(o['probabilities'])) if not o['warming'] else -1,decision=decision,eligible=output_accepted,quality_valid=quality_ok,
                               probabilities=o['probabilities'],motor_scores=o['motor_scores'],auxiliary=o['auxiliary_status']))
            previous=decision
        if first is not None:latencies.append(max(0.,first))
        if stop is not None:stop_latencies.append(max(0.,stop))
    recall=np.diag(conf)/np.maximum(raw_den,1);accepted_recall=np.diag(accepted)/np.maximum(den,1)
    report=dict(balanced_accuracy=float(recall.mean()),recall={LABELS[i]:float(recall[i]) for i in range(5)},
                accepted_macro_recall=float(accepted_recall.mean()),accepted_recall={LABELS[i]:float(accepted_recall[i]) for i in range(5)},
                confusion_matrix=conf.tolist(),accepted_confusion_matrix=accepted.tolist(),labelled_updates=den.tolist(),
                false_starts=false_starts,explicit_rest_seconds=rest_seconds,false_starts_per_minute=false_starts/rest_seconds*60 if rest_seconds else None,
                first_response_p50_s=float(np.median(latencies)) if latencies else None,responded_trials=len(latencies),
                stop_p50_s=float(np.median(stop_latencies)) if stop_latencies else None,measured_stops=len(stop_latencies),
                coverage=accepted_count/max(1,int(den.sum())),quality_rejected_updates=quality_rejected,raw_scored_updates=raw_den.tolist(),hold_phases=phases,
                complete_trials=len(rows),independent_test=False,scope=SCOPE,
                event_truth='button/instruction anchored; saccades and brain onset unmeasured')
    return report,frames


def select_policy(model,validation,finite=False):
    # Temperature fitted only on labelled validation outputs, trial-balanced.
    original=model.details['decision_policy'];model.details['decision_policy']=dict(original,temperature=1.)
    _,frames=evaluate(model,validation,finite=finite);trial_ids=sorted({r['trial_id'] for r in frames});temperatures=[]
    for t in [.75,1.,1.5,2.]:
        losses=[]
        for tid in trial_ids:
            f=[r for r in frames if r['trial_id']==tid and r['label']>=0 and r['raw_prediction']>=0]
            if f:
                p=np.asarray([r['probabilities'] for r in f]);p=np.maximum(p,1e-8)**(1/t);p/=p.sum(1,keepdims=True)
                losses.append(float(-np.log(p[np.arange(len(f)),[r['label'] for r in f]]).mean()))
        temperatures.append((np.mean(losses) if losses else float('inf'),t))
    temperature=min(temperatures)[1];best=None;selected=None;table=[]
    for threshold in [.7,.75,.8,.85]:
        for gain in [.0,.25,.5]:
            policy=dict(DEFAULT_POLICY,aligned_threshold=threshold,conservative_threshold=min(.95,threshold+.1),auxiliary_gain=gain,temperature=temperature)
            model.details['decision_policy']=policy;report,_=evaluate(model,validation,finite=finite)
            key=(-report['false_starts'],report['accepted_macro_recall'],-(report['first_response_p50_s'] or 1000))
            table.append(dict(policy=policy,validation=report))
            if best is None or key>best:best=key;selected=policy
    model.details['decision_policy']=selected
    return table


def run_study(runner,jid,dataset,protocol):
    from .artifacts import save_artifact,load_artifact
    root=runner.root;registry=runner.registry;started=time.monotonic();out=root/'logs'/('jepa-'+jid);out.mkdir(parents=True,exist_ok=True)
    if (out/'progress.json').exists():started-=float(read_json(out/'progress.json').get('elapsed_s',0.))
    frozen=read_json(out/'selection-freeze.json') if (out/'selection-freeze.json').exists() else None
    if frozen and frozen.get('protocol_sha256')!=digest(protocol):raise ValueError('Frozen selection belongs to another protocol')
    def check():
        runner._check_cancel(jid)
        if time.monotonic()-started>protocol['max_wall_seconds']:raise InterruptedError('Approved aggregate wall/GPU budget exhausted; checkpoints retained')
    context={}
    def progress(row):
        check();row=dict(context,**row);registry.event(jid,'jepa_progress',row);atomic(out/'progress.json',dict(row,elapsed_s=time.monotonic()-started))
    with training_authority(protocol,dataset,runner.code_root,runner.profile,root/'protocols'/(digest(protocol)+'.json')):
        device=compute_device();torch.set_num_threads(2)
        inventory=read_json(out/'reuse-inventory.json') if frozen else reuse_inventory(root);atomic(out/'reuse-inventory.json',inventory)
        train=prepared(load_episodes(dataset,'train'));validation=load_episodes(dataset,'validation')
        atomic(out/'training-quality.json',dict(trials=[dict(trial_id=r['id'],eligible_source_ends=(r['quality_eligible_ends']+r['metadata']['context_start']+1).tolist(),
             normalization_samples=int(r['normalization_mask'].sum())) for r in train],policy=ELECTRICAL,labels_changed=False))
        regions=unlabelled_regions(dataset)+[dict(x=r['x'],stream_id=r['metadata']['stream_id'],start=r['metadata']['context_start'],end=r['metadata']['context_end']) for r in train]
        if not regions:raise ValueError('No leakage-free SSL training support')
        atomic(out/'ssl-support.json',dict(regions=[{k:v for k,v in r.items() if k!='x'} for r in regions],held_out_excluded=True,guard_samples=dataset['ssl_guard_samples']))
        mean,scale=normalization(train);registry.transition(jid,'running');candidates=frozen['candidates'] if frozen else [];init_audit=read_json(out/'initialization.json') if frozen else {}
        # Fixed reference arms share identical private fitting and validation roles.
        for seed in ([] if frozen else protocol['seeds']):
            for variant in (['jepa_transfer','transfer_no_jepa','random_jepa','finite_history_baseline'] if seed==17 else ['jepa_transfer']):
                context.update(seed=seed,variant=variant)
                check();torch.manual_seed(seed);rng=np.random.default_rng(seed);transfer=variant!='random_jepa'
                net,sources=initialize(root,device,transfer);init_audit[f'{seed}/{variant}']=sources
                finite=variant=='finite_history_baseline'
                reuse=protocol.get('ssl_reuse') if seed==17 and variant=='jepa_transfer' else None
                if reuse:
                    p=within(root/reuse['path'],root/'logs')
                    if file_hash(p)!=reuse['sha256']:raise ValueError('SSL checkpoint changed')
                    s=torch.load(p,map_location=device,weights_only=False)
                    if s['phase']!='ssl' or s['protocol_sha256']!=reuse['protocol_sha256'] or s['step']!=protocol['ssl_steps']:raise ValueError('SSL state/step mismatch')
                    net.load_state_dict(s['model']);rng.bit_generator.state=s['numpy_rng'];restore_torch_rng(s)
                    domain_totals=s['extra']['domain_totals'];init_audit[f'{seed}/{variant}']['ssl_reuse']=reuse
                    progress(dict(phase='ssl_phase_reused',source_job=reuse['job_id'],completed_steps=s['step'],phase_sha256=reuse['phase_sha256'],new_ssl_updates=0))
                else:
                    domains=native_domains(root,net,inventory,device,progress,transfer=transfer)
                    domain_totals=ssl_fit(net,regions,mean,scale,domains,protocol,rng,progress,check,out/f'{seed}-{variant}-ssl.pt',use_jepa=variant not in ['transfer_no_jepa','finite_history_baseline'])
                    del domains
                best_epoch=finetune(net,train,validation,mean,scale,protocol,rng,progress,check,out/f'{seed}-{variant}-personal.pt',finite=finite)
                model=StreamModel(arrays_from(net,mean,scale),dict(decision_policy=dict(DEFAULT_POLICY),selected_epoch=best_epoch,epochs=protocol['max_epochs'],gru_hidden=32,
                    history='finite_trailing_window' if finite else 'persistent_state_with_8s_training_unroll',normalization='train_trial_balanced_static',private_phase_truth='instructed_joint_task_weak_auxiliary'),device)
                calibration=select_policy(model,validation,finite=finite);report,_=evaluate(model,validation,finite=finite)
                name=f'{seed}-{variant}';np.savez(out/(name+'.npz'),**model.arrays)
                atomic(out/(name+'.json'),dict(details=model.details,validation=report,calibration=calibration,best_epoch=best_epoch,domain_totals=domain_totals))
                candidates.append(dict(seed=seed,variant=variant,name=name,validation=report,details=copy.deepcopy(model.details),weights_sha256=file_hash(out/(name+'.npz'))))
                progress(dict(phase='candidate_complete',seed=seed,variant=variant,validation=report))
        atomic(out/'reuse-inventory.json',inventory);atomic(out/'initialization.json',init_audit)
        jepa=[r for r in candidates if r['variant']=='jepa_transfer']
        winner=frozen['winner'] if frozen else max(jepa,key=lambda r:(-r['validation']['false_starts'],r['validation']['accepted_macro_recall'],-(r['validation']['first_response_p50_s'] or 1000)))
        for candidate in candidates:
            if file_hash(out/(candidate['name']+'.npz'))!=candidate['weights_sha256']:raise ValueError('Frozen candidate weights changed')
        # Commit the selection before opening the internal held-out test NPZ.
        if not frozen:atomic(out/'selection-freeze.json',dict(winner=winner,candidates=candidates,protocol_sha256=digest(protocol),test_accessed=False))
        registry.transition(jid,'evaluating')
        test=load_episodes(dataset,'test');comparisons=[]
        for candidate in candidates:
            with np.load(out/(candidate['name']+'.npz'),allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
            model=StreamModel(arrays,candidate['details'],device)
            score,frames=evaluate(model,test,finite=candidate['variant']=='finite_history_baseline');comparisons.append(dict(name=candidate['name'],variant=candidate['variant'],test=score))
            atomic(out/(candidate['name']+'.test.json'),dict(report=score,frames=frames))
        with np.load(out/(winner['name']+'.npz'),allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
        model=StreamModel(arrays,winner['details'],device)
        for name,disable,finite in [('no_eye',True,False),('finite_history',False,True)]:
            score,frames=evaluate(model,test,disable,finite);comparisons.append(dict(name=name,variant=name,test=score));atomic(out/(name+'.test.json'),dict(report=score,frames=frames))
        score,frames=evaluate(model,test);train_score,_=evaluate(model,load_episodes(dataset,'train'))
        report=dict(schema='gnautilus.jepa_study_result.v1',winner=winner,comparisons=comparisons,training=train_score,validation=winner['validation'],internal_test=score,
                    source_confounds=dataset['contract_review'],real_hardware_acceptance=False,elapsed_s=time.monotonic()-started,model_selection_used_test=False,
                    unimplemented_claims=['objective_eye_position','pure_MI_contribution','cross_wearing_generalization'])
        atomic(out/'report.json',report)
        registry.transition(jid,'exporting')
        # Bind the native hardware contract; SDK transport/network settings are not EEG preprocessing.
        native=next(r for r in train if r['metadata']['contract_id'].startswith('native_'))
        descriptor=read_json(Path(dataset['folder'])/'.metadata/canonical'/native['metadata']['stream_id']/'descriptor.original.json')
        from .stream_runtime import contract_from_descriptor
        contract=contract_from_descriptor(descriptor)
        evidence=dict(runtime_schema=RUNTIME,dataset_title='个人导航数据集',dataset_sha256=digest(dataset),protocol_sha256=digest(protocol),job_id=jid,
            evaluation_stage=SCOPE,evaluation_scope=dataset['evaluation_scope'],runtime_accepted=True,class_labels=LABELS,
            input_contract=contract,electrical_policy=ELECTRICAL,metrics=dict(training=train_score,development=winner['validation'],internal_test=score,independent_test=None),
            code_sha256=protocol['code_sha256'],seed=winner['seed'],reuse_audit_sha256=file_hash(out/'reuse-inventory.json'),real_hardware_acceptance=False,
            montage_attestation=dict(channels=CHANNELS,source='explicit_user_confirmation',swap16_19=True,reference_site_verified=False,ground_site_verified=False))
        path=save_artifact(root/'models',model,evidence);loaded,meta=load_artifact(path,device=device)
        before=model.new_session().step(test[0]['x']);after=loaded.new_session().step(test[0]['x'])
        np.testing.assert_allclose(before['probabilities'],after['probabilities'],atol=1e-6)
        registry.transition(jid,'registering');registry.register(path,meta,jid);registry.transition(jid,'completed')
        atomic(out/'completed.json',dict(model_id=meta['model_id'],path=str(path),report=str(out/'report.json'),runtime_loaded=False,inference_enabled=False,motion_enabled=False))
