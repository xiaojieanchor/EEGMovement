"""Explicit synthetic numerical fixtures; no recorded EEG, hardware or model service."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

from gnautilus_lab.contracts import RecordingIdentity, validate_identity, grouped_split, dataset_admission
from gnautilus_lab.signal import CausalFeatures
from gnautilus_lab.reference import ReferencePipeline, authorize_fit
from gnautilus_lab.assessment import evaluate_predictions, poisson_upper, cluster_interval, grouped_cv, nested_group_cv, permute_label_blocks
from gnautilus_lab.decision import EvidenceDecision


def identity(**changes):
    x=dict(recording_id='r1',source_sha256='a'*64,subject_id='p1',session_id='s1',
           wearing_id='w1',date='2026-10-22',role='train',task='LEFT_HAND_MI',
           channel_names=['C3','Cz','C4'],sample_rate_hz=500.,unit='uV',reference='ear',
           device_config_sha256='b'*64,provenance='synthetic_fixture')
    x.update(changes);return RecordingIdentity(**x)


class Contracts(unittest.TestCase):
    def test_unknown_hardware_metadata_cannot_pass(self):
        for change in [dict(sample_rate_hz=None),dict(unit=None),dict(reference=None),
                       dict(device_config_sha256=None),dict(channel_names=['C3','C3'])]:
            with self.subTest(change=change),self.assertRaises(ValueError):validate_identity(identity(**change))

    def test_nonfinite_rate_and_unrecognized_role_rejected(self):
        for change in [dict(sample_rate_hz=float('nan')),dict(role='new_test'),dict(task='')]:
            with self.assertRaises(ValueError):validate_identity(identity(**change))

    def test_renamed_source_cannot_cross_roles(self):
        x=identity();y=identity(recording_id='renamed',session_id='other',wearing_id='other',date='2026-10-23',role='test')
        with self.assertRaisesRegex(ValueError,'source'):grouped_split([x,y])

    def test_date_and_wearing_cannot_cross_roles(self):
        x=identity()
        for y in [identity(source_sha256='c'*64,role='test',session_id='s2'),
                  identity(source_sha256='c'*64,role='test',date='2026-10-23',session_id='s2')]:
            with self.assertRaises(ValueError):grouped_split([x,y])

    def test_valid_cross_date_roles_and_unknown_not_rest(self):
        x=identity(task='UNKNOWN');y=identity(source_sha256='c'*64,recording_id='r2',role='test',session_id='s2',wearing_id='w2',date='2026-10-23')
        result=grouped_split([x,y]);self.assertEqual(result['train'],[0]);self.assertEqual(x.task,'UNKNOWN')

    def test_public_admission_is_fail_closed_and_preserves_task(self):
        row=dict(status='quarantined',license='CC-BY-4.0',native_tasks=['LEFT_HAND_MI'],
                 target_tasks=['LEFT_HAND_MI'],source_overlap_checked=True,metadata_complete=True)
        self.assertFalse(dataset_admission(row)['approved'])
        row['status']='reviewed_for_method_validation';row['target_tasks']=['LEFT_TURN']
        self.assertIn('task_semantics',dataset_admission(row)['reasons'])
        row['target_tasks']=['LEFT_HAND_MI'];row['license']=None
        self.assertFalse(dataset_admission(row)['approved'])


class Causality(unittest.TestCase):
    def test_chunking_does_not_change_features(self):
        x=np.random.default_rng(2).normal(size=(500,3))
        a=CausalFeatures(200,['C3','Cz','C4'],band=(8,30),window_seconds=.5)
        b=CausalFeatures(200,['C3','Cz','C4'],band=(8,30),window_seconds=.5)
        one=a.process(x,0,1)
        chunks=[b.process(x[:73],0,1),b.process(x[73:208],73,1),b.process(x[208:],208,1)]
        np.testing.assert_allclose(one['power'],np.concatenate([v['power'] for v in chunks]),equal_nan=True,rtol=1e-12,atol=1e-12)

    def test_future_changes_do_not_change_past(self):
        rng=np.random.default_rng(8);x=rng.normal(size=(400,2));y=x.copy();y[200:]*=500
        def calc(z):return CausalFeatures(200,['C3','C4'],window_seconds=.25).process(z,0,1)['power']
        np.testing.assert_allclose(calc(x)[:200],calc(y)[:200],equal_nan=True)

    def test_gap_resets_history_and_requires_warmup(self):
        f=CausalFeatures(100,['Cz'],band=(8,30),window_seconds=.2)
        f.process(np.ones((40,1)),0,1)
        r=f.process(np.ones((3,1)),50,1)
        self.assertTrue(r['reset']);self.assertFalse(r['ready'].any());self.assertTrue(np.isnan(r['power']).all())

    def test_invalid_sample_resets_only_then_rebuilds(self):
        f=CausalFeatures(100,['Cz'],window_seconds=.1)
        valid=np.ones(30,dtype=bool);valid[15]=False
        r=f.process(np.ones((30,1)),0,1,valid)
        self.assertTrue(r['ready'][14]);self.assertFalse(r['ready'][15:25].any());self.assertTrue(r['ready'][25])

    def test_wrong_channels_nonfinite_and_overlapping_input_fail(self):
        f=CausalFeatures(100,['Cz'],window_seconds=.1)
        for bad in [np.zeros((4,2)),np.array([[np.nan]])]:
            with self.assertRaises(ValueError):f.process(bad,0,1)
        f.process(np.zeros((10,1)),0,1)
        with self.assertRaisesRegex(ValueError,'overlap'):f.process(np.zeros((1,1)),9,1)


def synthetic_trials(seed=3,n=24):
    rng=np.random.default_rng(seed);y=np.arange(n)%2
    x=rng.normal(size=(n,3,120));x[y==0,0]*=3;x[y==1,2]*=3
    return x,y


class ReferenceMath(unittest.TestCase):
    def test_real_fit_has_no_enabled_entrypoint(self):
        x,y=synthetic_trials()
        with self.assertRaisesRegex(RuntimeError,'governance'):ReferencePipeline().fit(x,y,['C3','Cz','C4'])

    def fit_synthetic(self,x,y,names):
        # Explicitly mocked only in this synthetic test, never a production switch.
        with patch('gnautilus_lab.reference.authorize_fit',return_value=None):
            return ReferencePipeline(n_components=2,shrinkage=.2).fit(x,y,names)

    def test_csp_lda_detects_synthetic_covariance_and_margin_not_probability(self):
        x,y=synthetic_trials();m=self.fit_synthetic(x,y,['C3','Cz','C4'])
        z,t=synthetic_trials(seed=9);out=m.predict(z,['C3','Cz','C4'])
        self.assertGreater((out['labels']==t).mean(),.85);self.assertEqual(out['score_semantics'],'uncalibrated_lda_margin')
        self.assertNotIn('probabilities',out)

    def test_car_rank_deficiency_is_regularized(self):
        x,y=synthetic_trials();x-=x.mean(axis=1,keepdims=True)
        m=self.fit_synthetic(x,y,['C3','Cz','C4']);self.assertTrue(np.isfinite(m.spatial).all())

    def test_channel_removal_reorder_and_one_class_fail(self):
        x,y=synthetic_trials();m=self.fit_synthetic(x,y,['C3','Cz','C4'])
        for z,names in [(x[:,[0,2]],['C3','C4']),(x,['C4','Cz','C3'])]:
            with self.assertRaises(ValueError):m.predict(z,names)
        with patch('gnautilus_lab.reference.authorize_fit'),self.assertRaises(ValueError):
            ReferencePipeline().fit(x,np.zeros(len(y)),['C3','Cz','C4'])

    def test_npz_export_reopen_identical_without_pickle(self):
        x,y=synthetic_trials();m=self.fit_synthetic(x,y,['C3','Cz','C4'])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'candidate.npz';m.save(p);r=ReferencePipeline.load(p)
            np.testing.assert_allclose(m.predict(x,m.channels)['margin'],r.predict(x,r.channels)['margin'])


class Evaluation(unittest.TestCase):
    def test_nested_selection_keeps_outer_group_unseen(self):
        x,y=synthetic_trials(n=40);g=np.repeat(['a','b','c','d','e'],8)
        with patch('gnautilus_lab.reference.authorize_fit'):
            r=nested_group_cv(x,y,g,['C3','Cz','C4'],[{'n_components':2,'shrinkage':.1},{'n_components':2,'shrinkage':.5}])
        self.assertEqual(len(r['outer_folds']),5)
        for f in r['outer_folds']:
            self.assertNotIn(f['held_group'],f['selection_groups'])
            self.assertEqual(len(f['selection_groups']),4)

    def test_permutation_moves_whole_equal_blocks_and_refuses_unequal(self):
        y=np.array([0,0,1,1,2,2,3,3]);g=np.repeat(['a','b','c','d'],2)
        p=permute_label_blocks(y,g,seed=9)
        self.assertEqual(sorted(p.tolist()),sorted(y.tolist()))
        self.assertTrue(all(p[i]==p[i+1] for i in range(0,8,2)))
        with self.assertRaises(ValueError):permute_label_blocks(y,['a']+['b']*7)

    def test_reject_warmup_invalid_count_in_total_coverage(self):
        r=evaluate_predictions(['A','A','B','B','UNKNOWN'],['A',None,None,None,None],['OK','REJECT','WARMUP','INVALID','UNKNOWN'])
        self.assertEqual(r['total_labeled'],4);self.assertEqual(r['coverage'],.25);self.assertEqual(r['success_including_rejection'],.25)
        self.assertEqual(r['unknown_count'],1);self.assertEqual(r['conditional_accuracy'],1)

    def test_zero_false_event_upper_bound_requires_exposure(self):
        self.assertAlmostEqual(poisson_upper(0,30),-.0+(-np.log(.05)/30),places=7)
        with self.assertRaises(ValueError):poisson_upper(0,0)

    def test_cluster_interval_never_calls_two_sessions_high_confidence(self):
        r=cluster_interval({'s1':[1,1,1],'s2':[0,0,0]},seed=4)
        self.assertFalse(r['high_confidence']);self.assertEqual(r['n_groups'],2)
        self.assertEqual(r,cluster_interval({'s1':[1,1,1],'s2':[0,0,0]},seed=4))

    def test_csp_refit_within_each_group_fold_not_globally(self):
        x,y=synthetic_trials(n=24);groups=np.repeat(['a','b','c'],8);seen=[]
        original=ReferencePipeline.fit
        def audit(obj,tx,ty,cn):seen.append(len(tx));return original(obj,tx,ty,cn)
        with patch('gnautilus_lab.reference.authorize_fit'),patch.object(ReferencePipeline,'fit',audit):
            r=grouped_cv(x,y,groups,['C3','Cz','C4'])
        self.assertEqual(seen,[16,16,16]);self.assertEqual(len(r['folds']),3)
        for fold in r['folds']:self.assertNotIn(fold['held_group'],fold['train_groups'])


class OfflineDecision(unittest.TestCase):
    def test_reserved_nonaction_label_cannot_activate_even_if_status_ok(self):
        d=EvidenceDecision(confirmations=1,min_confirm_seconds=0)
        self.assertFalse(d.update(self.record(label='REST'),1.)['active'])

    def test_duplicate_flood_does_not_keep_confirmation_alive(self):
        d=EvidenceDecision(confirmations=2,min_confirm_seconds=.05,max_age=.2)
        d.update(self.record(),1.)
        d.update(self.record(produced_at=1.15),1.15)
        d.update(self.record(produced_at=1.3),1.3)
        self.assertFalse(d.update(self.record(sample_end=120,produced_at=1.4),1.4)['active'])

    def test_long_update_gap_cannot_complete_old_evidence(self):
        d=EvidenceDecision(confirmations=2,max_age=.2)
        d.update(self.record(),1.)
        self.assertFalse(d.update(self.record(sample_end=200,produced_at=2.),2.)['active'])

    def test_invalid_numeric_clock_configuration_rejected(self):
        for k in [{'max_age':float('inf')},{'min_confirm_seconds':float('nan')}]:
            with self.assertRaises(ValueError):EvidenceDecision(**k)

    def record(self,**kw):
        r=dict(source_epoch=1,model_id='m',sample_end=100,produced_at=1.,
               status='OK',score_semantics='calibrated_probability',label='LEFT_HAND_MI',probability=.95)
        r.update(kw);return r

    def test_margin_cannot_drive_probability_gate(self):
        d=EvidenceDecision(confirmations=2)
        r=d.update(self.record(score_semantics='uncalibrated_lda_margin'),1.)
        self.assertFalse(r['active']);self.assertEqual(r['reason'],'unsupported_score_semantics')

    def test_duplicate_is_not_second_confirmation(self):
        d=EvidenceDecision(confirmations=2,min_confirm_seconds=.05)
        self.assertFalse(d.update(self.record(),1.)['active'])
        self.assertFalse(d.update(self.record(),1.1)['active'])
        self.assertTrue(d.update(self.record(sample_end=120,produced_at=1.1),1.1)['active'])

    def test_rest_reject_unknown_invalid_warmup_stop_distinctly(self):
        for status in ['REST','REJECT','UNKNOWN','INVALID','WARMUP']:
            d=EvidenceDecision(confirmations=1,min_confirm_seconds=0)
            d.update(self.record(),1.)
            r=d.update(self.record(status=status,sample_end=120),1.1)
            self.assertFalse(r['active']);self.assertEqual(r['reason'],status.lower())

    def test_stale_epoch_switch_and_backward_samples_stop(self):
        d=EvidenceDecision(confirmations=1,min_confirm_seconds=0)
        self.assertFalse(d.update(self.record(),2.)['active'])
        d=EvidenceDecision(confirmations=2)
        d.update(self.record(),1.)
        self.assertFalse(d.update(self.record(source_epoch=2,sample_end=110),1.1)['active'])
        self.assertEqual(d.update(self.record(source_epoch=2,sample_end=109),1.2)['reason'],'out_of_order')


if __name__=='__main__':unittest.main()
