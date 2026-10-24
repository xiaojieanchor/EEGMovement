"""Submit only source-reviewed pilot datasets through the normal model service.

Password is consumed from the process environment/stdin, never saved. Each job
gets a real CLI preflight before submission, and an idempotent request identity.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.ssh import ComputeBridge
from gnautilus_ml.common import digest,read_json,atomic


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',default=str(ROOT/'docs/gnautilus/initial_training_20261023/study-plan.json'))
    parser.add_argument('--readers',nargs='+');args=parser.parse_args()
    plan=read_json(args.plan);plan_hash=digest(plan);output=ROOT/'docs/gnautilus/initial_training_20261023'
    launch=read_json(output/'qc-launch.json');bridge=ComputeBridge(ROOT)
    bridge.connect(os.environ.pop('EEG_ADMIN_PASSWORD',None) or getpass.getpass('Compute password: '))
    if not bridge.status['worker_connected']:raise ConnectionError(bridge.status['last_error'])
    base=bridge.profile['root'];snapshot=bridge.call('GET','/snapshot');existing={j['request_key']:j for j in snapshot['jobs']}
    receipts=read_json(output/'jobs.json') if (output/'jobs.json').exists() else []
    try:
        for data in snapshot['datasets']:
            if data.get('study_plan_sha256')!=plan_hash or data.get('scope')!='public_method_development':continue
            reader=data['records'][0]['reader']
            if args.readers and reader not in args.readers:continue
            for algorithm in plan['algorithms']:
                key=digest(dict(study=plan_hash,dataset=data['id'],algorithm=algorithm,seed=plan['seed'],epochs=plan['max_epochs']))
                if key in existing:
                    print(json.dumps(dict(existing_job=existing[key]['id'],reader=reader,algorithm=algorithm,state=existing[key]['state'])),flush=True)
                    continue
                request=dict(dataset_id=data['id'],algorithm=algorithm,window_s=plan['datasets'][reader]['window_s'],seed=plan['seed'],max_epochs=plan['max_epochs'],request_key=key)
                prepared=bridge.call('POST','/jobs/prepare',request);spec=prepared['spec']
                print(json.dumps(dict(phase='source_preflight',reader=reader,algorithm=algorithm,protocol=spec['protocol_id'])),flush=True)
                code="""from pathlib import Path
import json,subprocess,os,hashlib
root=Path(BASE);release=root/'releases'/(root/'current-release').read_text().strip();checker=Path(CHECKER)
protocol=root/'protocols'/(PID+'.json');dataset=root/'datasets'/(DID+'.json')
command=[str(root/'.venv/bin/python'),str(checker),'--protocol',str(protocol),'--dataset',str(dataset),'--code-root',str(release),'--entrypoint',str(release/'gnautilus_ml/runner.py')]
result=subprocess.run(command,capture_output=True,text=True,timeout=240,env={**os.environ,'PYTHONPATH':str(release),'EEGMOVEMENT_TRAINING_PROTOCOL':str(protocol),'CUDA_VISIBLE_DEVICES':GPU,'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2'})
receipt={'command':command,'returncode':result.returncode,'stdout':result.stdout[-4000:],'stderr':result.stderr[-2000:],'protocol_file_sha256':hashlib.sha256(protocol.read_bytes()).hexdigest(),'checker_sha256':hashlib.sha256(checker.read_bytes()).hexdigest()}
(root/'logs'/('public-pilot-preflight-'+PID+'.json')).write_text(json.dumps(receipt,indent=2))
if result.returncode:raise ValueError(result.stderr[-2000:])
print(json.dumps({'passed':True,'records':json.loads(result.stdout)['records']}))
""".replace('BASE',repr(base)).replace('CHECKER',repr(launch['staging']+'/scripts/check_gnautilus_training.py')).replace('PID',repr(spec['protocol_id'])).replace('DID',repr(data['id'])).replace('GPU',repr(bridge.profile['gpu_uuid']))
                checked=bridge.command(code,timeout=270)
                job=bridge.call('POST','/jobs',request);existing[key]=job
                row=dict(reader=reader,algorithm=algorithm,job_id=job['id'],dataset_id=data['id'],protocol_id=spec['protocol_id'],preflight=checked,state_at_submission=job['state'])
                receipts.append(row);atomic(output/'jobs.json',receipts)
                print(json.dumps(row),flush=True)
    finally:bridge.disconnect()


if __name__=='__main__':main()
