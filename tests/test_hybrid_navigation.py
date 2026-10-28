import unittest
import numpy as np
from gnautilus_ml.hybrid_navigation import GazePolicy,EventLatch

class HybridPolicyTests(unittest.TestCase):
    def test_eye_alone_never_starts_and_same_direction_threshold_lower(self):
        p=GazePolicy();p.eye_event(3);a=p.step([.6,.1,.1,.1,.1])
        self.assertEqual(a['direction'],0);self.assertLess(a['thresholds'][3],a['thresholds'][4]);self.assertGreater(a['thresholds'][0],.75)

    def test_no_eye_is_not_center_and_weak_navigation_stops(self):
        p=GazePolicy();p.eye_event(3)
        for _ in range(3):a=p.step([.05,.05,.05,.8,.05])
        self.assertEqual(a['direction'],3)
        p.step([.4,.15,.15,.15,.15]);a=p.step([.4,.15,.15,.15,.15]);self.assertEqual(a['direction'],0)
        self.assertEqual(a['cue_direction'],3)

    def test_return_revokes_old_bias_without_automatic_reverse(self):
        p=GazePolicy();p.eye_event(3);p.eye_event(4);a=p.step([.1,.1,.1,.1,.6])
        self.assertEqual(a['direction'],0);self.assertEqual(a['cue_direction'],0);self.assertEqual(a['auxiliary'],'RETURN_OR_SWITCH')
        for _ in range(3):a=p.step([.01,.01,.01,.01,.96])
        self.assertEqual(a['cue_direction'],4)

    def test_new_nonopposite_event_overwrites_and_reset_clears(self):
        p=GazePolicy();p.eye_event(3);p.eye_event(1);self.assertEqual(p.cue,1);p.reset();self.assertEqual(p.cue,0);self.assertEqual(p.active,0)

    def test_latch_requires_new_predictions_and_no_repeat_burst(self):
        e=EventLatch();v=[.01,.01,.01,.96,.01]
        self.assertEqual([e.step(v) for _ in range(7)],[0,0,3,0,0,0,0])
        e.step([.8,.05,.05,.05,.05]);e.step([.8,.05,.05,.05,.05]);self.assertEqual([e.step(v) for _ in range(3)],[0,0,3])

    def test_training_mass_balances_complete_trials(self):
        from gnautilus_ml.hybrid_training import sample_weights
        y=np.array([0,0,0,1,1,2,3,4]);ids=np.array(['r1','r1','r2','a','a','b','c','d']);w=sample_weights(y,ids,.4)
        self.assertAlmostEqual(w[y==0].sum(),.4);self.assertAlmostEqual(w[ids=='r1'].sum(),w[ids=='r2'].sum())
        for c in [1,2,3,4]:self.assertAlmostEqual(w[y==c].sum(),.15)

    def test_all_data_targets_keep_original_roles_and_do_not_fake_eye_boundaries(self):
        from gnautilus_ml.hybrid_training import targets
        from gnautilus_ml.navigation_contract import CHANNELS
        rows=[]
        for i,role in enumerate(['train','validation','test']):
            rows.append(dict(trial_id=str(i),stream_id=role,label='LEFT',split=role,task_start=1000,task_end=4000,annotation=dict(phases=dict(onset=dict(status='marked',sample_end=1200),offset=dict(status='marked',sample_start=3800)))))
        c=dict(channels=CHANNELS,sampling_rate=250,trials=rows);t,e=targets(c);self.assertEqual({r['split'] for r in t.values()},{'train','validation','test'})
        c['trials'][0]['annotation']['phases']['offset']={'status':'absent'};t,e=targets(c);self.assertNotIn('0',t);self.assertEqual(len(e),1)

class HybridRuntimeTests(unittest.TestCase):
    def test_relative_power_rejects_common_band_gain_without_moving_columns(self):
        from gnautilus_ml.hybrid_navigation import feature_view
        x=np.random.default_rng(1).normal(size=(3,38)).astype('float32');changed=x.copy();changed[:,:32]+=np.repeat(np.arange(8),4)[None]
        a=feature_view(x,4,'spatial_relative');b=feature_view(changed,4,'spatial_relative');np.testing.assert_allclose(a,b,atol=1e-6);np.testing.assert_array_equal(a[:,32:],x[:,32:])

    def test_production_head_fit_requires_training_authority(self):
        from gnautilus_ml.hybrid_training import fit_head
        with self.assertRaisesRegex(RuntimeError,'governed'):fit_head(None,None,None,.2,17,'cpu',1,lambda:None)

    def model(self):
        from gnautilus_ml.joint_stream import JointJEPA
        from gnautilus_ml.ocular_event_training import build_model
        from gnautilus_ml.hybrid_navigation import HybridNavigationModel
        net=JointJEPA();eye=build_model();a={'backbone__net__'+k:v.detach().numpy() for k,v in net.state_dict().items()}
        a.update(backbone__input_mean=np.zeros(32,dtype='float32'),backbone__input_scale=np.ones(32,dtype='float32'))
        a.update({'ocular__net__'+k:v.detach().numpy() for k,v in eye.state_dict().items()});a.update(ocular__input_mean=np.zeros((32,1),dtype='float32'),ocular__input_scale=np.ones((32,1),dtype='float32'))
        w=np.zeros(32,dtype='float32');w[[8,15,18,28]]=1;a['navigation_input_weights']=w;a['latent_gain']=np.asarray(.25,dtype='float32')
        for prefix,n in [('spectral__',38),('latent__',32)]:
            a.update({prefix+'mean':np.zeros(n,dtype='float32'),prefix+'scale':np.ones(n,dtype='float32'),prefix+'weight':np.zeros((5,n),dtype='float32'),prefix+'bias':np.zeros(5,dtype='float32')})
        return HybridNavigationModel(a,{},device='cpu')

    def test_chunking_preserves_state_and_invalid_sample_clears_cue(self):
        m=self.model();t=np.arange(600)/250;x=np.stack([np.sin(2*np.pi*10*t+i*.03) for i in range(32)]).astype('float32')
        a=m.new_session().step(x);s=m.new_session()
        for at in range(0,600,17):b=s.step(x[:,at:at+17])
        np.testing.assert_allclose(a['probabilities'],b['probabilities']);self.assertEqual(a['history_samples'],600);self.assertEqual(b['pending_samples'],0)
        s.policy.eye_event(3)
        with self.assertRaises(ValueError):s.step(np.full((32,25),np.nan))
        self.assertEqual(s.policy.cue,0)

    def test_mask_lists_keep_neural_dtype(self):
        from gnautilus_ml.hybrid_navigation import NavEvidence
        m=self.model();s=NavEvidence(m.backbone,m.arrays['navigation_input_weights'].tolist(),'cpu');f,h,ready=s.step(np.random.default_rng(4).normal(size=(32,25)).astype('float32'))
        self.assertEqual(h.shape,(32,));self.assertFalse(ready)

    def test_artifact_roundtrip_and_hash_guard(self):
        import tempfile
        from pathlib import Path
        from gnautilus_ml.artifacts import save_artifact,load_artifact
        from gnautilus_ml.hybrid_navigation import RUNTIME
        with tempfile.TemporaryDirectory() as folder:
            path=save_artifact(folder,self.model(),dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False))
            m,meta=load_artifact(path,device='cpu');self.assertEqual(m.algorithm,'hybrid_navigation');self.assertFalse(meta['runtime_accepted'])
            with (Path(path)/'weights.npz').open('ab') as f:f.write(b'changed')
            with self.assertRaisesRegex(ValueError,'hash'):load_artifact(path,device='cpu')

    def test_service_duplicate_partial_and_gap_do_not_reuse_actions(self):
        import tempfile
        from pathlib import Path
        from tests.test_navigation_jepa import service_fixture,request
        from gnautilus_ml.hybrid_navigation import RUNTIME
        with tempfile.TemporaryDirectory() as folder:
            s=service_fixture(Path(folder));s.model=self.model();s.manifest['runtime_schema']=RUNTIME
            a=s.predict(request(0,375));b=s.predict(request(375,392));self.assertEqual(b['new_prediction_updates'],0);self.assertFalse(b['eligible'])
            r=request(392,400);s.predict(r);dup=s.predict(r);self.assertTrue(dup['duplicate']);self.assertFalse(dup['eligible'])
            s.stream_session.policy.eye_event(3)
            with self.assertRaisesRegex(ValueError,'gap'):s.predict(request(425,450))
            self.assertIsNone(s.stream_session)

if __name__=='__main__':unittest.main()
