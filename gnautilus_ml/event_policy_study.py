"""Governed postprocessor selection; frozen neural weights, zero gradient updates."""
import time,itertools
from pathlib import Path
import numpy as np
from .common import read_json,atomic,digest,file_hash,within
from .intent_repair import MovementLatch,IntentPolicy,factor_scores
from .intent_repair_training import policy_metrics
from .eye_evidence import EyeEvidenceLatch,score_events,RUNTIME,EventHybridModel

SCHEMA='gnautilus.event_policy_study.v1'

def recipes():
    result=[dict(name='legacy_consecutive',config=None)]
    peaks=[(None,None,None)]+list(itertools.product([.7,.85],[.55,.7],[2.5,4.]))
    for window,cool,peak in itertools.product([3,5],[4,6],peaks):
        a,d,m=peak;c=dict(window_updates=window,votes=2,cooldown_updates=cool,quiet_updates=2,fast_activity=a,fast_direction=d,fast_motion=m);result.append(dict(name='bounded-'+str(len(result)),config=c))
    return result

def replay(frames,navp,eyep,motion,ds,details,recipe,disabled=False):
    na,nd=factor_scores(navp);ea,ed=factor_scores(eyep);previous=None;rows=[];predicted=[]
    for f in frames:
        if previous is None or previous['trial_id']!=f['trial_id'] or not f['quality_valid']:
            policy=IntentPolicy(details['intent_policy']);latch=MovementLatch(details['eye_policy']) if recipe['config'] is None else EyeEvidenceLatch(details['eye_policy'],recipe['config'])
        previous=f;i=f['cache_index'];event=0;decision=dict(direction=0,status='UNKNOWN',reason='electrical_quality' if i<0 else 'real_history_prewarm')
        if i>=0:
            if not disabled:event=latch.step(float(ea[i]),ed[i],float(motion[i]))
            if event:
                policy.eye_event(event)
                if f['stream_id']!='official_rest_hdf5':predicted.append(dict(trial_id=f['trial_id'],source_end=f['source_end'],direction=event))
            if not f['warming']:decision=policy.step(float(na[i]),nd[i])
        rows.append(dict(trial_id=f['trial_id'],source_end=f['source_end'],label=f['navigation_label'],quality_valid=f['quality_valid'],warming=f['warming'],decision=decision,eye_event=event))
    return dict(eye=score_events(ds,predicted),navigation=policy_metrics(rows)),rows

def select_policy(frames,navp,eyep,motion,ds,details=None,candidates=None):
    from .governance import authorize_training
    authorize_training();results=[]
    for recipe in candidates:
        stats,_=replay(frames,navp,eyep,motion,ds,details,recipe);results.append(dict(recipe=recipe,stats=stats))
    baseline=results[0];b=baseline['stats'];eligible=[r for r in results if r['stats']['eye']['explicit_quiet_triggers']<=b['eye']['explicit_quiet_triggers'] and r['stats']['navigation']['false_starts_on_software_REST']<=b['navigation']['false_starts_on_software_REST'] and r['stats']['navigation']['wrong_accepted_macro']<=b['navigation']['wrong_accepted_macro']+.01]
    winner=max(eligible,key=lambda r:(r['stats']['eye']['strict_f1'],r['stats']['eye']['strict_matches'],-r['stats']['eye']['unmatched_scoped_triggers'],r['stats']['navigation']['balanced_accuracy']))
    if winner['stats']['eye']['strict_f1']<b['eye']['strict_f1']+.01:winner=baseline
    return dict(winner=winner,baseline=baseline,candidates=results,independent_test=False,selection='held-role strict event F1; quiet/REST errors and navigation error guard; baseline retained unless F1 gain >= .01')

def make_protocol(ds,root,sources,prior):
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},sources=sources,scope='same_wearing_exploratory_postprocessor_study',independent_test=False,neural_gradient_updates=0,seed=17,candidates=recipes(),selection='held-role strict event F1; quiet/REST errors and navigation error guard; baseline retained unless F1 gain >= .01',navigation_thresholds='inherited_unchanged',head_thresholds='inherited_unchanged',prior_elapsed_s=float(prior),max_wall_seconds=1200,total_budget_seconds=43200)

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Human annotation source changed')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Locked source/recipe/budget differs')
    base=Path(ds['parent']['folder']).parent.parent
    for source in p['sources'].values():
        if file_hash(within(base/source['path'],base))!=source['sha256']:raise ValueError('Frozen postprocessor input changed')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def run(base,root,p,ds,profile,out):
    from .governance import training_authority,compute_device
    from .artifacts import load_artifact,save_artifact
    out=Path(out);start=time.monotonic()
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();source=Path(base)/p['sources']['manifest']['path'];model,meta=load_artifact(source.parent,device=device);frames=read_json(Path(base)/p['sources']['frames']['path'])
        with np.load(Path(base)/p['sources']['predictions']['path'],allow_pickle=False) as z:cache={k:z[k].copy() for k in z.files}
        frozen=select_policy(frames,cache['navigation_oof'],cache['eye_oof'],cache['motion_oof'],ds,model.details,p['candidates']);atomic(out/'selection-freeze.json',frozen)
        if time.monotonic()-start>p['max_wall_seconds']:raise InterruptedError('Postprocessor budget exhausted')
        winner=frozen['winner']['recipe'];actual,rows=replay(frames,cache['navigation'],cache['eye'],cache['motion_final'],ds,model.details,winner);baseline,_=replay(frames,cache['navigation'],cache['eye'],cache['motion_final'],ds,model.details,frozen['baseline']['recipe']);disabled,_=replay(frames,cache['navigation'],cache['eye'],cache['motion_final'],ds,model.details,winner,True)
        artifact=None
        if winner['config'] is not None:
            details=dict(model.details,eye_event_config=winner['config'],eye_event_schema='gnautilus.bounded_eye_events.v1');new=EventHybridModel(model.arrays,details,device);evidence={k:v for k,v in meta.items() if k not in ['model_id','details','weights_sha256','schema','runtime_schema']};evidence.update(runtime_schema=RUNTIME,event_policy_protocol_sha256=digest(p),event_policy_code_sha256=p['code_sha256'],source_model_id=meta['model_id'],event_policy_optimization_updates=0,runtime_accepted=False,independent_test=False);evidence['metrics']=dict(meta.get('metrics',{}),continuous_replay=actual['navigation'],eye_events=actual['eye']);artifact=save_artifact(out/'artifacts',new,evidence);_,confirmed=load_artifact(artifact,device=device)
        report=dict(schema=SCHEMA,source_model=meta['model_id'],winner=winner,artifact_path=str(artifact) if artifact else None,model_id=confirmed['model_id'] if artifact else meta['model_id'],weights_sha256=confirmed['weights_sha256'] if artifact else meta['weights_sha256'],same_neural_weights=not artifact or confirmed['weights_sha256']==meta['weights_sha256'],actual=actual,baseline=baseline,disabled=disabled,held_role=frozen['winner']['stats'],held_role_baseline=frozen['baseline']['stats'],candidates=len(p['candidates']),neural_gradient_updates=0,elapsed_s=time.monotonic()-start,independent_test=False,real_hardware_acceptance=False)
        atomic(out/'replay-frames.json',rows);atomic(out/'report.json',report);print({k:v for k,v in report.items() if k not in ['actual','baseline','disabled','held_role','held_role_baseline']});return report

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for name in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+name,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
