"""Scan staged Git bytes without printing secrets; reads private references locally."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SENSITIVE={'secret','auth_secret','admin_key','password','passwd','private_key','access_token','refresh_token'}
PRIVATE=re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----')
TOKEN=re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})')


def secret_values(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k.lower() in SENSITIVE and isinstance(v,str) and len(v)>=8:yield v.encode()
            yield from secret_values(v)
    elif isinstance(value,list):
        for v in value:yield from secret_values(v)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--staged',action='store_true',required=True);parser.add_argument('--report')
    args=parser.parse_args();values=set()
    for p in (ROOT/'hub/runtime').rglob('*.json'):
        try:values.update(secret_values(json.loads(p.read_text(encoding='utf-8-sig'))))
        except (ValueError,OSError):continue
    # Optional transient check; this value is never written to the report.
    extra=os.environ.get('EEGMOVEMENT_AUDIT_SECRET')
    if extra:values.add(extra.encode())
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0');findings=[];total=0;size=0
    tracked=set(filter(None,paths))
    # Case-insensitive Windows ignores must not hide Unity's Runtime source folder.
    code_suffixes={'.py','.cs','.js','.cjs','.css','.ps1','.java','.sh'}
    trees=['gnautilus_acquisition','gnautilus_ml','gnautilus_lab','hub','eeg_relay','receiver','server','training','scripts','tests',
           'unity/EEGMovement/Assets/Scripts','unity/EEGMovement/Assets/Editor','unity/EEGMovement/Assets/Plugins']
    for tree in trees:
        for p in (ROOT/tree).rglob('*'):
            if not p.is_file() or p.suffix not in code_suffixes or any(v in p.parts for v in ['runtime','bin','__pycache__']):continue
            name=p.relative_to(ROOT).as_posix()
            if name not in tracked:findings.append(dict(path=name,reasons=['active_source_missing_from_git']))
    protected={'programfile_gtec','g-tec','mannul','outputs','runtime','relay_private','relay_enrollments','node_modules','Library','Validation~'}
    batch=subprocess.Popen(['git','cat-file','--batch'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    for name in filter(None,paths):
        p=Path(name);batch.stdin.write((':'+name+'\n').encode());batch.stdin.flush()
        header=batch.stdout.readline().decode().strip().split()
        if len(header)!=3:raise RuntimeError('Cannot read staged file: '+name)
        body=batch.stdout.read(int(header[2]));batch.stdout.read(1);total+=1;size+=len(body)
        kinds=[]
        if any(v in protected for v in p.parts):kinds.append('private_or_vendor_path')
        if p.suffix.lower() in ['.key','.pfx','.p12','.keystore','.jks','.sqlite','.db','.f32','.apk','.exe']:kinds.append('private_or_generated_extension')
        if len(body)>=100*1024*1024:kinds.append('github_file_limit')
        if PRIVATE.search(body):kinds.append('private_key_material')
        if TOKEN.search(body):kinds.append('access_token_pattern')
        if any(v in body for v in values):kinds.append('actual_local_secret_value')
        if kinds:findings.append(dict(path=name,reasons=kinds))
    batch.stdin.close();batch.wait(timeout=15)
    result=dict(passed=not findings,staged_files=total,staged_bytes=size,actual_secret_values_checked=len(values),findings=findings)
    if args.report:
        report=Path(args.report);report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed'] else 1)


if __name__=='__main__':main()
