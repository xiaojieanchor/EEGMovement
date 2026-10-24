import numpy as np
import pytest
from scipy.io import savemat

def test_matlab_requires_explicit_numeric_contract(tmp_path):
    from gnautilus_ml.matlab import convert_numeric_mat
    savemat(tmp_path/'vendor.mat',{'WV':np.zeros(5)})
    with pytest.raises(ValueError):convert_numeric_mat(tmp_path/'vendor.mat',{},tmp_path/'models')

def test_matlab_numeric_lda_conversion_keeps_signed_scores(tmp_path):
    from gnautilus_ml.matlab import convert_numeric_mat
    from gnautilus_ml.artifacts import load_artifact
    from tests.test_gnautilus_ml_v1 import trials
    from gnautilus_ml.algorithms import Classifier
    from unittest.mock import patch
    x,y,g,c,fs=trials()
    with patch('gnautilus_ml.algorithms.authorize_training'):m=Classifier('bandpower_lda',c,fs).fit(x,y,g)
    savemat(tmp_path/'numeric.mat',dict(m.arrays,classes=m.classes))
    meta=dict(format='gnautilus.numeric.v1',algorithm=m.algorithm,channels=c,sampling_rate=fs,window_s=1.5,details=m.details,evaluation_stage='synthetic')
    path=convert_numeric_mat(tmp_path/'numeric.mat',meta,tmp_path/'models');loaded,manifest=load_artifact(path)
    np.testing.assert_allclose(m.predict(x[:2])['probabilities'],loaded.predict(x[:2])['probabilities'],atol=1e-6)
    assert not manifest['runtime_accepted']
