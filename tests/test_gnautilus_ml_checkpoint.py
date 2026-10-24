from pathlib import Path
from unittest.mock import patch
import numpy as np

def test_synthetic_neural_resume_matches_uninterrupted_training(tmp_path):
    import pytest
    from gnautilus_ml.algorithms import Classifier
    from tests.test_gnautilus_ml_v1 import trials
    torch=pytest.importorskip('torch')
    torch.set_num_threads(2);x,y,g,c,fs=trials();x=x[:40];y=y[:40]
    with patch('gnautilus_ml.algorithms.authorize_training'),patch('gnautilus_ml.algorithms.compute_device',return_value='cpu'):
        first=Classifier('cnn_gru',c,fs,max_epochs=1);first.checkpoint_path=tmp_path/'latest.pt';first.fit(x,y)
        resumed=Classifier('cnn_gru',c,fs,max_epochs=2);resumed.checkpoint_path=tmp_path/'latest.pt';resumed.fit(x,y)
        full=Classifier('cnn_gru',c,fs,max_epochs=2).fit(x,y)
    np.testing.assert_allclose(resumed.predict(x[:3])['probabilities'],full.predict(x[:3])['probabilities'],atol=1e-7)
