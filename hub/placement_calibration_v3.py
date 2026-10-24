"""Placement signal calibration and a separate, optional motion qualification.

The historical v2 implementation/helpers remain frozen for locked experiments.
A signal check permits inspecting LIVE model scores; it makes no direction or
accuracy claim and never qualifies those scores to drive an avatar.
"""
import copy
import secrets
import numpy as np
from .session_calibration import (SessionCalibration as LegacyCalibration,
    CalibrationRequired, LABELS, PROMPTS, fit_bias, adjust_probabilities,
    calibration_evidence_passes, rest_execution_check)


class SessionCalibration(LegacyCalibration):
    def __init__(self, hub, clock=None):
        super().__init__(hub, **({'clock': clock} if clock is not None else {}))
        self.mode='signal';self.motion_qualified=False;self.details=[];self.failures=[]
        self.finished_at=None;self.required_seconds=15.;self.direction_status='not_checked'

    def motion_valid(self):
        return self.valid() and self.motion_qualified

    def require_motion(self):
        self.require()
        if not self.motion_valid():
            raise CalibrationRequired('信号校准可查看实时推理；启用人物动作还需通过方向验证')

    def invalidate(self, reason='重新佩戴，需要校准', new_placement=False):
        self.motion_qualified=False
        return super().invalidate(reason, new_placement)

    def start(self, surface='desktop', mode='signal'):
        if surface not in ['desktop','vr'] or mode not in ['signal','directions']:
            raise ValueError('Invalid calibration surface or mode')
        with self.hub.lock:
            if self.hub.source_mode!='live' or self.hub.last_packet is None or not 0<=self.clock()-self.hub.last_packet<.25:
                raise ValueError('快速校准需要新鲜的官方设备数据')
        if self.hub.acquisition.snapshot().get('active') or self.hub.recorder is not None:
            raise ValueError('请先结束当前采集，再开始校准')
        health=self.hub.intent.request('GET','/health')
        if not health.get('weights_loaded'):raise ValueError('模型尚未加载')
        classes=len(health.get('labels') or LABELS)
        if classes not in [5,9]:raise ValueError('模型类别配置不受支持')
        self.hub.intent.health=health;self.observe_model(health)
        with self.lock:
            if self.active:raise ValueError('已有校准任务，请勿重复开始')
        self.hub.intent.stop();self.hub.start_recording()
        with self.lock:
            self.mode=mode;self.motion_qualified=False;self.details=[];self.failures=[]
            self.direction_status='not_checked' if mode=='signal' else 'pending'
            self.owns_recording=True;self.active=True;self.state='calibrating';self.reason='保持静止，等待合格连续数据'
            self.calibration_id=secrets.token_hex(16);self.binding=self.fingerprint()
            self.surface=surface;self.presentation_token=secrets.token_urlsafe(24)
            self.display_at=None;self.display_cursor=None;self.display_seq=0;self.logged_cursor=None
            self.started=self.clock();self.finished_at=None;self.accepted=0.;self.last_key=None;self.last_valid_at=None
            self.observed=0.;self.adapt_seconds=np.zeros(5)
            self.phase_elapsed=0.;self.cursor=0;self.rows=[];self.truth=[];self.check=[];self.baseline=[]
            self.channel_counts=np.zeros(7);self.bias=[0.]*classes;self.normalizer=None
            self.schedule=[dict(stage='baseline',label='REST',seconds=15. if mode=='signal' else 20.,settle_seconds=0.)]
            if mode=='directions':
                # One complete adaptation pass, then a never-fitted check suffix.
                # Each cue lasts 10s, including 2s to prepare before scored data.
                rng=np.random.default_rng(int(self.calibration_id[:8],16))
                self.schedule.extend(dict(stage='adapt',label=str(label),seconds=10.,settle_seconds=2.) for label in rng.permutation(LABELS))
                self.schedule.extend(dict(stage='check',label=label,seconds=10.,settle_seconds=2.) for label in LABELS)
            self.required_seconds=sum(p['seconds'] for p in self.schedule)
            self._write(dict(event='start',protocol='manual_signal_and_motion_v3',mode=mode,binding=self.binding,schedule=self.schedule,
                check_suffix_fitted=False,independent_accuracy_estimate=False,display_timing_verified=False,motion_qualified=False))
        return dict(self.snapshot(),presentation_token=self.presentation_token)

    def cancel(self, reason='用户取消校准'):
        result=super().cancel(reason)
        with self.lock:
            if self.finished_at is None and self.started:self.finished_at=self.clock()
        return self.snapshot()

    def tick(self):
        with self.lock:
            expired=self.active and self.clock()-self.started>(60 if self.mode=='signal' else 240)
            abandoned=self.active and self.clock()-(self.display_at or self.started)>3
        if expired:self.cancel('合格数据等待超时；请调整佩戴后手动重试')
        elif abandoned:self.cancel('校准显示端失联，已停止并保留记录；请手动重试')

    def _baseline_ready(self):
        usable=np.flatnonzero(self.channel_counts>=self.accepted*.8).tolist()
        required=15. if self.mode=='signal' else 20.
        return (len(usable)>=4 and self.accepted/max(self.observed,1e-9)>=.8
            and len(self.baseline)/250>=required-.01),usable

    def _finish(self):
        ready,usable=self._baseline_ready()
        self.calibrated_channels=usable
        if self.mode=='directions':
            policy=self.hub.intent_control.snapshot() if hasattr(self.hub,'intent_control') else {}
            if not isinstance(policy,dict):policy={}
            policy=dict(policy,combo_qualified=bool((self.hub.intent.health or {}).get('combo_qualified')))
            for item in self.check:
                ps=np.asarray(item['probabilities']);label=item['label'];prediction=int(ps.mean(0).argmax())
                self.details.append(dict(label=label,prediction=prediction,seconds=item['seconds'],
                    idle_trigger=not rest_execution_check(ps.tolist(),policy) if label==0 else False))
            passed=calibration_evidence_passes(self.adapt_seconds,self.details,usable,self.accepted/max(self.observed,1e-9),len(self.baseline)/250)
            self.motion_qualified=bool(ready and passed);self.direction_status='passed' if self.motion_qualified else 'failed'
            for d in self.details:
                if d['prediction']!=d['label']:
                    predicted=LABELS[d['prediction']] if d['prediction']<5 else '组合方向'
                    self.failures.append(f"{PROMPTS[LABELS[d['label']]]} → 识别为{PROMPTS.get(predicted,predicted)}")
                if d['idle_trigger']:self.failures.append('静止检查出现误启动判定')
            if not passed and not self.failures:self.failures.append('方向检查的有效时长、覆盖或类别支持不足')
            if not self.motion_qualified:self.bias=[0.]*len(self.bias)
        self.state='valid' if ready else 'failed'
        self.reason=('信号校准完成，可开始实时推理；人物动作尚未验证' if self.mode=='signal' else
            '信号与方向验证通过，可开始实时推理；人物动作需另行启用' if self.motion_qualified else
            '信号校准完成，方向验证未通过；可查看实时推理，人物动作保持关闭') if ready else '信号校准未通过：合格通道、覆盖或静息时长不足'
        self._write(dict(event='finish',state=self.state,mode=self.mode,details=self.details,failure_reasons=self.failures,
            calibrated_channels=usable,motion_qualified=self.motion_qualified,bias=self.bias,operational_check_only=True,
            independent_accuracy_estimate=False))

    def consume(self,result,payload):
        self.tick();close=False
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
                advance=key[1]-previous[1] if previous and previous[0]==key[0] else min(25,len(payload['raw']))
                dt=min(.12,max(0.,advance/250));self.observed+=dt
                q=np.asarray(payload['quality'],bool);mask=q.all(0)
                if not (result.get('eligible') and np.sum(mask)>=4 and len(result.get('usable_channels',[]))>=4):
                    self.reason='等待至少4路合格连续数据';return
                phase=self.schedule[self.cursor]
                p=result.get('raw_probabilities') or result.get('probabilities')
                if phase['stage']!='baseline':
                    a=np.asarray(p,dtype=float)
                    if a.shape!=(len(self.bias),) or not np.isfinite(a).all() or (a<0).any() or not np.isclose(a.sum(),1):
                        self.reason='等待有效模型分数';return
                dt=min(dt,max(0.,phase['seconds']-self.phase_elapsed))
                before=self.phase_elapsed;self.phase_elapsed=round(before+dt,12)
                self.accepted=round(self.accepted+dt,12);self.channel_counts+=mask*dt;self.last_valid_at=self.clock()
                self.reason='正在采集静息信号' if phase['stage']=='baseline' else '准备动作，暂不计入方向检查' if self.phase_elapsed<=phase['settle_seconds'] else '保持当前动作，正在采集'
                self._write(dict(event='prediction',cursor=self.cursor,stage=phase['stage'],instructed_label=phase['label'],raw_probabilities=p,
                    usable_channels=result.get('usable_channels',[]),check_suffix_fitted=False))
                if phase['stage']=='baseline':
                    self.baseline.extend(np.asarray(payload['raw'],float)[-max(1,round(dt*250)):].tolist())
                else:
                    scored=max(0.,self.phase_elapsed-max(before,phase['settle_seconds']))
                    if scored>0:
                        label=LABELS.index(phase['label'])
                        if phase['stage']=='adapt':
                            self.rows.append(p);self.truth.append(label);self.adapt_seconds[label]=round(self.adapt_seconds[label]+scored,12)
                        else:
                            if not self.check or self.check[-1]['label']!=label:self.check.append(dict(label=label,probabilities=[],seconds=0.))
                            self.check[-1]['probabilities'].append(adjust_probabilities(p,self.bias).tolist());self.check[-1]['seconds']+=scored
                if self.phase_elapsed+1e-9>=phase['seconds']:
                    self._write(dict(event='phase_end',stage=phase['stage'],label=phase['label'],accepted_seconds=self.phase_elapsed))
                    self.cursor+=1;self.phase_elapsed=0.
                    if self.cursor==1:
                        x=np.asarray(self.baseline);scale=np.maximum(x.std(0),1.)
                        self.normalizer=dict(mean_uv=x.mean(0).tolist(),scale_uv=scale.tolist(),gain=np.clip(10/scale,.5,2).tolist(),applied_to_legacy_encoder=False)
                    if self.mode=='directions' and self.cursor==6:
                        if min(self.adapt_seconds)<8:
                            self.state='failed';self.reason='适配类别有效时长不足8秒';close=True
                        else:
                            self.bias=fit_bias(self.rows,self.truth,len(self.bias));self._write(dict(event='adapt_frozen',bias=self.bias,normalizer=self.normalizer))
                    if self.cursor>=len(self.schedule):self._finish();close=True
            if close:self.active=False;self.owns_recording=False;self.finished_at=self.clock()
        if close:self.hub.stop_recording()

    def apply(self,result):
        result=super().apply(result)
        if result.get('calibrated'):
            return dict(result,signal_calibrated=True,direction_calibrated=self.motion_valid(),motion_qualified=self.motion_valid())
        return result

    def snapshot(self):
        with self.lock:
            phase=self.schedule[self.cursor] if self.active and self.cursor<len(self.schedule) else None
            return copy.deepcopy(dict(schema='placement-calibration-v3',state=self.state,valid=self.valid(),active=self.active,mode=self.mode,
                reason=self.reason,placement_id=self.placement_id,calibration_id=self.calibration_id,
                accepted_seconds=round(self.accepted,2),required_seconds=self.required_seconds,
                wall_seconds=round((self.finished_at or self.clock())-self.started,1) if self.started else 0,
                effective_coverage=self.accepted/max(self.observed,1e-9),
                stage=phase['stage'] if phase else None,label=phase['label'] if phase else None,prompt=PROMPTS.get(phase['label'],'') if phase else '',
                phase_elapsed=self.phase_elapsed,phase_seconds=phase['seconds'] if phase else 0,phase_count=len(self.schedule),
                settle_seconds=phase['settle_seconds'] if phase else 0,cursor=self.cursor,surface=getattr(self,'surface',None),
                calibrated_channels=getattr(self,'calibrated_channels',[]),manual_start_required=True,independent_accuracy_estimate=False,
                motion_qualified=self.motion_valid(),direction_status=self.direction_status,check_details=self.details,failure_reasons=self.failures))
