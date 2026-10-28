import unittest
import numpy as np

class EyeJointProbeTests(unittest.TestCase):
    def test_causal_chunk_invariance_and_all32(self):
        from gnautilus_ml.eye_joint_probe import CausalEvidenceFeatures
        rng=np.random.default_rng(12);x=rng.normal(size=(32,900)).astype('float32')
        def run(chunks):
            f=CausalEvidenceFeatures();out=[];at=0
            for n in chunks:out.extend(f.step(x[:,at:at+n]));at+=n
            return out
        a=run([900]);b=run([12,37,174,200,477]);self.assertEqual(len(a),len(b))
        for u,v in zip(a,b):
            np.testing.assert_allclose(u['full32'],v['full32'],atol=1e-6);np.testing.assert_allclose(u['eye'],v['eye'],atol=1e-6)
        self.assertEqual(a[0]['sample_end'],375)
        f=CausalEvidenceFeatures();base=f.step(x)[-1]['full32']
        for k in range(32):
            xx=x.copy();xx[k]*=2
            got=CausalEvidenceFeatures().step(xx)[-1]['full32'];self.assertGreater(np.max(abs(base-got)),1e-4)
    def test_future_and_invalid_reset(self):
        from gnautilus_ml.eye_joint_probe import CausalEvidenceFeatures
        x=np.random.default_rng(13).normal(size=(32,700)).astype('float32');xx=x.copy();xx[:,600:]+=100
        a=CausalEvidenceFeatures().step(x);b=CausalEvidenceFeatures().step(xx)
        for u,v in zip(a,b):
            if u['sample_end']<=600:np.testing.assert_array_equal(u['eye'],v['eye'])
        f=CausalEvidenceFeatures();f.step(x)
        with self.assertRaises(ValueError):f.step(np.full((32,25),np.nan))
        self.assertEqual(f.samples,0);self.assertEqual(f.step(x[:,:374]),[])
    def test_weighted_readout_and_authority(self):
        from gnautilus_ml.eye_joint_probe import fit_readout,predict_readout
        from unittest.mock import patch
        x=np.eye(5).repeat(4,axis=0);y=np.repeat(np.arange(5),4)
        with self.assertRaises(RuntimeError):fit_readout(x,y,steps=2,device='cpu')
        with patch('gnautilus_ml.eye_joint_probe.authorize_training'):
            a,h=fit_readout(x,y,steps=80,device='cpu')
        p=predict_readout(x,a);self.assertTrue(np.isfinite(p).all());self.assertEqual(np.mean(p.argmax(1)==y),1.)
        self.assertGreater(h[-1]['gradient_norm'],0);self.assertLess(h[-1]['joint_ce'],h[0]['joint_ce'])
    def test_labels_unknown_not_rest_and_trial_mass(self):
        from gnautilus_ml.eye_joint_probe import trial_weights,metrics
        y=np.array([0,0,1,1,1,-1]);ids=np.array(['a','a','b','b','b','b'])
        w=trial_weights(y,ids);self.assertEqual(w[-1],0.);self.assertAlmostEqual(w[:2].sum(),w[2:5].sum())
        p=np.full((6,5),.2);r=metrics(y,p)
        self.assertEqual(r['labelled_updates'],5);self.assertEqual(r['unknown_updates'],1)
    def test_fusion_cap(self):
        from gnautilus_ml.eye_joint_probe import fuse_probabilities
        p=np.array([[.7,.1,.1,.05,.05]]);q=np.array([[.01,.01,.96,.01,.01]])
        r=fuse_probabilities(p,q,.2);self.assertEqual(int(r.argmax()),0)
        with self.assertRaises(ValueError):fuse_probabilities(p,q,.8)
    def test_decision_coverage_includes_prewarm_rejections(self):
        from gnautilus_ml.eye_joint_probe import decision_report
        p=np.array([[.99,.0025,.0025,.0025,.0025]])
        r=decision_report(np.array([0]),p,[dict(trial_id='a',reset=0,source_sample_end=375)],np.int64(2),np.float64(.2))
        import json
        json.dumps(r)
        self.assertEqual(r['coverage'],.5);self.assertEqual(r['eligible_coverage'],1.)
    def test_weak_eye_class_balance_uses_only_weak_support(self):
        from gnautilus_ml.joint_training import weak_supervision_weights
        rows=[]
        for label in range(5):
            y=np.full(2500,label,dtype='int8');rows.append(dict(y=y,quality_eligible_ends=np.arange(24,2500,25),metadata=dict(onset_in_episode=0)))
        counts,masks=weak_supervision_weights(rows)
        np.testing.assert_array_equal(counts,[100,20,20,20,20])
        for row,mask in zip(rows,masks):
            ids=row['quality_eligible_ends'];ids=ids[mask[ids]]
            self.assertAlmostEqual(float(np.sum(1/(5*counts[row['y'][ids]]))),.2)
    def test_paired_weight_probe_isolates_class_mass(self):
        from gnautilus_ml.eye_weight_probe import fit_head
        from unittest.mock import patch
        y=np.repeat(np.arange(5),100);x=np.zeros((500,32),dtype='float32');x[np.arange(500),y]=1
        weak=np.zeros(500,bool);weak[:100]=True
        for k in range(1,5):weak[k*100:k*100+20]=True
        initial=dict(weight=np.zeros((5,32),dtype='float32'),bias=np.zeros(5,dtype='float32'))
        p=dict(steps=3,lr=.003,weight_decay=.01,auxiliary_scale=.1)
        with self.assertRaises(RuntimeError):fit_head(x,y,weak,initial,p,'cpu','legacy_whole_class_denominator',lambda:None)
        with patch('gnautilus_ml.eye_weight_probe.authorize_training'):
            _,legacy=fit_head(x,y,weak,initial,p,'cpu','legacy_whole_class_denominator',lambda:None)
            _,balanced=fit_head(x,y,weak,initial,p,'cpu','balanced_actual_weak_support',lambda:None)
        self.assertGreater(legacy['rest_fraction'],.5);self.assertAlmostEqual(balanced['rest_fraction'],.2)
        self.assertEqual(legacy['full_class_support'],balanced['full_class_support']);self.assertGreater(balanced['history'][0]['gradient_norm'],0)
    def test_external_negative_runs_count_towards_budget(self):
        from gnautilus_ml.joint_training import prior_budget
        from gnautilus_ml.common import atomic
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        with TemporaryDirectory() as name:
            root=Path(name);atomic(root/'logs/eye-joint-probe-abc/report.json',dict(elapsed_s=50.))
            atomic(root/'logs/eye-weight-probe-def/protocol.json',dict(max_wall_seconds=600))
            atomic(root/'logs/eye-weight-probe-def/report.json',dict(elapsed_s=None))
            with patch('gnautilus_ml.registry.Registry') as registry:
                registry.return_value.jobs.return_value=[];budget=prior_budget(root)
            self.assertEqual(budget['prior_elapsed_s'],650.);self.assertEqual(len(budget['jobs']),2)
            self.assertTrue(all(r['receipt_path'].startswith('logs/') for r in budget['jobs']))
    def test_protocol_rejects_changed_recipe_and_entrypoint(self):
        from gnautilus_ml.eye_joint_probe import make_protocol,check_probe_protocol
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from gnautilus_ml.common import digest
        with TemporaryDirectory() as name:
            root=Path(name);(root/'gnautilus_ml').mkdir();(root/'gnautilus_ml/eye_joint_probe.py').write_text('bound',encoding='utf-8')
            ds=dict(schema='gnautilus.personal_study.v1',scope='same_wearing_internal_research',channels=__import__('gnautilus_ml.navigation_contract',fromlist=['CHANNELS']).CHANNELS,sampling_rate=250,folder=str(root/'data'),episodes=[],source_receipt={})
            sources={'candidate':{'path':'models/x/weights.npz','sha256':'a'*64}}
            p=make_protocol(ds,root,sources,0)
            with patch('gnautilus_ml.eye_joint_probe.verify_sources'),patch('gnautilus_ml.eye_joint_probe.verify_models'):
                self.assertTrue(check_probe_protocol(p,ds,root));p['test_access']='allow'
                with self.assertRaises(ValueError):check_probe_protocol(p,ds,root)

if __name__=='__main__':unittest.main()
