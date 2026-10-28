import numpy as np
import pytest

def test_closed_form_requires_the_same_training_authority_as_neural_fitting():
    from gnautilus_ml.shrinkage_readout import fit_closed_form
    with pytest.raises(RuntimeError,match='governed'):fit_closed_form(None,None,None,.5)

def test_shrunk_covariance_discriminates_correlated_synthetic_features(monkeypatch):
    import gnautilus_ml.governance as governance
    from gnautilus_ml.shrinkage_readout import fit_closed_form
    from gnautilus_ml.hybrid_navigation import readout_predict
    monkeypatch.setattr(governance,'authorize_training',lambda:None)
    x=np.array([[-2.,-1.],[-1.,-2.],[1.,2.],[2.,1.]]);a=fit_closed_form(x,np.array([0,0,1,1]),np.array(['a','b','c','d']),.5)
    np.testing.assert_array_equal(readout_predict(np.array([[-1.,-1.],[1.,1.]]),a,'').argmax(1),[0,1])
    assert np.isfinite(a['weight']).all()
