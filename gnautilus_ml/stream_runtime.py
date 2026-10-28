"""Source-bound streaming session ownership and idempotent sample advancement."""
import hashlib,time
import numpy as np
from .common import digest
from .navigation_contract import CHANNELS
from .data import LABELS
from .quality import inspect_signal


def signal_identity(d):
    c=d['configuration']
    return dict(device_serial=d['device_serial'],sampling_rate=d['sampling_rate'],reference=d.get('reference'),ground=d.get('ground'),
                input_signal=c.get('InputSignal'),car=c.get('CAR'),noise_reduction=c.get('NoiseReduction'),
                eeg_configuration=[{k:v for k,v in row.items() if k in ['BandpassFilterIndex','NotchFilterIndex','BipolarChannel','Sensitivity','Enabled','UsedForCar','UsedForNoiseReduction']} for row in c['Channels'][:32]],
                filters=c.get('filters'))


def contract_from_descriptor(d):
    if d['sampling_rate']!=250 or any(row.get('BipolarChannel')!=-1 for row in d['configuration']['Channels'][:32]):raise ValueError('Expected recorded 32-channel unipolar contract')
    return dict(channels=CHANNELS,sampling_rate=250,reference=d.get('reference'),ground=d.get('ground'),
                reference_semantics='as_recorded_dedicated_REF_physical_site_unrecorded',
                signal_identity=signal_identity(d),signal_identity_sha256=digest(signal_identity(d)),
                filter_configuration=d['configuration'],position_identity='operator_attested_swap16_19',
                unknown_reference_site=True)


def validate_live(d,meta):
    contract=meta['input_contract'];eeg=[c for c in d['channels'] if c['kind']=='EEG']
    if d.get('source_kind')!='gtec_live' or len(eeg)!=32 or [c.get('physical_index') for c in eeg]!=list(range(32)) or any(c.get('unit')!='uV' for c in eeg):raise ValueError('Real ordered physical 32-channel uV input required')
    names=[c['name'] for c in eeg]
    if names!=CHANNELS:
        # Old raw descriptors remain immutable. Only their literal CH01..CH32
        # aliases can be resolved by this explicit operator-confirmed sidecar.
        a=meta.get('montage_attestation',{})
        if names!=[f'CH{i:02}' for i in range(1,33)] or a.get('channels')!=CHANNELS or a.get('source')!='explicit_user_confirmation':raise ValueError('Live montage differs from confirmed physical mapping')
    elif not d.get('layout_verified'):raise ValueError('Named live montage must be verified')
    if digest(signal_identity(d))!=contract['signal_identity_sha256']:raise ValueError('Live rate/reference/device/filter contract changed')


def predict_stream(service,data,started=None):
    started=started or time.monotonic();meta=service.manifest;d=data['descriptor']
    try:validate_live(d,meta)
    except Exception:
        service.reset_stream();raise
    x=np.asarray(data['samples'],dtype='float32');a=data.get('sample_index_start');b=data.get('sample_index_end')
    if x.ndim!=2 or x.shape[0]!=32 or not 1<=x.shape[1]<=2000 or type(a) is not int or type(b) is not int or b-a!=x.shape[1] or a<0 or not np.isfinite(x).all():
        service.reset_stream();raise ValueError('Invalid incremental sample range/values; state reset')
    identity=(data['stream_id'],data['source_epoch']);signature=hashlib.sha256(x.tobytes()+digest(d).encode()).hexdigest()
    token=(identity,a,b)
    if getattr(service,'stream_last',None)==token:
        if service.stream_hash!=signature:
            service.reset_stream();raise ValueError('Duplicate identity has changed signal bytes')
        # Replayed replies never renew an inference lease or movement ticket.
        return dict(service.last,eligible=False,duplicate=True,rejection_reason='duplicate_no_new_evidence')
    if getattr(service,'stream_identity',None) is not None:
        if service.stream_identity!=identity or a!=service.stream_end:
            reason='identity_changed' if service.stream_identity!=identity else 'sample_gap_or_reorder'
            service.reset_stream();raise ValueError(reason+'; restart stream prewarm')
    if data.get('device_valid') is False:
        service.reset_stream();raise ValueError('Device quality rejected; state reset')
    if getattr(service,'stream_session',None) is None:service.stream_session=service.model.new_session()
    s=service.stream_session
    # QC sees only arrived samples; reject the current update before neural advance.
    fixed_hops=meta.get('runtime_schema') in ['gnautilus.causal_jepa_stream.v2','gnautilus.hybrid_navigation.v1','gnautilus.hybrid_navigation.v2','gnautilus.hybrid_navigation.v3','gnautilus.hybrid_navigation.v4','gnautilus.hybrid_navigation.v5','gnautilus.hybrid_navigation.v6','gnautilus.hybrid_navigation.v7']
    if not fixed_hops:
        history=np.concatenate((s.raw_history,x),1)[:,-375:]
        q=inspect_signal(history,CHANNELS,250,meta['electrical_policy']) if history.shape[1]>=375 else dict(electrical_valid=True,reasons=[],contact_quality_known=False,prewarm=True)
        if not q['electrical_valid']:
            service.reset_stream();raise ValueError('Electrical quality rejected; state reset: '+str(q['reasons']))
    try:result=s.step(x)
    except Exception:service.reset_stream();raise
    if fixed_hops:q=result['quality']
    new_prediction=result.get('new_prediction_updates',1)>0
    eligible=new_prediction and not result['warming'] and result['navigation_decision']['status']!='UNKNOWN';decision=result['navigation_decision']['direction']
    result.update(model_id=meta['model_id'],generation=service.generation,stream_id=identity[0],source_epoch=identity[1],sample_index_start=a,sample_index_end=b,
                  prediction=LABELS[decision] if eligible else 'UNKNOWN',raw_prediction=LABELS[int(np.argmax(result['probabilities']))],eligible=eligible,
                  quality=q,worker_ms=(time.monotonic()-started)*1000,runtime_schema=meta['runtime_schema'],
                  rejection_reason=None if eligible else 'real_history_prewarm' if result['warming'] else 'waiting_for_prediction_hop' if not new_prediction else 'navigation_evidence_rejected',duplicate=False)
    if fixed_hops:result['prediction_sample_index_end']=b-result['pending_samples']
    service.stream_identity=identity;service.stream_end=b;service.stream_last=token;service.stream_hash=signature
    service.last_identity=(identity[0],identity[1],b);service.last=result;service.lease=time.monotonic()
    return result
