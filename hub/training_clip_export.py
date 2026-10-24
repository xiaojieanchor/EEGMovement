"""Read-only export of score-independent contexts from the selected model's train split."""
import argparse,hashlib,json,os
from pathlib import Path
import numpy as np

LABELS=['REST','FORWARD','BACKWARD','LEFT','RIGHT','FORWARD_LEFT','FORWARD_RIGHT','BACKWARD_LEFT','BACKWARD_RIGHT']
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb')as f:
        for chunk in iter(lambda:f.read(1024**2),b''):h.update(chunk)
    return h.hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def bound(root,path):
    p=Path(path);p=(root/p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_relative_to(root.resolve()):raise ValueError('Input outside project')
    return p
def workspace(root,report):
    for p in report.parents:
        if p.name=='training' and p.parent.name=='outputs':
            w=p.parent.parent
            if w==root or w.parent==root/'experiments':return w
    raise ValueError('Unknown training workspace')

def export(root,row,model_id,target):
    root=Path(root).resolve();target=Path(target);report=bound(root,row['report_path']);w=workspace(root,report)
    if sha(report)!=row['report_sha256']:raise ValueError('Model report changed')
    protocol=bound(root,w/'configs'/(row['experiment']+'.protocol.json'))
    if sha(protocol)!=row['protocol_sha256']:raise ValueError('Locked protocol changed')
    p=read(protocol);manifest_path=bound(root,w/p['manifest']['path'])
    if sha(manifest_path)!=p['manifest']['sha256']:raise ValueError('Canonical manifest changed')
    manifest=read(manifest_path)
    policy_ref=p.get('device_policy',{});locked_policy_path=bound(root,w/policy_ref.get('path','configs/device_channels.json'))
    if sha(locked_policy_path)!=policy_ref.get('sha256'):raise ValueError('Locked training channel policy changed')
    # The root may retain an old experiment's config. Use the exact policy in
    # this isolated model workspace, never edit an old dependency to make it fit.
    policy=read(locked_policy_path);policy_sha=sha(locked_policy_path)
    if policy.get('disabled_channels_1based')!=[1,6] or policy.get('active_channels_1based')!=[2,3,4,5,7] or policy.get('minimum_usable_channels')!=4:raise ValueError('Current five-channel hardware policy required')
    excluded=[x-1 for x in policy['disabled_channels_1based']]
    train=[r for r in manifest['records']if r['role']=='train' and not r.get('public')]
    if not train:raise ValueError('No private training records')
    best={}
    for record in sorted(train,key=lambda r:r['recording'],reverse=True):
        if record.get('sfreq')!=250 or record.get('unit')!='uV' or record.get('quality_mode')!='online_known':raise ValueError('Unsupported raw input contract')
        file=next(f for f in record['files']if f['kind']=='canonical_raw');path=bound(root,w/file['path'])
        if sha(path)!=file['sha256']:raise ValueError('Canonical training pack changed')
        with np.load(path,allow_pickle=False)as pack:a={k:pack[k].copy()for k in ['raw_uv','quality','labels','indices','epochs','event_ids']}
        raw=a['raw_uv'];q=a['quality'];n=len(raw)
        if raw.shape!=(n,7) or q.shape!=raw.shape or q.dtype!=bool or not np.isfinite(raw).all():raise ValueError('Invalid raw/QC arrays')
        if any(a[k].shape!=(n,)for k in ['labels','indices','epochs','event_ids']):raise ValueError('Invalid sample identities')
        raw[:,excluded]=0;q[:,excluded]=False
        good=(q.sum(1)>=4)&(a['labels']>=0)&(a['labels']<9)&(a['event_ids']>=0)
        boundary=np.ones(n,bool)
        boundary[1:]=(np.diff(a['indices'])!=1)|(np.diff(a['epochs'])!=0)|(np.diff(a['labels'])!=0)|(np.diff(a['event_ids'])!=0)|(~good[:-1])|(~good[1:])
        starts=np.flatnonzero(boundary);stops=np.r_[starts[1:],n]
        for start,stop in zip(starts,stops):
            if not good[start] or stop-start<1250:continue
            label=LABELS[int(a['labels'][start])]
            # Four common good channels in bootstrap; no quality rule is relaxed.
            end=min(int(stop),int(start)+4000)
            if q[start:start+1000].all(0).sum()<4:continue
            ranking=(record['recording'],end-int(start))
            if label in best and ranking<=best[label]['ranking']:continue
            best[label]=dict(ranking=ranking,raw=raw[start:end].copy(),quality=q[start:end].copy(),
                labels=a['labels'][start:end].copy(),indices=a['indices'][start:end].copy(),epochs=a['epochs'][start:end].copy(),event_ids=a['event_ids'][start:end].copy(),
                recording=record['recording'],source_sha256=record['source_sha256'],canonical_sha256=file['sha256'],role='train',
                source_start=int(a['indices'][start]),source_end=int(a['indices'][end-1])+1,canonical_offset_start=int(start),canonical_offset_end=end,
                source_event=int(a['event_ids'][start]),input_surface=record.get('collection_surface',record.get('input_surface','unknown')))
    missing=set(LABELS[:5])-set(best)
    if missing:raise ValueError('No quality-passing trained context for '+','.join(sorted(missing)))
    target.mkdir(parents=True,exist_ok=True);clips={}
    for label,v in best.items():
        path=target/(label+'.npz');arrays={k:v[k]for k in ['raw','quality','labels','indices','epochs','event_ids']}
        np.savez_compressed(path,**arrays)
        clips[label]=dict({k:x for k,x in v.items()if k not in arrays and k!='ranking'},file=path.name,sha256=sha(path),samples=len(v['raw']),seconds=len(v['raw'])/250,loop_play_seconds=(len(v['raw'])-1000)/250)
    result=dict(version=2,model_id=model_id,artifact_sha256=row['weights_sha256'],protocol_sha256=row['protocol_sha256'],
        source_manifest_sha256=p['manifest']['sha256'],device_policy_sha256=policy_sha,training_device_policy_sha256=policy_ref['sha256'],disabled_channels_1based=policy['disabled_channels_1based'],
        bootstrap_samples=1000,labels=[label for label in LABELS if label in clips],clips=clips,
        training_recordings=[dict(recording=r['recording'],source_sha256=r['source_sha256'],role='train')for r in train],
        selection='newest trained recording, then longest contiguous same-instruction event; never predictions or scores',
        semantics='Recorded raw EEG with original chronological quality; context restart/loop is a chain test, not natural acquisition or independent accuracy.',independent_test=False)
    tmp=target/'library.next.json';tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,target/'library.json')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--row',required=True);parser.add_argument('--model-id',required=True);args=parser.parse_args()
    row=json.loads(args.row);root=Path(args.root).resolve();target=root/'run/training-test-v2'/row['weights_sha256']
    result=export(root,row,args.model_id,target);print(json.dumps(dict(directory=str(target),manifest=result)))
