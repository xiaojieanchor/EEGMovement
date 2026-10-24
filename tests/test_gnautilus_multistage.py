import json
from unittest.mock import patch
import numpy as np
import pytest
from gnautilus_ml.algorithms import Classifier
from gnautilus_ml.multistage import METHODS,training_folds,_spd_function
from gnautilus_ml.artifacts import save_artifact,load_artifact


def fixture():
    rng=np.random.default_rng(17);y=np.tile(np.arange(4),18);t=np.arange(150)/200
    x=rng.normal(0,.3,(len(y),4,len(t))).astype('float32')
    for i,c in enumerate(y):x[i,c]+=np.sin(2*np.pi*(8+4*c)*t)
    return x,y,np.repeat(['native1','native2','native3'],24)


@pytest.mark.parametrize('method',list(METHODS))
def test_numeric_pipeline_export_and_validation_isolation(tmp_path,method):
    x,y,groups=fixture()
    channels=['Fp1','Fp2','C3','C4'] if method=='gaze_pipeline' else ['C3','Cz','C4','Pz']
    with patch('gnautilus_ml.algorithms.authorize_training'):
        model=Classifier(method,channels,200,.75).fit(x,y,groups)
        other=Classifier(method,channels,200,.75).fit(x,y,groups,validation=(x*999,y[::-1],groups))
    p=model.predict(x)['probabilities'];np.testing.assert_allclose(p.sum(1),1)
    np.testing.assert_array_equal(p,other.predict(x)['probabilities'])
    path=save_artifact(tmp_path,model,{'evaluation_stage':'synthetic'});loaded,meta=load_artifact(path)
    np.testing.assert_allclose(p,loaded.predict(x)['probabilities'],atol=1e-10)
    assert len(model.arrays)<256 and meta['runtime_accepted'] is False
    if method=='pipeline_ensemble':
        assert model.details['selection']=='training_oof_only'
        assert model.details['training_oof_windows']==len(y)
        assert model.details['fold_kind']=='native_training_groups'


def test_native_and_purged_inner_fold_boundaries():
    _,y,g=fixture();folds,kind=training_folds(y,g)
    for train,valid in folds:assert not set(g[train])&set(g[valid])
    folds,kind=training_folds(y,np.repeat('one_source',len(y)))
    assert 'not_independent' in kind
    for train,valid in folds:
        assert not np.any(train&valid)
        a,b=np.flatnonzero(valid)[[0,-1]]
        assert not train[max(0,a-2):min(len(y),b+3)].any()


def test_spd_exponential_keeps_negative_log_eigenvalues():
    reference=np.diag([.3,.7]);np.testing.assert_allclose(_spd_function(np.log(reference.diagonal())*np.eye(2),np.exp,positive=False),reference)


def test_missing_training_authority_still_rejects():
    x,y,g=fixture()
    with pytest.raises(RuntimeError,match='governed'):
        Classifier('pipeline_ensemble',['C3','Cz','C4','Pz'],200,.75).fit(x,y,g)


def test_baseline_neural_export_and_rejection_keeps_native_label(tmp_path):
    pytest.importorskip('torch')
    x,y,g=fixture();events=[]
    with patch('gnautilus_ml.algorithms.authorize_training'),patch('gnautilus_ml.algorithms.compute_device',return_value='cpu'):
        model=Classifier('cnn_gru_baseline',['C3','Cz','C4','Pz'],200,.75,max_epochs=2).fit(x,y,g,progress=events.append,validation=(x,y,g))
    assert len(events)==2 and all('development_loss' in e for e in events)
    np.testing.assert_allclose(model._neural_input(x+100),model._neural_input(x),atol=2e-5)
    model.details['acceptance_policy']={'threshold':1.,'margin':1.}
    path=save_artifact(tmp_path,model,{'evaluation_stage':'synthetic'});loaded,_=load_artifact(path)
    a=model.predict(x);b=loaded.predict(x);np.testing.assert_allclose(a['probabilities'],b['probabilities'],atol=1e-7)
    assert not a['accepted'].any() and set(a['labels'])<=set(y),'rejecting never relabels the native task as REST'
