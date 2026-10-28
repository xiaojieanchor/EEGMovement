"""Direction competition respects each class's frozen eye-adjusted threshold."""
import numpy as np
from .intent_repair import IntentPolicy
from .eye_evidence import EventHybridModel,EventSession

RUNTIME='gnautilus.hybrid_navigation.v4'

class ThresholdIntentPolicy(IntentPolicy):
    def step(self,activity,direction):
        a=float(activity);p=np.asarray(direction,dtype=float);c=self.config
        if not np.isfinite(a) or not 0<=a<=1 or p.shape!=(4,) or not np.isfinite(p).all() or (p<0).any() or not np.isclose(p.sum(),1,atol=1e-5):self.reset();raise ValueError('Finite separated navigation evidence required')
        raworder=np.argsort(p);raw=int(raworder[-1])+1;rawmargin=p[raworder[-1]]-p[raworder[-2]]
        if self.pending:
            independent=a>=c['activity'] and p[raw-1]>=c['direction'] and rawmargin>=c['margin'];self.pending_count=self.pending_count+1 if raw==self.pending and independent else 0
            if self.pending_count>=c['confirm']:self.cue=self.pending;self.pending=self.pending_count=0
        activity_thresholds=np.full(4,c['activity']);direction_thresholds=np.full(4,c['direction'])
        if self.cue:
            activity_thresholds+=c['other_premium'];direction_thresholds+=c['other_premium'];activity_thresholds[self.cue-1]=c['activity']-c['eye_discount'];direction_thresholds[self.cue-1]=c['direction']-c['eye_discount']
        if self.active:activity_thresholds[self.active-1]-=c['hold_discount'];direction_thresholds[self.active-1]-=c['hold_discount']
        activity_thresholds=np.clip(activity_thresholds,.05,.95);direction_thresholds=np.clip(direction_thresholds,.26,.95);slack=p-direction_thresholds;order=np.argsort(slack);d=int(order[-1])+1;margin=float(slack[order[-1]]-slack[order[-2]]);at=float(activity_thresholds[d-1]);dt=float(direction_thresholds[d-1]);strong=a>=at and p[d-1]>=dt and margin>=c['margin'];reason='accepted' if strong else 'activity_below_threshold' if a<at else 'direction_below_threshold' if p[d-1]<dt else 'direction_margin_low'
        if strong:
            self.weak=0
            if self.active!=d:
                self.count=self.count+1 if self.candidate==d else 1;self.candidate=d;self.active=0;reason='confirming'
                if self.count>=c['confirm']:self.active=d;self.candidate=self.count=0;reason='accepted'
            else:self.candidate=self.count=0
        else:
            self.candidate=self.count=0;self.weak+=1
            if self.weak>=c['stop']:self.active=0
        rest=c['rest_activity']-(c['eye_discount'] if self.cue else 0);status='ACTIVE' if self.active else 'REST' if a<=rest else 'UNKNOWN'
        return dict(direction=self.active,status=status,cue_direction=self.cue,pending_direction=self.pending,confirming=self.candidate,activity_score=a,conditional_direction_scores=p.tolist(),activity_threshold=at,direction_threshold=dt,activity_thresholds=activity_thresholds.tolist(),direction_thresholds=direction_thresholds.tolist(),threshold_excess_scores=slack.tolist(),raw_direction=raw,rest_activity_threshold=rest,reason='rest_supported' if status=='REST' else reason,auxiliary='RETURN_OR_SWITCH' if self.pending else 'CUE_MEMORY' if self.cue else 'UNKNOWN',policy='threshold_aware_navigation_v1')

class ThresholdHybridModel(EventHybridModel):
    def new_session(self):return ThresholdSession(self)

class ThresholdSession(EventSession):
    def __init__(self,model):
        super().__init__(model);self.policy=ThresholdIntentPolicy(model.details['intent_policy'])
