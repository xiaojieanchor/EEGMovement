"""Governed operating-point comparison after frozen readout score calibration."""
import itertools,time,copy
from pathlib import Path
import numpy as np
from .common import read_json,atomic,digest,file_hash,within
from .navigation_stability_study import replay
SCHEMA='gnautilus.readout_policy_study.v1'

def make_protocol(ds,root,sources,prior):
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},sources=sources,candidates=[dict(activity=a,direction=d,rest_activity=r) for a,d,r in itertools.product([.55,.65,.75],[.3,.4],[.4,.5])],selection='held-role macro improves >= .01; no additional software REST starts; wrong accepted macro <= source + .01',score_weights='frozen',neural_gradient_updates=0,scope='same_wearing_exploratory_frozen_readout_operating_point',independent_test=False,prior_elapsed_s=float(prior),max_wall_seconds=1200,total_budget_seconds=43200)

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Manual intervals changed')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Operating point protocol/budget differs')
    base=Path(ds['parent']['folder']).parent.parent
    for s in p['sources'].values():
        if file_hash(within(base/s['path'],base))!=s['sha256']:raise ValueError('Frozen readout/source score file changed')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def run(base,root,p,ds,profile,out):
    from .governance import training_authority,compute_device
    from .artifacts import load_artifact,save_artifact
    from .spectral_navigation import SpectralHybridModel,RUNTIME
    start=time.monotonic();out=Path(out)
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        model,meta=load_artifact((Path(base)/p['sources']['manifest']['path']).parent,device=compute_device());assert meta['runtime_schema']==RUNTIME
        frames=read_json(Path(base)/p['sources']['frames']['path'])
        with np.load(Path(base)/p['sources']['predictions']['path'],allow_pickle=False) as z:cache={k:z[k].copy() for k in z.files}
        def evaluate(details,suffix):return replay(frames,cache['navigation'+suffix],cache['eye'+suffix],cache['motion_oof' if suffix else 'motion_final'],details,'raw_argmax')
        baseline,_=evaluate(model.details,'_oof');items=[]
        for c in p['candidates']:
            if time.monotonic()-start>p['max_wall_seconds']:raise InterruptedError('Operating-point study budget exhausted')
            details=copy.deepcopy(model.details);details['intent_policy'].update(c);nav,_=evaluate(details,'_oof');items.append(dict(config=c,navigation=nav));atomic(out/'candidates.json',items)
        eligible=[q for q in items if q['navigation']['balanced_accuracy']>=baseline['balanced_accuracy']+.01 and q['navigation']['false_starts_on_software_REST']<=baseline['false_starts_on_software_REST'] and q['navigation']['wrong_accepted_macro']<=baseline['wrong_accepted_macro']+.01]
        selected=max(eligible,key=lambda q:q['navigation']['balanced_accuracy']-.5*q['navigation']['wrong_accepted_macro']) if eligible else dict(config={k:model.details['intent_policy'][k] for k in ['activity','direction','rest_activity']},navigation=baseline)
        atomic(out/'selection-freeze.json',dict(selected=selected,admitted=bool(eligible),baseline=baseline,independent_test=False));details=copy.deepcopy(model.details);details['intent_policy'].update(selected['config']);actual,rows=evaluate(details,'');artifact=None;identifier=meta['model_id']
        if eligible:
            evidence={k:v for k,v in meta.items() if k not in ['model_id','details','schema','weights_sha256']};evidence.update(source_model_id=meta['model_id'],operating_point_protocol_sha256=digest(p),operating_point_code_sha256=p['code_sha256'],operating_point_optimization_updates=0,runtime_accepted=False,independent_test=False);evidence['metrics']=dict(meta['metrics'],continuous_replay=actual);artifact=save_artifact(out/'artifacts',SpectralHybridModel(model.arrays,details,compute_device()),evidence);_,confirmed=load_artifact(artifact,device=compute_device());identifier=confirmed['model_id']
        result=dict(schema=SCHEMA,source_model=meta['model_id'],model_id=identifier,artifact_path=str(artifact) if artifact else None,weights_sha256=meta['weights_sha256'],admitted=bool(eligible),baseline_navigation=baseline,selected=selected,actual_navigation=actual,neural_gradient_updates=0,elapsed_s=time.monotonic()-start,independent_test=False,real_hardware_acceptance=False);atomic(out/'replay-frames.json',rows);atomic(out/'report.json',result);return result

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for n in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+n,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    print(run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output)))
if __name__=='__main__':main()
