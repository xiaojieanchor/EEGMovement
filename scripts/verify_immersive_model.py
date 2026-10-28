"""A100 raw-source equivalence and replay export; never fits or executes VR."""
import argparse,copy,json,os,time
from pathlib import Path
import numpy as np
from gnautilus_ml.common import atomic,digest,file_hash,read_json
from gnautilus_ml.artifacts import load_artifact
from gnautilus_ml.experimental_live import SOURCE_ID,WEIGHTS_SHA256,promote
from gnautilus_ml.ocular_boundary_audit import load_contextual_episodes
from gnautilus_ml.personal_study import verify_sources


def run(base,out):
    import torch,subprocess
    import gnautilus_ml
    from gnautilus_ml.governance import check_runtime
    profile=read_json(Path(gnautilus_ml.__file__).resolve().parents[1]/'compute.json');check_runtime(profile)
    torch.set_num_threads(2);base=Path(base);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    prior=base/'logs/event-policy-3f4480bea6f486a3';ds=read_json(prior/'dataset.json')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt'])
    source=base/'models'/SOURCE_ID
    if not source.exists():source=prior/'artifacts'/SOURCE_ID
    before=file_hash(source/'manifest.json');target=promote(source,base/'models')
    original,old=load_artifact(source,device='cuda:0');model,meta=load_artifact(target,device='cuda:0')
    expected={(x['trial_id'],x['source_end']):x for x in read_json(prior/'runtime-frames.json')}
    rows=[];signals={};max_difference=0.;reference_difference=0.;errors=[];timings=[];trial_rows=[]
    for index,trial in enumerate(load_contextual_episodes(ds['parent'],'test',pre=2000,post=750)):
        a=original.new_session();b=model.new_session();values=trial['x'];signals['trial'+str(index)]=values
        metadata=trial['metadata'];trial_rows.append(dict(index=index,id=trial['id'],source_start=metadata['context_start'],samples=values.shape[1]))
        for end in range(25,values.shape[1]+1,25):
            hop=values[:,end-25:end];source_end=metadata['context_start']+end;old_result=None;new_result=None;old_error=None;new_error=None
            try:old_result=a.step(hop)
            except ValueError as e:old_error=str(e)
            started=time.monotonic()
            try:new_result=b.step(hop)
            except ValueError as e:new_error=str(e)
            timings.append((time.monotonic()-started)*1000)
            if old_error!=new_error:errors.append(dict(trial=trial['id'],sample=source_end,reason='exception_differs'))
            if new_result is None:
                rows.append(dict(trial=index,source_end=source_end,session_end=end,result=None,error=new_error));continue
            for key in ('probabilities','eye_scores'):
                max_difference=max(max_difference,float(np.max(np.abs(np.asarray(old_result[key])-new_result[key]))))
            if old_result['navigation_decision']!=new_result['navigation_decision'] or old_result['eye_event']!=new_result['eye_event']:errors.append(dict(trial=trial['id'],sample=source_end,reason='decision_differs'))
            ref=expected[(trial['id'],source_end)]
            for key in ('probabilities','eye_scores'):reference_difference=max(reference_difference,float(np.max(np.abs(np.asarray(ref[key])-new_result[key]))))
            if any(ref['decision'].get(k)!=new_result['navigation_decision'].get(k) for k in ['direction','status','reason','cue_direction']) or ref['eye_event']!=new_result['eye_event']:errors.append(dict(trial=trial['id'],sample=source_end,reason='historical_decision_differs'))
            record=copy.deepcopy(new_result);record.update(model_id=meta['model_id'],source='OFFLINE_REPLAY',runtime_schema=meta['runtime_schema'],worker_ms=timings[-1])
            rows.append(dict(trial=index,source_end=source_end,session_end=end,result=record,error=None))
    assert not errors and max_difference==0 and reference_difference<1e-5,(errors[:3],max_difference,reference_difference)
    assert file_hash(source/'manifest.json')==before and file_hash(target/'weights.npz')==WEIGHTS_SHA256
    np.savez_compressed(out/'replay-signals.npz',**signals)
    atomic(out/'replay.json',dict(schema='gnautilus.immersive_replay.v1',source='OFFLINE_REPLAY',independent_test=False,real_device_acceptance=False,
        model=meta,trials=trial_rows,frames=rows,signals_sha256=file_hash(out/'replay-signals.npz')))
    actual=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader'],text=True)
    devices=[line.split(',')[1].strip() for line in actual.splitlines() if line.split(',')[0].strip()==str(os.getpid())]
    assert devices==[profile['gpu_uuid']],devices
    atomic(out/'verification.json',dict(source_model_id=SOURCE_ID,deployment_model_id=meta['model_id'],frames=len(rows),trials=len(trial_rows),
        probability_max_difference=max_difference,historical_probability_max_difference=reference_difference,decision_mismatches=errors,
        original_manifest_unchanged=True,same_weight_bytes=True,independent_test=False,real_device_acceptance=False,
        gpu_uuid=profile['gpu_uuid'],pid=os.getpid(),latency_ms={k:float(np.quantile(timings,q)) for k,q in [('p50',.5),('p95',.95),('p99',.99)]}))
    print(json.dumps(dict(deployment_model_id=meta['model_id'],frames=len(rows),max_difference=max_difference,source='OFFLINE_REPLAY')))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--base',required=True);ap.add_argument('--output',required=True);args=ap.parse_args();run(args.base,args.output)
