import pytest
import numpy as np

CONFIG=dict(window_updates=3,votes=2,cooldown_updates=4,quiet_updates=2,fast_activity=.85,fast_direction=.7,fast_motion=4.)
HEAD=dict(activity=.55,direction=.4,margin=.1,confirm=2,motion_threshold=2.5)

def test_short_event_survives_one_weak_prediction_without_unbounded_accumulation():
    from gnautilus_ml.eye_evidence import EyeEvidenceLatch
    latch=EyeEvidenceLatch(HEAD,CONFIG)
    assert latch.step(.7,[.65,.15,.1,.1],3)==0
    assert latch.step(.3,[.25]*4,.5)==0
    assert latch.step(.7,[.65,.15,.1,.1],3)==1
    latch=EyeEvidenceLatch(HEAD,CONFIG)
    for _ in range(6):
        assert latch.step(.7,[.65,.15,.1,.1],3)==0
        for _ in range(3):assert latch.step(.3,[.25]*4,.5)==0

def test_confident_short_peak_can_trigger_but_ambiguous_motion_cannot():
    from gnautilus_ml.eye_evidence import EyeEvidenceLatch
    latch=EyeEvidenceLatch(HEAD,CONFIG)
    for _ in range(10):assert latch.step(.99,[.25]*4,20)==0
    assert latch.step(.95,[.1,.05,.8,.05],5)==3
    for _ in range(10):assert latch.step(.95,[.1,.05,.8,.05],5)==0
    assert latch.step(.1,[.25]*4,.1)==0
    assert latch.step(.1,[.25]*4,.1)==0
    assert latch.step(.95,[.1,.05,.8,.05],5)==3

def test_invalid_evidence_resets_pending_short_event():
    from gnautilus_ml.eye_evidence import EyeEvidenceLatch
    latch=EyeEvidenceLatch(HEAD,CONFIG);latch.step(.7,[.65,.15,.1,.1],3)
    with pytest.raises(ValueError):latch.step(np.nan,[.65,.15,.1,.1],3)
    assert latch.step(.7,[.65,.15,.1,.1],3)==0

def test_eye_event_scoring_excludes_neighbour_trial_context_without_hiding_it():
    from gnautilus_ml.eye_evidence import score_events
    ds=dict(catalog=dict(trials=[dict(trial_id='a',task_start=100,task_end=500)]),events=dict(segments=[dict(trial_id='a',segment_id='s',kind='manual_direction',class_label='UP',start=100,end=200),dict(trial_id='a',segment_id='q',kind='manual_absent_head_0_2s',class_label='NO_EYE',start=300,end=400)]))
    events=[dict(trial_id='a',source_end=t,direction=1) for t in [50,150,250,350,550]]
    result=score_events(ds,events)
    assert result['strict_matches']==1 and result['scoped_triggers']==3
    assert result['outside_context_triggers']==2 and result['unmatched_scoped_triggers']==2
    assert result['explicit_quiet_triggers']==1

def test_policy_selection_is_a_governed_fit_even_without_new_neural_gradients():
    from gnautilus_ml.event_policy_study import select_policy
    with pytest.raises(RuntimeError,match='governed'):select_policy(None,None,None,None,None)

def test_event_runtime_artifact_roundtrip_and_packet_boundaries(tmp_path):
    from tests.test_intent_repair import repaired_model
    from gnautilus_ml.eye_evidence import EventHybridModel,RUNTIME
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    old=repaired_model();details=dict(old.details,eye_event_config=CONFIG,eye_event_schema='gnautilus.bounded_eye_events.v1');m=EventHybridModel(old.arrays,details,'cpu');x=np.random.default_rng(14).normal(size=(32,777)).astype('float32');whole=m.new_session().step(x);s=m.new_session()
    for at in range(0,777,13):chunked=s.step(x[:,at:at+13])
    assert whole['navigation_decision']==chunked['navigation_decision']
    path=save_artifact(tmp_path,m,dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False));loaded,_=load_artifact(path,device='cpu');np.testing.assert_allclose(loaded.new_session().step(x)['probabilities'],whole['probabilities'])
