"""Manual, placement-bound calibration. No persisted result grants a new boot LIVE.

The check suffix is never fitted. This is an operational check, not an accuracy
estimate. All timing advances on fresh, accepted EEG, not browser timers.
"""
import copy
import hashlib
import json
import secrets
import threading
import time
import numpy as np

LABELS = ['REST','FORWARD','BACKWARD','LEFT','RIGHT']
PROMPTS = dict(zip(LABELS, ['保持静止','想象向前移动','想象向后移动','想象向左转','想象向右转']))

def rest_execution_check(probabilities,policy):
    """Replay the real Hub control rule; max(single direction) is insufficient
    when non-opposite axes can combine. This never sends an actual command.
    """
    from hub.intent_control import IntentControl
    now=[10.];controller=IntentControl(True,lambda:now[0])
    if isinstance(policy,dict):controller.configure({k:v for k,v in policy.items() if k in ['active_threshold','direction_threshold','axis_mass_threshold','speed','yaw_rate']})
    for i,p in enumerate(probabilities):
        now[0]=10.+i*.1
        result=controller.update(dict(eligible=True,source='LIVE',model_id='calibration-check',source_epoch=1,sample_index_end=1000+i*25,
            input_origin_monotonic=now[0]-.01,probabilities=p,usable_channels=list(range(7)),combo_qualified=bool(isinstance(policy,dict) and policy.get('combo_qualified'))))
        if result['active']:return False
    return True

def calibration_evidence_passes(adapt_seconds,details,channels,coverage,baseline_seconds):
    return bool(len(adapt_seconds)==5 and min(adapt_seconds)>=8 and len(channels)>=4 and coverage>=.8 and baseline_seconds>=19.99
        and len(details)==5 and {d['label'] for d in details}==set(range(5)) and sum(d['label']==d['prediction'] for d in details)>=4
        and min(d['seconds'] for d in details)>=3 and not any(d['idle_trigger'] for d in details))


class CalibrationRequired(ValueError):
    code = 'calibration_required'


def adjust_probabilities(probabilities, bias, temperature=1.):
    p = np.asarray(probabilities, dtype=float)
    b = np.asarray(bias, dtype=float)
    if p.ndim != 1 or p.shape != b.shape or len(p) not in [5,9] or not np.isfinite(p).all() or (p < 0).any() or not np.isclose(p.sum(),1) or not 0.1 <= temperature <= 10:
        raise ValueError('Invalid calibration probabilities')
    z = np.log(np.maximum(p,1e-8))/temperature+b
    z -= z.max(); p = np.exp(z); return p/p.sum()


def fit_bias(rows, labels, classes):
    """Small ridge-anchored bias, never updates encoder/temperature/check suffix."""
    p=np.asarray(rows,dtype=float); y=np.asarray(labels,dtype=int)
    if p.ndim!=2 or p.shape[1]!=classes or set(y.tolist())!=set(range(5)):
        raise ValueError('Five adaptation classes required')
    # For nine classes, four independent axis biases preserve joint semantics.
    if classes==9:
        from training.action_schema import LABEL_AXES
        design=np.asarray([[int(a==1)-int(a==0),int(a==2)-int(a==0),int(b==1)-int(b==0),int(b==2)-int(b==0)] for a,b in LABEL_AXES],float)
    else: design=np.eye(5)-np.ones((5,5))/5
    weights=np.zeros(design.shape[1]); logits=np.log(np.maximum(p,1e-8))
    # Balance classes by duration; long surviving classes must not dominate.
    sample_weight=np.asarray([1/max(1,np.sum(y==v)) for v in y]);sample_weight/=sample_weight.sum()
    for _ in range(200):
        z=logits+design@weights;z-=z.max(1,keepdims=True);q=np.exp(z);q/=q.sum(1,keepdims=True)
        q[np.arange(len(y)),y]-=1
        grad=(q*sample_weight[:,None]).sum(0)@design+.1*weights
        weights=np.clip(weights-.2*grad,-2,2)
    return (design@weights).tolist()


class SessionCalibration:
    def __init__(self,hub,clock=time.monotonic):
        self.hub=hub;self.clock=clock;self.lock=threading.RLock()
        self.placement_id=secrets.token_hex(12);self.last_model=None
        self.state='required';self.reason='本次佩戴尚未校准';self.active=False
        self.calibration_id=None;self.binding=None;self.bias=None;self.normalizer=None
        self.owns_recording=False;self.rows=[];self.truth=[];self.check=[];self.baseline=[]
        self.started=0.;self.accepted=0.;self.last_key=None;self.channel_counts=np.zeros(7)
        self.observed=0.;self.adapt_seconds=np.zeros(5)
        self.last_valid_at=None;self.phase_elapsed=0.;self.cursor=0;self.schedule=[]

    def fingerprint(self):
        h=getattr(getattr(self.hub,'intent',None),'health',None) or {}
        model=(h.get('model_id'),h.get('artifact_sha256')) if h.get('model_id') else self.last_model
        config=getattr(self.hub,'config',{})
        return dict(placement_id=self.placement_id,model=model,
            device=config.get('device_id','neuradock-physical-identity-unverified'),
            configuration_sha256=hashlib.sha256(json.dumps({k:config.get(k) for k in ['sfreq','channel_names','disabled_channels_1based','source_mode']},sort_keys=True).encode()).hexdigest(),
            preprocessing=h.get('preprocessing_sha256','causal-shared-v2'),normalization=h.get('normalization_support','legacy_output_bias_only'))

    def observe_model(self,health):
        identity=(health.get('model_id'),health.get('artifact_sha256'))
        if not identity[0]:return
        with self.lock:
            changed=self.last_model is not None and self.last_model!=identity
            self.last_model=identity
        if changed:self.invalidate('模型已更换，请重新校准')

    def require(self):
        if not self.valid():raise CalibrationRequired('请先点击快速校准，并通过本次佩戴检查')

    def valid(self):
        with self.lock:
            return self.state=='valid' and self.binding==self.fingerprint()

    def _write(self,row):
        lock=getattr(self.hub,'record_lock',None)
        if lock:
            with lock:
                if self.hub.recorder:
                    self.hub.recorder.write(json.dumps(dict(kind='calibration',calibration_id=self.calibration_id,placement_id=self.placement_id,
                        hub_receive_monotonic=self.clock(),sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch,**row),ensure_ascii=False,allow_nan=False)+'\n')
                    self.hub.recorder.flush()

    def invalidate(self,reason='重新佩戴，需要校准',new_placement=False):
        self.cancel(reason)
        with self.lock:
            if new_placement:self.placement_id=secrets.token_hex(12)
            self.state='required';self.reason=reason;self.bias=None;self.binding=None;self.normalizer=None
        if hasattr(self.hub,'intent_control'):self.hub.intent_control.configure({'enabled':False})
        if hasattr(self.hub,'intent'):self.hub.intent.stop()
        return self.snapshot()

    def start(self,surface='desktop'):
        if surface not in ['desktop','vr']:raise ValueError('Invalid calibration surface')
        with self.hub.lock:
            if self.hub.source_mode!='live' or self.hub.last_packet is None or not 0<=self.clock()-self.hub.last_packet<.25:
                raise ValueError('快速校准需要新鲜的官方设备数据')
        if self.hub.acquisition.snapshot().get('active') or self.hub.recorder is not None:
            raise ValueError('请先结束当前采集，再开始校准')
        health=self.hub.intent.request('GET','/health')
        if not health.get('weights_loaded'):raise ValueError('模型尚未加载')
        self.hub.intent.health=health;self.observe_model(health)
        with self.lock:
            if self.active:raise ValueError('已有校准任务，请勿重复开始')
        self.hub.intent.stop();self.hub.start_recording()
        with self.lock:
            self.owns_recording=True;self.active=True;self.state='calibrating';self.reason='等待合格连续数据'
            self.calibration_id=secrets.token_hex(16);self.binding=self.fingerprint()
            self.surface=surface;self.presentation_token=secrets.token_urlsafe(24);self.display_at=None;self.display_cursor=None;self.display_seq=0
            self.logged_cursor=None
            self.started=self.clock();self.accepted=0.;self.last_key=None;self.last_valid_at=None
            self.observed=0.;self.adapt_seconds=np.zeros(5)
            self.phase_elapsed=0.;self.cursor=0;self.rows=[];self.truth=[];self.check=[];self.baseline=[]
            self.channel_counts=np.zeros(7);self.bias=None;self.normalizer=None
            self.schedule=[dict(stage='baseline',label='REST',seconds=20.)]
            rng=np.random.default_rng(int(self.calibration_id[:8],16))
            for _ in range(3):
                self.schedule.extend(dict(stage='adapt',label=str(label),seconds=5.) for label in rng.permutation(LABELS))
            self.schedule.extend(dict(stage='check',label=label,seconds=5.) for label in LABELS)
            self._write(dict(event='start',protocol='manual_calibration_v2',binding=self.binding,schedule=self.schedule,
                check_suffix_fitted=False,independent_accuracy_estimate=False,display_timing_verified=False))
        return dict(self.snapshot(),presentation_token=self.presentation_token)

    def present(self,data):
        with self.lock:
            if not self.active or data.get('calibration_id')!=self.calibration_id or not secrets.compare_digest(str(data.get('presentation_token','')),self.presentation_token):
                raise ValueError('Calibration presentation owner changed')
            seq=data.get('seq');cursor=data.get('cursor')
            if type(seq) is not int or seq<=self.display_seq or type(cursor) is not int or cursor!=self.cursor:
                raise ValueError('Stale presentation frame')
            self.display_seq=seq;self.display_cursor=cursor;self.display_at=self.clock()
            if getattr(self,'logged_cursor',None)!=cursor:
                phase=self.schedule[cursor];self._write(dict(event='phase_presented',cursor=cursor,stage=phase['stage'],label=phase['label'],display_timing_verified=False))
                self.logged_cursor=cursor
            return dict(ok=True,cursor=self.cursor,display_timing_verified=False)

    def cancel(self,reason='用户取消校准'):
        with self.lock:
            close=self.owns_recording
            if self.active:self._write(dict(event='cancel',reason=reason))
            self.active=False;self.owns_recording=False
            if self.state=='calibrating':self.state='failed';self.reason=reason
        if close:self.hub.stop_recording()
        return self.snapshot()

    def tick(self):
        with self.lock:
            expired=self.active and self.clock()-self.started>240
            abandoned=self.active and self.clock()-(self.display_at or self.started)>3
        if expired:self.cancel('超过4分钟，合格数据不足；请调整佩戴后手动重试')
        elif abandoned:self.cancel('校准显示端失联，已停止并保留记录；请手动重试')

    def consume(self,result,payload):
        self.tick()
        close=False
        with self.lock:
            if not self.active:return
            if self.display_cursor!=self.cursor or self.display_at is None or self.clock()-self.display_at>.6:
                self.reason='等待校准页面显示确认';return
            if self.binding!=self.fingerprint():
                self.state='failed';self.reason='模型或配置改变';close=True
            else:
                key=(payload['source_epoch'],payload['sample_index_end'])
                if key==self.last_key:return
                previous=self.last_key;self.last_key=key
                advance=(key[1]-previous[1]) if previous and previous[0]==key[0] else min(25,len(payload['raw']))
                dt=min(.12,max(0.,advance/250))
                self.observed+=dt
                q=np.asarray(payload['quality'],bool);mask=q.all(0)
                accepted=bool(result.get('eligible') and np.sum(mask)>=4 and len(result.get('usable_channels',[]))>=4)
                if not accepted:self.reason='等待至少4路合格连续数据';return
                self.last_valid_at=self.clock();self.channel_counts+=mask*dt
                self.accepted+=dt;self.phase_elapsed+=dt;self.reason='正在采集本次佩戴校准数据'
                phase=self.schedule[self.cursor];p=result.get('raw_probabilities') or result.get('probabilities')
                self._write(dict(event='prediction',cursor=self.cursor,stage=phase['stage'],instructed_label=phase['label'],raw_probabilities=p,usable_channels=result.get('usable_channels',[]),check_suffix_fitted=False))
                if phase['stage']=='baseline':self.baseline.extend(np.asarray(payload['raw'],float)[-max(1,round(dt*250)):].tolist())
                elif self.phase_elapsed>=1. and p is not None:
                    label=LABELS.index(phase['label'])
                    if phase['stage']=='adapt':self.rows.append(p);self.truth.append(label);self.adapt_seconds[label]+=dt
                    else:
                        if not self.check or self.check[-1]['label']!=label:self.check.append(dict(label=label,probabilities=[],seconds=0.))
                        self.check[-1]['probabilities'].append(adjust_probabilities(p,self.bias).tolist());self.check[-1]['seconds']+=dt
                if self.phase_elapsed+1e-6>=phase['seconds']:
                    self._write(dict(event='phase_end',stage=phase['stage'],label=phase['label'],accepted_seconds=self.phase_elapsed))
                    self.cursor+=1;self.phase_elapsed=0.
                    if self.cursor==1:
                        x=np.asarray(self.baseline);scale=np.maximum(x.std(0),1.)
                        self.normalizer=dict(mean_uv=x.mean(0).tolist(),scale_uv=scale.tolist(),gain=np.clip(10/scale,.5,2).tolist(),applied_to_legacy_encoder=False)
                    if self.cursor==16:
                        support=self.adapt_seconds
                        if min(support)<8:
                            self.state='failed';self.reason='适配类别有效时长不足8秒';close=True
                        else:
                            self.bias=fit_bias(self.rows,self.truth,len(self.rows[0]))
                            self._write(dict(event='adapt_frozen',bias=self.bias,normalizer=self.normalizer))
                    if self.cursor>=len(self.schedule):
                        details=[]
                        for item in self.check:
                            ps=np.asarray(item['probabilities']);mean=ps.mean(0)
                            policy=self.hub.intent_control.snapshot() if hasattr(self.hub,'intent_control') else {}
                            if isinstance(policy,dict):policy=dict(policy,combo_qualified=bool((self.hub.intent.health or {}).get('combo_qualified')))
                            idle_trigger=not rest_execution_check(ps.tolist(),policy) if item['label']==0 else False
                            details.append(dict(label=item['label'],prediction=int(mean.argmax()),seconds=item['seconds'],idle_trigger=idle_trigger))
                        correct=sum(d['label']==d['prediction'] for d in details)
                        usable=np.flatnonzero(self.channel_counts>=self.accepted*.8).tolist()
                        passed=calibration_evidence_passes(self.adapt_seconds,details,usable,self.accepted/max(self.observed,1e-9),len(self.baseline)/250)
                        self.state='valid' if passed else 'failed';self.reason='本次佩戴校准通过' if passed else '检查未通过，请调整佩戴或补充训练后手动重试'
                        self.calibrated_channels=usable
                        self._write(dict(event='finish',state=self.state,details=details,correct_events=correct,calibrated_channels=usable,bias=self.bias,operational_check_only=True))
                        close=True
            if close:self.active=False;self.owns_recording=False
        if close:self.hub.stop_recording()

    def apply(self,result):
        with self.lock:
            if not self.valid() or not result.get('eligible'):return result
            if result.get('model_id') and result['model_id']!=self.binding['model'][0]:return dict(result,eligible=False,reason='calibration_model_changed')
            if not set(result.get('usable_channels',[])).issubset(set(self.calibrated_channels)):
                return dict(result,eligible=False,reason='uncalibrated_channel_set')
            raw=result.get('raw_probabilities') or result.get('probabilities')
            p=adjust_probabilities(raw,self.bias)
            from training.action_schema import probability_summary
            return dict(result,raw_probabilities=raw,probabilities=p.tolist(),calibration_id=self.calibration_id,
                placement_id=self.placement_id,calibrated=True,probability_reliability_validated=False,**probability_summary(p))

    def snapshot(self):
        with self.lock:
            phase=self.schedule[self.cursor] if self.active and self.cursor<len(self.schedule) else None
            prompt=PROMPTS.get(phase['label'],'') if phase and self.phase_elapsed<.5 else ''
            return copy.deepcopy(dict(schema='placement-calibration-v2',state=self.state,valid=self.valid(),active=self.active,
                reason=self.reason,placement_id=self.placement_id,calibration_id=self.calibration_id,
                accepted_seconds=round(self.accepted,2),required_seconds=120,wall_seconds=round(self.clock()-self.started,1) if self.started else 0,
                effective_coverage=self.accepted/max(self.observed,1e-9),
                stage=phase['stage'] if phase else None,label=phase['label'] if phase else None,prompt=prompt,phase_elapsed=self.phase_elapsed,cursor=self.cursor,surface=getattr(self,'surface',None),
                calibrated_channels=getattr(self,'calibrated_channels',[]),manual_start_required=True,independent_accuracy_estimate=False))
