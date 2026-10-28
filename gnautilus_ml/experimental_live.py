"""One explicitly authorized experimental deployment; historical artifacts stay immutable."""
import shutil
from pathlib import Path
from .common import atomic,digest,file_hash,read_json

SOURCE_ID='d5fcad0824c32086f544e9be13612cfbf4d63d2825021ea635d779247df5eecd'
WEIGHTS_SHA256='7b26bc17711d4156da0d6c54917279ac4eaf6b0b64bd1f165c9ffe8a79554046'
STAGE='experimental_live_v1'

def authorized_experimental(meta):
    d=meta.get('deployment') or {}
    return (d.get('stage')==STAGE and d.get('source_model_id')==SOURCE_ID and
            d.get('authorization')=='explicit_user_request_20261026' and
            meta.get('weights_sha256')==WEIGHTS_SHA256 and
            meta.get('runtime_schema')=='gnautilus.hybrid_navigation.v3' and
            meta.get('independent_test') is False)

def promote(source,root):
    source=Path(source);meta=read_json(source/'manifest.json');identity=dict(meta);mid=identity.pop('model_id',None)
    if mid!=SOURCE_ID or digest(identity)!=mid or file_hash(source/'weights.npz')!=WEIGHTS_SHA256:
        raise ValueError('Only the explicitly authorized v3 artifact can be deployed')
    if meta.get('runtime_accepted') or meta.get('runtime_schema')!='gnautilus.hybrid_navigation.v3':raise ValueError('Expected original offline v3 research artifact')
    record=dict(meta,runtime_accepted=True,independent_test=False,
        deployment=dict(stage=STAGE,title='阶段一 · 实验性 LIVE',source_model_id=mid,
                        authorization='explicit_user_request_20261026',scientific_validation=False,
                        semantics='Operator-authorized experimental execution; not independent or stable-navigation acceptance'))
    record.pop('model_id',None);record['model_id']=digest(record)
    if not authorized_experimental(record):raise ValueError('Experimental deployment contract differs')
    target=Path(root)/record['model_id'];target.mkdir(parents=True,exist_ok=True)
    if (target/'manifest.json').exists():
        if read_json(target/'manifest.json')!=record or file_hash(target/'weights.npz')!=WEIGHTS_SHA256:raise ValueError('Immutable deployment conflict')
    else:
        shutil.copyfile(source/'weights.npz',target/'weights.npz');atomic(target/'manifest.json',record)
    return target
