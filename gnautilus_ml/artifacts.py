import os,secrets,shutil
from pathlib import Path
import numpy as np
from .common import atomic,digest,file_hash,read_json
from .algorithms import Classifier

def save_artifact(root,model,evidence):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);stage=root/('.staging-'+secrets.token_hex(8));stage.mkdir()
    try:
        np.savez(stage/'weights.npz',**model.arrays,classes=model.classes)
        meta=dict(schema='gnautilus.model.v1',runtime_schema='gnautilus.finite_history.v1',algorithm=model.algorithm,channels=model.channels,sampling_rate=model.fs,window_s=model.window_s,hop_s=.1,
                  details=model.details,weights_sha256=file_hash(stage/'weights.npz'),runtime_accepted=False)
        meta.update(evidence)
        if meta.get('evaluation_stage')=='synthetic':meta['runtime_accepted']=False
        meta['model_id']=digest(meta);atomic(stage/'manifest.json',meta);target=root/meta['model_id']
        if target.exists():
            if read_json(target/'manifest.json')!=meta:raise ValueError('Immutable artifact conflict')
            shutil.rmtree(stage)
        else:os.replace(stage,target)
        return target
    except BaseException:
        if stage.exists():shutil.rmtree(stage)
        raise

def load_artifact(path,expected_channels=None):
    path=Path(path);meta=read_json(path/'manifest.json')
    if meta.get('schema')!='gnautilus.model.v1':raise ValueError('Unknown model manifest')
    if meta.get('runtime_schema')!='gnautilus.finite_history.v1':raise ValueError('Unsupported classifier runtime/preprocessing version')
    identity=dict(meta);mid=identity.pop('model_id',None)
    if digest(identity)!=mid or file_hash(path/'weights.npz')!=meta.get('weights_sha256'):raise ValueError('Model manifest/weights hash mismatch')
    if expected_channels is not None and expected_channels!=meta['channels']:raise ValueError('Model channel identity/order changed')
    if (path/'weights.npz').stat().st_size>128*1024**2:raise ValueError('Model exceeds bound')
    import zipfile
    with zipfile.ZipFile(path/'weights.npz') as z:
        if sum(v.file_size for v in z.infolist())>128*1024**2 or len(z.infolist())>256:raise ValueError('Expanded model exceeds bounds')
    model=Classifier(meta['algorithm'],meta['channels'],meta['sampling_rate'],meta['window_s']);model.details=meta['details']
    with np.load(path/'weights.npz',allow_pickle=False) as z:
        model.classes=z['classes'].copy();model.arrays={k:z[k].copy() for k in z.files if k!='classes'}
    if model.classes.ndim!=1 or len(model.classes)<2 or len(np.unique(model.classes))!=len(model.classes) or not np.array_equal(model.classes,np.sort(model.classes)):raise ValueError('Model class identity/order is invalid')
    if meta.get('runtime_accepted'):
        from .data import LABELS
        contract=meta.get('input_contract') or {}
        if not np.array_equal(model.classes,np.arange(5)) or meta.get('class_labels')!=LABELS:raise ValueError('Live navigation class order must be REST/FORWARD/BACKWARD/LEFT/RIGHT')
        if contract.get('channels')!=meta['channels'] or contract.get('sampling_rate')!=meta['sampling_rate'] or not contract.get('reference') or 'filter_configuration' not in contract or not meta.get('electrical_policy'):raise ValueError('Live navigation input contract is incomplete')
    if not all(np.isfinite(v).all() for v in model.arrays.values()):raise ValueError('Nonfinite model parameters')
    probe=model.predict(np.ones((1,len(model.channels),round(model.fs*model.window_s)),dtype=np.float32))
    if not np.isfinite(probe['probabilities']).all():raise ValueError('Model failed load probe')
    return model,meta
