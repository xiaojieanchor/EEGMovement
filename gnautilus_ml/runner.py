"""Private-server task executor; registration is part of task completion."""
import json,threading,time,traceback
from pathlib import Path
import numpy as np
from .common import atomic,read_json,digest,file_hash,within
from .algorithms import Classifier,ALGORITHMS,balanced
from .artifacts import save_artifact,load_artifact
from .data import windows,check_record,LABELS
from .governance import training_authority,check_protocol,record_group

def metrics(y,p,groups=None):
    labels=np.unique(y);pred=np.asarray(p['labels']);recall={str(c):float(np.mean(pred[y==c]==c)) for c in labels}
    pooled=float(np.mean(list(recall.values())));per_group=[]
    if groups is not None:
        for group in np.unique(groups):
            at=np.asarray(groups)==group;per_group.append(dict(group=str(group),balanced_accuracy=balanced(y[at],pred[at]),windows=int(at.sum())))
    matrix=[[int(np.sum((y==a)&(pred==b))) for b in labels] for a in labels]
    accepted=np.asarray(p.get('accepted',np.ones(len(y),bool)),dtype=bool)
    selective=dict(coverage=float(accepted.mean()),accepted=int(accepted.sum()),accuracy=float(np.mean(y[accepted]==pred[accepted])) if accepted.any() else None,
        correct_and_accepted_fraction=float(np.mean((pred==y)&accepted)),per_class_coverage={str(c):float(np.mean(accepted[y==c])) for c in labels},
        per_class_correct_and_accepted={str(c):float(np.mean(((pred==c)&accepted)[y==c])) for c in labels},
        scope='Eligible labelled trials only; chronological electrical QC coverage is separate; no continuous false-activation claim')
    return dict(accuracy=float(np.mean(y==pred)),balanced_accuracy=float(np.mean([v['balanced_accuracy'] for v in per_group])) if per_group else pooled,pooled_balanced_accuracy=pooled,recall=recall,windows=len(y),recordings=len(per_group) or None,per_group=per_group,coverage=None,selective=selective,
                class_order=labels.tolist(),confusion_matrix=matrix,semantics='group macro BA of electrically eligible windows; complete chronological coverage reported separately; not independent frames')

def compile_inputs(dataset,window_s,hop_s):
    buckets={'train':[],'development':[]};contract=None
    for record in dataset['records']:
        if record['role']=='test':continue # Independent data is never read for fitting or selection.
        if record.get('reader','gnautilus')!='gnautilus':
            from .public_windows import public_windows
            x,y,ids=public_windows(record,window_s,hop_s)
        else:x,y,ids=windows(record,window_s,hop_s)
        signature=(record['channels'],record['sampling_rate'],record['reference'],record.get('filter_configuration'))
        if contract is None:contract=signature
        if contract!=signature:raise ValueError('Channel/rate/reference/filter contracts differ; create separate dataset/model')
        group='|'.join(str(v) for v in record_group(record))
        buckets[record['role']].append((x,y,np.repeat(group,len(y)),ids))
    result={}
    for role,rows in buckets.items():
        if not rows:raise ValueError('Missing '+role+' records')
        result[role]=(np.concatenate([r[0] for r in rows]),np.concatenate([r[1] for r in rows]),np.concatenate([r[2] for r in rows]),sum([r[3] for r in rows],[]))
    if not np.array_equal(np.unique(result['train'][1]),np.unique(result['development'][1])):raise ValueError('Training/development class support differs')
    return result,contract

def evaluation_scope(dataset,protocol):
    scope=dict(dataset.get('evaluation_scope') or {})
    if 'selection' in scope:scope['dataset_selection_note']=scope['selection']
    scope['selection']=protocol['selection']
    return scope


class Runner:
    def __init__(self,root,registry,profile,code_root):
        self.root=Path(root);self.registry=registry;self.profile=profile;self.code_root=Path(code_root);self.resource=threading.Lock();self.stopping=threading.Event();self.cancelled=set();self.inference_enabled=False;self.before_poll=lambda:None
    def cancel(self,jid):
        with self.registry.lock:
            self.registry.job(jid);self.cancelled.add(jid)
            if self.registry.job(jid)['state'] in ['queued','waiting_resource','interrupted']:self.registry.transition(jid,'cancelled')
        return self.registry.job(jid)
    def _check_cancel(self,jid):
        if jid in self.cancelled or self.stopping.is_set():raise InterruptedError('Task cancelled; committed artifacts are retained')
    def run(self,jid):
        if self.inference_enabled or not self.resource.acquire(blocking=False):
            with self.registry.lock:
                if self.registry.job(jid)['state'] in ['queued','waiting_resource']:self.registry.transition(jid,'waiting_resource')
            return
        try:
            with self.registry.lock:
                job=self.registry.job(jid)
                if job['state'] not in ['queued','waiting_resource']:return
                if self.inference_enabled:self.registry.transition(jid,'waiting_resource');return
                spec=job['spec'];self.registry.transition(jid,'preparing')
            dataset=read_json(self.root/'datasets'/(spec['dataset_id']+'.json'));protocol=read_json(self.root/'protocols'/(spec['protocol_id']+'.json'))
            if spec.get('algorithm')=='causal_jepa_stream':
                if protocol.get('schema') in ['gnautilus.training_protocol.v3','gnautilus.training_protocol.v4']:
                    from .joint_training import run_joint_pilot
                    run_joint_pilot(self,jid,dataset,protocol)
                else:
                    from .jepa_training import run_study
                    run_study(self,jid,dataset,protocol)
                return
            # A retry after failed registration reuses verified final artifacts.
            receipt=self.root/'logs'/(jid+'.artifact.json')
            if receipt.exists():
                artifact=read_json(receipt)['path'];_,meta=load_artifact(within(artifact,self.root/'models'));self.registry.transition(jid,'registering');self.registry.register(artifact,meta,jid);self.registry.transition(jid,'completed');return
            self._check_cancel(jid)
            with training_authority(protocol,dataset,self.code_root,self.profile,protocol_path=self.root/'protocols'/(spec['protocol_id']+'.json')):
                values,contract=compile_inputs(dataset,protocol['window_s'],protocol['hop_s']);train,y,groups,ids=values['train'];dev,dy,dg,dids=values['development']
                support={str(label):len({(r['source'],r['trial']) for r,target in zip(ids,y) if target==label}) for label in np.unique(y)}
                if any(n<3 for n in support.values()):raise ValueError('At least three distinct labelled training trials per class are required; overlapping windows are not trials: '+str(support))
                self.registry.event(jid,'input_verified',{'training_windows':len(y),'development_windows':len(dy),'groups':len(np.unique(groups)),'training_trials_by_class':support})
                self.registry.transition(jid,'running');model=Classifier(protocol['algorithm'],contract[0],contract[1],protocol['window_s'],protocol['seed'],protocol['max_epochs'])
                model.checkpoint_path=self.root/'logs'/(jid+'.latest.pt')
                def progress(row):self._check_cancel(jid);self.registry.event(jid,'training_progress',row)
                model.fit(train,y,groups,progress=progress,validation=(dev,dy,dg) if protocol['algorithm'] in ['cnn_gru','cnn_gru_baseline'] else None);self._check_cancel(jid);self.registry.transition(jid,'evaluating')
                scores={'training':metrics(y,model.predict(train),groups),'development':metrics(dy,model.predict(dev),dg),'independent_test':None}
                self.registry.event(jid,'evaluation',scores);self.registry.transition(jid,'exporting')
                personal=all(r.get('reader','gnautilus')=='gnautilus' for r in dataset['records'])
                evidence=dict(dataset_sha256=digest(dataset),protocol_sha256=digest(protocol),job_id=jid,evaluation_stage='personal_development' if personal else 'public_method_validation',
                    runtime_accepted=personal and np.array_equal(model.classes,np.arange(5)),class_labels=LABELS if personal else model.classes.tolist(),
                    input_contract=dict(channels=contract[0],sampling_rate=contract[1],reference=contract[2],filter_configuration=contract[3]),
                    electrical_policy=dataset['records'][0]['electrical_policy'],metrics=scores,seed=protocol['seed'],code_sha256=protocol['code_sha256'])
                evidence.update(dataset_title=dataset.get('title'),evaluation_scope=evaluation_scope(dataset,protocol),subjects=sorted({str(r.get('subject')) for r in dataset['records']}))
                path=save_artifact(self.root/'models',model,evidence);loaded,meta=load_artifact(path)
                before=model.predict(dev);after=loaded.predict(dev)
                np.testing.assert_allclose(before['probabilities'],after['probabilities'],rtol=1e-5,atol=1e-6)
                np.testing.assert_array_equal(before['labels'],after['labels'])
                self.registry.event(jid,'export_verified',dict(windows=len(dev),model_id=meta['model_id'],maximum_probability_difference=float(np.max(np.abs(before['probabilities']-after['probabilities']))),labels_identical=True))
                if not personal:
                    for role,xx,yy,gg in [('training',train,y,groups),('development',dev,dy,dg)]:
                        prediction=loaded.predict(xx)
                        np.savez(self.root/'logs'/(jid+'.'+role+'.predictions.npz'),targets=yy,labels=prediction['labels'],probabilities=prediction['probabilities'],accepted=prediction['accepted'],groups=gg)
                atomic(self.root/'logs'/(jid+'.windows.json'),{'training':ids,'development':dids});atomic(receipt,{'path':str(path),'model_id':meta['model_id']})
                self._check_cancel(jid);self.registry.transition(jid,'registering');self.registry.register(path,meta,jid);self.registry.transition(jid,'completed')
        except InterruptedError as error:self.registry.event(jid,'cancelled',{'reason':str(error)});self.registry.transition(jid,'cancelled')
        except Exception as error:
            self.registry.event(jid,'task_failed',{'error':str(error),'traceback':traceback.format_exc()[-7000:]})
            if self.registry.job(jid)['state'] not in ['cancelled','completed']:self.registry.transition(jid,'failed')
        finally:self.resource.release()
    def loop(self):
        while not self.stopping.wait(.5):
            self.before_poll()
            for job in self.registry.pending_jobs():self.run(job['id']);break
