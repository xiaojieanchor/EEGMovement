"""Offline decision simulation only; emits no network packet or VR command."""
import math

class EvidenceDecision:
    def __init__(self,confirmations=2,min_confirm_seconds=.1,max_age=.25,threshold=.8):
        if any(type(v) not in [int,float] or not math.isfinite(v) for v in [min_confirm_seconds,max_age,threshold]) or type(confirmations) is not int or confirmations<1 or not 0<=min_confirm_seconds or not max_age>0 or not 0<threshold<=1:raise ValueError('Invalid research decision parameters')
        self.confirmations=confirmations;self.min_seconds=min_confirm_seconds;self.max_age=max_age;self.threshold=threshold
        self.identity=None;self.sample=None;self.candidate=None;self.count=0;self.since=None;self.active=False;self.last_update=None

    def _stop(self,reason):
        self.candidate=None;self.count=0;self.since=None;self.active=False
        return {'active':False,'reason':reason,'offline_only':True}

    def update(self,r,now):
        if type(now) not in [int,float] or not math.isfinite(now):return self._stop('invalid_clock')
        if self.last_update is not None and (now<self.last_update or now-self.last_update>self.max_age):self._stop('update_gap')
        status=r.get('status')
        if status in ['REST','REJECT','UNKNOWN','INVALID','WARMUP']:return self._stop(status.lower())
        produced=r.get('produced_at')
        if type(produced) not in [int,float] or not math.isfinite(produced) or not 0<=now-produced<=self.max_age:return self._stop('stale')
        if status!='OK':return self._stop('invalid_status')
        if r.get('score_semantics')!='calibrated_probability':return self._stop('unsupported_score_semantics')
        p=r.get('probability');sample=r.get('sample_end');identity=(r.get('source_epoch'),r.get('model_id'))
        if type(p) not in [int,float] or not math.isfinite(p) or not 0<=p<=1 or type(sample) is not int or any(v is None for v in identity):return self._stop('invalid_record')
        if identity!=self.identity:self._stop('identity_changed');self.identity=identity;self.sample=None
        if self.sample is not None and sample<self.sample:return self._stop('out_of_order')
        if sample==self.sample:return {'active':self.active,'reason':'duplicate','offline_only':True}
        self.sample=sample;self.last_update=now
        if r.get('label') in ['REST','NC','UNKNOWN','REJECT','INVALID','WARMUP']:return self._stop('nonaction_label')
        if p<self.threshold or not r.get('label'):return self._stop('reject')
        if r['label']!=self.candidate:self.candidate=r['label'];self.count=0;self.since=now
        self.count+=1;self.active=self.count>=self.confirmations and now-self.since>=self.min_seconds
        return {'active':self.active,'label':self.candidate if self.active else None,
                'reason':'supported' if self.active else 'confirming','offline_only':True}
