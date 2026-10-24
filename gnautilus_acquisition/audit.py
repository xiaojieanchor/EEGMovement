"""Read-only audit and logical block comparison; never opens a journal for append."""
import bisect
import hashlib
import json
from pathlib import Path
from .contract import json_bytes,validate_descriptor,validate_block


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for part in iter(lambda:source.read(1024*1024),b''):digest.update(part)
    return digest.hexdigest()


def audit_journal(path):
    path=Path(path);errors=[];records={};intervals=[];offset=0;index_bytes=0;event_count=0;event_tail=0
    d=validate_descriptor(json.loads((path/'descriptor.json').read_text('utf-8')))
    names=['descriptor.json','raw.f32','blocks.jsonl','events.jsonl']
    before={name:(path/name).stat().st_size for name in names}
    with (path/'raw.f32').open('rb') as raw,(path/'blocks.jsonl').open('rb') as index:
        for line in index:
            if not line.endswith(b'\n'):break
            record=json.loads(line);h=record['header'];length=record['length']
            if record['offset']!=offset or type(length) is not int or not 0<length<=4*1024*1024:raise ValueError('提交索引偏移或长度错误')
            body=raw.read(length);validate_block(h,body,d)
            if h['seq'] in records:raise ValueError('提交索引包含重复序号')
            start,end=h['sample_start'],h['sample_start']+h['sample_count'];at=bisect.bisect_left(intervals,(start,-1))
            if any(start<b and a<end for a,b in intervals[max(0,at-1):at+1]):raise ValueError('提交样本范围重叠')
            bisect.insort(intervals,(start,end));records[h['seq']]=h;offset+=length;index_bytes+=len(line)
    with (path/'events.jsonl').open('rb') as events:
        for line in events:
            if not line.endswith(b'\n'):event_tail=len(line);break
            json.loads(line);event_count+=1
    gaps=[];next_seq=1;logical=hashlib.sha256();samples=0
    for seq,h in sorted(records.items()):
        if seq>next_seq:gaps.append([next_seq,seq-1])
        next_seq=seq+1;samples+=h['sample_count'];logical.update(json_bytes([seq,h['sample_start'],h['sample_count'],h['sha256']]))
    hashes={name:file_hash(path/name) for name in names};closed_path=path/'closed.json';closed=closed_path.exists()
    if closed:
        manifest=json.loads(closed_path.read_text('utf-8'))
        for name in names:
            entry=manifest.get('files',{}).get(name,{})
            if entry.get('sha256')!=hashes[name] or entry.get('bytes')!=before[name]:errors.append('关闭清单不匹配：'+name)
        if manifest.get('samples')!=samples or manifest.get('blocks')!=len(records):errors.append('关闭清单数量不匹配')
    after={name:(path/name).stat().st_size for name in names}
    if before!=after:errors.append('审查期间文件仍在变化，请结束保存后重试')
    tails=before['raw.f32']-offset+before['blocks.jsonl']-index_bytes+event_tail
    return dict(schema='gnautilus.audit.v1',path=str(path.resolve()),stream_id=d['stream_id'],configuration_sha256=d['configuration_sha256'],
        committed_integrity_passed=not errors,complete_closed=closed and not errors and tails==0,blocks=len(records),samples=samples,events=event_count,
        missing_sequences=gaps,uncommitted_tail_bytes=tails,logical_blocks_sha256=logical.hexdigest(),files_sha256=hashes,errors=errors,read_only=True)


def audit_and_compare(path,other=None):
    result=audit_journal(path)
    if other:
        peer=audit_journal(other);result['peer']=peer
        keys=['stream_id','configuration_sha256','blocks','samples','logical_blocks_sha256']
        result['comparison_match']=all(result[k]==peer[k] for k in keys) and result['complete_closed'] and peer['complete_closed']
        result['comparison_semantics']='Compare committed blocks by sequence/sample position/hash; physical append order may differ.'
    return result
