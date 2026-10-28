"""Authoritative private-server dataset, task, classifier and inference service."""
import json,re,sys,threading,time
from pathlib import Path
import numpy as np
from .common import atomic,read_json,digest,file_hash,within
from .registry import Registry
from .runner import Runner
from .algorithms import ALGORITHMS
from .artifacts import load_artifact
from .data import check_record,LABELS,MAPPING,MODES,DEFAULT_MODE
from .tasks import task_spec,RESEARCH_OBJECTIVE
from .quality import inspect_signal

def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32,64}',value):raise ValueError('Invalid content/job identity')
    return value

class ModelService:
    def __init__(self,root,profile,code_root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.code_root=Path(code_root);self.profile=profile
        self.registry=Registry(self.root/'state.sqlite');self.runner=Runner(root,self.registry,profile,code_root)
        self.model=None;self.manifest=None;self.lock=threading.RLock();self.generation=0;self.lease=0;self.last=None;self.last_identity=None
        self.runner.before_poll=self.inference_status
        self.resources={};self.resources_at=0
    def get(self,path):
        route,_,query=path.partition('?')
        if route=='/health':
            if time.monotonic()-self.resources_at>10:
                import subprocess
                try:
                    r=subprocess.run(['nvidia-smi','--query-gpu=uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,check=True,timeout=3)
                    row=next(v for v in r.stdout.splitlines() if v.split(',')[0].strip()==self.profile.get('gpu_uuid'));uuid,name,used,total,util,temp=[v.strip() for v in row.split(',')]
                    self.resources=dict(device=name,gpus=[dict(index=0,uuid=uuid,name=name,used_mb=int(used),total_mb=int(total),utilization=int(util),temperature=int(temp))])
                except (OSError,subprocess.SubprocessError,StopIteration,ValueError):self.resources=dict(resource_status='unknown')
                self.resources_at=time.monotonic()
            return dict(project='EEGMovement',schema='gnautilus.compute.v1',version='1.0.0',host=self.profile.get('host'),gpu_uuid=self.profile.get('gpu_uuid'),python=sys.executable,service='private_gnautilus_ml',**self.resources)
        if route=='/capabilities':return dict(algorithms=[dict(id=k,title=v,trainable=True) for k,v in ALGORITHMS.items()],window_s=1.5,hop_s=.1,
            label_mapping=MAPPING,label_mapping_scope='legacy_four_limb_only',default_experiment_mode=DEFAULT_MODE,research_objective=RESEARCH_OBJECTIVE,
            task_specs_by_mode={mode:{label:task_spec(label,mode) for label in LABELS} for mode in MODES},
            experiment_modes=MODES,readers=['gnautilus','halt','bci_iv_1','bci_iv_2a','eegmmidb','openbmi','eegeyenet'],jepa=dict(trainable=True,runtime_schema='gnautilus.causal_jepa_stream.v2',supported_runtimes=['gnautilus.causal_jepa_stream.v1','gnautilus.causal_jepa_stream.v2','gnautilus.hybrid_navigation.v1','gnautilus.hybrid_navigation.v2','gnautilus.hybrid_navigation.v3','gnautilus.hybrid_navigation.v4','gnautilus.hybrid_navigation.v5','gnautilus.hybrid_navigation.v6','gnautilus.hybrid_navigation.v7'],scope='same_wearing_internal_research'))
        if route=='/models':return dict(models=self.registry.models(),selected=self.registry.selected(),loaded=self.manifest['model_id'] if self.manifest else None)
        if route=='/jobs':return dict(jobs=self.registry.jobs())
        if route=='/datasets':
            rows=[]
            for p in (self.root/'datasets').glob('*.json'):
                row=dict(read_json(p),id=p.stem);records=row.get('records',[])
                if records and records[0].get('prepared_windows'):
                    report=read_json(within(records[0]['prepared_windows']['report_path'],self.root/'data'))
                    row.update(prepared_window_s=report['window_s'],window_sampling='one_per_native_trial',source_sampling_rate=report['source_sampling_rate'])
                rows.append(row)
            return dict(datasets=rows)
        if route.startswith('/jobs/'):
            jid=identifier(route.split('/')[2])
            if route.endswith('/events'):
                from urllib.parse import parse_qs
                return dict(events=self.registry.events(jid,int(parse_qs(query).get('after',[0])[0])))
            return dict(job=self.registry.job(jid),events=self.registry.events(jid))
        if route=='/snapshot':
            with self.lock,self.registry.lock:return dict(**self.get('/health'),**self.get('/models'),**self.get('/jobs'),**self.get('/datasets'),inference=self.inference_status())
        raise ValueError('Unsupported compute GET operation')
    def post(self,path,data):
        if path=='/datasets/personal_study':
            from .personal_study import compile_study
            folder=within(self.root/'data'/identifier(data['upload_id']),self.root/'data')
            key,study=compile_study(self.root,folder,self.code_root,folder/'source-receipt.json')
            return dict(dataset_id=key,dataset=study)
        if path=='/datasets/import':
            records=[]
            for item in data.get('records',[]):
                p=within(self.root/'data'/identifier(item['upload_id']),self.root/'data');attrs=item['attributes']
                row=check_record(p,attrs)
                if not row['training_ready']:raise ValueError('Record not ready: '+', '.join(row['blocking_reasons']))
                row['reader']='gnautilus';records.append(row)
            if not records:raise ValueError('At least one verified closed recording required')
            manifest=dict(schema='gnautilus.dataset.v1',title=str(data.get('title','个人数据集'))[:120],records=records)
            key=digest(manifest);atomic(self.root/'datasets'/(key+'.json'),manifest)
            return dict(dataset_id=key,dataset=manifest)
        if path=='/datasets/public':
            from .readers import read_public
            item=dict(data);p=within(item.pop('path'),self.root/'data');metadata=item['public_metadata'];r=read_public(item['reader'],p,metadata)
            row=dict(item,path=str(p),source_sha256=file_hash(p),channels=r['channels'],sampling_rate=r['sampling_rate'],filter_configuration=metadata.get('filter_configuration'),training_ready=True)
            manifest=dict(schema='gnautilus.dataset.v1',title=data.get('title','公开方法数据'),records=[row]);key=digest(manifest);atomic(self.root/'datasets'/(key+'.json'),manifest);return dict(dataset_id=key,dataset=manifest)
        if path=='/datasets/combine':
            records=[]
            for key in data['dataset_ids']:records.extend(read_json(self.root/'datasets'/(identifier(key)+'.json'))['records'])
            manifest=dict(schema='gnautilus.dataset.v1',title=data.get('title','组合数据集'),records=records);key=digest(manifest);atomic(self.root/'datasets'/(key+'.json'),manifest);return dict(dataset_id=key,dataset=manifest)
        if path in ['/jobs','/jobs/prepare']:
            from .governance import check_protocol
            did=identifier(data['dataset_id']);dataset=read_json(self.root/'datasets'/(did+'.json'))
            if data.get('algorithm')=='causal_jepa_stream':
                from .personal_study import make_protocol
                if data.get('architecture')=='shared_full32_joint_v2':
                    from .joint_training import make_joint_protocol,prior_budget,freeze_public_resources
                    protocol=make_joint_protocol(dataset,self.code_root,data,prior_budget(self.root),freeze_public_resources(self.root))
                else:protocol=make_protocol(dataset,self.code_root,data,self.root)
                check_protocol(protocol,dataset,self.code_root)
                pid=digest(protocol);atomic(self.root/'protocols'/(pid+'.json'),protocol)
                spec=dict(dataset_id=did,protocol_id=pid,algorithm='causal_jepa_stream')
                if path=='/jobs/prepare':return dict(spec=spec,protocol=protocol,fit_started=False)
                return self.registry.create_job(spec,data.get('request_key',digest(spec)))
            source={str(p.relative_to(self.code_root)).replace('\\','/'):file_hash(p) for p in sorted((self.code_root/'gnautilus_ml').glob('*.py'))}
            source.update({str(p.relative_to(self.code_root)).replace('\\','/'):file_hash(p) for p in (self.code_root/'gnautilus_acquisition').glob('*.py')})
            algorithm=data['algorithm']
            if algorithm not in ALGORITHMS:raise ValueError('Unsupported algorithm')
            protocol=dict(schema='gnautilus.training_protocol.v1',dataset_sha256=digest(dataset),algorithm=algorithm,code_sha256=source,seed=int(data.get('seed',17)),
                          max_epochs=int(data.get('max_epochs',50)),window_s=float(data.get('window_s',1.5)),hop_s=.1,selection='development_group_macro_BA_tie_earliest' if algorithm in ['cnn_gru','cnn_gru_baseline'] else 'training_oof_only' if algorithm in ['pipeline_ensemble','gaze_pipeline'] else 'fixed_recipe_no_test_access',quality_policy=dataset['records'][0]['electrical_policy'].get('schema','gnautilus.electrical.v1'))
            protocol.update(window_sampling='source_bound_one_window_per_native_trial' if dataset.get('scope')=='public_method_development' else 'label_intervals_hop',
                            study_plan_sha256=dataset.get('study_plan_sha256'),learned_statistics_roles=['train'],independent_test_access=False)
            if not .5<=protocol['window_s']<=5:raise ValueError('Model history outside supported range')
            check_protocol(protocol,dataset,self.code_root);pid=digest(protocol);atomic(self.root/'protocols'/(pid+'.json'),protocol)
            spec=dict(dataset_id=did,protocol_id=pid,algorithm=algorithm)
            if path=='/jobs/prepare':return dict(spec=spec,protocol=protocol,fit_started=False)
            return self.registry.create_job(spec,data.get('request_key',digest(spec)))
        if path.startswith('/jobs/'):
            parts=path.split('/');jid=identifier(parts[2]);action=parts[-1]
            if action=='cancel':return self.runner.cancel(jid)
            if action=='resume':
                value=self.registry.resume(jid);self.runner.cancelled.discard(jid);return value
            raise ValueError('Unknown task action')
        if path=='/models/import_matlab':
            from .matlab import convert_numeric_mat
            p=within(self.root/'data'/identifier(data['upload_id'])/'numeric.mat',self.root/'data')
            target=convert_numeric_mat(p,data['metadata'],self.root/'models');_,meta=load_artifact(target);self.registry.register(target,meta);return meta
        if path=='/models/import':
            p=within(self.root/'data'/identifier(data['upload_id']),self.root/'data');_,meta=load_artifact(p)
            if meta.get('evaluation_stage')=='synthetic':raise ValueError('Synthetic artifacts cannot enter production library')
            # Immutable content is copied into the managed model root.
            import shutil
            target=self.root/'models'/meta['model_id'];target.parent.mkdir(exist_ok=True)
            if not target.exists():shutil.copytree(p,target)
            load_artifact(target);self.registry.register(target,meta);return meta
        if path=='/models/replay':
            self.stop()
            if not self.runner.resource.acquire(blocking=False):raise ValueError('Compute resource is busy; wait for the active task')
            try:return self.replay(data)
            finally:self.runner.resource.release()
        if path=='/models/activate':
            self.stop()
            if not self.runner.resource.acquire(blocking=False):raise ValueError('Training owns the compute resource; wait or cancel it')
            try:
                mid=identifier(data.get('id',data.get('model_id')));row=self.registry.model(mid);model,meta=load_artifact(within(row['path'],self.root/'models'))
                with self.lock:self.model=model;self.manifest=meta;self.registry.select(mid);self.last_identity=None;self.last=None;self.generation+=1
                return self.get('/models')
            finally:self.runner.resource.release()
        if path=='/inference/start':
            if not self.runner.resource.acquire(blocking=False):raise ValueError('Training owns the compute resource')
            try:
                with self.lock:
                    if self.model is None:raise ValueError('Select and load a registered classifier first')
                    if not self.manifest.get('runtime_accepted'):raise ValueError('This classifier is for offline/native-task validation and has no live navigation contract')
                    self.runner.inference_enabled=True;self.lease=time.monotonic();self.generation+=1;self.last_identity=None;self.reset_stream()
            finally:self.runner.resource.release()
            return self.inference_status()
        if path=='/inference/stop':self.stop();return self.inference_status()
        if path=='/inference/predict':return self.predict(data)
        raise ValueError('Unsupported compute POST operation')
    def replay(self,data):
        from .runner import compile_inputs,metrics
        if self.model is None:raise ValueError('Select a registered model first')
        dataset=read_json(self.root/'datasets'/(identifier(data['dataset_id'])+'.json'))
        if dataset.get('schema')=='gnautilus.personal_study.v1':
            if self.manifest.get('runtime_schema') not in ['gnautilus.causal_jepa_stream.v1','gnautilus.causal_jepa_stream.v2','gnautilus.hybrid_navigation.v1','gnautilus.hybrid_navigation.v2','gnautilus.hybrid_navigation.v3','gnautilus.hybrid_navigation.v4','gnautilus.hybrid_navigation.v5','gnautilus.hybrid_navigation.v6','gnautilus.hybrid_navigation.v7'] or self.manifest.get('dataset_sha256')!=digest(dataset):raise ValueError('Select the matching personal streaming model')
            role=data.get('role','development')
            if role not in ['train','development']:raise ValueError('Held-out internal test is evaluated only by the locked study')
            from .personal_study import load_episodes
            from .jepa_training import evaluate
            scores,frames=evaluate(self.model,load_episodes(dataset,'validation' if role=='development' else 'train'))
            report=dict(schema='gnautilus.replay.v2',source='OFFLINE_REPLAY',motion_enabled=False,role=role,model_id=self.manifest['model_id'],metrics=scores,independent_test=False,frames=frames)
            key=digest(dict(model=self.manifest['model_id'],dataset=data['dataset_id'],role=role));atomic(self.root/'logs'/('replay-'+key+'.json'),report)
            return {k:v for k,v in report.items() if k!='frames'}
        values,_=compile_inputs(dataset,self.manifest['window_s'],self.manifest['hop_s']);role=data.get('role','development')
        if role not in ['train','development']:raise ValueError('Independent test data needs a separately locked acceptance protocol')
        x,y,groups,ids=values[role];prediction=self.model.predict(x);report=dict(schema='gnautilus.replay.v1',source='OFFLINE_REPLAY',motion_enabled=False,role=role,model_id=self.manifest['model_id'],metrics=metrics(y,prediction),windows=ids,
            probabilities=prediction['probabilities'].tolist(),labels=prediction['labels'].tolist(),independent_test=False)
        if all(r.get('reader','gnautilus')=='gnautilus' for r in dataset['records']):
            from .replay import continuous_record
            report['continuous']=[dict(record=r['source_sha256'],summary=summary,frames=frames) for r in dataset['records'] if r['role']==role for summary,frames in [continuous_record(self.model,self.manifest,r)]]
        key=digest({'model':self.manifest['model_id'],'dataset':data['dataset_id'],'role':role});atomic(self.root/'logs'/('replay-'+key+'.json'),report)
        out={k:v for k,v in report.items() if k not in ['windows','probabilities','labels','continuous']}
        if 'continuous' in report:out['continuous']=[dict(record=r['record'],summary=r['summary']) for r in report['continuous']]
        return out
    def stop(self):
        with self.lock:self.runner.inference_enabled=False;self.generation+=1;self.last=None;self.last_identity=None;self.reset_stream()
    def reset_stream(self):
        self.stream_session=None;self.stream_identity=None;self.stream_end=None;self.stream_last=None;self.stream_hash=None
    def inference_status(self):
        with self.lock:
            if self.runner.inference_enabled and time.monotonic()-self.lease>3:self.stop()
            return dict(inference_enabled=self.runner.inference_enabled,generation=self.generation,model_id=self.manifest['model_id'] if self.manifest else None,last=self.last)
    def predict(self,data):
        started=time.monotonic()
        with self.lock:
            if not self.inference_status()['inference_enabled']:raise ValueError('Inference lease expired or inference is stopped')
            meta=self.manifest;d=data['descriptor'];contract=meta['input_contract']
            if data['model_id']!=meta['model_id'] or data['generation']!=self.generation:raise ValueError('Inference generation/model changed')
            if meta.get('runtime_schema') in ['gnautilus.causal_jepa_stream.v1','gnautilus.causal_jepa_stream.v2','gnautilus.hybrid_navigation.v1','gnautilus.hybrid_navigation.v2','gnautilus.hybrid_navigation.v3','gnautilus.hybrid_navigation.v4','gnautilus.hybrid_navigation.v5','gnautilus.hybrid_navigation.v6','gnautilus.hybrid_navigation.v7']:
                from .stream_runtime import predict_stream
                return predict_stream(self,data,started)
            self.lease=started
            names=[c['name'] for c in d['channels'] if c['kind']=='EEG']
            if names!=contract['channels'] or d['sampling_rate']!=contract['sampling_rate'] or d.get('reference')!=contract['reference'] or d['configuration']!=contract['filter_configuration']:raise ValueError('Live montage/rate/reference/filter differs from model contract')
            if d.get('source_kind')!='gtec_live' or not d.get('layout_verified'):raise ValueError('Live g.Nautilus verified montage required')
            x=np.asarray(data['samples'],dtype=np.float32);n=round(meta['window_s']*d['sampling_rate'])
            if x.shape!=(len(names),n):raise ValueError('Incomplete trailing history')
            identity=(data['stream_id'],data['source_epoch'],data['sample_index_end'])
            if type(identity[2]) is not int or identity[2]<n:raise ValueError('Invalid sample position')
            if self.last_identity:
                if self.last_identity[:2]!=identity[:2]:
                    self.stop();raise ValueError('Input identity changed; manually restart inference')
                if identity[2]<=self.last_identity[2]:raise ValueError('Duplicate/out-of-order samples do not advance inference evidence')
            q=inspect_signal(x,names,d['sampling_rate'],meta['electrical_policy'])
            if not q['electrical_valid'] or data.get('device_valid') is False:raise ValueError('Electrical/device quality rejected: '+str(q['reasons']))
            result=self.model.predict(x[None]);probs=result['probabilities'][0].tolist();branches={k:v[0].tolist() for k,v in (result.get('branches') or {}).items()}
            accepted=bool(result.get('accepted',[True])[0])
            out=dict(model_id=meta['model_id'],generation=self.generation,stream_id=identity[0],source_epoch=identity[1],sample_index_end=identity[2],probabilities=probs,prediction=LABELS[int(result['labels'][0])] if accepted else 'UNKNOWN',
                raw_prediction=LABELS[int(result['labels'][0])],rejection_reason=None if accepted else 'classifier_oof_policy_rejected',
                branches=branches,quality=q,worker_ms=(time.monotonic()-started)*1000,eligible=accepted,score_semantics=result['score_semantics'])
            self.last_identity=identity;self.last=out;return out
