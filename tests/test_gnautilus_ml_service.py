import numpy as np
import pytest
from unittest.mock import patch
from gnautilus_ml.common import atomic,digest

def test_service_caps_and_jobs_share_durable_registry(tmp_path):
    from gnautilus_ml.service import ModelService
    s=ModelService(tmp_path,{},tmp_path)
    assert 'csp_lda' in [v['id'] for v in s.get('/capabilities')['algorithms']]
    assert s.get('/models')['models']==[]
    with pytest.raises(ValueError):s.post('/jobs',{'dataset_id':'missing','algorithm':'csp_lda'})
    assert s.get('/snapshot')['inference']['inference_enabled'] is False

def test_governance_rejects_source_reuse_across_wearings(tmp_path):
    from gnautilus_ml.governance import check_protocol
    p=tmp_path/'code.py';p.write_text('code')
    from gnautilus_ml.common import file_hash
    rows=[{'subject':'p','date':d,'wearing_id':d,'role':role,'source_sha256':'same'} for d,role in [('d1','train'),('d2','development')]]
    data={'records':rows};protocol=dict(schema='gnautilus.training_protocol.v1',dataset_sha256=digest(data),algorithm='csp_lda',code_sha256={'code.py':file_hash(p)},seed=17,max_epochs=2)
    with pytest.raises(ValueError,match='Same source'):check_protocol(protocol,data,tmp_path)

def test_cnn_export_roundtrip_synthetic(tmp_path):
    from tests.test_gnautilus_ml_v1 import trials
    from gnautilus_ml.algorithms import Classifier
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    import torch
    torch.set_num_threads(2);x,y,g,c,fs=trials()
    with patch('gnautilus_ml.algorithms.authorize_training'),patch('gnautilus_ml.algorithms.compute_device',return_value='cpu'):
        m=Classifier('cnn_gru',c,fs,max_epochs=2).fit(x[:40],y[:40],g[:40])
    p=save_artifact(tmp_path,m,{'evaluation_stage':'synthetic'});m2,_=load_artifact(p)
    np.testing.assert_allclose(m.predict(x[:2])['probabilities'],m2.predict(x[:2])['probabilities'],atol=1e-6)
