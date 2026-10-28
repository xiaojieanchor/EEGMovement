"""Read-only immersive projection and continuous native-rate electrical observations."""
import collections,copy,math,threading,time
import numpy as np
from gnautilus_ml.common import digest
from gnautilus_ml.quality import inspect_signal
from gnautilus_ml.stream_runtime import validate_live


def receiver_fresh(peer):
    t=(peer or {}).get('telemetry') or {};age=(peer or {}).get('telemetry_age_ms')
    return bool(peer and peer.get('role')=='unity' and peer.get('online') and isinstance(age,(int,float)) and 0<=age<300
        and peer.get('applied_seq',0)>0 and t.get('state') in ('STOP','MOVING') and t.get('mode') in ('LIVE','TEST')
        and all(isinstance(t.get(k),(int,float)) and math.isfinite(t[k]) for k in ('actual_speed','actual_yaw_rate')))


class ImmersiveWorkspace:
    def __init__(self,hub):
        self.hub=hub;self.lock=threading.RLock();self.observed={};self.last_key=None
        self.arrivals=collections.deque(maxlen=60);self.identity=None
    def model(self):
        m=self.hub.ml;mid=m.cached.get('loaded')
        return next((x for x in m.cached.get('models',[]) if x['model_id']==mid),None)
    def compute_fresh(self):
        m=self.hub.ml;s=m.bridge.status
        return bool(m.updated and time.time()-m.updated<8 and not m.error and s.get('ssh_connected') and s.get('worker_connected'))
    def loop(self):
        while not self.hub.stop_event.wait(.1):
            try:self.observe()
            except (ValueError,KeyError,TypeError,IndexError) as error:
                with self.lock:self.observed=dict(state='unknown',valid=False,reasons=[str(error)])
    def observe(self):
        h=self.hub;meta=self.model()
        with h.lock:
            d=copy.deepcopy(h.gnautilus_runtime.descriptor);epoch=h.epoch;end=h.sample_index
            if not d or not meta or not h.gnautilus_runtime.fresh():return
            rate=d['sampling_rate'];count=math.ceil(rate*1.5);ix=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG']
            policy=meta.get('electrical_policy') or {};key=(epoch,end,meta['model_id'],digest(policy))
            if key==self.last_key:return
            rows=list(h.raw)[-count:];data=np.asarray(rows,dtype=float)
        names=meta.get('channels') or [d['channels'][i]['name'] for i in ix]
        unknown=dict(state='warming',valid=False,channels=[],good_channels=0,bad_channels=0,unknown_channels=len(ix),reasons=['quality_history_warming'])
        identity=(epoch,d['stream_id'])
        if identity!=self.identity:self.arrivals.clear();self.identity=identity
        self.arrivals.append((end,time.monotonic()))
        measured=None
        if len(self.arrivals)>1 and self.arrivals[-1][1]-self.arrivals[0][1]>=1:
            measured=(end-self.arrivals[0][0])/(self.arrivals[-1][1]-self.arrivals[0][1])
        if len(ix)!=len(names) or any(d['channels'][i].get('unit')!='uV' for i in ix):
            q=dict(unknown,state='unknown',reasons=['ordered_uV_channels_required'])
        elif len(rows)<count:q=unknown
        else:
            q=inspect_signal(data[:,ix].T,names,rate,policy)
            if not q['channels']:q=dict(unknown,state='unknown',reasons=q['reasons'])
            else:
                good=sum(c['valid'] for c in q['channels']);q.update(state='valid' if q['electrical_valid'] else 'invalid',valid=q['electrical_valid'],good_channels=good,bad_channels=len(ix)-good,unknown_channels=0)
                for row,i in zip(q['channels'],ix):row.update(physical_index=d['channels'][i].get('physical_index',i),unit='uV',kind='EEG')
        validation=[i for i,c in enumerate(d['channels']) if c['kind']=='VALIDATION']
        device_valid=bool(np.all(data[:,validation]==1)) if validation and len(data) else None
        if device_valid is False:q.update(valid=False,state='invalid',reasons=q['reasons']+['device_invalid'])
        q.update(source_epoch=epoch,stream_id=d['stream_id'],sample_index_start=end-len(rows),sample_index_end=end,
            policy_sha256=digest(policy),model_id=meta['model_id'],checked_monotonic=time.monotonic(),window_s=1.5,
            contact_quality_known=False,device_valid=device_valid,configured_rate=rate,arrival_rate=measured)
        with h.lock:
            if epoch!=h.epoch or self.model() is None or meta['model_id']!=self.model()['model_id']:return
        with self.lock:self.observed=q;self.last_key=key
    def quality(self):
        h=self.hub;meta=self.model()
        with self.lock:q=copy.deepcopy(self.observed)
        d=h.gnautilus_runtime.descriptor or {};width=sum(c['kind']=='EEG' for c in d.get('channels',[]))
        age=(time.monotonic()-q.get('checked_monotonic',-1e9))*1000
        fresh=bool(h.gnautilus_runtime.fresh() and meta and q.get('model_id')==meta['model_id'] and q.get('source_epoch')==h.epoch and 0<=age<500 and self.compute_fresh())
        q.update(fresh=fresh,age_ms=round(age,1) if age<1e9 else None,required_channels=len(meta.get('channels',[])) if meta else 0,contact_quality_known=False)
        if not fresh:
            q.update(valid=False,state='unknown',good_channels=0,bad_channels=0,unknown_channels=width,
                     channels=[dict(c,valid=None,reasons=['quality_stale']) for c in q.get('channels',[])],reasons=['quality_unknown_or_stale'])
        return q
    def status(self,receiver_id=None):
        h=self.hub;meta=self.model();d=copy.deepcopy(h.gnautilus_runtime.descriptor);compute=self.compute_fresh();q=self.quality()
        vr=h.vr.snapshot();clients=[c for c in vr.get('clients',[]) if c.get('role')=='unity']
        candidates=[c for c in clients if receiver_fresh(c)]
        target=next((c for c in clients if c['receiver_id']==receiver_id),None) if receiver_id else candidates[0] if len(candidates)==1 else None
        reasons=[]
        if not h.gnautilus_runtime.fresh():reasons.append(dict(code='eeg_disconnected',message='等待采集端的新鲜真实 EEG 数据'))
        if not compute:reasons.append(dict(code='compute_disconnected',message='等待身份已核验的私有计算服务'))
        if not meta:reasons.append(dict(code='model_not_loaded',message='尚未加载模型'))
        elif not meta.get('runtime_accepted'):reasons.append(dict(code='offline_model',message='当前模型仅用于研究回放，请加载阶段一实验性 LIVE 版本'))
        match=False;match_reason=None
        if d and meta:
            try:validate_live(d,meta);match=True
            except (ValueError,KeyError,TypeError) as error:match_reason=str(error)
        if not match:reasons.append(dict(code='input_mismatch',message='模型输入尚未匹配：'+(match_reason or '等待实际设备配置')))
        if not receiver_fresh(target):reasons.append(dict(code='receiver_required' if len(candidates)<=1 else 'receiver_selection_required',message='等待目标 Unity 的新鲜执行回执' if len(candidates)<=1 else '多个 VR 在线，请选择目标设备'))
        collecting=bool(h.recorder or h.acquisition.snapshot().get('active'))
        if collecting:reasons.append(dict(code='collection_active',message='正在采集记录；结束采集后再进入推理'))
        intent=h.intent.snapshot();age=(time.monotonic()-h.last_packet)*1000 if h.last_packet else None
        mirror=h.quest.snapshot(target.get('device_id')) if h.quest and target else dict(enabled=bool(h.quest),mirror_fresh=False)
        safe_meta={k:meta.get(k) for k in ('model_id','algorithm','runtime_schema','runtime_accepted','deployment','class_labels','sampling_rate','channels','window_s','hop_s','details')} if meta and compute else None
        models=[dict(id=m['model_id'],title=(m.get('deployment') or {}).get('title') or m.get('dataset_title') or m['model_id'][:12],runtime_accepted=m.get('runtime_accepted',False),deployment=m.get('deployment')) for m in h.ml.cached.get('models',[])]
        return dict(schema='gnautilus.immersive.v1',entry_eligible=not reasons,blockers=reasons,
            can_start=not reasons and q.get('valid') is True and q.get('fresh') is True,
            hub=dict(connected=True,uptime_s=time.monotonic()-h.started),
            source=dict(fresh=h.gnautilus_runtime.fresh(),age_ms=age,source_epoch=h.epoch,sample_index_end=h.sample_index,stream_id=(d or {}).get('stream_id'),configured_rate=(d or {}).get('sampling_rate'),
                        gap_count=h.gnautilus_runtime.gap_count,last_gap=h.gnautilus_runtime.last_gap,transport=h.gnautilus_runtime.active_transport,arrival_rate=q.get('arrival_rate')),
            descriptor=d,quality=q,compute=dict(fresh=compute,roundtrip_ms=h.ml.bridge.status.get('roundtrip_ms'),health=h.ml.bridge.status.get('last_health') if compute else None),
            model=safe_meta,models=models,input_match=match,intent=intent,vr=dict(target=target,clients=clients,ready=vr.get('ready'),mirror=mirror,command_to_ack_p95_ms=vr.get('command_to_ack_p95_ms'),input_to_execution_ack_p95_ms=vr.get('input_to_execution_ack_p95_ms')),
            collecting=collecting,mode='LIVE',scientific_validation=False)
    def signal(self,epoch=None,after=None):
        h=self.hub
        with h.lock:
            d=copy.deepcopy(h.gnautilus_runtime.descriptor);end=h.sample_index;current=h.epoch;rows=list(h.raw);fresh=h.gnautilus_runtime.fresh()
            rate=(d or {}).get('sampling_rate') or 250;rows=rows[-int(rate*3):];begin=end-len(rows)
            reset=epoch!=current or after is None or not begin<=after<=end
            start=begin if reset else after
            values=rows[start-begin:] if fresh else []
        return dict(schema='gnautilus.signal_delta.v1',source_epoch=current,sample_index_start=start,sample_index_end=end,reset=reset,
                    fresh=fresh,descriptor=d,sampling_rate=rate,samples=values,seconds=3)
