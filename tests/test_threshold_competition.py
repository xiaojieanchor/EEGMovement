import pytest
from gnautilus_ml.intent_repair import IntentPolicy

CONFIG=dict(activity=.75,direction=.4,margin=.05,eye_discount=.1,other_premium=.1,hold_discount=.05,rest_activity=.4,confirm=3,stop=2)

def test_each_direction_competes_against_its_own_frozen_threshold():
    from gnautilus_ml.threshold_competition import ThresholdIntentPolicy
    old=IntentPolicy(CONFIG);new=ThresholdIntentPolicy(CONFIG);old.eye_event(3);new.eye_event(3)
    for _ in range(3):
        before=old.step(.8,[.35,.15,.33,.17]);after=new.step(.8,[.35,.15,.33,.17])
    assert before['status']=='UNKNOWN'
    assert after['direction']==3 and after['status']=='ACTIVE'
    assert after['direction_threshold']==pytest.approx(.3)

def test_eye_prior_cannot_start_without_above_chance_navigation_and_activity():
    from gnautilus_ml.threshold_competition import ThresholdIntentPolicy
    new=ThresholdIntentPolicy(CONFIG);new.eye_event(3)
    for _ in range(10):assert new.step(.8,[.25]*4)['direction']==0
    for _ in range(10):assert new.step(.2,[.1,.1,.7,.1])['direction']==0

def test_strong_contradicting_navigation_can_still_select_another_direction():
    from gnautilus_ml.threshold_competition import ThresholdIntentPolicy
    new=ThresholdIntentPolicy(CONFIG);new.eye_event(3)
    for _ in range(3):r=new.step(.95,[.7,.1,.1,.1])
    assert r['direction']==1
    assert new.step(.1,[.25]*4)['direction']==1
    assert new.step(.1,[.25]*4)['direction']==0

def test_competition_artifact_retains_packet_and_class_contract(tmp_path):
    import numpy as np
    from tests.test_intent_repair import repaired_model
    from tests.test_eye_evidence import CONFIG as EYE_CONFIG
    from gnautilus_ml.threshold_competition import ThresholdHybridModel,RUNTIME
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    old=repaired_model();m=ThresholdHybridModel(old.arrays,dict(old.details,eye_event_config=EYE_CONFIG,eye_event_schema='gnautilus.bounded_eye_events.v1'),'cpu');x=np.random.default_rng(2).normal(size=(32,700)).astype('float32');a=m.new_session().step(x);session=m.new_session()
    for at in range(0,700,19):b=session.step(x[:,at:at+19])
    assert a['navigation_decision']==b['navigation_decision'];path=save_artifact(tmp_path,m,dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False));loaded,_=load_artifact(path,device='cpu');np.testing.assert_allclose(loaded.new_session().step(x)['probabilities'],a['probabilities'])
