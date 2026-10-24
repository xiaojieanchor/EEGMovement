"""Read-only inventory of sealed model checkpoints and identity-matched scores.

Also runs as a standalone remote probe; no torch import or training execution.
"""
import hashlib,json,math,time
from pathlib import Path

def read(path):
    if path.stat().st_size>12*1024**2:raise ValueError('Evidence exceeds bound')
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('Expected object')
    return value

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):h.update(block)
    return h.hexdigest()

def number(x):return x if type(x) in [int,float] and math.isfinite(x) else None

def training_workspace(root,directory):
    root=Path(root).resolve();directory=Path(directory).resolve()
    for parent in directory.parents:
        if parent.name=='training' and parent.parent.name=='outputs':
            workspace=parent.parent.parent
            if workspace==root or workspace.parent==root/'experiments':return workspace
    return None

def scores(value):
    pooled=value.get('pooled_duration') or value.get('frame') or {}
    ba=value.get('session_balanced_accuracy',pooled.get('balanced_accuracy'))
    return dict(ba=number(ba),pooled_ba=number(pooled.get('balanced_accuracy')),accuracy=number(pooled.get('accuracy')),
        macro_f1=number(pooled.get('macro_f1')),coverage=number(pooled.get('coverage',value.get('coverage'))),
        recordings=len(value.get('per_recording',[])) or None,
        semantics='录制宏平均 · 含拒识' if 'session_balanced_accuracy' in value else '合格帧 · 训练回放' if pooled else '尚无匹配评分')

def shadow_scores(value):
    timelines=[t['raw_duration_scores'] for t in value.get('timelines',[]) if t.get('intervention')=='normal' and isinstance(t.get('raw_duration_scores'),dict)]
    if not timelines:return scores({})
    support=[0.]*9;correct=[0.]*9;known=eligible=0.
    for t in timelines:
        matrix=t.get('confusion_seconds_with_reject_column',[])
        for i,row in enumerate(matrix[:9]):
            support[i]+=sum(row);correct[i]+=row[i] if len(row)>i else 0
        seconds=t.get('known_seconds',0);known+=seconds;eligible+=seconds*(t.get('coverage') or 0)
    supported=[i for i,v in enumerate(support)if v>0]
    return dict(ba=sum(t['balanced_accuracy'] for t in timelines)/len(timelines),
        pooled_ba=sum(correct[i]/support[i] for i in supported)/len(supported) if supported else None,
        accuracy=sum(correct)/known if known else None,macro_f1=None,coverage=eligible/known if known else None,
        recordings=len(timelines),semantics='录制宏平均 · 含拒识')

def family(row):
    stage=row.get('stage');placement=row.get('placement') or ('core' if 'sequence' in (stage or '') else 'core')
    params=row.get('parameters');name=row['experiment']
    capacity='large' if '-large-' in name or params and params>60000000 else 'main' if '-main-' in name or params and params>20000000 else 'pilot'
    if row.get('adapter')=='fit80':return 'fit80','JEPA · 冻结编码器分类头'
    if row.get('adapter')=='legacy':return row.get('legacy_family','legacy'),'历史训练模型'
    prefix=('公共 JEPA · '+('核心 GRU' if placement=='core' else '无 GRU' if placement=='none' else '后置 GRU')) if stage=='public_ssl' else 'JEPA · 核心 GRU' if placement=='core' else 'JEPA · 后置 GRU' if placement=='post' else 'JEPA · 无 GRU'
    return f'{stage=="public_ssl"}-{capacity}-{placement}',prefix+' / '+{'pilot':'小容量','main':'主容量','large':'大容量'}[capacity]

def group_models(rows):
    groups={}
    for row in sorted(rows,key=lambda r:r.get('updated_at',0),reverse=True):
        key,title=family(row);groups.setdefault(key,dict(id=key,title=title,versions=[]))['versions'].append(row)
    return list(groups.values())

def normalize(directory,report,root,weight_roots=()):
    name=directory.name;stage=report.get('stage');schema=report.get('schema','')
    if 'actual_updates' not in report or not report.get('protocol_sha256'):return None
    weight=directory/'best.pt'
    for folder in weight_roots:
        matches=list(folder.glob('*/'+name+'/best.pt'))
        if matches:weight=matches[0];break
    available=weight.is_file() and weight.resolve().is_relative_to(weight.parent.resolve())
    architecture=report.get('architecture') or {}
    if (directory/'architecture.json').is_file():
        try:architecture=read(directory/'architecture.json')
        except (OSError,ValueError):pass
    adapter='placement' if stage in ['supervised_stable_sequence','supervised_balanced_sequence'] or any(x in schema for x in ['d05-training','continuous-supervised','sequence-training']) else 'causal' if 'causal' in schema or directory.parent.name in ['causal_v2','causal_v6'] else None
    digest=report.get('weights_sha256');protocol=report['protocol_sha256'];step=report.get('best_step')
    valid_hash=isinstance(digest,str) and len(digest)==64 and all(c in '0123456789abcdef' for c in digest)
    seed=report.get('actual_seed')
    workspace=training_workspace(root,directory) or root
    protocol_path=workspace/'configs'/(name+'.protocol.json')
    if seed is None and protocol_path.is_file():
        try:
            if sha(protocol_path)==protocol:seed=read(protocol_path).get('runner',{}).get('seed')
        except (OSError,ValueError):pass
    result=report.get('results') or {};evaluation_path=None;evaluation_hash=None
    # Embedded results are the selected checkpoint results, never a last/probe maximum.
    if not result:
        evaluations=list((directory/'evaluation').glob('*/report.json'))
        evaluations+=list((root/'outputs/refactor_v2/private_eval_v2'/name).glob('*/report.json'))
        for path in sorted(evaluations,key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                ev=read(path)
                if ev.get('weights_sha256')!=digest or ev.get('protocol_sha256')!=protocol or ev.get('checkpoint_step')!=step:continue
                result=ev.get('results',{});evaluation_path=str(path);evaluation_hash=sha(path);break
            except (OSError,ValueError):continue
    def role(key):
        value=result.get(key,{})
        return shadow_scores(value) if 'timelines' in value else scores(value)
    reason='可实时试用 · 尚未独立验收'
    if stage=='public_ssl':reason='公共预训练底模，尚无导航分类适配'
    elif not adapter:reason='尚无兼容的实时适配器'
    elif not available:reason='尚无封存权重文件'
    elif not valid_hash:reason='缺少权重身份，不能加载'
    identity=hashlib.sha256((name+':'+str(digest)+':'+str(step)).encode()).hexdigest()[:24]
    return dict(id=identity,experiment=name,version=name,adapter=adapter,stage=stage,placement=report.get('placement',architecture.get('placement','core')),
        parameters=architecture.get('parameters'),seed=seed,updates=report.get('actual_updates'),checkpoint_step=step,
        initial_retained=step==0,weights_sha256=digest,protocol_sha256=protocol,report_sha256=sha(directory/'report.json'),report_path=str(directory/'report.json'),
        evaluation_path=evaluation_path,evaluation_sha256=evaluation_hash,weights_path=str(weight) if available else None,
        training=role('train'),development=role('validation'),updated_at=(directory/'report.json').stat().st_mtime,
        can_activate=bool(stage!='public_ssl' and adapter and available and valid_hash),availability_reason=reason,
        completed=True,independent_test=False,live_qualified=report.get('live_qualified') is True,source_kind='sealed_report')

def inventory(root,weight_roots=()):
    root=Path(root).resolve();roots=[p for p in (root/'outputs/training').glob('*') if p.is_dir() and p.resolve().is_relative_to(root)]
    roots += [p for p in (root/'experiments').glob('*/outputs/training/*') if p.is_dir() and p.resolve().is_relative_to(root)]
    roots += [p for p in (root/'outputs/refactor_v2').glob('completed-*') if p.is_dir()]
    out={};errors=[]
    for folder in roots:
        for path in sorted(folder.glob('*/report.json'))[:500]:
            try:
                if not path.resolve().is_relative_to(root):continue
                report=read(path);row=normalize(path.parent,report,root,weight_roots)
                if row:
                    previous=out.get(row['id'])
                    if previous is None or row['can_activate']:out[row['id']]=row
            except (OSError,ValueError,KeyError,TypeError) as error:errors.append(dict(path=str(path),reason=type(error).__name__))
    # Current fit80 runtime is retained as a real rollback/selection target.
    pointer=root/'outputs/live_intent_model.json'
    if pointer.is_file():
        try:
            p=read(pointer)
            if p.get('profile')=='vr_fit80':
                report_path=Path(p.get('report',str(root/'outputs/training/vr_fit80/runs/20261017-033136/report.json')));report_path=report_path if report_path.is_absolute() else root/report_path
                report=read(report_path);r=report['selected'];fit=r['strict_replay'];artifact=Path(p['artifact']);artifact=artifact if artifact.is_absolute() else root/artifact
                out['baseline-fit80']=dict(id='baseline-fit80',experiment=p['model_id'],version=p['model_id'],adapter='fit80',stage='supervised',placement='none',
                    parameters=70087769,seed=report.get('selected_training_fit_seed'),updates=None,checkpoint_step=r.get('epoch'),weights_sha256=p['artifact_sha256'],
                    report_sha256=p['report_sha256'],report_path=str(report_path),weights_path=str(artifact),training=scores(fit['metrics']),
                    development=scores(fit['development']['metrics']),updated_at=report.get('updated_at',0),can_activate=True,
                    availability_reason='现有实时模型 · 训练内拟合与开发成绩分别列示',completed=True,independent_test=False,live_qualified=False,source_kind='current_live_pointer')
        except (OSError,ValueError,KeyError,TypeError) as error:errors.append(dict(path=str(pointer),reason=type(error).__name__))
    return dict(schema='model-library-v1',models=list(out.values()),errors=errors,generated_at=time.time(),independent_test=False)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args()
    print(json.dumps(inventory(Path(args.root)),ensure_ascii=True,allow_nan=False))
