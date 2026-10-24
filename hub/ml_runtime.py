"""New-device model runtime. All production fitting and prediction is remote."""
import json,threading,time
from pathlib import Path
import numpy as np
from gnautilus_ml.common import atomic,read_json,within
from gnautilus_ml.algorithms import ALGORITHMS

class MLManager:
    def __init__(self,hub,root,bridge):
        self.hub=hub;self.root=Path(root);self.bridge=bridge;self.lock=threading.RLock();self.cached={'models':[],'jobs':[]};self.updated=0;self.error=None;self.switch={'state':'idle'}
        self.retry_at=0;self.retry_delay=2;self.restore_retry_at=0;self.restore_lock=threading.Lock();self.restore_state={'state':'waiting_connection'}
        self.cache_path=self.root/'hub/runtime/gnautilus_ml_cache.json'
        try:self.cached=read_json(self.cache_path)
        except (OSError,ValueError):pass
    def refresh(self):
        try:
            value=self.bridge.call('GET','/snapshot')
            with self.lock:self.cached=value;self.updated=time.time();self.error=None;atomic(self.cache_path,value)
        except Exception as error:self.error=str(error)
        return self.snapshot()
    def connection_tick(self,now=None):
        realtime=now is None;now=time.monotonic() if realtime else now
        epoch=getattr(self.bridge,'connection_epoch',None)
        if self.bridge.auto_connect_suspended or self.bridge.status.get('connecting'):return
        if self.bridge.status.get('ssh_connected') and self.bridge.status.get('worker_connected'):
            self.refresh()
            if not self.error:self.retry_delay=2;self.restore_selected(now);return
            if self.bridge.auto_connect_suspended:return
        if now<self.retry_at or not self.bridge.password:return
        self.hub.intent.invalidate('private_compute_link_reconnect',preserve_mode=True)
        ok=self.bridge.connect(expected_epoch=epoch);self.retry_at=(time.monotonic() if realtime else now)+self.retry_delay;self.retry_delay=min(30,self.retry_delay*2)
        if ok:self.refresh();self.restore_selected(now)
    def restore_selected(self,now=None):
        now=time.monotonic() if now is None else now
        if not self.restore_lock.acquire(blocking=False):return
        try:
            mid=self.cached.get('selected');loaded=self.cached.get('loaded')
            if not mid:self.restore_state={'state':'selection_required','message':'尚未选择模型；不会按分数自动替换'};return
            if not (self.updated and time.time()-self.updated<8 and not self.error and self.bridge.status.get('ssh_connected') and self.bridge.status.get('worker_connected')):
                self.restore_state={'state':'waiting_connection','model_id':mid};return
            meta=next((m for m in self.cached.get('models',[]) if m['model_id']==mid),None)
            if meta is None:self.restore_state={'state':'selected_model_missing','model_id':mid};return
            if loaded:
                actual=next((m for m in self.cached.get('models',[]) if m['model_id']==loaded),{})
                self.restore_state={'state':'loaded' if actual.get('runtime_accepted') else 'loaded_offline','model_id':loaded,'message':'推理与动作分别手动启动'};return
            if now<self.restore_retry_at or self.switching():return
            self.restore_retry_at=now+15;self.restore_state={'state':'loading','model_id':mid}
            try:
                self.hub.intent_control.configure({'enabled':False})
                self.bridge.call('POST','/models/activate',{'id':mid,'weights_sha256':meta.get('weights_sha256')},timeout=20)
                self.refresh()
                if self.cached.get('loaded')!=mid or self.error:raise ValueError('Model loading has no fresh matching receipt')
                self.restore_state={'state':'loaded' if meta.get('runtime_accepted') else 'loaded_offline','model_id':mid,'message':'已恢复原先选择的模型；推理与动作分别手动启动'}
            except Exception as error:self.restore_state={'state':'waiting_retry','model_id':mid,'error':str(error)}
        finally:self.restore_lock.release()
    def loop(self):
        while not self.hub.stop_event.wait(2):
            try:self.connection_tick()
            except Exception as error:self.error=str(error)
    def switching(self):return self.switch['state']=='switching'
    def inference_readiness(self):
        fresh=bool(self.updated and time.time()-self.updated<8 and not self.error and self.bridge.status.get('worker_connected') and self.bridge.status.get('ssh_connected'))
        loaded=self.cached.get('loaded');meta=next((m for m in self.cached.get('models',[]) if m['model_id']==loaded),None)
        count=sum(bool(m.get('runtime_accepted')) for m in self.cached.get('models',[]))
        if not fresh:code,message='compute_disconnected',('计算连接已暂停；请在设备页检查状态后重新连接' if self.bridge.auto_connect_suspended else '计算连接未就绪；中枢将按退避计划自动重连')
        elif not meta:code,message='model_not_loaded','等待恢复原先选择的模型；尚未选择时请到模型库选择'
        elif not meta.get('runtime_accepted'):code,message='offline_model','已加载公开数据离线分类器，可做开发回放；当前没有匹配 g.Nautilus 实时输入的导航合同'
        else:code,message='ready','模型实时合同已配置；启动后仍核对真实输入、连续性与电气质量'
        return dict(can_start=code=='ready',code=code,message=message,model_id=loaded if fresh else None,runtime_model_count=count)
    def request_refresh(self):
        if not self.bridge.status.get('ssh_connected'):raise ValueError('请先连接新的私有计算服务器')
        return self.refresh()
    def activate(self,data):
        self.hub.intent.stop();self.switch={'state':'switching','id':data.get('id')}
        try:
            self.bridge.call('POST','/models/activate',data,timeout=20);self.refresh();self.switch={'state':'completed','id':data.get('id')}
        except Exception as error:self.switch={'state':'failed','message':str(error)};raise
        return self.snapshot()
    def snapshot(self):
        with self.lock:
            cache=self.cached;groups={};selected=cache.get('selected');fresh=bool(self.updated and time.time()-self.updated<8 and not self.error and self.bridge.status.get('ssh_connected') and self.bridge.status.get('worker_connected'));loaded=cache.get('loaded') if fresh else None
            restore=self.restore_state if fresh else dict(state='waiting_connection',model_id=selected,message='等待计算连接核验；缓存中的模型不表示当前已加载')
            for m in cache.get('models',[]):
                alg=m['algorithm'];metrics=m.get('metrics',{})
                def score(key):
                    v=metrics.get(key) or {};return dict(ba=v.get('balanced_accuracy'),accuracy=v.get('accuracy'),semantics='已查看会话 · 含质量覆盖另报',recordings=v.get('recordings'),coverage=v.get('coverage'))
                row=dict(m,id=m['model_id'],version=m['model_id'][:12],experiment=alg,completed=True,current=m['model_id']==loaded,
                    can_activate=fresh,availability_reason='可加载；动作使用资格单独检查' if m.get('runtime_accepted') else '原生任务/离线分类器；不能直接输出实时导航',training=score('training'),development=score('development'),live_qualified=False)
                groups.setdefault(alg,dict(id=alg,title=ALGORITHMS.get(alg,alg),versions=[]))['versions'].append(row)
            return dict(schema='gnautilus.models.v1',models=cache.get('models',[]),groups=list(groups.values()),total_versions=len(cache.get('models',[])),active={'model_id':loaded} if loaded else None,
                        current_model_id=loaded,selected_model_id=selected,server_connected=bool(self.bridge.status.get('ssh_connected')),remote_fresh=fresh,refreshed_at=self.updated or None,error=self.error,
                        refreshing=False,refresh_state='failed' if self.error else 'complete' if self.updated else 'idle',switching=self.switching(),switch=self.switch,read_only=False,restore=restore,
                        runtime_model_count=sum(bool(m.get('runtime_accepted')) for m in cache.get('models',[])))
    def training(self):return dict(schema='gnautilus.training.v1',jobs=self.cached.get('jobs',[]),fresh=bool(self.updated and time.time()-self.updated<8 and not self.error and self.bridge.status.get('ssh_connected') and self.bridge.status.get('worker_connected')),updated_at=self.updated,error=self.error,server=dict(self.bridge.status))
    def preparation(self,state):
        import datetime
        now=datetime.datetime.now(datetime.timezone.utc).isoformat();fresh=bool(self.updated and time.time()-self.updated<8 and not self.error and self.bridge.status.get('ssh_connected') and self.bridge.status.get('worker_connected'))
        datasets=self.cached.get('datasets',[]);roles={r.get('role') for d in datasets for r in d.get('records',[])};data_ready={'train','development'}<=roles
        loaded=self.cached.get('loaded');d=state.get('gnautilus',{}).get('descriptor') or {};rows=[]
        def add(key,label,ok,text,source):rows.append(dict(id=key,label=label,status='ok' if ok else 'warn',summary=text,source=source,checked_at=now,age_ms=0))
        add('acquisition','数据采集',state.get('source',{}).get('fresh'),'实时数据新鲜' if state.get('source',{}).get('fresh') else '等待真实 g.Nautilus 数据；历史补传不算实时','/api/status.source')
        add('processing','因果处理',True,'保留原厂配置；分类器窗口内因果处理与训练一致，不套用旧设备滤波','gnautilus_ml/algorithms.py')
        add('ssh','私有计算连接',fresh,'172.25.56.83 · SSH隧道内访问环回服务' if fresh else '连接中断或状态过期；请在训练页连接服务器','/api/training/jobs.server')
        add('gpu','计算设备',fresh,'新服务器 A100 UUID 在启动与训练入口核对；不代表识别效果','gnautilus_ml/governance.py')
        add('model','实际加载模型',fresh and bool(loaded),loaded or '尚未手动选择模型；训练完成会自动登记模型库','/api/models.current_model_id')
        add('training_software','训练通路',fresh,f'{len(ALGORITHMS)}类算法、持久任务、导出登记已接通；合成验证不代表真实 EEG 验收','/api/ml/capabilities')
        add('training_data','个人数据',data_ready,f'已登记 {len(datasets)} 个数据集；仍按所选组合核查分组、试次及质量' if datasets else '尚未导入完整真实记录；训练与开发使用不同日期/佩戴组','/api/ml/datasets')
        add('montage','设备线路',d.get('layout_verified') is True,'以实际通道、参考和采样配置核对；未确认布局不能实时使用','/api/status.gnautilus.descriptor')
        add('execution','动作执行',False,'推理与动作独立手动启动；实际执行以新鲜接收端遥测为准','/api/intent/status')
        return dict(version=1,schema='gnautilus.preparation.v1',software_ready=fresh,formal_training_ready=fresh and data_ready,live_control_enabled=bool((state.get('control') or {}).get('enabled')),stages=rows,
            blockers=[dict(id=r['id'],label=r['label'],reason=r['summary'],next_action='在现有页面核查对应数据或连接；本报告不启动任务。') for r in rows if r['status']!='ok' and r['id']!='execution'])
    def api_get(self,path):
        if path=='/api/ml/capabilities':
            from gnautilus_ml.data import DEFAULT_MODE,MODES,LABELS
            from gnautilus_ml.tasks import RESEARCH_OBJECTIVE,task_spec
            return dict(algorithms=[dict(id=k,title=v) for k,v in ALGORITHMS.items()],server_connected=bool(self.bridge.status.get('ssh_connected')),
                default_experiment_mode=DEFAULT_MODE,research_objective=RESEARCH_OBJECTIVE,experiment_modes=MODES,
                task_specs_by_mode={mode:{label:task_spec(label,mode) for label in LABELS} for mode in MODES})
        if path=='/api/ml/local-records':
            records=[]
            import itertools
            for p in sorted(itertools.islice((self.root/'data/gnautilus').rglob('descriptor.json'),1024)):
                d=read_json(p);date=p.parents[2].name if p.parents[1].name=='sessions' else None
                records.append(dict(path=str(p.parent),date=date,closed=(p.parent/'closed.json').exists(),reference=d.get('reference'),wearing_id=d.get('wearing_id'),channels=len([c for c in d['channels'] if c['kind']=='EEG'])))
            return {'records':records}
        if path=='/api/ml/collection-profile':
            try:return read_json(self.root/'hub/runtime/gnautilus_collection.json')
            except FileNotFoundError:return {'experiment_mode':'navigation_mi_gaze'}
        if path=='/api/ml/datasets':return self.bridge.call('GET','/datasets')
        if path=='/api/training/jobs':return self.training()
        if path.startswith('/api/training/jobs/'):return self.bridge.call('GET',path.replace('/api/training','',1))
        raise ValueError('Unknown model GET operation')
    def api_post(self,path,data):
        if path=='/api/ml/collection-profile':
            from gnautilus_ml.data import MODES
            if data.get('experiment_mode') not in MODES:raise ValueError('Unknown experiment mode')
            if self.hub.acquisition.snapshot().get('active'):raise ValueError('Stop collection before changing task semantics')
            value={'experiment_mode':data['experiment_mode']};atomic(self.root/'hub/runtime/gnautilus_collection.json',value);return value
        if path=='/api/ml/datasets/import':
            records=[]
            for item in data['records']:
                local=within(item['path'],self.root/'data/gnautilus');upload=self.bridge.upload_record(local);records.append({'upload_id':upload,'attributes':item['attributes']})
            return self.bridge.call('POST','/datasets/import',dict(title=data.get('title','个人数据集'),records=records),timeout=60)
        if path=='/api/ml/datasets/combine':return self.bridge.call('POST','/datasets/combine',data)
        if path=='/api/ml/replay':self.hub.intent.stop();return self.bridge.call('POST','/models/replay',data,timeout=60)
        if path=='/api/models/import':
            if data.get('local_path'):
                p=within(data['local_path'],self.root);upload=self.bridge.upload_artifact(p);data=dict(data,upload_id=upload)
            return self.bridge.call('POST','/models/import',data)
        if path=='/api/ml/import-matlab':
            p=within(data['local_path'],self.root);upload=self.bridge.upload_numeric_mat(p)
            result=self.bridge.call('POST','/models/import_matlab',dict(upload_id=upload,metadata=data['metadata']));self.refresh();return result
        if path=='/api/training/jobs':
            value=self.bridge.call('POST','/jobs',data);self.refresh();return value
        if path.startswith('/api/training/jobs/'):
            value=self.bridge.call('POST',path.replace('/api/training','',1),data);self.refresh();return value
        raise ValueError('Unknown model POST operation')

class MLIntent:
    def __init__(self,hub,manager):
        self.hub=hub;self.manager=manager;self.lock=threading.RLock();self.inference_enabled=False;self.generation=0;self.remote_generation=None;self.latest=None;self.last_return=0;self.error=None;self.rejection=None;self.state='stopped';self.model_id=None;self.last_sample=None
        self.clip_library=self
        self.retry_at=0
    def catalog(self):return {'schema':'gnautilus.replay.v1','records':[],'reason':'在训练工作台选择已登记数据集，回放不会进入实时动作'}
    def start(self,purpose='normal'):
        if purpose!='normal':raise ValueError('新设备诊断请使用工作台的独立离线回放')
        self.hub.gnautilus_runtime.model_input();self.stop();tag=self.generation;reply=self.manager.bridge.call('POST','/inference/start',{})
        with self.lock:
            if self.generation!=tag:raise ValueError('Input changed while starting inference; restart manually')
            self.inference_enabled=True;self.remote_generation=reply['generation'];self.model_id=reply['model_id'];self.state='waiting_input';self.generation+=1;self.error=None
        return self.snapshot()
    def stop(self):
        with self.lock:
            self.inference_enabled=False;self.latest=None;self.last_return=0;self.last_sample=None;self.remote_generation=None;self.state='stopped';self.generation+=1
        self.hub.intent_control.configure({'enabled':False});self.hub.vr.stop_test()
        def remote_stop():
            try:self.manager.bridge.call('POST','/inference/stop',{},timeout=3)
            except Exception:pass
        # Synchronous for an explicit operator request; no hub acquisition lock is held here.
        remote_stop();return self.snapshot()
    def invalidate(self,reason,*,preserve_mode=False):
        with self.lock:
            enabled=self.inference_enabled and preserve_mode
            if self.state=='waiting_input' and self.rejection==reason and self.latest is None and self.remote_generation is None and self.last_sample is None and enabled:return
            self.latest=None;self.last_return=0;self.last_sample=None;self.remote_generation=None;self.rejection=reason
            self.generation+=1;self.inference_enabled=enabled;self.state='waiting_input' if enabled else 'stopped'
        self.hub.intent_control.configure({'enabled':False})
    def cancel_test(self):pass
    def stop_replay(self):return self.stop()
    def start_replay(self,*a,**kw):raise ValueError('使用新工作台离线回放；旧模型回放入口已归档')
    def test_command(self,data):
        if data.get('action')=='release':return self.stop()
        raise ValueError('旧训练片段动作入口已归档；新数据回放不驱动动作')
    def snapshot(self):
        with self.lock:
            fresh=bool(self.inference_enabled and self.latest and time.monotonic()-self.latest['input_origin_monotonic']<.25 and self.latest['source_epoch']==self.hub.epoch)
            meta=next((r for r in self.manager.cached.get('models',[]) if r['model_id']==self.model_id),None)
            current=self.model_id or self.manager.cached.get('loaded');meta=next((r for r in self.manager.cached.get('models',[]) if r['model_id']==current),None)
            health=dict(model_id=current,weights_loaded=self.manager.cached.get('loaded')==current,algorithm_title=ALGORITHMS.get(meta['algorithm']),fusion_weight=meta.get('details',{}).get('fusion_weight'),labels=meta.get('class_labels'),evidence_ms=meta['window_s']*1000,hop_ms=meta['hop_s']*1000,training_fit=(meta.get('metrics') or {}).get('training'),development=(meta.get('metrics') or {}).get('development')) if meta else None
            return dict(schema='gnautilus.intent.v1',state=self.state,inference_state=self.state,inference_enabled=self.inference_enabled,enabled=self.inference_enabled,generation=self.generation,mode='LIVE',fresh=fresh,health=health,
                model=self.model_id,latest=self.latest if fresh else None,error=self.error,rejection=self.rejection,control=self.hub.intent_control.snapshot(),training_test={'active':False},age_ms=(time.monotonic()-self.last_return)*1000 if self.last_return else None)
    def loop(self):
        while not self.hub.stop_event.wait(.1):
            if not self.inference_enabled:continue
            generation=self.generation;started=time.monotonic()
            try:
                payload=self.hub.gnautilus_runtime.model_input();meta=next((m for m in self.manager.cached.get('models',[]) if m['model_id']==self.model_id),None)
                if meta is None:raise ValueError('Model metadata is not synchronized')
                end=payload['sample_index_end']
                if end==self.last_sample:continue
                d=payload['descriptor'];indices=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG'];n=round(meta['window_s']*d['sampling_rate'])
                if len(payload['samples'])<n:self.state='warming';continue
                origin=payload['input_origin_monotonic'];history=payload['samples'][-n:];validation=[i for i,c in enumerate(d['channels']) if c['kind']=='VALIDATION']
                device_valid=bool(np.all(history[:,validation]==1)) if validation else None
                if device_valid is False:raise ValueError('History contains device-invalid samples; waiting for a clean full window')
                if self.remote_generation is None:
                    if time.monotonic()<self.retry_at:continue
                    self.retry_at=time.monotonic()+1
                    if self.manager.cached.get('loaded')!=self.model_id:raise ValueError('等待原模型重新加载，不能自动切换到其它模型')
                    lease=self.manager.bridge.call('POST','/inference/start',{},timeout=3)
                    with self.lock:
                        if generation!=self.generation or not self.inference_enabled:continue
                        if lease['model_id']!=self.model_id:raise ValueError('等待原模型重新加载，不能自动切换到其它模型')
                        self.remote_generation=lease['generation']
                request=dict(descriptor=d,model_id=self.model_id,generation=self.remote_generation,stream_id=payload['stream_id'],source_epoch=payload['source_epoch'],sample_index_end=end,
                    samples=history[:,indices].T.tolist(),device_valid=device_valid)
                result=self.manager.bridge.call('POST','/inference/predict',request,timeout=2)
                if generation!=self.generation or not self.inference_enabled or result['source_epoch']!=self.hub.epoch or result['sample_index_end']!=end or result['model_id']!=self.model_id:continue
                if origin is None or not 0<=time.monotonic()-origin<.25:raise ValueError('late_return: 输入已超过250ms')
                p=result['probabilities'];active=1-p[0];result.update(source='LIVE',input_origin_monotonic=origin,usable_channels=indices,channel_names=[d['channels'][i]['name'] for i in indices],control_policy='gnautilus_fixed_speed_v1',classifier_family=meta['algorithm'],
                    active_probability=active,direction_probabilities=[v/active if active>1e-9 else 0 for v in p[1:]],roundtrip_ms=(time.monotonic()-started)*1000,input_age_ms=(time.monotonic()-origin)*1000,
                    input_advance_ms=(end-self.last_sample)/d['sampling_rate']*1000 if self.last_sample is not None else None)
                with self.lock:
                    if generation!=self.generation or not self.inference_enabled:continue
                    self.latest=result;self.last_return=time.monotonic();self.last_sample=end;self.state='running';self.error=None;self.rejection=None
            except Exception as error:
                with self.lock:self.latest=None;self.state='waiting_input';self.error=str(error);self.rejection=str(error)
                self.hub.intent_control.update(None)
                if isinstance(error,(ConnectionError,OSError)) or 'lease expired' in str(error) or 'generation/model' in str(error):self.invalidate(str(error),preserve_mode=True)
    def readiness(self):
        snap=self.snapshot();prediction=snap['latest'];collecting=bool(self.hub.recorder or self.hub.acquisition.snapshot().get('active') or self.manager.switching())
        decision=self.hub.intent_control.update(prediction if snap['fresh'] else None,collecting)
        return dict(acquisition_active=collecting,source_fresh=self.hub.gnautilus_runtime.fresh(),source_valid=snap['fresh'],server_fresh=snap['fresh'],epoch=self.hub.epoch,
            reason=decision['reason'],input_origin=decision.get('input_origin'),decision_id=decision.get('decision_id',0),model_fresh=snap['fresh'],model_decision=decision)
