import numpy as np
import pytest
from gnautilus_ml.intent_repair import IntentPolicy,EyeFeatures,eye_embedding,evidence

CONFIG=dict(activity=.7,direction=.5,margin=.1,eye_discount=.1,other_premium=.1,hold_discount=.05,rest_activity=.4,confirm=3,stop=2)

def test_activity_and_conditional_direction_do_not_require_joint_product_threshold():
    p=IntentPolicy(CONFIG)
    for _ in range(3):r=p.step(.8,[.55,.15,.15,.15])
    assert r['direction']==1 and .8*.55<.6

def test_eyes_alone_and_rest_uncertainty_cannot_hold_action():
    p=IntentPolicy(CONFIG);p.eye_event(3)
    for _ in range(20):assert p.step(.2,[.1,.1,.7,.1])['direction']==0
    for _ in range(3):r=p.step(.8,[.1,.1,.7,.1])
    assert r['direction']==3
    p.step(.4,[.1,.1,.7,.1]);assert p.step(.4,[.1,.1,.7,.1])['direction']==0
    assert p.cue==3

def test_opposite_eye_event_revokes_old_bias_before_independent_switch():
    p=IntentPolicy(CONFIG);p.eye_event(3);p.eye_event(4)
    assert p.cue==0 and p.pending==4 and p.active==0
    for _ in range(10):assert p.step(.5,[.1,.1,.1,.7])['direction']==0
    for _ in range(3):r=p.step(.8,[.1,.1,.1,.7])
    assert p.cue==4 and r['direction']==4

def test_eye_embedding_matches_original_frozen_head_and_uses_real_partial_pool():
    import torch
    from gnautilus_ml.ocular_event_training import build_model
    torch.set_num_threads(2);net=build_model().eval();x=torch.randn(1,32,103)
    with torch.no_grad():h,p=eye_embedding(net,x);expected=net(x,[103]).softmax(-1)
    torch.testing.assert_close(p,expected,atol=1e-7,rtol=1e-6);assert h.shape==(1,32)

def test_eye_multichannel_features_are_prefix_causal_and_chunk_consistent():
    x=np.random.default_rng(1).normal(size=(32,525));a=EyeFeatures([0,1,4,5,6,8,18,22]);b=EyeFeatures([0,1,4,5,6,8,18,22]);hidden=np.arange(32)
    f=a.step(x,hidden)
    for at in range(0,525,25):g=b.step(x[:,at:at+25],hidden)
    np.testing.assert_allclose(f,g,atol=1e-6);assert f.shape==(128,)

def repaired_model():
    from tests.test_hybrid_navigation import HybridRuntimeTests
    from gnautilus_ml.intent_repair import RepairedHybridModel
    old=HybridRuntimeTests().model();a={k:v.copy() for k,v in old.arrays.items()};a['navigation_input_weights']=np.ones(32,dtype='float32');a['navigation_input_weights'][4:]=0
    a['ocular__net__input_weights']=np.ones((1,32,1),dtype='float32');a['ocular__net__input_weights'][:,8:]=0
    for prefix,n,classes in [('nav__active__',70,2),('nav__direction__',70,4),('ocular_adapter__active__',128,2),('ocular_adapter__direction__',128,4)]:
        a.update({prefix+'mean':np.zeros(n,dtype='float32'),prefix+'scale':np.ones(n,dtype='float32'),prefix+'weight':np.zeros((classes,n),dtype='float32'),prefix+'bias':np.zeros(classes,dtype='float32')})
    a['nav__active__bias'][1]=2;a['nav__direction__bias'][0]=1;a['ocular_adapter__active__bias'][0]=4
    return RepairedHybridModel(a,dict(intent_policy=dict(CONFIG,direction=.4),eye_policy=dict(activity=.7,direction=.5,margin=.1,confirm=3),navigation_features='joint',navigation_architecture='hierarchical',navigation_temperatures=[1.,1.],ocular_temperatures=[1.,1.]),'cpu')

def test_real_runtime_chunking_and_load_contract(tmp_path):
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    from gnautilus_ml.intent_repair import RUNTIME
    model=repaired_model();x=np.random.default_rng(9).normal(size=(32,425)).astype('float32');s=model.new_session();a=s.step(x)
    other=model.new_session()
    for at in range(0,425,17):b=other.step(x[:,at:at+17])
    assert a['navigation_decision']==b['navigation_decision'] and a['navigation_decision']['direction']==1
    assert not model.predict(x[None,:,:375])['accepted'][0]
    path=save_artifact(tmp_path,model,dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False));loaded,_=load_artifact(path,device='cpu');np.testing.assert_allclose(loaded.new_session().step(x)['probabilities'],a['probabilities'])

def test_production_repair_fit_requires_authority():
    from gnautilus_ml.intent_repair_training import fit
    with pytest.raises(RuntimeError,match='governed'):fit(None,None,None,.02,'cpu','unused',lambda:None)

def test_uninformative_probability_average_reproduces_unreachable_direction():
    prior=np.asarray([.5,.125,.125,.125,.125]);perfect=np.asarray([0.,1.,0.,0.,0.]);fused=(prior+perfect)/2
    assert fused[1]==.5625 and fused[1]<.6

def test_legacy_unknown_reason_does_not_break_report_serialization():
    from gnautilus_ml.intent_repair_training import policy_metrics
    from gnautilus_ml.common import encoded
    rows=[dict(trial_id=str(i),label=i,decision=dict(status='UNKNOWN',direction=0)) for i in range(5)]
    r=policy_metrics(rows);assert r['rejection_reasons']=={'legacy_unspecified_rejection':5};assert encoded(r)

def test_motion_gate_blocks_stale_direction_score_and_rearms_without_center_claim():
    from gnautilus_ml.intent_repair import MovementLatch
    latch=MovementLatch(dict(activity=.7,direction=.5,margin=.1,confirm=2,motion_threshold=2.5))
    for _ in range(20):assert latch.step(.95,[.8,.1,.05,.05],motion=.8)==0
    assert latch.step(.95,[.8,.1,.05,.05],motion=3)==0
    assert latch.step(.95,[.8,.1,.05,.05],motion=3)==1
    assert latch.step(.95,[.8,.1,.05,.05],motion=3)==0

def test_frontal_feature_view_uses_same_causal_filter_without_advancing_twice():
    x=np.random.default_rng(7).normal(size=(32,100));a=EyeFeatures([0,1,4,5,6,8,18,22]);b=EyeFeatures(range(8));h=np.arange(32)
    a.step(x,h);expected=b.step(x,h);observed=a.features(h,range(8));np.testing.assert_array_equal(expected,observed);assert a.history.shape[1]==100

def test_near_certain_rest_preserves_conditional_direction_simplex():
    from gnautilus_ml.intent_repair import factor_scores
    active,direction=factor_scores(np.array([1.,1e-20,2e-20,3e-20,4e-20]));np.testing.assert_allclose(direction,[.1,.2,.3,.4]);assert active>0 and active<1e-18
    assert IntentPolicy(CONFIG).step(float(active),direction)['status']=='REST'
    active,direction=factor_scores(np.array([1.,0.,0.,0.,0.]));assert active==0;np.testing.assert_allclose(direction,[.25]*4)


def test_temperature_rescaling_preserves_tiny_conditional_direction_ratios():
    from gnautilus_ml.intent_repair_training import rescale
    from gnautilus_ml.intent_repair import factor_scores
    p=np.array([[1.,1e-20,2e-20,3e-20,4e-20]])
    _,d=factor_scores(rescale(p,'hierarchical',[1.,1.]));np.testing.assert_allclose(d,[[.1,.2,.3,.4]],atol=1e-7)
