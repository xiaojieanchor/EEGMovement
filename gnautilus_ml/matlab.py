"""Explicit interchange contract; never evaluates MATLAB objects or callbacks."""
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from .algorithms import Classifier
from .artifacts import save_artifact
from .common import file_hash

def convert_numeric_mat(path,metadata,root):
    if metadata.get('format')!='gnautilus.numeric.v1' or metadata.get('algorithm') not in ['bandpower_lda','csp_lda','gaze_lda']:raise ValueError('Supported numeric sidecar required; vendor WV or protected objects alone are insufficient')
    if Path(path).stat().st_size>64*1024**2:raise ValueError('MAT exceeds size limit')
    values=loadmat(path,squeeze_me=False,struct_as_record=False)
    required=['mean','scale','weight','bias','classes']+(['spatial'] if metadata['algorithm']=='csp_lda' else [])
    if any(k not in values for k in required):raise ValueError('Missing preprocessing/classifier arrays')
    model=Classifier(metadata['algorithm'],metadata['channels'],metadata['sampling_rate'],metadata['window_s']);model.details=metadata['details']
    for key in required:
        value=np.asarray(values[key])
        if value.dtype.kind not in 'fiu' or not np.isfinite(value).all():raise ValueError('Numeric finite MAT arrays required')
        if key in ['classes','mean','scale','bias']:value=value.ravel()
        if key=='classes':model.classes=value
        else:model.arrays[key]=value
    if np.any(model.arrays['scale']<=0):raise ValueError('Invalid normalization scale')
    model.predict(np.ones((1,len(model.channels),round(model.fs*model.window_s)),dtype='float32'))
    evidence={k:metadata[k] for k in ['evaluation_stage','input_contract','electrical_policy','class_labels'] if k in metadata}
    evidence.update(runtime_accepted=False,matlab_source_sha256=file_hash(path),import_semantics='explicit_numeric_conversion_not_vendor_object_execution')
    return save_artifact(root,model,evidence)
