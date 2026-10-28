"""Source-bound offline ocular-assisted navigation experiment; never an action model.

The frontal classifier learns joint instruction labels, not objective eye position.
No public ocular electrode is renamed to a scalp EEG position.
"""
import time
from pathlib import Path
import numpy as np
from scipy.signal import butter,sosfilt
from .common import digest,file_hash,read_json,atomic,within
from .governance import authorize_training,training_authority,check_runtime
from .personal_study import verify_sources,episode_bounds,load_episodes
from .navigation_contract import CHANNELS,ELECTRICAL,SCOPE,DEFAULT_POLICY

SCHEMA='gnautilus.eye_joint_probe_protocol.v1'
FEATURES=['eye_signed','all32_multiscale','transfer_eye_latent','transfer_joint_latent','joint_feature_eye_transfer']

class CausalEvidenceFeatures:
    """New samples only, fixed 25-sample hops; no labels or cue timing inputs."""
    def __init__(self):
        self.low=butter(4,8,fs=250,btype='lowpass',output='sos')
        self.bands=[butter(4,b,fs=250,btype='bandpass',output='sos') for b in [[.5,4],[4,8],[8,13],[13,30]]]
        self.reset()
    def reset(self):
        self.samples=0;self.pending=np.empty((32,0));self.lp=np.empty((8,0));self.bp=np.empty((4,32,0))
        self.lzi=np.zeros((len(self.low),8,2));self.bzi=[np.zeros((len(s),32,2)) for s in self.bands]
    def step(self,x):
        x=np.asarray(x,dtype=np.float64)
        if x.ndim!=2 or x.shape[0]!=32 or not np.isfinite(x).all():self.reset();raise ValueError('Physical finite 32-channel input required; state reset')
        self.pending=np.concatenate((self.pending,x),1);out=[]
        while self.pending.shape[1]>=25:
            xx=self.pending[:,:25];self.pending=self.pending[:,25:]
            low,self.lzi=sosfilt(self.low,xx[:8],zi=self.lzi,axis=-1);bands=[]
            for i,s in enumerate(self.bands):
                z,self.bzi[i]=sosfilt(s,xx,zi=self.bzi[i],axis=-1);bands.append(z)
            self.samples+=25;self.lp=np.concatenate((self.lp,low),1)[:,-1500:];self.bp=np.concatenate((self.bp,np.asarray(bands)),2)[:,:,-1500:]
            if self.samples<375:continue
            eye=[];all32=[]
            for n in [125,375,750,1500]:
                lo=self.lp[:,-n:];k=max(1,lo.shape[1]//4)
                # Scalp differences are engineered features, not bipolar EOG.
                diff=np.stack([lo[0]-lo[1],lo[2]-lo[3],lo[4]-lo[5],lo[6]-lo[7],(lo[0]+lo[1]-lo[6]-lo[7])/2])
                for v in [lo,diff]:eye.extend([v.mean(1),v.std(1),v[:,-k:].mean(1)-v[:,:k].mean(1)])
                all32.append(np.log(np.maximum(np.mean(self.bp[:,:,-n:]**2,2),1e-8)).ravel())
            v=self.bp.sum(0)[:,-750:];v=v-v.mean(1,keepdims=True);cov=v@v.T/max(1,v.shape[1]-1)
            den=np.sqrt(np.maximum(np.diag(cov),1e-8));corr=cov/den[:,None]/den[None,:]
            all32.append(corr[np.triu_indices(32,1)])
            out.append(dict(sample_end=self.samples,eye=np.concatenate(eye).astype('float32'),full32=np.concatenate(all32).astype('float32')))
        return out

def trial_weights(y,trial_ids=None):
    y=np.asarray(y);ids=np.asarray(trial_ids) if trial_ids is not None else y;w=np.zeros(len(y))
    known=y>=0;groups=np.unique(ids[known])
    for g in groups:
        at=known&(ids==g);w[at]=1/at.sum()/len(groups)
    return w

def fit_readout(x,y,trial_ids=None,steps=250,device='cuda:0'):
    authorize_training()
    import torch
    torch.manual_seed(17);x=np.asarray(x,dtype='float32');y=np.asarray(y);w=trial_weights(y,trial_ids);known=w>0
    if not known.any() or set(np.unique(y[known]))!=set(range(5)):raise ValueError('Five explicit training classes required')
    mean=(x*w[:,None]).sum(0);scale=np.sqrt(np.maximum(((x-mean)**2*w[:,None]).sum(0),1e-4))
    xx=torch.as_tensor((x[known]-mean)/scale,device=device,dtype=torch.float32);yy=torch.as_tensor(y[known],device=device,dtype=torch.long);ww=torch.as_tensor(w[known],device=device,dtype=torch.float32)
    head=torch.nn.Linear(x.shape[1],5).to(device);torch.nn.init.zeros_(head.weight);torch.nn.init.zeros_(head.bias)
    opt=torch.optim.AdamW(head.parameters(),lr=.02,weight_decay=0.);history=[]
    for step in range(steps):
        opt.zero_grad(set_to_none=True);ce=(torch.nn.functional.cross_entropy(head(xx),yy,reduction='none')*ww).sum()
        penalty=.05*head.weight.square().sum();loss=ce+penalty;loss.backward();norm=torch.nn.utils.clip_grad_norm_(head.parameters(),5.)
        if not torch.isfinite(loss) or not torch.isfinite(norm):raise ValueError('Nonfinite actual readout training')
        opt.step()
        if step==0 or (step+1)%50==0 or step+1==steps:history.append(dict(step=step+1,joint_ce=float(ce.detach()),objective=float(loss.detach()),gradient_norm=float(norm)))
    return dict(mean=mean.astype('float32'),scale=scale.astype('float32'),weight=head.weight.detach().cpu().numpy(),bias=head.bias.detach().cpu().numpy()),history

def predict_readout(x,a):
    z=(np.asarray(x)-a['mean'])/a['scale'];logits=z@a['weight'].T+a['bias'];logits-=logits.max(1,keepdims=True);p=np.exp(logits);return p/p.sum(1,keepdims=True)

def fuse_probabilities(p,q,gain=.2):
    if not 0<=gain<=.25:raise ValueError('Auxiliary convex gain exceeds locked cap')
    return (1-gain)*np.asarray(p)+gain*np.asarray(q)

def metrics(y,p):
    y=np.asarray(y);p=np.asarray(p);at=y>=0;pred=p.argmax(1);support=np.bincount(y[at],minlength=5)
    recall=[float(np.mean(pred[at&(y==k)]==k)) if support[k] else None for k in range(5)]
    cm=np.zeros((5,5),int)
    for a,b in zip(y[at],pred[at]):cm[a,b]+=1
    direction=at&(y>0)
    return dict(balanced_accuracy=float(np.mean(recall)) if all(v is not None for v in recall) else None,
        direction_macro_recall=float(np.mean(recall[1:])) if all(v is not None for v in recall[1:]) else None,
        labelled_updates=int(at.sum()),unknown_updates=int((~at).sum()),class_support=support.tolist(),recall=recall,confusion_matrix=cm.tolist(),
        ce=float(-np.log(np.maximum(p[np.arange(len(y))[at],y[at]],1e-8)).mean()) if at.any() else None,
        max_score_p50=float(np.median(p[at].max(1))) if at.any() else None,
        prediction_counts=np.bincount(pred[at],minlength=5).tolist())

def make_protocol(dataset,code_root,models,prior_seconds):
    root=Path(code_root)
    return dict(schema=SCHEMA,scope=SCOPE,dataset_sha256=digest(dataset),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((root/'gnautilus_ml').glob('*.py'))},
        model_sources=models,roles=['train','validation'],fit_roles=['train'],selection_roles=['validation'],test_access='forbidden',
        features=FEATURES,filter='causal LP8 frontal, full32 four bands .5-4/4-8/8-13/13-30; no extra HP in signed frontal path; native hardware HP not undone',
        horizons_samples=[125,375,750,1500],hop_samples=25,warmup_samples=375,readout_steps=250,readout_l2_sum=.05,seed=17,
        fusion_gains=[0.,.1,.2],postprocessing_ewma=[0.,1.],max_wall_seconds=900,prior_seconds=float(prior_seconds),total_training_budget_seconds=43200,
        model_selection='Development raw BA descriptive ranking; no deployment or independent claim',
        normalization='Training labelled complete-trial balanced static; no validation refit',label_truth='Joint instructions; no objective eye position or blink labels',
        quality=ELECTRICAL,reset='Existing full-episode and causal electrical-QC boundaries; unknown outputs retained',registration='offline_supporting_experiment_only')

def verify_models(root,models):
    for binding in models.values():
        p=within(Path(root)/binding['path'],root)
        if file_hash(p)!=binding['sha256']:raise ValueError('Frozen source model changed')

def check_probe_protocol(p,dataset,code_root):
    expected=make_protocol(dataset,code_root,p['model_sources'],p['prior_seconds'])
    if p!=expected or p['prior_seconds']<0 or p['prior_seconds']+p['max_wall_seconds']>p['total_training_budget_seconds']:raise ValueError('Eye probe source/recipe/budget differs')
    if dataset.get('schema')!='gnautilus.personal_study.v1' or dataset.get('scope')!=SCOPE or dataset.get('channels')!=CHANNELS or dataset.get('sampling_rate')!=250:raise ValueError('Wrong private physical input contract')
    if not p['code_sha256'].get('gnautilus_ml/eye_joint_probe.py'):raise ValueError('Actual entrypoint missing')
    episode_bounds(dataset['episodes']);verify_sources(dataset['folder'],dataset['source_receipt']);verify_models(Path(dataset['folder']).parent.parent,p['model_sources'])
    return True

def extract_rows(rows,model,check):
    """All readout inputs and neural states use identical QC reset supports."""
    import torch
    result=[]
    for row in rows:
        session=model.new_session();features=CausalEvidenceFeatures();resets=0
        for end in range(25,row['x'].shape[1]+1,25):
            check();x=row['x'][:,end-25:end]
            try:o=session.step(x)
            except ValueError:session=model.new_session();features.reset();resets+=1;continue
            ff=features.step(x)
            if o['warming']:continue
            if len(ff)!=1:raise ValueError('Feature/neural real-sample supports differ')
            f=ff[0];h=session.state
            with torch.inference_mode():
                m=h['motor']['hidden'][-1];e=h['eye']['hidden'][-1];j=model.net.fusion(torch.cat((m,e),-1))
            elapsed=(end-row['metadata']['onset_in_episode'])/250
            result.append(dict(trial_id=row['id'],source_sample_end=row['metadata']['context_start']+end,reset=resets,label=int(row['y'][end-1]),
                phase='onset_0_2s' if elapsed<=2 else 'hold_after2s' if end<=row['metadata']['offset_in_episode'] else 'release_UNKNOWN',
                eye_signed=f['eye'],all32_multiscale=f['full32'],transfer_eye_latent=e[0].cpu().numpy(),transfer_joint_latent=j[0].cpu().numpy(),
                joint_feature_eye_transfer=np.concatenate((f['full32'],f['eye'],m[0].cpu().numpy(),e[0].cpu().numpy())),
                original_scores=np.asarray(o['probabilities']),original_eye=np.asarray(o['eye_scores'])))
    return result

def evidence_average(p,frames,tau):
    if not tau:return p.copy()
    out=[];previous=None;state=None
    for prob,row in zip(p,frames):
        key=(row.get('sequence_id',row['trial_id']),row['reset'])
        if key!=previous:state=prob.copy()
        else:state=state+(1-np.exp(-.1/tau))*(prob-state)
        out.append(state.copy());previous=key
    return np.asarray(out)

def decision_report(y,p,frames,total_labelled=None,total_rest_seconds=None):
    from .joint_stream import JointDecoder
    decoder=JointDecoder();previous=None;accepted=0;correct=0;starts=0;rest=0;first={};last_direction=0
    for truth,prob,row in zip(y,p,frames):
        key=(row.get('sequence_id',row['trial_id']),row['reset'])
        if key!=previous:decoder.reset();last_direction=0
        o=decoder.step(prob,prob);d=o['direction'];valid=o['status']!='UNKNOWN';previous=key
        if truth>=0:accepted+=int(valid);correct+=int(valid and d==truth)
        if truth==0:rest+=.1;starts+=int(d>0 and d!=last_direction)
        if truth>0 and d==truth and row['trial_id'] not in first:first[row['trial_id']]=row['source_sample_end']
        last_direction=d
    eligible=int(np.sum(y>=0));total=eligible if total_labelled is None else int(total_labelled)
    rest_total=rest if total_rest_seconds is None else float(total_rest_seconds)
    if total<eligible or rest_total+1e-7<rest:raise ValueError('Full trial support cannot be smaller than eligible support')
    return dict(coverage=accepted/max(1,total),eligible_coverage=accepted/max(1,eligible),labelled_denominator=total,eligible_labelled_updates=eligible,
                accepted_correct_updates=correct,false_starts=starts,explicit_rest_s=rest_total,
                false_starts_per_minute=starts/rest_total*60 if rest_total else None,responded_direction_trials=len(first),policy='unchanged DEFAULT_POLICY, auxiliary UNKNOWN; offline replay only')

def run_probe(base,code_root,p,dataset,profile,out):
    import torch
    from .joint_stream import JointStreamModel
    started=time.monotonic();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    def check():
        if time.monotonic()-started>p['max_wall_seconds']:raise InterruptedError('Locked probe wall budget exhausted')
    with training_authority(p,dataset,code_root,profile,out/'protocol.json'):
        device=check_runtime(profile);torch.set_num_threads(2)
        source=Path(base)/p['model_sources']['candidate']['path'];manifest=read_json(source.parent/'manifest.json')
        with np.load(source,allow_pickle=False) as z:arrays={k:z[k].copy() for k in z.files}
        details=dict(manifest['details']);details['decision_policy']=dict(details['decision_policy'],temperature=1.)
        model=JointStreamModel(arrays,details,device)
        raw={r:load_episodes(dataset,r) for r in p['roles']}
        full_support={role:np.bincount(np.asarray([r['y'][end-1] for r in raw[role] for end in range(25,r['x'].shape[1]+1,25) if r['y'][end-1]>=0]),minlength=5).tolist() for role in raw}
        rows={r:extract_rows(raw[r],model,check) for r in p['roles']}
        labels={r:np.asarray([v['label'] for v in rows[r]]) for r in rows};ids=np.asarray([v['trial_id'] for v in rows['train']])
        candidates=[];saved={};predictions={r:{} for r in rows};support=[]
        for role in rows:
            support.extend([dict(role=role,**{k:v for k,v in row.items() if k in ['trial_id','source_sample_end','reset','label','phase']}) for row in rows[role]])
            predictions[role]['existing_joint']=np.asarray([v['original_scores'] for v in rows[role]])
            predictions[role]['existing_eye']=np.asarray([v['original_eye'] for v in rows[role]])
        for feature in p['features']:
            check();x=np.asarray([r[feature] for r in rows['train']]);a,h=fit_readout(x,labels['train'],ids,p['readout_steps'],device)
            np.savez(out/(feature+'.npz'),**a);atomic(out/(feature+'-training.json'),h);saved[feature]=dict(file=feature+'.npz',sha256=file_hash(out/(feature+'.npz')),dimension=x.shape[1])
            for role in rows:predictions[role][feature]=predict_readout(np.asarray([r[feature] for r in rows[role]]),a)
            atomic(out/'progress.json',dict(phase='readout_complete',feature=feature,elapsed_s=time.monotonic()-started));print(feature,flush=True)
        for role in rows:
            for gain in p['fusion_gains'][1:]:
                for base_name in ['existing_joint','all32_multiscale']:
                    predictions[role][base_name+'+eye'+str(gain)]=fuse_probabilities(predictions[role][base_name],predictions[role]['eye_signed'],gain)
        for name in list(predictions['train']):
            for tau in p['postprocessing_ewma']:
                key=name+('/ema1s' if tau else '/instant');item=dict(name=key,feature_name=name,ema_s=tau,roles={})
                for role in rows:
                    q=evidence_average(predictions[role][name],rows[role],tau);y=labels[role]
                    cuts={}
                    for phase in ['onset_0_2s','hold_after2s']:
                        at=np.asarray([v['phase']==phase for v in rows[role]]);cuts[phase]=metrics(y[at],q[at])
                    item['roles'][role]=dict(**metrics(y,q),phases=cuts,decision=decision_report(y,q,rows[role],sum(full_support[role]),full_support[role][0]*.1))
                    predictions[role][key]=q
                candidates.append(item)
        export={}
        for role in rows:
            export[role+'__labels']=labels[role]
            for name,values in predictions[role].items():export[role+'__'+name.replace('/','__')]=values
        np.savez(out/'predictions.npz',**export);atomic(out/'supports.json',support)
        ranking=sorted(candidates,key=lambda r:-(r['roles']['validation']['balanced_accuracy'] or -1))
        atomic(out/'report.json',dict(schema='gnautilus.eye_joint_probe_result.v1',dataset_id=digest(dataset),protocol_sha256=digest(p),
            candidates=candidates,ranking=[r['name'] for r in ranking],readouts=saved,elapsed_s=time.monotonic()-started,test_accessed=False,full_labelled_support=full_support,
            trial_count={role:len(set(v['trial_id'] for v in rows[role])) for role in rows},optimization='Train-only regularized linear readouts; frozen original recurrent encoders',
            caveats=['Frontal labels are joint-task instructions, not measured eye positions.','Only one complete trial per class/role; repeated windows are correlated.',
              'Training REST official source differs from native development REST.','Development reused and multiple candidates inspected; exploratory ranking only.',
              'Episode reset boundaries known to research protocol; continuous unknown-intention timing remains unverified.',
              'No runtime registration, model selection change, inference start or motion.']))
    verify_sources(dataset['folder'],dataset['source_receipt']);verify_models(base,p['model_sources'])

def main():
    import argparse,os
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--code-root',required=True);ap.add_argument('--protocol',required=True);ap.add_argument('--dataset',required=True);ap.add_argument('--profile',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    p=read_json(a.protocol);ds=read_json(a.dataset)
    if p['code_sha256'].get('gnautilus_ml/eye_joint_probe.py')!=file_hash(__file__):raise ValueError('Actual experiment entrypoint changed')
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve():raise ValueError('Actual protocol environment differs')
    if Path(a.output).resolve()!=(Path(a.protocol).parent).resolve():raise ValueError('Run output must retain its checked protocol')
    run_probe(Path(a.root),Path(a.code_root),p,ds,read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
