"""Causal probability history and explicit experimental virtual-avatar control."""
import math,threading,time
import numpy as np
from training.action_schema import action_label


class IntentControl:
    def __init__(self,enabled=False,clock=time.monotonic):
        self.clock=clock;self.lock=threading.RLock();self.enabled=enabled
        self.active_threshold=.75;self.direction_threshold=.7;self.axis_mass_threshold=.2;self.speed=.45;self.yaw_rate=35.
        self.reset()

    def reset(self):
        self.logs=None;self.last_time=None;self.last_sample=None;self.identity=None
        self.axes={key:dict(sign=0,candidate=0,count=0,since=None) for key in ['linear','turn']}
        self.direction=0;self.decision_id=0;self.last=None

    def configure(self,data):
        allowed={'enabled','active_threshold','direction_threshold','axis_mass_threshold','speed','yaw_rate'}
        if set(data)-allowed:raise ValueError('Unknown model control field')
        if 'enabled' in data and type(data['enabled']) is not bool:raise ValueError('enabled must be boolean')
        ranges={'active_threshold':(.5,.995),'direction_threshold':(.55,.995),'axis_mass_threshold':(.15,.45),'speed':(.1,.8),'yaw_rate':(10.,45.)}
        with self.lock:
            updates={}
            for key,(lo,hi) in ranges.items():
                if key in data:
                    value=data[key]
                    if type(value) not in [int,float] or not math.isfinite(value) or not lo<=value<=hi:raise ValueError(key+' outside experimental bounds')
                    updates[key]=float(value)
            for key,value in updates.items():setattr(self,key,value)
            if 'enabled' in data:self.enabled=data['enabled']
            self.reset();return self.snapshot()

    def _axis(self,key,positive,negative,pa,now):
        mass=float(positive+negative);sign=1 if positive>=negative else -1
        confidence=float(max(positive,negative)/mass) if mass>1e-12 else .5
        state=self.axes[key];hold_conf=max(.52,self.direction_threshold-.1);hold_mass=max(.1,self.axis_mass_threshold-.06)
        if pa<max(.35,self.active_threshold-.2) or mass<hold_mass or confidence<hold_conf:
            state.update(sign=0,candidate=0,count=0,since=None)
        elif state['sign']!=sign:
            state['sign']=0
            if pa>=self.active_threshold and confidence>=self.direction_threshold and mass>=self.axis_mass_threshold:
                if state['candidate']!=sign:state.update(candidate=sign,count=1,since=now)
                else:state['count']+=1
                if state['count']>=2 and now-state['since']>=.075:state.update(sign=sign,candidate=0,count=0,since=None)
            else:state.update(candidate=0,count=0,since=None)
        strength=min(1.,max(0.,2*confidence-1))*min(1.,mass/.45) if state['sign'] else 0.
        return dict(axis=state['sign'],confidence=confidence,mass=mass,strength=strength)

    def update(self,result,acquisition_active=False,training_test_authorized=False):
        now=self.clock()
        with self.lock:
            if not self.enabled or acquisition_active or not result or (result.get('source')!='LIVE' and not (training_test_authorized and result.get('source')=='TRAINING_TEST')) or not result.get('eligible'):
                self.reset();return self.stopped('collection_active' if acquisition_active else 'experimental_motion_disabled' if not self.enabled else 'invalid_or_replay')
            origin=result.get('input_origin_monotonic',0)
            if not 0<=now-origin<.5:self.reset();return self.stopped('model_input_stale')
            p=np.asarray(result.get('probabilities'),dtype=float)
            if p.shape not in [(5,),(9,)] or not np.isfinite(p).all() or np.any(p<0) or not np.isclose(p.sum(),1,atol=1e-6):
                self.reset();return self.stopped('invalid_model_probabilities')
            identity=(result.get('source_epoch'),tuple(result.get('usable_channels',range(7))),result.get('model_id'),result.get('source'))
            sample=result.get('sample_index_end')
            if type(sample) is not int:self.reset();return self.stopped('missing_sample_identity')
            if self.identity!=identity or self.last_time is None or now-self.last_time>.5 or (self.last_sample is not None and sample<self.last_sample):
                self.reset();self.identity=identity
            if sample==self.last_sample:return self.last or self.stopped('duplicate_model_update')
            dt=.1 if self.last_time is None else max(.001,now-self.last_time)
            log=np.log(np.maximum(p,1e-8));alpha=1-math.exp(-dt/.15)
            self.logs=log if self.logs is None else (1-alpha)*self.logs+alpha*log
            stable=np.exp(self.logs-self.logs.max());stable/=stable.sum()
            from training.action_schema import probability_summary
            summary=probability_summary(stable)
            active=summary['active_probability'];conditional=np.asarray(summary['direction_probabilities'])
            linear=self._axis('linear',conditional[0],conditional[1],active,now)
            turn=self._axis('turn',conditional[3],conditional[2],active,now)
            if result.get('control_policy')=='gnautilus_fixed_speed_v1':
                winner=int(np.argmax(conditional));uncertain=float(np.max(conditional))<self.direction_threshold
                if uncertain or winner in [2,3]:linear=dict(linear,axis=0,strength=0.);self.axes['linear'].update(sign=0,candidate=0,count=0,since=None)
                if uncertain or winner in [0,1]:turn=dict(turn,axis=0,strength=0.);self.axes['turn'].update(sign=0,candidate=0,count=0,since=None)
                linear['strength']=1. if linear['axis'] else 0.;turn['strength']=1. if turn['axis'] else 0.
            combo_blocked=len(p)==9 and not result.get('combo_qualified',False) and linear['axis'] and turn['axis']
            if combo_blocked:
                linear=dict(linear,axis=0,strength=0.);turn=dict(turn,axis=0,strength=0.)
            moving=bool(linear['axis'] or turn['axis'])
            self.last_time=now;self.last_sample=sample;self.decision_id+=1
            self.direction=(1 if linear['axis']>0 else 2) if linear['axis'] else (4 if turn['axis']>0 else 3) if turn['axis'] else 0
            reason='experimental_model_active' if moving else 'below_active_threshold' if active<self.active_threshold else 'confirming_model_updates' if any(s['candidate'] for s in self.axes.values()) else 'below_axis_threshold'
            if combo_blocked:reason='combination_not_qualified'
            confidence=max(linear['confidence'] if linear['axis'] else 0,turn['confidence'] if turn['axis'] else 0)
            self.last=dict(enabled=self.enabled,model_ready=True,active=moving,direction=self.direction,p_active=active,confidence=confidence,
                           motion_schema=2,linear_axis=linear['axis'],turn_axis=turn['axis'],linear_confidence=linear['confidence'],turn_confidence=turn['confidence'],
                           linear_mass=linear['mass'],turn_mass=turn['mass'],linear_strength=linear['strength'],turn_strength=turn['strength'],
                           action_label=action_label(linear['axis'],turn['axis']),score_semantics='nine_class_joint_marginals' if len(p)==9 else 'legacy_five_class_axis_fusion_not_independent_probabilities',
                           output_schema=summary['output_schema'],axes_probabilities=summary['axes_probabilities'],decision_owner='hub',calibration_id=result.get('calibration_id'),
                           speed=self.speed,yaw_rate=self.yaw_rate,reason=reason,decision_id=self.decision_id,input_origin=origin,epoch=result['source_epoch'],
                           active_threshold=self.active_threshold,direction_threshold=self.direction_threshold,axis_mass_threshold=self.axis_mass_threshold,
                           probabilities=stable.tolist(),direction_probabilities=conditional.tolist(),raw_probabilities=p.tolist(),usable_channels=list(identity[1]),evidence_policy='causal_time_weighted_log_probability_average_v1',learned_recurrent_head=False)
            return dict(self.last)

    def stopped(self,reason):
        return dict(enabled=self.enabled,model_ready=False,active=False,direction=0,linear_axis=0,turn_axis=0,motion_schema=2,p_active=0.,confidence=0.,speed=0.,yaw_rate=0.,reason=reason,decision_id=0,input_origin=None,action_label='REST')

    def snapshot(self):
        with self.lock:return dict(enabled=self.enabled,active_threshold=self.active_threshold,direction_threshold=self.direction_threshold,axis_mass_threshold=self.axis_mass_threshold,speed=self.speed,yaw_rate=self.yaw_rate,decision=self.last,calibrated=False,motion_schema=2)
