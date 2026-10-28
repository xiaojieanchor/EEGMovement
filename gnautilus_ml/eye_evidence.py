"""Bounded causal eye-event confirmation, with scope-correct offline scoring."""
from collections import deque
import numpy as np
from .intent_repair import RepairedHybridModel,RepairedSession

RUNTIME='gnautilus.hybrid_navigation.v3'

class EyeEvidenceLatch:
    def __init__(self,head,config):
        self.head=dict(head);self.config=dict(config)
        for k,lo,hi in [('window_updates',2,8),('votes',2,8),('cooldown_updates',0,20),('quiet_updates',1,10)]:
            if type(config.get(k)) is not int or not lo<=config[k]<=hi:raise ValueError('Bounded integer eye-event configuration required')
        if config['votes']>config['window_updates']:raise ValueError('Eye votes exceed bounded history')
        self.history=deque(maxlen=config['window_updates']);self.latched=0;self.quiet=0;self.since=100
    def step(self,active,direction,motion=None):
        p=np.asarray(direction,dtype=float)
        if not np.isfinite(active) or not 0<=active<=1 or p.shape!=(4,) or not np.isfinite(p).all() or (p<0).any() or not np.isclose(p.sum(),1,atol=1e-5) or motion is not None and not np.isfinite(motion):
            self.__init__(self.head,self.config);raise ValueError('Invalid eye evidence; state reset')
        h=self.head;c=self.config;order=np.argsort(p);winner=int(order[-1])+1;self.since+=1
        strong=active>=h['activity'] and p[order[-1]]>=h['direction'] and p[order[-1]]-p[order[-2]]>=h['margin'] and (h.get('motion_threshold',0)<=0 or motion is not None and motion>=h['motion_threshold'])
        self.history.append(winner if strong else 0)
        if not strong:
            self.quiet+=1
            if self.quiet>=c['quiet_updates']:self.latched=0
            return 0
        self.quiet=0
        if winner==self.latched or self.since<c['cooldown_updates']:return 0
        fast=c.get('fast_activity') is not None and active>=c['fast_activity'] and p[winner-1]>=c['fast_direction'] and motion is not None and motion>=c['fast_motion']
        if not fast and sum(v==winner for v in self.history)<c['votes']:return 0
        self.latched=winner;self.since=0;self.history.clear();return winner

class EventHybridModel(RepairedHybridModel):
    def new_session(self):return EventSession(self)

class EventSession(RepairedSession):
    def __init__(self,model):
        super().__init__(model);self.latch=EyeEvidenceLatch(model.details['eye_policy'],model.details['eye_event_config'])

def score_events(ds,predicted):
    """Keep context for inference; limit event scoring to each trial's own envelope."""
    trials={t['trial_id']:t for t in ds['catalog']['trials']};truth=[s for s in ds['events']['segments'] if s['kind']=='manual_direction'];quiet=[s for s in ds['events']['segments'] if s['kind']=='manual_absent_head_0_2s'];mapping={'UP':1,'DOWN':2,'LEFT':3,'RIGHT':4};scoped=[];outside=[];quiet_events=[]
    for p in predicted:
        t=trials[p['trial_id']];manual=[s for s in truth if s['trial_id']==p['trial_id']];start=min([t['task_start']]+[s['start'] for s in manual]);end=max([t['task_end']]+[s['end'] for s in manual]);sample=p['source_end']-1
        (scoped if start<=sample<end else outside).append(p)
        if any(s['trial_id']==p['trial_id'] and s['start']<=sample<s['end'] for s in quiet):quiet_events.append(p)
    matched=set();details=[]
    for p in scoped:
        for s in truth:
            if s['segment_id'] not in matched and p['trial_id']==s['trial_id'] and p['direction']==mapping[s['class_label']] and s['start']<=p['source_end']-1<s['end']:
                matched.add(s['segment_id']);details.append(dict(segment_id=s['segment_id'],trial_id=p['trial_id'],direction=p['direction'],source_end=p['source_end'],delay_from_human_range_start_s=(p['source_end']-s['start'])/250));break
    fraction=len(matched)/max(1,len(scoped));recall=len(matched)/max(1,len(truth));f1=2*fraction*recall/max(1e-9,fraction+recall)
    return dict(manual_events=len(truth),strict_matches=len(matched),strict_event_recall=recall,scoped_triggers=len(scoped),unmatched_scoped_triggers=len(scoped)-len(matched),outside_context_triggers=len(outside),explicit_quiet_triggers=len(quiet_events),matching_fraction=fraction,strict_f1=f1,matches=details,predicted=predicted,scope='trial_button_envelope_extended_only_to_its_human_event_bounds',scoring_uses_human_bounds=True,online_uses_trial_bounds=False,independent_test=False)
