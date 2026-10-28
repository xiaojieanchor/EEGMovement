"""Locked stability experiments: old/new competition on identical frozen scores."""
import time
from pathlib import Path
import numpy as np
from .common import atomic,read_json,digest,file_hash,within
from .intent_repair import IntentPolicy,factor_scores
from .eye_evidence import EyeEvidenceLatch
from .threshold_competition import ThresholdIntentPolicy,ThresholdHybridModel,RUNTIME
from .intent_repair_training import policy_metrics

SCHEMA='gnautilus.navigation_stability_study.v1'

def replay(frames,navp,eyep,motion,details,variant):
    na,nd=factor_scores(navp);ea,ed=factor_scores(eyep);last=None;rows=[]
    for f in frames:
        if last is None or last['trial_id']!=f['trial_id'] or not f['quality_valid']:
            policy=(ThresholdIntentPolicy if variant=='threshold_aware' else IntentPolicy)(details['intent_policy']);latch=EyeEvidenceLatch(details['eye_policy'],details['eye_event_config'])
        last=f;i=f['cache_index'];event=0;decision=dict(direction=0,status='UNKNOWN',reason='electrical_quality' if i<0 else 'real_history_prewarm')
        if i>=0:
            event=latch.step(float(ea[i]),ed[i],float(motion[i]))
            if event:policy.eye_event(event)
            if not f['warming']:decision=policy.step(float(na[i]),nd[i])
        rows.append(dict(trial_id=f['trial_id'],source_end=f['source_end'],label=f['navigation_label'],warming=f['warming'],quality_valid=f['quality_valid'],decision=decision,eye_event=event))
    return policy_metrics(rows),rows

def make_protocol(ds,root,sources,prior):
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},sources=sources,variants=['raw_argmax','threshold_aware'],scope='same_wearing_exploratory_control_comparison',neural_gradient_updates=0,independent_test=False,selection='held-role macro recall improves, no additional REST starts, wrong accepted macro increases <= .01',thresholds='all inherited unchanged',prior_elapsed_s=float(prior),max_wall_seconds=1200,total_budget_seconds=43200,seed=17)

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Frozen manual source changed')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Locked navigation recipe/source/budget differs')
    base=Path(ds['parent']['folder']).parent.parent
    for s in p['sources'].values():
        if file_hash(within(base/s['path'],base))!=s['sha256']:raise ValueError('Frozen score/model input changed')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def run(base,root,p,ds,profile,out):
    from .governance import training_authority,compute_device
    from .artifacts import load_artifact,save_artifact
    start=time.monotonic();out=Path(out)
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();source=Path(base)/p['sources']['manifest']['path'];model,meta=load_artifact(source.parent,device=device);frames=read_json(Path(base)/p['sources']['frames']['path'])
        with np.load(Path(base)/p['sources']['predictions']['path'],allow_pickle=False) as z:cache={k:z[k].copy() for k in z.files}
        held={}
        for variant in p['variants']:held[variant],_=replay(frames,cache['navigation_oof'],cache['eye_oof'],cache['motion_oof'],model.details,variant)
        old=held['raw_argmax'];new=held['threshold_aware'];admit=new['balanced_accuracy']>old['balanced_accuracy'] and new['false_starts_on_software_REST']<=old['false_starts_on_software_REST'] and new['wrong_accepted_macro']<=old['wrong_accepted_macro']+.01;freeze=dict(held_role=held,recommended='threshold_aware' if admit else 'raw_argmax',admitted=admit,independent_test=False);atomic(out/'selection-freeze.json',freeze)
        fitted={};traces={}
        for variant in p['variants']:fitted[variant],traces[variant]=replay(frames,cache['navigation'],cache['eye'],cache['motion_final'],model.details,variant)
        details=dict(model.details,direction_competition='margin_above_each_class_threshold_v1');proposed=ThresholdHybridModel(model.arrays,details,device);evidence={k:v for k,v in meta.items() if k not in ['model_id','details','weights_sha256','schema','runtime_schema']};evidence.update(runtime_schema=RUNTIME,source_model_id=meta['model_id'],control_protocol_sha256=digest(p),control_code_sha256=p['code_sha256'],control_optimization_updates=0,runtime_accepted=False,independent_test=False);evidence['metrics']=dict(meta.get('metrics',{}),continuous_replay=fitted['threshold_aware']);path=save_artifact(out/'artifacts',proposed,evidence);_,confirmed=load_artifact(path,device=device)
        result=dict(schema=SCHEMA,source_model=meta['model_id'],model_id=confirmed['model_id'],weights_sha256=confirmed['weights_sha256'],same_neural_weights=confirmed['weights_sha256']==meta['weights_sha256'],artifact_path=str(path),held_role=held,fitted=fitted,admitted=admit,recommended=freeze['recommended'],neural_gradient_updates=0,elapsed_s=time.monotonic()-start,independent_test=False,real_hardware_acceptance=False)
        atomic(out/'replay-frames.json',traces['threshold_aware']);atomic(out/'report.json',result);print({k:v for k,v in result.items() if k not in ['fitted','held_role']});return result

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for name in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+name,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
