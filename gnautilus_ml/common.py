import hashlib,json,os,secrets
from pathlib import Path

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()
def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):h.update(part)
    return h.hexdigest()
def read_json(path,max_bytes=16*1024*1024):
    p=Path(path)
    if p.stat().st_size>max_bytes:raise ValueError('JSON exceeds limit')
    return json.loads(p.read_text('utf-8-sig'))
def atomic(path,value):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.'+secrets.token_hex(5)+'.tmp')
    with tmp.open('wb') as f:f.write(encoded(value));f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
def within(path,root):
    p=Path(path).resolve();r=Path(root).resolve()
    if not p.is_relative_to(r):raise ValueError('Path is outside the allowed data root')
    return p

