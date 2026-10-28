"""Synthetic signals only: full-channel causal architecture and conservative selection."""
import copy
import numpy as np
import torch


def test_all_32_channels_reach_joint_prediction():
    from gnautilus_ml.joint_stream import JointJEPA
    torch.manual_seed(14);net=JointJEPA();x=torch.randn(2,32,400,requires_grad=True)
    out=net(x);out['logits'].square().sum().backward()
    assert torch.all(x.grad.abs().sum((0,2))>0)
    assert net.motor.spatial.weight.shape[2]==32
    assert out['joint'].shape==(2,32)


def test_joint_sequence_is_causal_and_packet_invariant():
    from gnautilus_ml.joint_stream import JointJEPA
    torch.manual_seed(17);net=JointJEPA().eval();x=torch.randn(1,32,777)
    with torch.no_grad():
        seq,_=net.sequence(x);changed=x.clone();changed[:,:,500:]+=90
        other,_=net.sequence(changed)
        torch.testing.assert_close(seq['logits'][:,:125],other['logits'][:,:125],rtol=0,atol=0)
        full,_=net.step(x);state=None;a=0
        for width in [3,25,1,150,97,501]:
            end=min(a+width,777);out,state=net.step(x[:,:,a:end],state);a=end
        torch.testing.assert_close(full['logits'],out['logits'],atol=2e-6,rtol=2e-5)


def test_broadband_preserves_mu_beta_in_frontal_and_low_frequency_posterior():
    from gnautilus_ml.joint_stream import JointPreprocessor
    t=np.arange(2500)/250;x=np.zeros((32,len(t)))
    x[0]=np.sin(2*np.pi*20*t);x[31]=np.sin(2*np.pi*2*t)
    p=JointPreprocessor();z=p.step(x)
    assert z[0,1000:].std()>.5 and z[31,1000:].std()>.5
    q=JointPreprocessor();chunks=np.concatenate([q.step(x[:,:17]),q.step(x[:,17:913]),q.step(x[:,913:])],1)
    np.testing.assert_array_equal(z,chunks)


def test_all_reject_policy_search_reports_failure():
    from gnautilus_ml.joint_training import choose_useful_policy
    table=[dict(policy={'temperature':1},validation=dict(coverage=0.,false_starts=0,accepted_macro_recall=0.,responded_trials=0,first_response_p50_s=None))]
    result=choose_useful_policy(table)
    assert result['status']=='no_useful_policy' and result['selected'] is None


def test_joint_decoder_does_not_require_an_isolated_regional_classifier():
    from gnautilus_ml.joint_stream import JointDecoder
    d=JointDecoder();joint=[.01,.96,.01,.01,.01]
    for _ in range(3):result=d.step([.2]*5,joint,'UNKNOWN',0)
    assert result['direction']==1 and result['policy']=='shared_full32_joint_v2'
    for _ in range(3):result=d.step(joint,[.2]*5,'UNKNOWN',0)
    assert result['direction']==0


def test_joint_jepa_reaches_fusion_and_both_grus():
    from gnautilus_ml.joint_stream import JointJEPA,joint_jepa_loss
    torch.manual_seed(7);student=JointJEPA();target=copy.deepcopy(student).requires_grad_(False)
    loss,parts=joint_jepa_loss(student,target,torch.randn(8,32,400),[torch.randn(8,32,125),torch.randn(8,32,125)])
    loss.backward()
    for layer in [student.fusion[0],student.motor.gru,student.eye.gru]:
        assert sum(float(p.grad.abs().sum()) for p in layer.parameters() if p.grad is not None)>0
    assert set(parts)=={'motor','eye','joint'}


def test_joint_round_accumulates_all_trials_before_single_update():
    from unittest.mock import patch
    from gnautilus_ml.joint_training import adaptation_round
    from gnautilus_ml.joint_stream import JointJEPA,JointPreprocessor
    from gnautilus_ml.jepa_training import prepared,normalization
    from tests.test_navigation_jepa_training import episodes
    rows=prepared(episodes(),JointPreprocessor);mean,scale=normalization(rows)
    net=JointJEPA();optimizer=torch.optim.AdamW(net.parameters(),lr=.001)
    with patch.object(optimizer,'step',wraps=optimizer.step) as call:
        stats=adaptation_round(net,rows,mean,scale,optimizer,lambda:None)
    assert call.call_count==1 and stats['complete_training_trials']==5
    assert stats['joint_ce']>0 and stats['gradient_norms']['motor']>0 and stats['gradient_norms']['eye']>0


def test_joint_protocol_rejects_old_ssl_and_test_role(tmp_path):
    import pytest
    from gnautilus_ml.joint_training import make_joint_protocol,check_joint_protocol
    from gnautilus_ml.navigation_contract import CHANNELS,SCOPE
    from gnautilus_ml.common import digest
    dataset=dict(schema='gnautilus.personal_study.v1',scope=SCOPE,channels=CHANNELS,sampling_rate=250,episodes=[],source_receipt={},folder=str(tmp_path))
    budget=dict(prior_elapsed_s=1000.,reserve_s=600.,job_ids=['previous'])
    protocol=make_joint_protocol(dataset,tmp_path,dict(ssl_steps=2,max_epochs=2),budget)
    for update in [dict(ssl_reuse={'job_id':'old'}),dict(test_access='after_selection_freeze_only'),dict(normalization_roles=['train','validation'])]:
        altered=dict(protocol,**update)
        with pytest.raises(ValueError):check_joint_protocol(altered,dataset,tmp_path)
    assert protocol['dataset_sha256']==digest(dataset)


def test_training_spans_merge_same_role_and_never_cross_held_out_context():
    from gnautilus_ml.joint_training import training_spans
    rows=[dict(stream_id='s',split=role,context_start=a,context_end=b) for role,a,b in [('train',1200,2000),('train',3000,4000),('validation',4500,6000),('train',7000,8000)]]
    assert training_spans(rows,'s',[0,10000])==[[200,4500],[6000,9000]]


def test_official_episode_without_native_canonical_stream_is_preserved(tmp_path):
    from unittest.mock import patch
    from gnautilus_ml.joint_training import continuous_training_rows
    from tests.test_navigation_jepa_training import episodes
    row=episodes()[0];row['metadata'].update(stream_id='official_rest_hdf5',contract_id='official_fixture')
    dataset=dict(folder=str(tmp_path),episodes=[])
    with patch('gnautilus_ml.joint_training.load_episodes',return_value=[row]):
        result=continuous_training_rows(dataset)
    np.testing.assert_array_equal(result[0]['x'],row['x'])
    assert result[0]['metadata']['context_extension']=='official_immutable_episode_only'


def test_public_reuse_inventory_excludes_new_private_pilot(tmp_path):
    from gnautilus_ml.personal_study import reuse_inventory
    from gnautilus_ml.registry import Registry
    from gnautilus_ml.navigation_contract import SCOPE
    registry=Registry(tmp_path/'state.sqlite')
    common=dict(weights_sha256='fixture',algorithm='cnn_gru',class_labels=[0,1],metrics={'development':{'balanced_accuracy':.8}})
    registry.register(tmp_path/'public',dict(common,model_id='public',evaluation_stage='public_method_validation',input_contract={'fixture':True}))
    registry.register(tmp_path/'private',dict(common,model_id='private',evaluation_stage=SCOPE))
    assert [m['model_id'] for m in reuse_inventory(tmp_path)]==['public']


def test_complete_joint_pilot_never_opens_test_and_registers_offline(tmp_path):
    from unittest.mock import patch
    from types import SimpleNamespace
    from gnautilus_ml.joint_training import run_joint_pilot
    from gnautilus_ml.joint_stream import JointJEPA,RUNTIME
    from gnautilus_ml.registry import Registry
    from gnautilus_ml.common import atomic,digest
    from tests.test_navigation_jepa_training import episodes
    root=tmp_path;registry=Registry(root/'state.sqlite');dataset=dict(contract_review={'fixture':True},ssl_guard_samples=2275)
    p=dict(candidate_matrix=[[17,'jepa_transfer'],[17,'transfer_no_jepa']],ssl_steps=1,max_epochs=1,max_wall_seconds=90,head_lr=.003,backbone_lr=.0003,freeze_rounds=5,
           probe_steps=2,target_supports=[[25,150],[150,275]],aggregate_budget={},code_sha256={'fixture':'x'},public_resources={'inventory':[],'teachers':{}})
    atomic(root/'protocols'/(digest(p)+'.json'),p);job=registry.create_job(dict(algorithm='causal_jepa_stream'),'fixture');registry.transition(job['id'],'preparing')
    runner=SimpleNamespace(root=root,code_root=root,profile={},registry=registry,_check_cancel=lambda _:None)
    def load(_,role):
        assert role in ['train','validation'];return episodes()
    def native(_,net,*args,**kwargs):
        from gnautilus_ml.jepa_training import DomainStudent
        teacher=SimpleNamespace(channels=list(range(32)),classes=np.arange(5))
        student=DomainStudent(net.motor,teacher,copy_parameters=False)
        return [dict(id='synthetic-native',kind='motor',student=student,x=torch.randn(20,32,125),y=torch.arange(20)%5,target=torch.full((20,5),.2))]
    with patch('gnautilus_ml.joint_training.training_authority'),patch('gnautilus_ml.joint_training.compute_device',return_value='cpu'),patch('gnautilus_ml.joint_training.load_episodes',side_effect=load),patch('gnautilus_ml.joint_training.continuous_training_rows',return_value=episodes()),patch('gnautilus_ml.joint_training.unlabelled_regions',return_value=[]),patch('gnautilus_ml.joint_training.reuse_inventory',return_value=[]),patch('gnautilus_ml.joint_training.initialize_joint',side_effect=lambda *_:(JointJEPA(),{})),patch('gnautilus_ml.joint_training.native_domains',side_effect=native):
        run_joint_pilot(runner,job['id'],dataset,p)
    assert registry.job(job['id'])['state']=='completed'
    meta=registry.models()[0]
    assert meta['runtime_schema']==RUNTIME and meta['runtime_accepted'] is False and meta['test_accessed'] is False
    assert meta['metrics']['internal_test'] is None and registry.selected() is None


def test_joint_adaptation_resume_matches_uninterrupted_committed_updates(tmp_path):
    import pytest
    from gnautilus_ml.joint_training import fit_joint
    from gnautilus_ml.joint_stream import JointJEPA,JointPreprocessor
    from gnautilus_ml.jepa_training import prepared,normalization
    from gnautilus_ml.common import read_json
    from tests.test_navigation_jepa_training import episodes
    torch.set_num_threads(2);torch.manual_seed(123);initial=JointJEPA().state_dict();rows=prepared(episodes(),JointPreprocessor);mean,scale=normalization(rows)
    p=dict(max_epochs=6,head_lr=.003,backbone_lr=.0003,freeze_rounds=1);full=JointJEPA();full.load_state_dict(initial)
    resumed=JointJEPA();resumed.load_state_dict(initial);left=tmp_path/'full';right=tmp_path/'resumed';left.mkdir();right.mkdir()
    fit_joint(full,rows,episodes(),episodes(),mean,scale,p,lambda _:None,lambda:None,left/'personal.pt')
    stopped=[False]
    def progress(row):
        if row['epoch']==1:stopped[0]=True
    def check():
        if stopped[0]:raise InterruptedError('synthetic interruption after committed round')
    with pytest.raises(InterruptedError):fit_joint(resumed,rows,episodes(),episodes(),mean,scale,p,progress,check,right/'personal.pt')
    assert read_json(right/'personal.json')['step']==1
    fresh=JointJEPA();fit_joint(fresh,rows,episodes(),episodes(),mean,scale,p,lambda _:None,lambda:None,right/'personal.pt')
    a=torch.load(left/read_json(left/'personal.json')['file'],weights_only=False)
    b=torch.load(right/read_json(right/'personal.json')['file'],weights_only=False)
    assert a['step']==b['step']==6
    for key in a['model']:torch.testing.assert_close(a['model'][key],b['model'][key],atol=0,rtol=0)
    with pytest.raises(ValueError,match='protocol'):
        fit_joint(fresh,rows,episodes(),episodes(),mean,scale,dict(p,head_lr=.002),lambda _:None,lambda:None,right/'personal.pt')


def test_ssl_pool_excludes_rejected_raw_boundaries():
    from gnautilus_ml.joint_training import screen_ssl_regions
    x=np.random.default_rng(10).normal(size=(32,4000)).astype('float32');x[:,1500:1525]=2000
    regions=screen_ssl_regions([dict(x=x,stream_id='s',start=100,end=4100)])
    assert regions and all(not(r['start']<1625 and r['end']>1600) for r in regions)
    assert [(r['start'],r['end']) for r in regions]==[(100,1600),(1625,4100)]


def test_actual_ssl_draws_do_not_cross_rejected_context_or_target_gap(tmp_path):
    from gnautilus_ml.joint_training import screen_ssl_regions
    from gnautilus_ml.joint_stream import JointJEPA,JointPreprocessor,joint_jepa_loss
    from gnautilus_ml.jepa_training import ssl_fit
    import json
    torch.set_num_threads(2);x=np.random.default_rng(10).normal(size=(32,4000)).astype('float32');x[:,1500:1525]=2000
    regions=screen_ssl_regions([dict(x=x,stream_id='s',start=100,end=4100)])
    p=dict(ssl_steps=4,target_supports=[[25,150],[150,275]])
    ssl_fit(JointJEPA(),regions,np.zeros(32,dtype='float32'),np.ones(32,dtype='float32'),[],p,np.random.default_rng(17),lambda _:None,lambda:None,tmp_path/'ssl.pt',preprocessor_factory=JointPreprocessor,loss_function=joint_jepa_loss)
    for line in (tmp_path/'ssl.supports.jsonl').read_text().splitlines():
        for r in json.loads(line)['private_supports']:assert r['targets'][-1][1]<=1600 or r['context'][0]>=1625


def test_quality_failure_inside_large_packet_resets_neural_and_action_state():
    import pytest
    from gnautilus_ml.joint_stream import JointJEPA,JointStreamModel
    from gnautilus_ml.stream_jepa import arrays_from
    from gnautilus_ml.navigation_contract import DEFAULT_POLICY
    model=JointStreamModel(arrays_from(JointJEPA(),np.zeros(32),np.ones(32)),dict(decision_policy=DEFAULT_POLICY),'cpu')
    s=model.new_session();x=np.random.default_rng(2).normal(size=(32,1500)).astype('float32');x[:,500:525]=2000
    with pytest.raises(ValueError,match='quality'):s.step(x)
    assert s.samples==0 and s.decoder.active==0 and s.state is None and s.pending.shape[1]==0


def test_session_confirmation_counts_fixed_sample_hops_after_real_prewarm():
    from gnautilus_ml.joint_stream import JointJEPA,JointStreamModel
    from gnautilus_ml.stream_jepa import arrays_from
    from gnautilus_ml.navigation_contract import DEFAULT_POLICY
    net=JointJEPA()
    with torch.no_grad():
        net.joint_head.weight.zero_();net.joint_head.bias.copy_(torch.tensor([-5.,5.,-5.,-5.,-5.]))
        net.eye_head.weight.zero_();net.eye_head.bias.zero_();net.native_eye_head.weight.zero_();net.native_eye_head.bias.zero_()
    model=JointStreamModel(arrays_from(net,np.zeros(32),np.ones(32)),dict(decision_policy=DEFAULT_POLICY),'cpu');x=np.random.default_rng(2).normal(size=(32,525)).astype('float32')
    one=model.new_session();small=model.new_session();a=one.step(x[:,:375])
    for i in range(0,375,25):b=small.step(x[:,i:i+25])
    assert a['navigation_decision']['direction']==b['navigation_decision']['direction']==0
    assert a['new_prediction_updates']==1
    for session in [one,small]:out=session.step(x[:,375:425]);assert out['navigation_decision']['direction']==1
    irregular=model.new_session();offset=0
    for width in [3,8,17,69,128,300]:
        c=irregular.step(x[:,offset:offset+width]);offset+=width
    whole=model.new_session().step(x)
    assert c['navigation_decision']==whole['navigation_decision'] and c['new_prediction_updates']>0
    np.testing.assert_allclose(c['probabilities'],whole['probabilities'],atol=1e-6)
    assert irregular.step(x[:,:1])['new_prediction_updates']==0


def test_frozen_public_teacher_plan_survives_new_registry_model(tmp_path):
    from gnautilus_ml.joint_training import freeze_public_resources,verify_public_resources
    from gnautilus_ml.jepa_training import native_teacher_ids
    from gnautilus_ml.registry import Registry
    from gnautilus_ml.navigation_contract import OPENBMI,EOG
    from gnautilus_ml.personal_study import PHYSIONET,reuse_inventory
    from gnautilus_ml.common import atomic,digest,file_hash
    registry=Registry(tmp_path/'state.sqlite')
    def add(mid,domain,algorithm):
        p=tmp_path/'models'/mid;p.mkdir(parents=True);(p/'weights.npz').write_bytes(b'synthetic-metadata-fixture')
        dataset={'domain':domain,'records':[]};did=digest(dataset);atomic(tmp_path/'datasets'/(did+'.json'),dataset)
        m=dict(model_id=mid,algorithm=algorithm,channels=['fixture'],window_s=1.5,input_contract={'domain':domain},class_labels=['a','b'],weights_sha256=file_hash(p/'weights.npz'),dataset_sha256=did,evaluation_stage='public_method_validation',metrics={'development':{'balanced_accuracy':.9}})
        atomic(p/'manifest.json',m);registry.register(p,m)
    for mid in [OPENBMI,EOG,PHYSIONET]:add(mid,mid,'cnn_gru_baseline')
    resources=freeze_public_resources(tmp_path);assert verify_public_resources(tmp_path,resources)
    add('f'*64,OPENBMI,'bandpower_lda')
    assert native_teacher_ids(tmp_path,OPENBMI,reuse_inventory(tmp_path),resources['teachers'])==[OPENBMI]
    assert 'f'*64 in native_teacher_ids(tmp_path,OPENBMI,reuse_inventory(tmp_path))
    (tmp_path/'models'/OPENBMI/'weights.npz').write_bytes(b'changed')
    import pytest
    with pytest.raises(ValueError):verify_public_resources(tmp_path,resources)


def test_v2_service_partial_hops_cannot_issue_new_action_and_bad_interior_resets(tmp_path):
    import pytest
    from tests.test_navigation_jepa import service_fixture,request
    from gnautilus_ml.joint_stream import JointJEPA,JointStreamModel,RUNTIME
    from gnautilus_ml.navigation_contract import DEFAULT_POLICY
    from gnautilus_ml.stream_jepa import arrays_from
    s=service_fixture(tmp_path);net=JointJEPA()
    with torch.no_grad():net.joint_head.weight.zero_();net.joint_head.bias.copy_(torch.tensor([-5.,5.,-5.,-5.,-5.]))
    s.model=JointStreamModel(arrays_from(net,np.zeros(32),np.ones(32)),dict(decision_policy=DEFAULT_POLICY),'cpu');s.manifest['runtime_schema']=RUNTIME
    a=s.predict(request(0,375));b=s.predict(request(375,392))
    assert a['navigation_decision']['direction']==0 and b['new_prediction_updates']==0 and not b['eligible'] and b['prediction_sample_index_end']==375
    s.predict(request(392,400));r=request(400,425);out=s.predict(r)
    assert out['navigation_decision']['direction']==1 and out['eligible']
    duplicate=s.predict(r);assert duplicate['duplicate'] and not duplicate['eligible']
    bad=request(425,1500)
    for channel in bad['samples']:channel[75:100]=[2000.]*25
    with pytest.raises(ValueError,match='quality'):s.predict(bad)
    assert s.stream_session is None and s.stream_identity is None
