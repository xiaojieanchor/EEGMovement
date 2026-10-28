"""The complete entrypoint runs on synthetic CPU fixtures under a mocked authority."""
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import numpy as np
import torch
import pytest
from gnautilus_ml.common import atomic,digest
from gnautilus_ml.navigation_contract import DEFAULT_POLICY,OPENBMI
from gnautilus_ml.stream_jepa import CausalJEPA
from gnautilus_ml.jepa_training import evaluate,DomainStudent,run_study
from tests.test_navigation_jepa import descriptor


def episodes():
    rng=np.random.default_rng(10);rows=[]
    for label in range(5):
        y=np.full(650,-1,dtype='int8');y[200:600]=label
        rows.append(dict(id=str(label),x=rng.normal(size=(32,650)).astype('float32'),y=y,
                         metadata=dict(stream_id='fixture',context_start=0,context_end=650,onset_in_episode=200,offset_in_episode=600,contract_id='native_fixture')))
    return rows


def test_evaluation_includes_rejections_and_keeps_event_anchors():
    from gnautilus_ml.stream_jepa import StreamModel,arrays_from
    net=CausalJEPA();model=StreamModel(arrays_from(net,np.zeros(32),np.ones(32)),dict(decision_policy=DEFAULT_POLICY),'cpu')
    report,frames=evaluate(model,episodes())
    assert len(report['recall'])==5 and report['complete_trials']==5
    assert report['independent_test'] is False
    assert 0<=report['coverage']<=1
    assert any(r['raw_prediction']==-1 for r in frames)


def test_actual_supervised_trial_path_carries_detached_state():
    from gnautilus_ml.jepa_training import prepared,normalization,supervised_trial_backward
    r=episodes()[1];r['x']=np.random.default_rng(2).normal(size=(32,5500)).astype('float32');r['y']=np.ones(5500,dtype='int8')
    rows=prepared([r]);mean,scale=normalization(rows);net=CausalJEPA()
    with patch.object(net,'sequence',wraps=net.sequence) as call:
        loss=supervised_trial_backward(net,rows[0],mean,scale,'cpu')
        assert call.call_count==3
        assert call.call_args_list[0].args[1] is None
        assert all(c.args[1]['motor']['hidden'].grad_fn is None for c in call.call_args_list[1:])
    assert np.isfinite(loss) and net.motor.gru.weight_hh_l0.grad.norm()>0


def test_ssl_phase_identity_allows_only_non_ssl_changes(tmp_path):
    import shutil
    from gnautilus_ml.personal_study import ssl_phase_identity
    import gnautilus_ml
    source=Path(gnautilus_ml.__file__).resolve().parent.parent;shutil.copytree(source/'gnautilus_ml',tmp_path/'gnautilus_ml',ignore=shutil.ignore_patterns('__pycache__'))
    p=dict(dataset_sha256='fixture',ssl_steps=1000,context_max_samples=2000,target_supports=[[25,150],[150,275]],hop_samples=25,reuse_ids=['a'],normalization_roles=['train'])
    before=ssl_phase_identity(tmp_path,p);file=tmp_path/'gnautilus_ml/jepa_training.py';s=file.read_text('utf-8')
    file.write_text(s.replace('best=None;best_key=None;best_epoch=None','best=None;best_key=None;best_epoch=None;supervised_only_test_flag=True'),encoding='utf-8')
    assert ssl_phase_identity(tmp_path,p)==before
    file=tmp_path/'gnautilus_ml/stream_jepa.py';s=file.read_text('utf-8');file.write_text(s.replace('reg=.1*torch.relu','reg=.2*torch.relu'),encoding='utf-8')
    assert ssl_phase_identity(tmp_path,p)!=before


@pytest.mark.skipif(not torch.cuda.is_available(),reason='synthetic CUDA checkpoint restore check')
def test_cuda_loaded_checkpoint_restores_cpu_byte_rng_state(tmp_path):
    from gnautilus_ml.jepa_training import restore_torch_rng
    torch.manual_seed(123);state=dict(torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all())
    expected=torch.rand(8,device='cuda:0');p=tmp_path/'synthetic-rng.pt';torch.save(state,p)
    restored=torch.load(p,map_location='cuda:0',weights_only=True)
    assert restored['cuda_rng'][0].device.type=='cuda'
    restore_torch_rng(restored);actual=torch.rand(8,device='cuda:0')
    torch.testing.assert_close(expected,actual,atol=0,rtol=0)


def test_complete_study_entry_exports_and_registers_only_after_freeze(tmp_path):
    from gnautilus_ml.registry import Registry
    from gnautilus_ml.runner import Runner
    from gnautilus_ml.network import build
    from gnautilus_ml.navigation_contract import MOTOR
    root=tmp_path;registry=Registry(root/'state.sqlite');runner=Runner(root,registry,{},root)
    dataset=dict(folder=str(root/'data'),contract_review=dict(synthetic_fixture=True),evaluation_scope=dict(independent_test=False),ssl_guard_samples=2275)
    d=descriptor();atomic(root/'data/.metadata/canonical/fixture/descriptor.original.json',d)
    protocol=dict(schema='gnautilus.training_protocol.v2',seeds=[17,29,41],max_wall_seconds=120,ssl_steps=1,max_epochs=1,
                  target_supports=[[25,150],[150,275]],code_sha256={'synthetic':'fixture'})
    atomic(root/'protocols'/(digest(protocol)+'.json'),protocol)
    job=registry.create_job(dict(algorithm='causal_jepa_stream'),'synthetic-full-entry');registry.transition(job['id'],'preparing')
    def domains(root,net,inventory,device,progress,transfer=True):
        old=build(24,2);teacher=SimpleNamespace(channels=MOTOR,classes=np.arange(2),arrays={'net__'+k:v.detach().numpy() for k,v in old.state_dict().items()})
        student=DomainStudent(net.motor,teacher,copy_parameters=False)
        return [dict(id=OPENBMI,kind='motor',student=student,x=torch.randn(16,24,375),y=torch.arange(16)%2,target=torch.full((16,2),.5))]
    with patch('gnautilus_ml.governance.check_protocol'),patch('gnautilus_ml.governance.check_runtime',return_value='cpu'),\
         patch('gnautilus_ml.jepa_training.reuse_inventory',return_value=[]),patch('gnautilus_ml.jepa_training.load_episodes',side_effect=lambda *a:episodes()),\
         patch('gnautilus_ml.jepa_training.unlabelled_regions',return_value=[]),patch('gnautilus_ml.jepa_training.native_domains',side_effect=domains),\
         patch('gnautilus_ml.jepa_training.initialize',side_effect=lambda *a:(CausalJEPA(),{})),\
         patch('gnautilus_ml.jepa_training.select_policy',return_value=[]):
        with patch('gnautilus_ml.artifacts.save_artifact',side_effect=OSError('injected late export failure')):
            with pytest.raises(OSError,match='late export'):run_study(runner,job['id'],dataset,protocol)
        registry.transition(job['id'],'interrupted');registry.resume(job['id']);registry.transition(job['id'],'preparing')
        with patch('gnautilus_ml.jepa_training.finetune',side_effect=AssertionError('must not refit after test access')):
            run_study(runner,job['id'],dataset,protocol)
    assert registry.job(job['id'])['state']=='completed'
    folder=root/'logs'/('jepa-'+job['id'])
    assert (folder/'selection-freeze.json').exists() and (folder/'completed.json').exists()
    assert len(registry.models())==1 and registry.selected() is None
    assert runner.inference_enabled is False
