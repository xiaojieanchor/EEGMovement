from unittest.mock import patch
import numpy as np
import pytest


def test_position_target_and_return_are_not_displacement_navigation():
    from scripts.prepare_gaze_position import phase_label
    assert phase_label('dataset1',0,[12,0])=='TARGET_H_POS'
    assert phase_label('dataset1',1,[12,0])=='CENTER_RETURN_TASK'
    assert phase_label('stationary',1,[12,0])=='TARGET_H_POS'
    assert phase_label('nonstationary',2,[0,-12])=='BLINK_TASK'
    assert phase_label('stationary',0,[10,10]) is None


def test_head_motion_angle_stream_can_vary_without_changing_its_native_direction():
    from scripts.prepare_gaze_position import stable_target_label
    stream=np.stack([np.linspace(18,12,192),np.ones(192)])
    assert stable_target_label(stream)=='TARGET_H_POS'
    stream[0,-32:]=-12
    assert stable_target_label(stream) is None


def test_gaze_lowpass_keeps_polarity_and_declared_finite_history():
    from gnautilus_ml.multistage import NumericPipeline
    x=np.zeros((2,2,128));x[0,:,40:]=5;x[1,:,40:]=-5
    model=NumericPipeline('gaze_shape_lda',128)
    a=model.features(x)
    assert not np.array_equal(a[0],a[1])
    # Per-window constant offsets do not become a direction in the shape branch.
    np.testing.assert_allclose(a,model.features(x+500),atol=1e-10)
    level=NumericPipeline('gaze_level_lda',128)
    assert not np.allclose(level.features(x),level.features(x+500))


def test_gaze_pipeline_is_governed_exportable_and_does_not_fit_development():
    from gnautilus_ml.algorithms import Classifier
    rng=np.random.default_rng(3);x=rng.normal(0,.1,(48,2,128));y=np.tile(['NEG','POS'],24)
    x[y=='NEG',:,40:]-=2;x[y=='POS',:,40:]+=2;groups=np.repeat(['a','b','c','d'],12)
    with pytest.raises(RuntimeError):Classifier('gaze_pipeline',['EOG_H','EOG_V'],128,1).fit(x,y,groups)
    with patch('gnautilus_ml.algorithms.authorize_training'):
        a=Classifier('gaze_pipeline',['EOG_H','EOG_V'],128,1).fit(x,y,groups)
        b=Classifier('gaze_pipeline',['EOG_H','EOG_V'],128,1).fit(x,y,groups,validation=(x+100,y[::-1],groups))
    for k in a.arrays:np.testing.assert_array_equal(a.arrays[k],b.arrays[k])
    assert {v['method'] for v in a.details['inner_candidates']}=={'gaze_shape_lda','gaze_level_lda','temporal_lda'}
    restored=Classifier('gaze_pipeline',['EOG_H','EOG_V'],128,1);restored.arrays={k:v.copy() for k,v in a.arrays.items()};restored.classes=a.classes.copy();restored.details=a.details
    np.testing.assert_allclose(a.predict(x)['probabilities'],restored.predict(x)['probabilities'],atol=0)


def test_personal_gaze_pipeline_does_not_use_motor_channels_as_direction_features():
    from gnautilus_ml.algorithms import Classifier
    rng=np.random.default_rng(8);x=rng.normal(size=(48,5,128));y=np.tile(['LEFT','RIGHT'],24);groups=np.repeat(['a','b','c','d'],12)
    with patch('gnautilus_ml.algorithms.authorize_training'):
        model=Classifier('gaze_pipeline',['Fp1','Fp2','Fz','C3','C4'],128,1).fit(x,y,groups)
    changed=x.copy();changed[:,3:]+=2000
    np.testing.assert_array_equal(model.predict(x)['probabilities'],model.predict(changed)['probabilities'])
    assert model.details['analysis_channels']==['Fp1','Fp2','Fz']
