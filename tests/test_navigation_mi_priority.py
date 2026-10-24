"""Synthetic task/metadata checks; no EEG training or movement execution."""
import json
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest


@pytest.mark.parametrize('label,motor,eye',[
    ('FORWARD','IMAGINE_WALK_FORWARD','UP'),('BACKWARD','IMAGINE_WALK_BACKWARD','DOWN'),
    ('LEFT','IMAGINE_TURN_LEFT','LEFT'),('RIGHT','IMAGINE_TURN_RIGHT','RIGHT')])
def test_navigation_imagery_is_primary_and_has_no_prescribed_limb(label,motor,eye):
    from gnautilus_ml.tasks import task_spec
    spec=task_spec(label,'navigation_mi_gaze')
    assert spec['motor_instruction']==motor and spec['gaze_instruction']==eye
    assert spec['limb_instruction']=='NOT_PRESCRIBED'
    assert spec['primary_source']=='motor_imagery' and spec['auxiliary_source']=='gaze'
    assert '第一人称' in spec['instruction'] and '眼动仅作辅助' in spec['instruction']
    assert '可正常操作手柄' in spec['instruction']
    assert '无需肢体想象' not in spec['instruction'] and '想象左手' not in spec['instruction']
    control=task_spec(label,'navigation_mi_only')
    assert control['motor_instruction']==motor and control['gaze_instruction']=='CENTER'
    assert control['auxiliary_source']=='none'


def test_default_and_profile_accept_navigation_without_rewriting_explicit_history(tmp_path):
    from hub.ml_runtime import MLManager
    from gnautilus_ml.common import atomic
    manager=MLManager(Mock(),tmp_path,Mock());manager.hub.acquisition.snapshot.return_value={'active':False}
    assert manager.api_get('/api/ml/collection-profile')['experiment_mode']=='navigation_mi_gaze'
    assert manager.api_get('/api/ml/capabilities')['research_objective']['primary_source']=='motor_imagery'
    assert manager.api_post('/api/ml/collection-profile',{'experiment_mode':'navigation_mi_only'})['experiment_mode']=='navigation_mi_only'
    atomic(tmp_path/'hub/runtime/gnautilus_collection.json',{'experiment_mode':'gaze_only'})
    assert manager.api_get('/api/ml/collection-profile')['experiment_mode']=='gaze_only'


def test_actual_record_markers_keep_navigation_imagery_and_release_unknown():
    from tests.test_acquisition import FakeHub
    from hub.acquisition import GuidedAcquisition
    from gnautilus_ml.data import event_intervals
    hub=FakeHub();hub.new_device_mode=True;hub.gnautilus_runtime=SimpleNamespace(fresh=lambda:True,descriptor=None)
    hub.ml=SimpleNamespace(api_get=lambda path:{'experiment_mode':'navigation_mi_gaze'})
    a=GuidedAcquisition(hub,clock=lambda:100)
    request=dict(build_id='synthetic',presentation_protocol='self_paced_axes_v2',input_surface='desktop')
    with pytest.raises(ValueError,match='提示'):a.start_manual(request)
    assert hub.recorder is None
    session=a.start_manual(dict(request,task_guidance_version='gaze_navigation_v2'))
    assert session['control_intent']=='navigation_motor_imagery_with_gaze_auxiliary'
    for seq,phase in [(1,'hold_start'),(2,'hold_end')]:
        hub.sample_index=500+seq*100
        a.event(dict(session_id=session['session_id'],client_token=session['client_token'],seq=seq,label='FORWARD',phase=phase,sender_monotonic_ms=0,sender_frame=1,clock_offset_ms=0,sync_rtt_ms=1,xr_active=False,pointer_source='desktop_pointer',page_active=True))
    rows=[json.loads(s) for s in hub.recorder.getvalue().splitlines()]
    hold=next(v for v in rows if v.get('phase')=='hold_start')
    assert hold['motor_instruction']=='IMAGINE_WALK_FORWARD' and hold['limb_instruction']=='NOT_PRESCRIBED'
    assert hold['task_binding_version']=='navigation_mi_gaze_v3'
    intervals=event_intervals(rows,1000,epoch=7)
    assert intervals[0]['motor']=='IMAGINE_WALK_FORWARD' and intervals[0]['primary_source']=='motor_imagery'
    assert a.snapshot()['label'] is None and intervals[0]['end']==700


def test_gaze_or_limb_record_cannot_be_imported_as_navigation_imagery(tmp_path):
    from gnautilus_ml.data import check_record
    descriptor={'layout_verified':True,'reference':'REF','source_kind':'gtec_live','configuration_sha256':'hash','configuration':{}}
    record={'descriptor':descriptor,'source_sha256':'source','channels':['C3','Cz','C4'],'sampling_rate':256,
            'intervals':[{'mode':'gaze_only','motor':None}]}
    attrs=dict(subject='fixture',date='2026-10-24',wearing_id='fixture',role='train',reference='REF',experiment_mode='navigation_mi_gaze',electrical_policy={'fixture':True})
    with patch('gnautilus_ml.data.read_native',return_value=record):result=check_record(tmp_path,attrs)
    assert not result['training_ready'] and 'navigation_task_metadata_missing_or_mismatched' in result['blocking_reasons']


def test_capabilities_do_not_present_limb_mapping_as_navigation_default(tmp_path):
    from gnautilus_ml.service import ModelService
    caps=ModelService(tmp_path,{},tmp_path).get('/capabilities')
    assert caps['default_experiment_mode']=='navigation_mi_gaze'
    assert caps['research_objective']['primary_source']=='motor_imagery'
    assert caps['task_specs_by_mode']['navigation_mi_gaze']['FORWARD']['motor_instruction']=='IMAGINE_WALK_FORWARD'


def test_navigation_import_requires_exact_motor_eye_and_source_semantics(tmp_path):
    from gnautilus_ml.data import check_record
    from gnautilus_ml.tasks import task_spec
    spec=task_spec('FORWARD','navigation_mi_gaze')
    interval=dict(label='FORWARD',mode=spec['experiment_mode'],motor=spec['motor_instruction'],gaze=spec['gaze_instruction'],limb=spec['limb_instruction'],
                  **{k:spec[k] for k in ['task_binding_version','primary_source','auxiliary_source']})
    record=dict(descriptor=dict(layout_verified=True,reference='REF',source_kind='gtec_live',configuration_sha256='hash',configuration={}),source_sha256='source',channels=['C3','Cz','C4'],sampling_rate=256,intervals=[interval])
    attrs=dict(subject='fixture',date='2026-10-24',wearing_id='fixture',role='train',reference='REF',experiment_mode='navigation_mi_gaze',electrical_policy={'fixture':True})
    with patch('gnautilus_ml.data.read_native',return_value=record):
        assert check_record(tmp_path,attrs)['training_ready']
        interval['motor']='LEFT_HAND_MI'
        assert 'navigation_task_metadata_missing_or_mismatched' in check_record(tmp_path,attrs)['blocking_reasons']
