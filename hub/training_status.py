"""Read-only training progress; never changes acquisition or LIVE readiness."""
import hashlib,json,time
from .file_io import read_shared_json


def training_snapshot(root):
    result={}
    for name,relative in dict(observer='outputs/training_observer_status.json',acceptance='outputs/resource_acceptance_status.json',stage='outputs/training_stage_status.json',public='outputs/training/public_mi/status.json',capacity='outputs/jepa_capacity_check.json',first_model='outputs/jepa_first_status.json',personal_v2='outputs/personal_v2_status.json',personal_v2_report='outputs/personal_v2_report.json',personal_v2_preparation='outputs/personal_v2_preparation.json',public_ssl='outputs/public_ssl_preparation_status.json',large_jepa='outputs/large_jepa_status.json',fewshot_v3='outputs/fewshot_v3_status.json',fewshot_v3_report='outputs/fewshot_v3_report.json',eye_control='outputs/eye_control_status.json',gaze_lowshot='outputs/gaze_lowshot_status.json',large_personal='outputs/large_personal_status.json',gaze_lowshot_report='outputs/gaze_lowshot_report.json',large_personal_report='outputs/large_personal_report.json',causal_gaze='outputs/causal_gaze_status.json',causal_gaze_report='outputs/causal_gaze_report.json',measured_gaze='outputs/measured_gaze_status.json',public_gaze='outputs/public_gaze_status.json',gaze_transfer='outputs/gaze_transfer_status.json',public_gaze_report='outputs/public_gaze_report.json',gaze_transfer_report='outputs/gaze_transfer_report.json').items():
        try:
            path=root/relative
            if path.stat().st_size>6*1024**2:raise ValueError('Training evidence exceeds bound')
            value=read_shared_json(path)
            if not isinstance(value,dict):raise ValueError('Expected training evidence object')
            result[name]=value
        except (OSError,ValueError,TypeError):result[name]={'state':'unavailable'}
    observer=result['observer'];age=time.time()-observer.get('updated_at',0)
    result['connection_fresh']=observer.get('state')=='watching' and 0<=age<45
    result['completed_snapshot']=observer.get('state')=='complete'
    for name in ['vr_fit80','vr_fit80_report','targeted_gate_context','targeted_gate_context_report','targeted_gate_local','targeted_gate_local_report','lowshot_reset','lowshot_reset_report','retraining_validation','retraining_validation_report','vr_training_replay','vr_training_replay_report','telemetry']:
        try:
            path=root/'outputs'/('training_telemetry.json' if name=='telemetry' else name+'.json' if name.endswith('_report') else name+'_status.json')
            if path.stat().st_size>6*1024**2:raise ValueError('Training evidence exceeds bound')
            value=read_shared_json(path,max_bytes=6*1024**2)
            result[name]=value if isinstance(value,dict) else {'state':'unavailable'}
        except (OSError,ValueError,TypeError):result[name]={'state':'unavailable'}
    result['telemetry_fresh']=result['telemetry'].get('state')=='watching' and 0<=time.time()-result['telemetry'].get('synced_at',0)<25
    result['personal_v2_fresh']=0<=time.time()-result['personal_v2'].get('updated_at',0)<45
    for name in ['vr_fit80','targeted_gate_context','targeted_gate_local','public_ssl','large_jepa','fewshot_v3','eye_control','gaze_lowshot','large_personal','causal_gaze','measured_gaze','public_gaze','gaze_transfer','lowshot_reset','retraining_validation']:result[name+'_fresh']=0<=time.time()-result[name].get('updated_at',0)<60
    try:
        audit=read_shared_json(root/'outputs/lowshot_development_audit.json')
        for row in audit.get('results',[]):
            report=result.get(row.get('job','')+'_report',{})
            digest=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if report.get('directory')==row.get('directory') and digest==row.get('source_report_fingerprint'):
                report['audited_aggregate_selected']=row['aggregate_recomputed']
                report['audit_revision']=audit['revision']
    except (OSError,ValueError,TypeError,KeyError,AttributeError):pass
    result['live_eligible']=False
    return result
