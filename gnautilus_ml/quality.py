"""Electrical observations are not contact impedance. Policies are explicit inputs."""
import numpy as np

def inspect_signal(x,channels,fs,policy):
    x=np.asarray(x,dtype=float);reasons=[];rows=[]
    keys={'rail_uv','flat_ptp_uv','max_rms_uv','max_line_ratio'}
    if not isinstance(policy,dict) or not keys<=set(policy):return dict(electrical_valid=False,contact_quality_known=False,reasons=['electrical_policy_not_configured'],channels=[])
    if any(type(policy[k]) not in [float,int] or not np.isfinite(policy[k]) or policy[k]<=0 for k in keys) or policy['max_line_ratio']>1:raise ValueError('Invalid electrical policy values')
    if x.ndim!=2 or x.shape[0]!=len(channels) or x.shape[1]<3:raise ValueError('Quality input must be channels x history')
    freq=np.fft.rfftfreq(x.shape[1],1/fs)
    for name,values in zip(channels,x):
        why=[]
        if not np.isfinite(values).all():why.append('nonfinite')
        else:
            centered=values-np.median(values);power=np.abs(np.fft.rfft(centered))**2
            line=float(power[(freq>=48)&(freq<=52)].sum()/(power[(freq>=1)&(freq<=min(100,fs/2))].sum()+1e-20))
            if np.max(np.abs(values))>=policy['rail_uv']:why.append('rail')
            if np.ptp(values)<=policy['flat_ptp_uv']:why.append('flatline')
            if np.sqrt(np.mean(centered**2))>policy['max_rms_uv']:why.append('rms')
            if line>policy['max_line_ratio']:why.append('line_noise')
        rows.append(dict(name=name,valid=not why,reasons=why));reasons.extend(name+':'+s for s in why)
    return dict(electrical_valid=not reasons,contact_quality_known=False,reasons=reasons,channels=rows,policy='gnautilus.electrical.v1')
