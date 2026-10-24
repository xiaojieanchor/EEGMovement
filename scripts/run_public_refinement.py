"""Reviewed public-source subsets, normal preflight/API, persistent idempotency."""
import argparse,getpass,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.ssh import ComputeBridge
from gnautilus_ml.common import digest,atomic,read_json


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan');args=parser.parse_args()
    out=Path(args.plan).resolve().parent if args.plan else ROOT/'docs/gnautilus/improvement_20261024';out.mkdir(parents=True,exist_ok=True)
    plan=dict(schema='gnautilus.public_refinement.v1',id='public-refinement-20261024',source_study='public-mi-pilot-20261023-ef5ec07d-6765fc23',
        source_plan_sha256='ef5ec07d11b9958fd403eb1753fa4c5919fcb29c83bfe1a263a4d1303856a4a6',
        readers=['halt','bci_iv_2a','openbmi','eegmmidb'],personalized_readers=['halt','bci_iv_2a','openbmi'],
        personalized_algorithms=['csp_lda','pipeline_ensemble'],pooled_algorithms=['pipeline_ensemble'],
        selection='all learned spatial filters/scalers/features/temperatures/mixtures/rejection thresholds fit on training or training OOF only',
        outer_role='previously inspected public development sessions; not independent personal acceptance',
        quality='reuse unchanged source-bound pilot quality receipts, exact original trial/window identities',seed=17,max_epochs=1,
        reporting_stability_target=dict(development_ba=.8,minimum_class_recall=.7,continuous_control_acceptance=False,
            inference_start_requirement=False,meaning='Reporting target, not a score prerequisite for manual inference'),
        forbidden=['automatic navigation relabeling','QC relaxation','automatic model activation','automatic motion','test-partition selection'])
    if args.plan:plan=read_json(args.plan)
    plan_id=digest(plan);atomic(out/'plan.json',plan);bridge=ComputeBridge(ROOT);bridge.connect(os.environ.pop('EEG_ADMIN_PASSWORD',None) or getpass.getpass('Compute password: '))
    if not bridge.status['worker_connected']:raise RuntimeError(bridge.status['last_error'])
    base=bridge.profile['root'];study=base+'/studies/'+plan['id']+'-'+plan_id[:12]
    code="""from pathlib import Path
import json,sys,collections
r=Path(BASE);release=r/'releases'/(r/'current-release').read_text().strip();sys.path.insert(0,str(release))
from gnautilus_ml.common import atomic,digest
plan=PLAN;out=Path(STUDY);out.mkdir(parents=True,exist_ok=True);atomic(out/'plan.json',plan)
source=r/'studies'/plan['source_study'];completion=json.loads((source/'quality-completion.json').read_text())
if not completion['completed'] or completion['error_count']:raise ValueError('Source quality review is incomplete')
rows=[]
for reader in plan['readers']:
 original=json.loads((source/(reader+'-dataset.json')).read_text());data=original['dataset'];assert data['study_plan_sha256']==plan['source_plan_sha256']
 options=[(None,data['records'],plan['pooled_algorithms'])]
 if reader in plan['personalized_readers']:
  for subject in sorted({v['subject'] for v in data['records']}):
   records=[v for v in data['records'] if v['subject']==subject]
   if {v['role'] for v in records}=={'train','development'}:options.append((subject,records,plan['personalized_algorithms']))
 for subject,records,algorithms in options:
  d=dict(data,title='公开改进 / '+reader+' / '+('多人共享' if subject is None else '参与者'+str(subject)),records=records,
      refinement_plan_sha256=digest(plan),parent_dataset=original['id'],evaluation_scope=dict(subject=subject,kind='pooled' if subject is None else 'subject_specific',outer='previously_inspected_development',selection='training_oof_only'))
  did=digest(d);atomic(r/'datasets'/(did+'.json'),d)
  rows.append(dict(dataset_id=did,reader=reader,subject=subject,algorithms=algorithms,window_s=json.loads(Path(records[0]['prepared_windows']['report_path']).read_text())['window_s']))
atomic(out/'dataset-selection.json',rows);print(json.dumps(dict(study=str(out),release=str(release),datasets=rows)))
""".replace('BASE',repr(base)).replace('PLAN',repr(plan)).replace('STUDY',repr(study))
    try:
        selection=bridge.command(code,timeout=30);atomic(out/'dataset-selection.json',selection)
        with bridge.client.open_sftp() as s:s.put(str(ROOT/'scripts/check_gnautilus_training.py'),study+'/check_gnautilus_training.py')
        receipts=read_json(out/'jobs.json') if (out/'jobs.json').exists() else []
        for row in selection['datasets']:
            for algorithm,seed in [(algorithm,seed) for algorithm in row['algorithms'] for seed in plan.get('seeds_by_algorithm',{}).get(algorithm,[plan['seed']])]:
                key=digest(dict(plan=plan_id,dataset=row['dataset_id'],algorithm=algorithm,seed=seed))
                request=dict(dataset_id=row['dataset_id'],algorithm=algorithm,window_s=row['window_s'],seed=seed,max_epochs=plan.get('epochs_by_algorithm',{}).get(algorithm,plan['max_epochs']),request_key=key)
                prepared=bridge.call('POST','/jobs/prepare',request);spec=prepared['spec']
                code="""from pathlib import Path
import json,subprocess,os,hashlib
r=Path(BASE);s=Path(STUDY);release=Path(RELEASE);protocol=r/'protocols'/(PID+'.json');checker=s/'check_gnautilus_training.py'
result=subprocess.run([str(r/'.venv/bin/python'),str(checker),'--protocol',str(protocol),'--dataset',str(r/'datasets'/(DID+'.json')),'--code-root',str(release),'--entrypoint',str(release/'gnautilus_ml/runner.py')],capture_output=True,text=True,timeout=240,env={**os.environ,'PYTHONPATH':str(release),'CUDA_VISIBLE_DEVICES':GPU,'OPENBLAS_NUM_THREADS':'2','OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2'})
receipt=dict(returncode=result.returncode,stdout=result.stdout[-3000:],stderr=result.stderr[-2000:],protocol_file_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),checker_sha256=hashlib.sha256(checker.read_bytes()).hexdigest())
(s/('preflight-'+PID+'.json')).write_text(json.dumps(receipt,indent=2))
if result.returncode:raise ValueError(result.stderr[-2000:])
print(json.dumps(receipt))
""".replace('BASE',repr(base)).replace('STUDY',repr(study)).replace('RELEASE',repr(selection['release'])).replace('PID',repr(spec['protocol_id'])).replace('DID',repr(row['dataset_id'])).replace('GPU',repr(bridge.profile['gpu_uuid']))
                previous=next((v for v in receipts if v['request_key']==key),None)
                if previous:continue
                preflight=bridge.command(code,timeout=260);job=bridge.call('POST','/jobs',request)
                receipt=dict(**row,algorithm=algorithm,seed=seed,job_id=job['id'],protocol_id=spec['protocol_id'],request_key=key,preflight=preflight,study=study)
                receipts.append(receipt);atomic(out/'jobs.json',receipts);print(json.dumps(dict(submitted=len(receipts),reader=row['reader'],subject=row['subject'],algorithm=algorithm,job_id=job['id'])),flush=True)
        with bridge.client.open_sftp() as s:s.put(str(out/'jobs.json'),study+'/jobs.json')
        print(json.dumps(dict(study=study,jobs=len(receipts),submission_complete=True)),flush=True)
    finally:bridge.disconnect()


if __name__=='__main__':main()
