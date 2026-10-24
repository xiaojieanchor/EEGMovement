"""Full synthetic recording -> private-service executor -> registry -> inference test."""
import json,threading
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
from gnautilus_acquisition.storage import StreamJournal
from gnautilus_acquisition.contract import block_frame,fingerprint
from tests.test_gnautilus_transport import descriptor
from gnautilus_ml.common import atomic,digest,read_json

def fixture_record(path,seed,epoch=0):
    rng=np.random.default_rng(seed);d=descriptor(source='gtec_live',width=7,fs=128)
    names=['C3','Cz','C4','Fp1','Fp2','F7','F8']
    for c,n in zip(d['channels'],names):c['name']=n
    d.update(stream_id=f'{seed:032x}',reference='right_ear',layout_verified=True)
    journal=StreamJournal(path,d);xs=[];events=[];at=0
    for label in range(5):
        t=np.arange(384)/128;x=rng.normal(0,1,(384,7));x[:,label%3]+=10*np.sin(2*np.pi*(9+label*3)*t);x[:,3:]+=label*4
        xs.append(x);events.extend([dict(kind='marker',phase='task',label=['REST','FORWARD','BACKWARD','LEFT','RIGHT'][label],sample_index_end=at,source_epoch=0),dict(kind='marker',phase='rest',sample_index_end=at+384,source_epoch=0)]);at+=384
    x=np.concatenate(xs).astype('float32');h,raw=block_frame(d,1,0,x);journal.append(h,raw)
    for event in events:journal.event(event)
    journal.close();return d

def test_complete_job_is_registered_and_production_entry_is_governed(tmp_path):
    from gnautilus_ml.service import ModelService
    import gnautilus_ml
    root=tmp_path/'remote';code=Path(gnautilus_ml.__file__).resolve().parents[1];s=ModelService(root,{},code);records=[]
    for i in range(4):
        uid=f'{i+1:064x}';fixture_record(root/'data'/uid,i+1)
        records.append(dict(upload_id=uid,attributes=dict(subject='S',date=f'2026-10-{i+1:02}',wearing_id=f'wear{i}',role='train' if i<3 else 'development',experiment_mode='paired',reference='right_ear',electrical_policy=dict(rail_uv=20000,flat_ptp_uv=.001,max_rms_uv=5000,max_line_ratio=.8))))
    dataset=s.post('/datasets/import',{'records':records})
    for algorithm in ['bandpower_lda','csp_lda','gaze_lda','hybrid']:
        job=s.post('/jobs',dict(dataset_id=dataset['dataset_id'],algorithm=algorithm,max_epochs=2))
        with patch('gnautilus_ml.governance.check_runtime',return_value='cpu'):s.runner.run(job['id'])
        actual=s.registry.job(job['id']);assert actual['state']=='completed',s.registry.events(job['id'])[-2:]
        assert s.registry.model(actual['model_id'])['job_id']==job['id']
    mid=actual['model_id'];s.post('/models/activate',{'id':mid});reply=s.post('/inference/start',{})
    d=read_json(root/'data'/records[0]['upload_id']/'descriptor.json');values=np.fromfile(root/'data'/records[0]['upload_id']/'raw.f32',dtype='<f4').reshape(-1,7)
    out=s.post('/inference/predict',dict(model_id=mid,generation=reply['generation'],descriptor=d,stream_id=d['stream_id'],source_epoch=0,sample_index_end=192,samples=values[:192].T.tolist(),device_valid=True))
    assert out['eligible'] and len(out['probabilities'])==5
    with pytest.raises(ValueError,match='Duplicate'):s.post('/inference/predict',dict(model_id=mid,generation=reply['generation'],descriptor=d,stream_id=d['stream_id'],source_epoch=0,sample_index_end=192,samples=values[:192].T.tolist()))
    report=s.post('/models/replay',{'dataset_id':dataset['dataset_id'],'role':'development'})
    assert report['motion_enabled'] is False and report['independent_test'] is False
    s.post('/inference/stop',{})
    with pytest.raises(ValueError):s.post('/inference/predict',{})

def test_immutable_native_record_detects_changed_event(tmp_path):
    from gnautilus_ml.data import read_native
    fixture_record(tmp_path,12);read_native(tmp_path)
    with (tmp_path/'events.jsonl').open('a') as f:f.write('{}\n')
    with pytest.raises(ValueError,match='changed'):read_native(tmp_path)
