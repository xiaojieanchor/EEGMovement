"""Synthetic-only causal, transfer, governance and execution boundary regressions."""
import copy,time
import numpy as np
import pytest
import torch
from gnautilus_ml.navigation_contract import CHANNELS,MOTOR,DEFAULT_POLICY,ELECTRICAL
from gnautilus_ml.stream_jepa import CausalJEPA,Preprocessor,NavigationDecoder,StreamModel,arrays_from,transfer_branch,jepa_loss

torch.set_num_threads(2)


def test_preprocessing_is_prefix_causal_and_chunk_invariant():
    x=np.random.default_rng(1).normal(size=(32,1003))
    whole=Preprocessor().step(x);p=Preprocessor();parts=[]
    for a,b in [(0,17),(17,103),(103,909),(909,1003)]:parts.append(p.step(x[:,a:b]))
    np.testing.assert_allclose(whole,np.concatenate(parts,1),atol=1e-7)
    changed=x.copy();changed[:,501:]*=100
    np.testing.assert_array_equal(whole[:,:501],Preprocessor().step(changed)[:,:501])


def test_neural_state_survives_arbitrary_pooling_remainders():
    torch.manual_seed(3);net=CausalJEPA().eval();x=torch.randn(1,32,1003)
    with torch.no_grad():
        full,state=net.step(x);s=None
        for a,b in [(0,1),(1,17),(17,103),(103,909),(909,1003)]:last,s=net.step(x[:,:,a:b],s)
    for key in ['motor','eye','logits']:torch.testing.assert_close(full[key],last[key],atol=1e-6,rtol=1e-5)
    assert s['motor']['tail'].shape[-1]==3
    altered=x.clone();altered[:,:,600:]*=10
    with torch.no_grad():
        a,_=net.step(x[:,:,:600]);b,_=net.step(altered[:,:,:600])
    torch.testing.assert_close(a['logits'],b['logits'],rtol=0,atol=0)


def test_actual_parameter_transfer_respects_swapped_positions_and_zero_new_columns():
    from gnautilus_ml.network import build
    source=['FC5','FC3','FC1','FC2','FC4','FC6','C5','C3','C1','Cz','C2','C4','C6','CP5','CP3','CP1','CPz','CP2','CP4','CP6']
    torch.manual_seed(5);old=build(20,2).eval();net=CausalJEPA().eval()
    arrays={'net__'+k:v.detach().numpy() for k,v in old.state_dict().items()}
    transfer_branch(net.motor,arrays,source,MOTOR)
    x=torch.randn(2,24,375);selected=x[:,[MOTOR.index(c) for c in source]]
    with torch.no_grad():
        z=torch.nn.functional.elu(old.spatial(old.temporal(torch.nn.functional.pad(selected[:,None],(16,0)))))
        z=torch.nn.functional.avg_pool2d(z,(1,4)).squeeze(2).transpose(1,2);_,h=old.gru(z)
        got,_=net.motor.step(x)
    torch.testing.assert_close(h[-1],got,atol=1e-6,rtol=1e-5)
    assert CHANNELS[15]=='Cz' and CHANNELS[18]=='C5'
    for name in ['FCz','P3','Pz','P4']:assert torch.count_nonzero(net.motor.spatial.weight[:,:,MOTOR.index(name)])==0


def test_jepa_updates_both_encoder_grus_without_teacher_gradients():
    torch.manual_seed(7);net=CausalJEPA();target=copy.deepcopy(net).requires_grad_(False)
    loss,_=jepa_loss(net,target,torch.randn(8,32,375),[torch.randn(8,32,125),torch.randn(8,32,125)])
    loss.backward()
    for branch in [net.motor,net.eye]:
        assert branch.gru.weight_hh_l0.grad.norm()>0
        assert branch.temporal.weight.grad.norm()>0
    assert all(p.grad is None for p in target.parameters())


def test_packed_training_batches_ignore_future_padding():
    torch.manual_seed(4);net=CausalJEPA().eval();x=torch.randn(3,32,650);lengths=[375,503,650]
    with torch.no_grad():
        batched=net.batch(x,lengths)
        for i,n in enumerate(lengths):
            single=net(x[i:i+1,:,:n]);torch.testing.assert_close(single['logits'][0],batched['logits'][i],atol=1e-6,rtol=1e-5)
        for i,n in enumerate(lengths):x[i,:,n:]=10000
        changed=net.batch(x,lengths)
        torch.testing.assert_close(batched['logits'],changed['logits'],atol=0,rtol=0)


def test_sequence_training_preserves_inference_state_across_8_second_chunks():
    torch.manual_seed(8);net=CausalJEPA().eval();x=torch.randn(1,32,4251)
    with torch.no_grad():
        whole,_=net.step(x);state=None
        for a,b in [(0,2000),(2000,4000),(4000,4251)]:o,state=net.sequence(x[:,:,a:b],state)
    torch.testing.assert_close(whole['logits'],o['logits'][:,-1],atol=1e-6,rtol=1e-5)


def motor(p):return [1-p,p,0.,0.,0.]


def test_gaze_support_center_higher_threshold_and_stop_hysteresis():
    d=NavigationDecoder();scores=motor(.8)
    for _ in range(2):assert d.step(scores,scores,'ALIGNED',1)['direction']==0
    assert d.step(scores,scores,'ALIGNED',1)['direction']==1
    # .8 meets aligned holding, but center requires .85-.05; use .79.
    assert d.step(motor(.79),scores,'CENTER')['direction']==1
    assert d.step(motor(.79),scores,'CENTER')['direction']==0
    for _ in range(3):out=d.step(motor(.9),motor(.9),'CENTER')
    assert out['direction']==1


def test_eye_alone_cannot_start_and_return_cannot_reverse():
    d=NavigationDecoder()
    for _ in range(10):assert d.step(motor(.3),motor(.99),'ALIGNED',1)['direction']==0
    for _ in range(3):d.step(motor(.95),motor(.95),'ALIGNED',1)
    for _ in range(10):
        out=d.step([.01,.05,.94,0,0],[.01,.05,.94,0,0],'RETURN',2)
        assert out['direction']!=2


def descriptor():
    return dict(source_kind='gtec_live',stream_id='a'*32,epoch=0,device_serial='synthetic-fixture',sampling_rate=250,
        channels=[dict(name=f'CH{i+1:02}',kind='EEG',unit='uV',physical_index=i) for i in range(32)],reference=None,ground=None,
        configuration=dict(Channels=[dict(BipolarChannel=-1) for _ in range(32)],SamplingRate=250,CAR=0,NoiseReduction=0),layout_verified=False)


def service_fixture(tmp_path):
    from gnautilus_ml.service import ModelService
    from gnautilus_ml.stream_runtime import contract_from_descriptor
    from gnautilus_ml.navigation_contract import RUNTIME
    s=ModelService(tmp_path,{},tmp_path);net=CausalJEPA();s.model=StreamModel(arrays_from(net,np.zeros(32),np.ones(32)),dict(decision_policy=DEFAULT_POLICY),'cpu')
    s.manifest=dict(model_id='m',runtime_schema=RUNTIME,input_contract=contract_from_descriptor(descriptor()),electrical_policy=ELECTRICAL,
        montage_attestation=dict(channels=CHANNELS,source='explicit_user_confirmation'))
    s.runner.inference_enabled=True;s.lease=time.monotonic();s.generation=2
    return s


def request(start,end):
    return dict(descriptor=descriptor(),model_id='m',generation=2,stream_id='a'*32,source_epoch=0,sample_index_start=start,sample_index_end=end,
                samples=np.random.default_rng(9).normal(size=(32,end-start)).tolist(),device_valid=None)


def test_duplicate_never_advances_state_or_renews_lease_and_changed_bytes_reject(tmp_path):
    s=service_fixture(tmp_path);r=request(0,375);a=s.predict(r);lease=s.lease;count=s.stream_session.samples
    b=s.predict(r);assert b['duplicate'] and not b['eligible'] and s.stream_session.samples==count and s.lease==lease
    r['samples'][0][0]+=1
    with pytest.raises(ValueError,match='changed signal'):s.predict(r)
    assert s.stream_session is None


def test_gap_reconnect_and_invalid_samples_reset_all_neural_state(tmp_path):
    s=service_fixture(tmp_path);s.predict(request(0,375))
    with pytest.raises(ValueError,match='gap'):s.predict(request(400,425))
    assert s.stream_session is None
    assert s.predict(request(400,425))['warming']
    r=request(425,450);r['device_valid']=False
    with pytest.raises(ValueError,match='Device quality'):s.predict(r)
    assert s.stream_session is None


def test_reference_or_montage_drift_is_rejected(tmp_path):
    s=service_fixture(tmp_path);r=request(0,375);r['descriptor']['reference']='changed'
    with pytest.raises(ValueError,match='contract changed'):s.predict(r)
    r=request(0,375);r['descriptor']['channels'][15]['name']='wrong'
    with pytest.raises(ValueError,match='montage'):s.predict(r)


def test_episode_roles_must_not_overlap_original_sample_support():
    from gnautilus_ml.personal_study import episode_bounds
    rows=[dict(trial_id=str(i),split=role,stream_id='s',context_start=a,context_end=b) for i,(role,a,b) in enumerate([('train',0,100),('test',99,200)])]
    with pytest.raises(ValueError,match='overlap'):episode_bounds(rows)
