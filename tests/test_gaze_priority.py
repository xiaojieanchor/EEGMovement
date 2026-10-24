from unittest.mock import Mock
import pytest


def test_navigation_profile_is_default_without_overwriting_explicit_older_profile(tmp_path):
    from hub.ml_runtime import MLManager
    from gnautilus_ml.common import atomic
    manager=MLManager(Mock(),tmp_path,Mock())
    assert manager.api_get('/api/ml/collection-profile')['experiment_mode']=='navigation_mi_gaze'
    atomic(tmp_path/'hub/runtime/gnautilus_collection.json',{'experiment_mode':'paired'})
    assert manager.api_get('/api/ml/collection-profile')['experiment_mode']=='paired'


def test_gaze_task_does_not_require_limb_imagery_or_forbid_controllers():
    from gnautilus_ml.tasks import task_spec
    for label,eye in [('FORWARD','UP'),('BACKWARD','DOWN'),('LEFT','LEFT'),('RIGHT','RIGHT')]:
        spec=task_spec(label,'gaze_only')
        assert spec['label']==label and spec['gaze_instruction']==eye
        assert spec['limb_instruction']=='NO_CONTROL'
        assert '无需肢体想象' in spec['instruction'] and '可正常操作手柄' in spec['instruction']
        assert '不要实际移动手腿' not in spec['instruction']
        assert spec['task_binding_version']=='gaze_navigation_v2'
    assert '不发出导航意图' in task_spec('REST','gaze_only')['instruction']


def test_no_control_is_explicit_and_legacy_paired_mapping_remains():
    from gnautilus_ml.tasks import task_spec
    assert task_spec('FORWARD','no_control')['label']=='REST'
    assert task_spec('LEFT','interference')['label']=='REST'
    assert task_spec('LEFT','paired')['limb_instruction']=='LEFT_HAND_MI'
    with pytest.raises(ValueError):task_spec('UNKNOWN','gaze_only')


def test_download_sources_separate_bipolar_and_head_motion_references():
    from scripts.download_gaze_reference import SOURCES
    assert SOURCES['dataset1']=='DATASET.zip'
    assert SOURCES['stationary']=='Dataset_Stationary.zip'
    assert SOURCES['nonstationary']=='Dataset_NonStationary.zip'


def test_model_selection_identity_comes_from_actual_protocol_not_dataset_note():
    from gnautilus_ml.runner import evaluation_scope
    dataset={'evaluation_scope':{'selection':'training_oof_only','outer':'viewed_development'}}
    result=evaluation_scope(dataset,{'selection':'development_group_macro_BA_tie_earliest'})
    assert result['selection']=='development_group_macro_BA_tie_earliest'
    assert result['dataset_selection_note']=='training_oof_only'
    assert dataset['evaluation_scope']['selection']=='training_oof_only'


def test_actual_guided_response_delivers_gaze_instructions_without_limb_requirement():
    from types import SimpleNamespace
    from tests.test_acquisition import FakeHub
    from hub.acquisition import GuidedAcquisition
    hub=FakeHub();hub.new_device_mode=True;hub.gnautilus_runtime=SimpleNamespace(fresh=lambda:True,descriptor=None)
    hub.ml=SimpleNamespace(api_get=lambda path:{'experiment_mode':'gaze_only'})
    acquisition=GuidedAcquisition(hub,clock=lambda:100)
    session=acquisition.start(dict(build_id='synthetic-test',paradigm='guided_motor_imagery',presentation_protocol='gnautilus_hybrid_neutral_v1',blocks=1))
    assert session['task_binding_version']=='gaze_navigation_v2'
    assert all(t['limb_instruction']=='NO_CONTROL' and '可正常操作手柄' in t['instruction'] for t in session['trials'])
    assert set(t['label'] for t in session['trials'])=={'FORWARD','BACKWARD','LEFT','RIGHT','REST'}


def test_old_selfpaced_client_cannot_silently_record_gaze_only_without_guidance():
    from types import SimpleNamespace
    from tests.test_acquisition import FakeHub
    from hub.acquisition import GuidedAcquisition
    hub=FakeHub();hub.new_device_mode=True;hub.ml=SimpleNamespace(api_get=lambda path:{'experiment_mode':'gaze_only'})
    a=GuidedAcquisition(hub,clock=lambda:100)
    request=dict(build_id='synthetic-test',paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2')
    with pytest.raises(ValueError,match='提示'):a.start_manual(request)
    assert hub.recorder is None
    result=a.start_manual(dict(request,task_guidance_version='gaze_navigation_v2'))
    assert len(result['task_instructions_list'])==5 and all('无需肢体想象' in v['instruction'] for v in result['task_instructions_list'])


def test_updated_preparer_cannot_overwrite_an_older_study_namespace():
    from scripts.prepare_gaze_position import study_name
    assert study_name('plan','a'*64,'b'*64)!=study_name('plan','a'*64,'c'*64)
