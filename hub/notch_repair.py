"""Line-noise-only admission after real causal-notch and native rechecking.

Raw samples stay raw on disk and on the model wire. Training and the A100
IntentStream apply the identical 50Hz/Q30 notch exactly once before encoding.
"""
import math

POLICY='neuradock_notch50_rechecked_v2'

def rechecked_quality(raw_quality,filtered_quality,filtered_native,warm_samples):
    result=dict(raw_quality);channels=[];mask=[]
    raw_channels=raw_quality.get('channels',[]);post=filtered_quality.get('channels',[]);verified=filtered_native.get('channels',[])
    if len(raw_channels)!=7 or len(post)!=7 or len(verified)!=7:return result
    for i,(raw,clean,native) in enumerate(zip(raw_channels,post,verified)):
        c=dict(raw);reasons=raw.get('reasons')
        line_candidate=(raw.get('native_code')==0 or raw.get('native_code')==1 and reasons==['50Hz干扰'])
        raw_safe=isinstance(reasons,list) and not (set(reasons)-{'50Hz干扰'}) and raw.get('native_code') in [0,1]
        before=raw.get('line_power_uv2');after=clean.get('line_power_uv2')
        suppression=(10*math.log10(max(before,1e-20)/max(after,1e-20))
            if type(before) in [int,float] and type(after) in [int,float] and math.isfinite(before) and math.isfinite(after) and before>=0 and after>=0 else None)
        repaired=bool(line_candidate and raw_safe and warm_samples>=500 and clean.get('valid') is True
            and native.get('native_code')==1 and native.get('training_eligible') is True
            and suppression is not None and suppression>=10 and clean.get('line_ratio',1)<.2)
        c.update(line_repaired=repaired,raw_native_code=raw.get('native_code'),raw_reasons=list(reasons or []),
            raw_rms_uv=raw.get('rms_uv'),raw_line_ratio=raw.get('line_ratio'),native_filtered_code=native.get('native_code'),
            raw_line_power_uv2=before,
            post_filter=dict(clean),line_suppression_db=round(suppression,2) if suppression is not None else None,
            raw_safety_passed=raw_safe,repair_validated=repaired,
            signal_used='notch50_rechecked' if repaired else 'standard_model_preprocessing')
        if repaired:
            c.update(valid=True,reasons=[],color='green',quality_label='50Hz 已滤除 · 复检通过',training_eligible=True,
                rms_uv=clean['rms_uv'],peak_uv=clean['peak_uv'],line_ratio=clean['line_ratio'],high_ratio=clean['high_ratio'],line_power_uv2=clean['line_power_uv2'])
        elif line_candidate:
            c['quality_label']='50Hz 滤波预热／复检中' if warm_samples<500 or native.get('color')=='amber' else '50Hz 滤后未通过，排除'
        channels.append(c);mask.append(c.get('training_eligible') is True)
    result.update(channels=channels,training_mask=mask,training_good_channels=sum(mask),good_channels=sum(mask),valid=all(mask),
        raw_software_valid=raw_quality.get('valid'),reasons=sorted({r for c in channels if not c['training_eligible'] for r in c.get('reasons',[])}),
        notch50=dict(applied=True,frequency_hz=50,q=30,causal=True,warm_samples=warm_samples,
            repaired_channels=[i+1 for i,c in enumerate(channels) if c['line_repaired']],
            model_input='raw_wire_then_identical_notch_once_on_server_and_training',min_suppression_db=10),
        policy=POLICY,policy_revision='line_only_notch50_native_recheck_v3')
    return result
