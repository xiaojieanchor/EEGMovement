"""Pinned private-server administration. Never starts avatar motion or device capture."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.common import atomic,read_json,file_hash,digest
from gnautilus_ml.ssh import ComputeBridge


def mkdirs(sftp,path,base):
    p=Path(path).as_posix()
    pieces=p.split('/');current=''
    for part in pieces:
        if not part:continue
        current+='/'+part
        if not current.startswith(base):continue
        try:sftp.mkdir(current,0o700)
        except OSError:sftp.stat(current)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['upload','deploy','prepare','start','status','download'])
    ap.add_argument('--folder');ap.add_argument('--output',required=True);ap.add_argument('--ssl-steps',type=int,default=1000);ap.add_argument('--epochs',type=int,default=60)
    ap.add_argument('--reuse-ssl-job')
    ap.add_argument('--joint-pilot',action='store_true')
    args=ap.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    b=ComputeBridge(ROOT)
    if not b.connect():raise RuntimeError(b.status['last_error'])
    base=b.profile['root']
    try:
        if args.action=='upload':
            from gnautilus_ml.personal_study import source_manifest
            folder=Path(args.folder).resolve();receipt=source_manifest(folder);key=digest(receipt);remote=base+'/data/'+key
            atomic(out/'source-receipt.json',receipt)
            known=b.command('import json,hashlib\nfrom pathlib import Path\nb=Path('+repr(remote)+')\nnames='+repr(list(receipt['files']))+'\nresult={}\nfor name in names:\n p=b/name\n if p.exists():\n  h=hashlib.sha256()\n  with p.open("rb") as f:\n   for block in iter(lambda:f.read(1048576),b""):h.update(block)\n  result[name]=h.hexdigest()\nprint(json.dumps(result))',timeout=60)
            with b.client.open_sftp() as s:
                s.get_channel().settimeout(120)
                for i,(name,sha) in enumerate(receipt['files'].items()):
                    if known.get(name)==sha:continue
                    target=remote+'/'+name;mkdirs(s,str(Path(target).parent).replace('\\','/'),base)
                    s.put(str(folder/name),target+'.upload');s.posix_rename(target+'.upload',target);s.chmod(target,0o600)
                    print(json.dumps(dict(phase='upload',file=name,completed=i+1,total=len(receipt['files']))),flush=True)
                s.put(str(out/'source-receipt.json'),remote+'/source-receipt.json')
            atomic(out/'upload.json',dict(upload_id=key,remote_folder=remote,source_sha256=digest(receipt)))
        elif args.action=='deploy':
            snap=b.call('GET','/snapshot');active=[j for j in snap['jobs'] if j['state'] not in ['cancelled','failed','completed','interrupted']]
            if active or snap['inference']['inference_enabled']:raise ValueError('Compute must be idle for versioned deployment')
            files=list((ROOT/'gnautilus_ml').glob('*.py'))+list((ROOT/'gnautilus_acquisition').glob('*.py'))
            sources={f.relative_to(ROOT).as_posix():file_hash(f) for f in files};release=digest(sources);remote=base+'/releases/'+release
            with b.client.open_sftp() as s:
                for f in files:
                    name=f.relative_to(ROOT).as_posix();target=remote+'/'+name;mkdirs(s,str(Path(target).parent).replace('\\','/'),base);s.put(str(f),target)
                for name,value in [('compute.json',b.profile),('source.json',sources)]:
                    with s.open(remote+'/'+name,'w') as f:f.write(json.dumps(value))
                check=base+'/validation/navigation-jepa';mkdirs(s,check+'/tests',base)
                for name in ['test_navigation_jepa.py','test_navigation_jepa_training.py','test_joint_navigation.py','test_gnautilus_ml_service.py','test_gnautilus_ml_boundaries.py','test_gnautilus_ml_v1.py','test_contextual_readout.py','test_ocular_boundary_audit.py','test_eye_joint_probe.py']:
                    s.put(str(ROOT/'tests'/name),check+'/tests/'+name)
                with s.open(check+'/tests/__init__.py','w') as f:f.write('')
            code='import os,json,subprocess\nfrom pathlib import Path\nb=Path('+repr(base)+');release='+repr(release)+'\nenv=dict(os.environ,CUDA_VISIBLE_DEVICES='+repr(b.profile['gpu_uuid'])+',PYTHONPATH=str(b/"releases"/release)+":"+str(b/"validation/navigation-jepa"),OMP_NUM_THREADS="2",MKL_NUM_THREADS="2")\nr=subprocess.run([str(b/".venv/bin/python"),"-m","pytest",str(b/"validation/navigation-jepa/tests/test_navigation_jepa.py"),str(b/"validation/navigation-jepa/tests/test_navigation_jepa_training.py"),str(b/"validation/navigation-jepa/tests/test_joint_navigation.py"),str(b/"validation/navigation-jepa/tests/test_contextual_readout.py"),str(b/"validation/navigation-jepa/tests/test_ocular_boundary_audit.py"),str(b/"validation/navigation-jepa/tests/test_eye_joint_probe.py"),"-q","--tb=short"],env=env,cwd=b/"validation/navigation-jepa",capture_output=True,text=True,timeout=180)\nprint(json.dumps(dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr[-2000:])))'
            tests=b.command(code,timeout=210);atomic(out/'remote-synthetic-tests.json',tests)
            if tests['returncode']:raise RuntimeError(tests['stdout']+tests['stderr'])
            code='import json,subprocess\nfrom pathlib import Path\nb=Path('+repr(base)+');p=b/"current-release";prior=p.read_text().strip();p.write_text('+repr(release)+')\nr=subprocess.run(["systemctl","--user","restart","eegmovement-gnautilus.service"],capture_output=True,text=True)\nif r.returncode:\n p.write_text(prior)\n raise RuntimeError(r.stderr)\nprint(json.dumps(dict(release='+repr(release)+',previous_release=prior,supervisor="eegmovement-gnautilus.service")))'
            deployed=b.command(code,timeout=30)
            for _ in range(80):
                try:
                    if b.call('GET','/capabilities')['jepa'].get('trainable'):break
                except Exception:pass
                time.sleep(.25)
            else:raise RuntimeError('New service health did not confirm JEPA capability')
            atomic(out/'deployment.json',deployed);print(json.dumps(deployed),flush=True)
        elif args.action=='prepare':
            upload=read_json(out/'upload.json');reply=b.call('POST','/datasets/personal_study',dict(upload_id=upload['upload_id']),timeout=90)
            atomic(out/'study.json',reply)
            spec=b.call('POST','/jobs/prepare',dict(dataset_id=reply['dataset_id'],algorithm='causal_jepa_stream',ssl_steps=args.ssl_steps,max_epochs=args.epochs,reuse_ssl_job=args.reuse_ssl_job,architecture='shared_full32_joint_v2' if args.joint_pilot else None),timeout=90)
            atomic(out/'protocol.json',spec);print(json.dumps(dict(dataset_id=reply['dataset_id'],protocol_id=spec['spec']['protocol_id'])))
        elif args.action=='start':
            study=read_json(out/'study.json');protocol=read_json(out/'protocol.json')['protocol']
            job=b.call('POST','/jobs',dict(dataset_id=study['dataset_id'],algorithm='causal_jepa_stream',ssl_steps=protocol['ssl_steps'],max_epochs=protocol['max_epochs'],reuse_ssl_job=(protocol.get('ssl_reuse') or {}).get('job_id'),architecture=protocol.get('architecture'),request_key='navigation-jepa-'+digest(protocol)),timeout=90)
            atomic(out/'job.json',job);print(json.dumps(job))
        elif args.action=='status':
            job=read_json(out/'job.json');reply=b.call('GET','/jobs/'+job['id']);atomic(out/'job-status.json',reply)
            print(json.dumps(dict(job=reply['job'],events=reply['events'][-3:])))
        elif args.action=='download':
            job=read_json(out/'job.json');folder=base+'/logs/jepa-'+job['id']
            with b.client.open_sftp() as s:
                for attr in s.listdir_attr(folder):
                    if attr.filename.endswith('.json') and attr.st_size<32*1024*1024:s.get(folder+'/'+attr.filename,str(out/attr.filename))
            print(json.dumps(dict(downloaded_to=str(out))))
    finally:b.disconnect(quiet=True)

if __name__=='__main__':main()
