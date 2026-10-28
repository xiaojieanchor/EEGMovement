"""Synthetic Hub increment/rejection path; never claims hardware acceptance."""
import time
from unittest.mock import Mock
import numpy as np
import pytest
from hub.ml_runtime import MLIntent
from hub.intent_control import IntentControl


@pytest.mark.parametrize('runtime',['gnautilus.causal_jepa_stream.v1','gnautilus.causal_jepa_stream.v2','gnautilus.hybrid_navigation.v1','gnautilus.hybrid_navigation.v2','gnautilus.hybrid_navigation.v3','gnautilus.hybrid_navigation.v4','gnautilus.hybrid_navigation.v5','gnautilus.hybrid_navigation.v6','gnautilus.hybrid_navigation.v7'])
def test_hub_sends_only_new_samples_after_initial_history(runtime):
    hub=Mock();hub.epoch=4;hub.stop_event.wait.side_effect=[False,False,True]
    d=dict(sampling_rate=250,channels=[dict(name=f'CH{i+1:02}',kind='EEG') for i in range(32)])
    values=np.random.default_rng(1).normal(size=(400,32)).astype('float32')
    def payload(n):return dict(descriptor=d,sample_index_end=n,stream_id='s',source_epoch=4,samples=values[:n],input_origin_monotonic=time.monotonic())
    hub.gnautilus_runtime.model_input.side_effect=[payload(375),payload(400)]
    manager=Mock();manager.cached={'models':[dict(model_id='m',window_s=1.5,algorithm='causal_jepa_stream',runtime_schema=runtime,channels=['c']*32)]}
    intent=MLIntent(hub,manager);intent.model_id='m';intent.inference_enabled=True;intent.remote_generation=1
    def returned(method,path,data,**kw):return dict(source_epoch=4,sample_index_end=data['sample_index_end'],model_id='m',probabilities=[.2]*5,eligible=False,warming=False)
    manager.bridge.call.side_effect=returned;intent.loop()
    requests=[c.args[2] for c in manager.bridge.call.call_args_list]
    assert [(r['sample_index_start'],r['sample_index_end']) for r in requests]==[(0,375),(375,400)]
    assert len(requests[1]['samples'][0])==25


@pytest.mark.parametrize('runtime,policy',[('gnautilus.causal_jepa_stream.v1','motor_evidence_with_bounded_gaze_v1'),('gnautilus.causal_jepa_stream.v2','shared_full32_joint_v2'),('gnautilus.hybrid_navigation.v1','bounded_eye_memory_navigation_v1'),('gnautilus.hybrid_navigation.v2','factorized_navigation_v2'),('gnautilus.hybrid_navigation.v3','factorized_navigation_v2'),('gnautilus.hybrid_navigation.v4','threshold_aware_navigation_v1'),('gnautilus.hybrid_navigation.v5','factorized_navigation_v2'),('gnautilus.hybrid_navigation.v6','factorized_navigation_v2'),('gnautilus.hybrid_navigation.v7','factorized_navigation_v2')])
def test_confirmed_stream_decision_obeys_manual_enable_acquisition_and_duplicates(runtime,policy):
    now=[10.];c=IntentControl(clock=lambda:now[0]);p=dict(source='LIVE',eligible=True,input_origin_monotonic=10.,source_epoch=1,model_id='m',sample_index_end=375,probabilities=[.01,.96,.01,.01,.01],
        control_policy='gnautilus_stream_decision_v1',runtime_schema=runtime,navigation_decision=dict(direction=1,policy=policy))
    assert not c.update(p)['active']
    c.configure({'enabled':True});first=c.update(p);assert first['linear_axis']==1 and first['turn_axis']==0
    assert c.update(p)['decision_id']==first['decision_id']
    assert not c.update(p,acquisition_active=True)['active']
    p['navigation_decision']['direction']=4;p['sample_index_end']=400
    assert c.update(p)['turn_axis']==1
    now[0]=11.;assert not c.update(p)['active']


def test_streaming_thresholds_are_frozen_but_manual_motion_switch_remains_available():
    import pytest
    h=Mock();manager=Mock();manager.cached=dict(loaded='m',models=[dict(model_id='m',runtime_schema='gnautilus.causal_jepa_stream.v1')])
    intent=MLIntent(h,manager)
    with pytest.raises(ValueError,match='冻结'):intent.configure_control(dict(active_threshold=.5))
    h.intent_control.configure.assert_not_called()
    intent.configure_control(dict(enabled=False));h.intent_control.configure.assert_called_once_with(dict(enabled=False))

def test_v2_confirmed_factorized_decision_is_not_gated_again_by_legacy_joint_thresholds():
    c=IntentControl(clock=lambda:10.);c.configure({'enabled':True})
    r=dict(source='LIVE',eligible=True,input_origin_monotonic=10.,source_epoch=1,model_id='m',sample_index_end=400,
        probabilities=[.4,.33,.09,.09,.09],runtime_schema='gnautilus.hybrid_navigation.v2',control_policy='gnautilus_stream_decision_v1',navigation_decision=dict(direction=1,policy='factorized_navigation_v2'))
    assert c.update(r)['active']
    r['sample_index_end']=425;r['navigation_decision']['policy']='unrecognized'
    assert c.update(r)['reason']=='invalid_stream_decision'


def test_private_pilot_is_not_described_as_a_public_native_classifier(tmp_path):
    import time
    from tests.test_compute_autoconnect import manager
    m,b,h=manager(tmp_path);m.cached=dict(loaded='private',selected='private',models=[dict(model_id='private',algorithm='causal_jepa_stream',runtime_accepted=False,pilot=True)])
    m.updated=time.time();b.status.update(ssh_connected=True,worker_connected=True)
    assert '个人联合导航' in m.inference_readiness()['message']
    assert '个人联合导航' in m.snapshot()['groups'][0]['versions'][0]['availability_reason']


def test_partial_prediction_hop_advances_raw_cursor_without_renewing_evidence():
    hub=Mock();hub.epoch=4;hub.stop_event.wait.side_effect=[False,False,False,True]
    d=dict(sampling_rate=250,channels=[dict(name=f'CH{i+1:02}',kind='EEG') for i in range(32)])
    values=np.random.default_rng(11).normal(size=(417,32)).astype('float32');origin=time.monotonic()
    def payload(n):return dict(descriptor=d,sample_index_end=n,stream_id='s',source_epoch=4,samples=values[:n],input_origin_monotonic=origin)
    hub.gnautilus_runtime.model_input.side_effect=[payload(375),payload(392),payload(417)]
    manager=Mock();manager.cached={'models':[dict(model_id='m',window_s=1.5,algorithm='causal_jepa_stream',runtime_schema='gnautilus.causal_jepa_stream.v2',channels=['c']*32)]}
    intent=MLIntent(hub,manager);intent.model_id='m';intent.inference_enabled=True;intent.remote_generation=1
    def returned(method,path,data,**kw):
        end=data['sample_index_end']
        if end==417:assert intent.last_sample==392 and intent.latest['sample_index_end']==375
        return dict(source_epoch=4,sample_index_end=end,prediction_sample_index_end=400 if end==417 else 375,model_id='m',probabilities=[.2]*5,eligible=False,warming=False,new_prediction_updates=0 if end==392 else 1)
    manager.bridge.call.side_effect=returned;intent.loop()
    requests=[c.args[2] for c in manager.bridge.call.call_args_list]
    assert [(r['sample_index_start'],r['sample_index_end']) for r in requests]==[(0,375),(375,392),(392,417)]
    assert intent.latest['input_origin_monotonic']==pytest.approx(origin-17/250)
