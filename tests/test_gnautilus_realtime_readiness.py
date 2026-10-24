"""Read-only readiness must not equate a populated library with live compatibility."""
from scripts.check_gnautilus_realtime import assess
import pytest


def fixture():
    channels=[dict(name='C'+str(i),kind='EEG',unit='uV') for i in range(32)]
    d=dict(channels=channels,sampling_rate=512,reference='fixture',configuration={},layout_verified=True,source_kind='gtec_live')
    model=dict(model_id='fixture',runtime_accepted=True,class_labels=['REST','FORWARD','BACKWARD','LEFT','RIGHT'],window_s=1.5,
               input_contract=dict(channels=[c['name'] for c in channels],sampling_rate=512,reference='fixture',filter_configuration={}))
    status=dict(source=dict(fresh=True,continuous_samples=1024,source_epoch=2),gnautilus=dict(descriptor=d),recording=dict(active=False),acquisition=dict(active=False),
                vr=dict(clients=[dict(role='unity',online=True,telemetry_age_ms=50,telemetry=dict(motion_schema=3))]),control=dict(enabled=True))
    models=dict(models=[model],current_model_id='fixture',remote_fresh=True)
    intent=dict(inference_enabled=True,fresh=True,latest=dict(eligible=True,model_id='fixture',source_epoch=2),
                control=dict(decision=dict(active=True,model_ready=True,epoch=2,reason='experimental_model_active')))
    jobs=dict(server=dict(worker_connected=True))
    return status,models,intent,jobs


def test_32_channels_alone_do_not_make_offline_model_live():
    args=fixture();args[1]['models'][0]['runtime_accepted']=False
    report=assess(*args)
    assert not report['ready_to_attempt_inference'] and not report['current_motion_conditions_met']
    assert 'live_navigation_model' in report['blockers']


def test_order_mismatch_is_not_fixed_by_matching_channel_count():
    args=fixture();args[0]['gnautilus']['descriptor']['channels'].reverse()
    assert 'input_contract_matches' in assess(*args)['blockers']


def test_fresh_compatible_input_still_needs_independent_manual_actions():
    args=fixture();args[0]['control']['enabled']=False
    report=assess(*args)
    assert report['ready_to_attempt_inference'] and not report['current_motion_conditions_met']
    args[0]['control']['enabled']=True
    assert assess(*args)['current_motion_conditions_met']
    args[0]['acquisition']['active']=True
    assert not assess(*args)['current_motion_conditions_met']


def test_csharp_harness_or_old_vr_is_not_a_current_unity_execution_path():
    args=fixture();args[0]['vr']['clients'][0]['role']='csharp_harness'
    assert not assess(*args)['current_motion_conditions_met']


@pytest.mark.parametrize('reason',['below_active_threshold','confirming_model_updates'])
def test_eligible_classifier_is_not_an_active_movement_decision(reason):
    args=fixture();args[2]['control']['decision'].update(active=False,reason=reason)
    assert not assess(*args)['current_motion_conditions_met']


def test_decision_and_prediction_must_belong_to_current_model_and_epoch():
    args=fixture();args[2]['latest']['model_id']='old-model'
    assert not assess(*args)['current_motion_conditions_met']
    args=fixture();args[2]['control']['decision']['epoch']=1
    assert not assess(*args)['current_motion_conditions_met']
    args=fixture();args[0]['vr']['clients'][0]['telemetry_age_ms']=300
    assert not assess(*args)['current_motion_conditions_met']
