"""Whole-episode research protocol. This never grants cross-wearing acceptance."""
import csv
import ast
from pathlib import Path
import numpy as np
from .common import read_json,atomic,digest,file_hash,within
from .navigation_contract import CHANNELS,SCOPE,ALGORITHM,OPENBMI,EOG,ELECTRICAL

PHYSIONET='100cca48ef1b862b6409beddf980dc4f62d1308c966cfb313e8a81778fbe4dd5'


def ssl_phase_identity(code_root,protocol):
    """Semantic identity for a completed SSL stage, excluding supervised adaptation."""
    selected={
        'stream_jepa.py':['Branch.__init__','Branch.step','CausalJEPA.__init__','CausalJEPA.step','CausalJEPA.forward','jepa_loss','ema_update','transfer_branch','Preprocessor'],
        'jepa_training.py':['initialize','DomainStudent','ssl_fit','gradient_evidence','normalization','prepared','checkpoint','native_domains','native_teacher_ids'],
        'personal_study.py':['load_episodes','unlabelled_regions','episode_bounds']}
    methods={}
    for filename,names in selected.items():
        tree=ast.parse((Path(code_root)/'gnautilus_ml'/filename).read_text(encoding='utf-8'))
        for name in names:
            parts=name.split('.');node=next(n for n in tree.body if getattr(n,'name',None)==parts[0])
            if len(parts)>1:node=next(n for n in node.body if getattr(n,'name',None)==parts[1])
            if name=='ssl_fit':
                # Completed-stage reuse compares learning, not checkpoint I/O.
                # A restored run must still pass its own full protocol/hash gate.
                node.body=[n for n in node.body if not (isinstance(n,ast.If) and isinstance(n.test,ast.Call) and isinstance(n.test.func,ast.Attribute) and n.test.func.attr=='exists' and isinstance(n.test.func.value,ast.Name) and n.test.func.value.id=='receipt')]
            methods[filename+':'+name]=ast.dump(node,include_attributes=False)
    dependencies=['navigation_contract.py','common.py','public_windows.py','public_study.py','algorithms.py','multistage.py','network.py','readers.py','quality.py']
    return digest(dict(methods=methods,dependencies={p:file_hash(Path(code_root)/'gnautilus_ml'/p) for p in dependencies},
                       parameters={k:protocol[k] for k in ['dataset_sha256','ssl_steps','context_max_samples','target_supports','hop_samples','reuse_ids','normalization_roles']}))


def source_manifest(folder):
    """Hash-only upload inventory: no signal transforms or role changes on the PC."""
    folder=Path(folder)
    wanted=[folder/'dataset.json',folder/'.metadata/episode-index.json',folder/'.metadata/unlabelled-window-index.csv']
    info=read_json(folder/'dataset.json')
    wanted += [folder/v['path'] for v in info['files'].values()]
    for p in sorted((folder/'.metadata/canonical').glob('*')):
        wanted += [p/name for name in ['samples.npy','segments.json','coverage.npy','conflict_mask.npy','descriptor.original.json'] if (p/name).exists()]
    return dict(schema='gnautilus.personal_source.v1',files={p.relative_to(folder).as_posix():file_hash(p) for p in wanted},
                metadata_sha256=digest(info),title='个人导航数据集',scope=SCOPE)


def verify_sources(folder,receipt):
    for name,expected in receipt['files'].items():
        if file_hash(within(Path(folder)/name,folder))!=expected:raise ValueError('Personal source hash changed: '+name)


def episode_bounds(index):
    seen={};by_stream={}
    for row in index:
        role=row['split'];tid=row['trial_id']
        if role not in ['train','validation','test'] or tid in seen:raise ValueError('Duplicate/invalid complete trial role')
        seen[tid]=role
        a,b=int(row['context_start']),int(row['context_end'])
        if not 0<=a<b:raise ValueError('Invalid episode support')
        by_stream.setdefault(row['stream_id'],[]).append((a,b,role,tid))
    for rows in by_stream.values():
        rows.sort()
        if any(a[1]>b[0] for a,b in zip(rows,rows[1:])):raise ValueError('Episode raw support overlaps across trials')
    return by_stream


def compile_study(root,folder,code_root,source_receipt):
    folder=within(folder,Path(root)/'data');receipt=read_json(source_receipt)
    verify_sources(folder,receipt)
    info=read_json(folder/'dataset.json');index=read_json(folder/'.metadata/episode-index.json');episode_bounds(index)
    if info['sampling_rate_hz']!=250 or info['total_complete_trials']!=15:raise ValueError('This protocol binds the approved 15-trial batch')
    if not info.get('user_approved_scope') or info.get('cross_wearing_generalization_claim') is not False:raise ValueError('Explicit internal evaluation scope required')
    # Unit/source compatibility was established from native GDS descriptor and HDF5
    # physical columns, sample rate and uV payload. Hardware filters remain separate.
    contract_review=dict(schema='gnautilus.personal_contract_review.v1',same_wearing=True,physical_columns='CH01..CH32',
        rate_hz=250,signal_unit='uV',bipolar=False,reference='dedicated_hardware_REF_site_unrecorded',ground='dedicated_hardware_GND_site_unrecorded',
        official_highpass_hz=.1,native_highpass_hz=.5,contracts_equal=False,
        official_rest_supervision='exploratory_cross_source_only',rest_source_class_confound=True,
        attestation='User confirmed all 32 positions and swap16/19; dedicated REF/GND outside 32; physical REF/GND sites remain unknown',
        original_descriptors_unchanged=True)
    dataset=dict(schema='gnautilus.personal_study.v1',title='个人导航数据集',scope=SCOPE,folder=str(folder),source_receipt=receipt,
                 source_receipt_sha256=digest(receipt),episodes=index,files=info['files'],channels=CHANNELS,
                 sampling_rate=250,records=[],contract_review=contract_review,electrical_policy=ELECTRICAL,
                 evaluation_scope=dict(independent_test=False,cross_wearing=False,previously_inspected=True,
                    selection='development only; held-out internal test after candidate freeze'),ssl_guard_samples=2275)
    key=digest(dataset);atomic(Path(root)/'datasets'/(key+'.json'),dataset)
    return key,dataset


def make_protocol(dataset,code_root,options=None,root=None):
    options=options or {}
    seeds=options.get('seeds',[17,29,41]);steps=int(options.get('ssl_steps',1000));epochs=int(options.get('max_epochs',60))
    if seeds!=[17,29,41] or not 1<=steps<=20000 or not 1<=epochs<=100:raise ValueError('Research budget/seed policy changed')
    code={p.relative_to(code_root).as_posix():file_hash(p) for p in sorted((Path(code_root)/'gnautilus_ml').glob('*.py'))}
    protocol=dict(schema='gnautilus.training_protocol.v2',scope=SCOPE,algorithm=ALGORITHM,dataset_sha256=digest(dataset),
                code_sha256=code,seeds=seeds,seed=17,ssl_steps=steps,max_epochs=epochs,max_wall_seconds=43200,
                private_sequence_passes_per_epoch=1,whole_trial_state_carry=True,head_lr=.001,backbone_lr=.00003,
                context_max_samples=2000,target_supports=[[25,150],[150,275]],hop_samples=25,
                normalization_roles=['train'],selection_roles=['validation'],test_access='after_selection_freeze_only',
                reuse_ids=[OPENBMI,EOG,PHYSIONET],variants=['jepa_transfer','transfer_no_jepa','random_jepa','finite_history_baseline','no_eye','finite_history'],
                selection='validation false starts then accepted macro recall then first-response latency',
                private_label_truth='instructed joint navigation; no measured pure-MI or eye-position claim')
    if options.get('reuse_ssl_job'):
        from .registry import Registry
        if root is None:raise ValueError('Private root required for source-bound phase reuse')
        root=Path(root);job=Registry(root/'state.sqlite').job(options['reuse_ssl_job'])
        old=read_json(root/'protocols'/(job['spec']['protocol_id']+'.json'))
        if job['state'] not in ['cancelled','failed','interrupted']:raise ValueError('Source study must be stopped before phase reuse')
        releases=[]
        for p in (root/'releases').glob('*/source.json'):
            hashes=read_json(p)
            if all(hashes.get(k)==v for k,v in old['code_sha256'].items()):releases.append(p.parent)
        if not releases:raise ValueError('Immutable source code for the checkpoint is unavailable')
        phase=ssl_phase_identity(code_root,protocol)
        if ssl_phase_identity(releases[0],old)!=phase:raise ValueError('SSL inputs, normalization or learning mechanism changed; rerun pretraining')
        folder=root/'logs'/('jepa-'+job['id'])
        if (folder/'selection-freeze.json').exists():raise ValueError('Source checkpoint must precede held-out test access')
        receipt=read_json(folder/'17-jepa_transfer-ssl.json');p=within(folder/receipt['file'],folder)
        if receipt['phase']!='ssl' or receipt['step']!=steps or receipt['protocol_sha256']!=digest(old) or file_hash(p)!=receipt['sha256']:raise ValueError('Completed verified SSL checkpoint required')
        protocol['ssl_reuse']=dict(job_id=job['id'],path=p.relative_to(root).as_posix(),sha256=receipt['sha256'],protocol_sha256=digest(old),phase_sha256=phase)
    return protocol


def check_study_protocol(protocol,dataset,code_root):
    if protocol.get('scope')!=SCOPE or dataset.get('scope')!=SCOPE or dataset.get('schema')!='gnautilus.personal_study.v1':raise ValueError('Explicit same-wearing research scope required')
    if protocol.get('dataset_sha256')!=digest(dataset):raise ValueError('Dataset differs from locked protocol')
    if protocol.get('algorithm')!=ALGORITHM or protocol.get('seeds')!=[17,29,41]:raise ValueError('Research identity changed')
    if protocol.get('target_supports')!=[[25,150],[150,275]] or protocol.get('context_max_samples')!=2000:raise ValueError('Future/raw support geometry changed')
    if protocol.get('normalization_roles')!=['train'] or protocol.get('selection_roles')!=['validation'] or protocol.get('test_access')!='after_selection_freeze_only':raise ValueError('Role leakage in research protocol')
    if not 1<=protocol.get('ssl_steps',0)<=20000 or not 1<=protocol.get('max_epochs',0)<=100 or not 0<protocol.get('max_wall_seconds',0)<=43200:raise ValueError('Training budget exceeds approved limit')
    if protocol.get('private_sequence_passes_per_epoch')!=1 or protocol.get('whole_trial_state_carry') is not True or protocol.get('head_lr')!=.001 or protocol.get('backbone_lr')!=.00003:raise ValueError('Private adaptation recipe differs')
    if not protocol.get('code_sha256'):raise ValueError('Training source identity missing')
    for name,expected in protocol['code_sha256'].items():
        if file_hash(within(Path(code_root)/name,code_root))!=expected:raise ValueError('Training source changed: '+name)
    if dataset.get('channels')!=CHANNELS or dataset.get('sampling_rate')!=250:raise ValueError('Montage mismatch including swap16/19')
    episode_bounds(dataset['episodes'])
    roles={r['split'] for r in dataset['episodes']}
    if roles!={'train','validation','test'}:raise ValueError('All three explicit internal roles required')
    verify_sources(dataset['folder'],dataset['source_receipt'])
    if protocol.get('ssl_reuse'):
        r=protocol['ssl_reuse'];root=Path(dataset['folder']).parent.parent
        if ssl_phase_identity(code_root,protocol)!=r['phase_sha256'] or file_hash(within(root/r['path'],root/'logs'))!=r['sha256']:raise ValueError('Reused SSL phase identity changed')
    return True


def load_episodes(dataset,role):
    if role not in ['train','validation','test']:raise ValueError('Unknown internal role')
    entry=dataset['files'][role];p=Path(dataset['folder'])/entry['path']
    if file_hash(p)!=entry['sha256']:raise ValueError('Episode NPZ changed')
    index={r['trial_id']:r for r in dataset['episodes'] if r['split']==role};rows=[]
    with np.load(p,allow_pickle=False) as z:
        # Load only episodes; legacy overlapping windows are deliberately unused.
        ids=z['episode_trial_id'];offsets=z['episode_offsets'];x=z['episode_eeg_uv'];labels=z['episode_sample_instruction'] if 'episode_sample_instruction' in z else z['sample_instruction']
        for i,tid in enumerate(ids):
            tid=str(tid);r=index[tid];a,b=map(int,offsets[i:i+2]);signal=x[a:b].T.copy();y=labels[a:b].copy()
            if signal.shape!=(32,r['context_end']-r['context_start']) or not np.isfinite(signal).all():raise ValueError('Episode shape/finite support mismatch')
            if set(np.unique(y))-set([-1,0,1,2,3,4]):raise ValueError('Unknown samples must remain -1')
            rows.append(dict(id=tid,x=signal,y=y,metadata=r))
    if set(index)!={r['id'] for r in rows}:raise ValueError('Complete trial role mismatch')
    return rows


def unlabelled_regions(dataset):
    """Accept only contiguous QC-screened spans, outside held-out raw supports."""
    folder=Path(dataset['folder']);blocked=episode_bounds(dataset['episodes']);guard=dataset['ssl_guard_samples'];by_stream={}
    with (folder/'.metadata/unlabelled-window-index.csv').open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            if row['all_32_screen_pass']!='True':continue
            stream=row['stream_id'];a,b=int(row['start']),int(row['end'])
            if any(a<end+guard and b>start-guard for start,end,role,_ in blocked.get(stream,[]) if role!='train'):continue
            by_stream.setdefault(stream,[]).append((a,b))
    out=[]
    for stream,spans in sorted(by_stream.items()):
        path=folder/'.metadata/canonical'/stream
        d=read_json(path/'descriptor.original.json')
        if d['source_kind']!='gtec_live' or d['sampling_rate']!=250:raise ValueError('Unlabelled origin mismatch')
        indices=[i for i,c in enumerate(d['channels']) if c['kind']=='EEG']
        if len(indices)!=32 or [d['channels'][i]['physical_index'] for i in indices]!=list(range(32)):raise ValueError('Unlabelled physical column order changed')
        merged=[]
        for a,b in sorted(spans):
            if merged and a==merged[-1][1]:merged[-1][1]=b
            else:merged.append([a,b])
        segments=read_json(path/'segments.json')['segments']
        x=np.load(path/'samples.npy',mmap_mode='r',allow_pickle=False)
        coverage=np.load(path/'coverage.npy',mmap_mode='r',allow_pickle=False);conflict=np.load(path/'conflict_mask.npy',mmap_mode='r',allow_pickle=False)
        for a,b in merged:
            for sa,sb in segments:
                lo,hi=max(a,sa),min(b,sb)
                if hi-lo<650:continue
                if not np.all(coverage[lo:hi]) or np.any(conflict[lo:hi]):raise ValueError('Missing/conflicting source samples entered SSL')
                values=np.asarray(x[lo:hi][:,indices].T,dtype='float32')
                if not np.isfinite(values).all():raise ValueError('Nonfinite SSL span')
                out.append(dict(x=values,stream_id=stream,start=lo,end=hi))
    return out


def reuse_inventory(root):
    from .registry import Registry
    rows=Registry(Path(root)/'state.sqlite').models();result=[]
    for m in rows:
        # A newly registered personal pilot must not enter the native public
        # teacher search, even when its development score is numerically high.
        if m.get('evaluation_stage')==SCOPE:continue
        score=(m.get('metrics',{}).get('development') or {}).get('balanced_accuracy')
        classes=m.get('class_labels') or []
        use='native_task_reference';reason='retained; selected native domain teachers are loaded separately'
        if m['model_id'] in [OPENBMI,EOG]:use='initialization_and_native_teacher';reason='hash-verified actual compatible temporal/GRU parameters'
        elif m['model_id']==PHYSIONET:use='native_teacher';reason='additional original-task motor prior'
        elif score is None or score<=1/max(1,len(classes))+.05:use='retained_not_distilled';reason='insufficient original development evidence'
        result.append(dict(model_id=m['model_id'],weights_sha256=m['weights_sha256'],algorithm=m['algorithm'],
            class_labels=classes,input_contract=m.get('input_contract'),development_balanced_accuracy=score,use=use,reason=reason))
    return result
