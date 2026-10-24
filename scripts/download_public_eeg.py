"""Download an explicit reviewed source plan. No training or model registration.

Run on the private Linux server; records immutable source hashes and resumable
partials under the named public-data root. Does not import the training runtime.
"""
import argparse,concurrent.futures,datetime,fcntl,hashlib,json,os,shutil,subprocess,threading,time,zipfile
from pathlib import Path

def atomic(p,value):
    p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.tmp')
    with t.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(t,p)

def hashes(p):
    s=hashlib.sha256();m=hashlib.md5()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):s.update(b);m.update(b)
    return {'sha256':s.hexdigest(),'md5':m.hexdigest()}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--plan',required=True);ap.add_argument('--workers',type=int,default=4);a=ap.parse_args()
    if not 1<=a.workers<=24:raise ValueError('Workers must be within 1..24')
    root=Path(a.root).resolve();root.mkdir(parents=True,exist_ok=True);plan=json.loads(Path(a.plan).read_text());identity=plan['id']
    if not identity.replace('-','').replace('_','').isalnum():raise ValueError('Invalid download plan identity')
    lock=(root/('.download-'+identity+'.lock')).open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    guard=threading.RLock();state={'schema':'eeg.public_download.v1','started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'plan_id':plan['id'],'training_started':False,'items':{i['relative_path']:dict(dataset=i['dataset'],relative_path=i['relative_path'],state='queued',expected_bytes=i.get('bytes')) for i in plan['items']}};last_publish=0
    def publish(force=False):
        nonlocal last_publish
        with guard:
            if not force and time.monotonic()-last_publish<1:return
            for row in state['items'].values():
                part=root/(row['relative_path']+'.part')
                if row['state']=='downloading' and part.exists():row['received_bytes']=part.stat().st_size
            state['updated_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();atomic(root/('download-status-'+identity+'.json'),state);last_publish=time.monotonic()
    def process(item):
        relative=item['relative_path'];p=(root/relative).resolve()
        if not p.is_relative_to(root) or p==root:raise ValueError('Download target outside root')
        p.parent.mkdir(parents=True,exist_ok=True);receipt=p.with_name(p.name+'.receipt.json');part=p.with_name(p.name+'.part');log=p.with_name(p.name+'.download.log')
        row=state['items'][relative];row['state']='downloading';publish()
        try:
            if p.exists():
                old=json.loads(receipt.read_text()) if receipt.exists() else {};actual=hashes(p)
                if old.get('sha256')!=actual['sha256']:raise ValueError('Existing immutable file has no matching receipt')
                if item.get('sha256') and actual['sha256']!=item['sha256']:raise ValueError('Existing upstream SHA256 mismatch')
                if item.get('md5') and actual['md5']!=item['md5']:raise ValueError('Existing upstream MD5 mismatch')
                row.update(state='verified',bytes=p.stat().st_size,sha256=actual['sha256'],reused=True);publish();return
            if shutil.disk_usage(root).free<20*1024**3+max(0,item.get('bytes',0)-(part.stat().st_size if part.exists() else 0)):raise OSError('Preserve at least 20GiB free space')
            resume=['--continue-at','-'] if part.exists() and part.stat().st_size else ['--range','0-']
            command=['curl','--fail','--location','--retry','5','--retry-all-errors','--retry-delay','3','--connect-timeout','20','--max-time','7200','--speed-limit','1024','--speed-time','60',*resume,'--output',str(part),item['url']]
            if item.get('bytes') is None or not part.exists() or part.stat().st_size!=item['bytes']:
                with log.open('ab') as output:run=subprocess.run(command,stdout=output,stderr=output)
                if run.returncode:raise RuntimeError('curl failed '+str(run.returncode)+'; partial retained, see '+str(log))
            if item.get('bytes') is not None and part.stat().st_size!=item['bytes']:raise ValueError('Publisher byte count mismatch')
            row['state']='checking';publish();actual=hashes(part)
            for kind in ['md5','sha256']:
                if item.get(kind) and actual[kind]!=item[kind]:raise ValueError('Publisher '+kind+' mismatch')
            if p.suffix.lower() in ['.zip','.npz']:
                with zipfile.ZipFile(part) as z:
                    bad=z.testzip()
                    if bad:raise ValueError('Archive CRC failed: '+bad)
                    members=[dict(name=v.filename,bytes=v.file_size,crc32=f'{v.CRC:08x}') for v in z.infolist()]
                atomic(p.with_name(p.name+'.members.json'),members)
            elif p.suffix.lower()=='.mat':
                with part.open('rb') as f:header=f.read(128)
                if not header.startswith(b'MATLAB'):raise ValueError('Not an original MATLAB file')
            os.replace(part,p);report=dict(item,**actual,bytes=p.stat().st_size,downloaded_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),training_admission='pending_content_and_protocol_review')
            atomic(receipt,report);row.update(state='verified',bytes=p.stat().st_size,sha256=actual['sha256'],upstream_hash_checked=bool(item.get('md5') or item.get('sha256')))
        except Exception as e:row.update(state='failed',error=type(e).__name__+': '+str(e))
        finally:publish()
    with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        futures=[pool.submit(process,item) for item in plan['items']]
        while any(not f.done() for f in futures):publish();time.sleep(3)
        for f in futures:f.result()
    state['completed_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();publish(force=True)
    failed=[r for r in state['items'].values() if r['state']!='verified'];print(json.dumps({'verified':len(state['items'])-len(failed),'failed':len(failed),'bytes':sum(r.get('bytes',0) for r in state['items'].values()),'training_started':False}))
    if failed:raise SystemExit(1)

if __name__=='__main__':main()
