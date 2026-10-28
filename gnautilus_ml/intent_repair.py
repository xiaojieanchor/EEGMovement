"""Versioned feature fusion and separately scored activity/direction control.

The old v1 runtime remains immutable. Only newly trained v2 artifacts use this
contract; no button/phase/human boundary is available to a session.
"""
import copy
import numpy as np
from .hybrid_navigation import HybridNavigationModel,HybridSession,readout_predict,feature_view
from .navigation_contract import CHANNELS,ELECTRICAL

RUNTIME='gnautilus.hybrid_navigation.v2'

def factor_scores(p):
    # Subtracting a rounded REST posterior from one loses tiny activity mass.
    # A fixed denominator floor also destroys the conditional simplex.
    p=np.asarray(p,dtype='float64');mass=p[...,1:].sum(axis=-1);total=p[...,0]+mass
    if p.shape[-1]!=5 or not np.isfinite(p).all() or (p<0).any() or (total<=0).any():raise ValueError('Five finite nonnegative class scores required')
    active=mass/total;direction=np.divide(p[...,1:],mass[...,None],out=np.full_like(p[...,1:],.25),where=mass[...,None]>0)
    return active,direction

def scaled(p,temperature):
    z=np.log(np.maximum(p,1e-9))/float(temperature);z-=z.max(axis=-1,keepdims=True);v=np.exp(z);return v/v.sum(axis=-1,keepdims=True)

def evidence(x,arrays,prefix,architecture,temperatures):
    if prefix+'kernel_mean' in arrays:
        from .kernel_readout import kernel_features
        x=kernel_features(x,arrays,prefix)
    if architecture=='flat':
        p=scaled(readout_predict(x,arrays,prefix+'flat__'),temperatures[0]);active,direction=factor_scores(p)
    else:
        active=scaled(readout_predict(x,arrays,prefix+'active__'),temperatures[0])[...,1]
        direction=scaled(readout_predict(x,arrays,prefix+'direction__'),temperatures[1]);p=np.concatenate(((1-active)[...,None],active[...,None]*direction),axis=-1)
    return p,active,direction

def eye_embedding(model,x):
    """Same packed, real-length CNN/GRU computation as the frozen eye head."""
    import torch
    n=x.shape[-1];z=torch.nn.functional.elu(model.branch.spatial(model.branch.temporal(torch.nn.functional.pad((x*model.input_weights)[:,None],(16,0))))).squeeze(2)
    parts=[];k=n//4*4
    if k:parts.append(z[0,:,:k].reshape(16,-1,4).mean(-1))
    if k<n:parts.append(z[0,:,k:n].mean(-1,keepdim=True))
    packed=torch.nn.utils.rnn.pack_sequence([torch.cat(parts,-1).T],enforce_sorted=False);_,h=model.branch.gru(packed);return h[-1],model.head(h[-1]).softmax(-1)

class EyeFeatures:
    def __init__(self,indices):
        from scipy.signal import butter
        self.indices=np.asarray(indices);self.sos=butter(2,15,fs=250,btype='lowpass',output='sos');self.zi=np.zeros((len(self.sos),32,2));self.history=np.empty((32,0))
    def step(self,hop,hidden):
        from scipy.signal import sosfilt
        v,self.zi=sosfilt(self.sos,np.asarray(hop,dtype=float),zi=self.zi,axis=-1);self.history=np.concatenate((self.history,v),-1)[:,-500:];return self.features(hidden)
    def features(self,hidden,indices=None):
        indices=self.indices if indices is None else np.asarray(indices);values=[]
        for length in [50,100,200,400]:
            z=self.history[indices,-length:];n=z.shape[1];width=min(25,max(1,n//2));t=np.arange(n)/250;t-=t.mean()
            values.extend([z[:,-width:].mean(1)-z[:,:width].mean(1),z@t/max(float(t@t),1e-8),np.log(np.maximum(z.std(1),1e-5))])
        return np.concatenate(values+[np.asarray(hidden).reshape(-1)]).astype('float32')

class MovementLatch:
    def __init__(self,policy):self.policy=policy;self.candidate=0;self.count=0;self.latched=0;self.quiet=0
    def step(self,active,direction,motion=None):
        d=np.asarray(direction);order=np.argsort(d);winner=int(order[-1])+1
        motion_ok=self.policy.get('motion_threshold',0)<=0 or motion is not None and np.isfinite(motion) and motion>=self.policy['motion_threshold']
        strong=motion_ok and active>=self.policy['activity'] and d[order[-1]]>=self.policy['direction'] and d[order[-1]]-d[order[-2]]>=self.policy['margin']
        if not strong:
            self.candidate=self.count=0;self.quiet+=1
            if self.quiet>=2:self.latched=0
            return 0
        self.quiet=0
        if winner==self.latched:return 0
        self.count=self.count+1 if winner==self.candidate else 1;self.candidate=winner
        if self.count<self.policy['confirm']:return 0
        self.latched=winner;self.candidate=self.count=0;return winner

class IntentPolicy:
    def __init__(self,config):self.config=copy.deepcopy(config);self.reset()
    def reset(self):self.cue=self.pending=self.pending_count=self.active=self.candidate=self.count=self.weak=0
    def thresholds(self,d):
        c=self.config;aligned=self.cue==d;other=bool(self.cue and not aligned);holding=self.active==d
        activity=c['activity']-(c['eye_discount'] if aligned else 0)+(c['other_premium'] if other else 0)-(c['hold_discount'] if holding else 0)
        direction=c['direction']-(c['eye_discount'] if aligned else 0)+(c['other_premium'] if other else 0)-(c['hold_discount'] if holding else 0)
        return dict(direction=d,activity=float(np.clip(activity,.05,.95)),direction_score=float(np.clip(direction,.26,.95)),margin=c['margin'])
    def eye_event(self,d):
        if d not in [1,2,3,4]:return
        if self.cue and d=={1:2,2:1,3:4,4:3}[self.cue]:self.cue=0;self.pending=d;self.pending_count=0
        elif d!=self.cue:self.cue=d;self.pending=0;self.pending_count=0
        else:return
        self.active=self.candidate=self.count=self.weak=0
    def step(self,activity,direction):
        a=float(activity);p=np.asarray(direction,dtype=float);c=self.config
        if not np.isfinite(a) or p.shape!=(4,) or not np.isfinite(p).all() or not 0<=a<=1 or (p<0).any() or (p>1).any() or not np.isclose(p.sum(),1,atol=1e-5): self.reset();raise ValueError('Finite separated activity and four directions required')
        order=np.argsort(p);d=int(order[-1])+1;margin=float(p[order[-1]]-p[order[-2]])
        independent=a>=c['activity'] and p[d-1]>=c['direction'] and margin>=c['margin']
        if self.pending:
            self.pending_count=self.pending_count+1 if d==self.pending and independent else 0
            if self.pending_count>=c['confirm']:self.cue=self.pending;self.pending=self.pending_count=0
        thresholds=[self.thresholds(i) for i in range(1,5)]
        activity_threshold=thresholds[d-1]['activity'];direction_threshold=thresholds[d-1]['direction_score']
        strong=a>=activity_threshold and p[d-1]>=direction_threshold and margin>=c['margin']
        reason='accepted' if strong else 'activity_below_threshold' if a<activity_threshold else 'direction_below_threshold' if p[d-1]<direction_threshold else 'direction_margin_low'
        if strong:
            self.weak=0
            if self.active!=d:
                self.count=self.count+1 if self.candidate==d else 1;self.candidate=d;self.active=0;reason='confirming'
                if self.count>=c['confirm']:self.active=d;self.candidate=self.count=0;reason='accepted'
            else:self.candidate=self.count=0
        else:
            self.candidate=self.count=0;self.weak+=1
            if self.weak>=c['stop']:self.active=0
        rest_threshold=c['rest_activity']-(c['eye_discount'] if self.cue else 0)
        status='ACTIVE' if self.active else 'REST' if a<=rest_threshold else 'UNKNOWN'
        return dict(direction=self.active,status=status,cue_direction=self.cue,pending_direction=self.pending,confirming=self.candidate,
            activity_score=a,conditional_direction_scores=p.tolist(),activity_threshold=activity_threshold,direction_threshold=direction_threshold,
            rest_activity_threshold=rest_threshold,reason='rest_supported' if status=='REST' else reason,auxiliary='RETURN_OR_SWITCH' if self.pending else 'CUE_MEMORY' if self.cue else 'UNKNOWN',policy='factorized_navigation_v2',
            effective_thresholds=thresholds,confirmation_count=self.count,confirmation_required=c['confirm'],stop_count=self.weak,stop_required=c['stop'])

class RepairedHybridModel(HybridNavigationModel):
    def new_session(self):return RepairedSession(self)
    def predict(self,x):
        rows=[self.new_session().step(v) for v in x];p=np.asarray([r['probabilities'] for r in rows]);return dict(probabilities=p,labels=p.argmax(1),accepted=np.asarray([not r['warming'] and r['navigation_decision']['status']!='UNKNOWN' for r in rows]),score_semantics='exploratory_cross_fitted_scaling_not_true_intent_probability')

class RepairedSession(HybridSession):
    def __init__(self,model):
        super().__init__(model);self.ocular=EyeFeatures(model.details.get('eye_feature_indices',np.flatnonzero(model.eye_arrays['net__input_weights'].reshape(-1))));self.policy=IntentPolicy(model.details['intent_policy']);self.latch=MovementLatch(model.details['eye_policy'])
        from .observation import SessionObservation
        self.observer=SessionObservation()
    def _hop(self,hop,disable_eye):
        import torch
        from .quality import inspect_signal
        from .ocular_event_training import centered
        self.raw_history=np.concatenate((self.raw_history,hop),1)[:,-500:];q=inspect_signal(self.raw_history[:,-375:],CHANNELS,250,ELECTRICAL) if self.raw_history.shape[1]>=375 else dict(electrical_valid=True,prewarm=True,contact_quality_known=False)
        if not q['electrical_valid']:raise ValueError('Electrical quality rejected; hybrid state reset')
        f,h,ready=self.nav.step(hop);a=self.model.arrays;details=self.model.details;f=feature_view(f,len(self.nav.indices),details.get('feature_mode','absolute'));nav=np.concatenate((f,h)) if details['navigation_features']=='joint' else f
        scores,activity,direction=evidence(nav,a,'nav__',details['navigation_architecture'],details['navigation_temperatures'])
        eye_x=(centered(self.raw_history)-self.model.eye_arrays['input_mean'])/self.model.eye_arrays['input_scale']
        with torch.inference_mode():hidden,old=eye_embedding(self.model.eye,torch.as_tensor(eye_x[None],device=self.model.device,dtype=torch.float32))
        ef=self.ocular.step(hop,hidden[0].cpu().numpy());eye,eye_active,eye_direction=evidence(ef,a,'ocular_adapter__','hierarchical',details['ocular_temperatures'])
        n=len(self.ocular.indices);motion=float(np.sqrt(np.mean(((ef[:n]-a['ocular_motion_mean'])/a['ocular_motion_scale'])**2))) if 'ocular_motion_mean' in a else None
        if disable_eye:
            self.policy.cue=self.policy.pending=self.policy.pending_count=0;event=0
        else:event=self.latch.step(float(eye_active),eye_direction,motion)
        if event:self.policy.eye_event(event)
        decision=self.policy.step(float(activity),direction) if ready else dict(direction=0,status='UNKNOWN',cue_direction=self.policy.cue,reason='real_history_prewarm',auxiliary='WARMUP')
        return dict(probabilities=scores.tolist(),motor_scores=scores.tolist(),activity_score=float(activity),conditional_direction_scores=direction.tolist(),eye_scores=eye.tolist(),legacy_eye_scores=old[0].cpu().numpy().tolist(),eye_activity_score=float(eye_active),eye_motion_score=motion,eye_conditional_direction_scores=eye_direction.tolist(),eye_event=event,
            navigation_decision=decision,auxiliary_status=decision['auxiliary'],warming=not ready,history_samples=self.nav.samples,quality=q,observation=self.observer.update(self.nav.samples,decision,event),
            evidence_semantics='learned_feature_fusion_of_multichannel_spectrum_covariance_and_frozen_JEPA',score_semantics='exploratory_cross_fitted_scaling_not_true_intent_probability')
