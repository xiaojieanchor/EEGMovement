import unittest
import numpy as np

class OcularBoundaryAuditTests(unittest.TestCase):
    def test_frontal_features_match_frozen_classifier_contract(self):
        from gnautilus_ml.ocular_boundary_audit import FrontalFeatures
        from gnautilus_ml.eye_joint_probe import CausalEvidenceFeatures
        x=np.random.default_rng(1).normal(size=(32,900)).astype('float32')
        full=CausalEvidenceFeatures().step(x);eye=FrontalFeatures().step(x[:8])
        self.assertEqual([r['sample_end'] for r in full],[r['sample_end'] for r in eye])
        for a,b in zip(full,eye):np.testing.assert_allclose(a['eye'],b['eye'],atol=1e-6)
    def test_context_preserves_old_ranges_and_never_crosses_roles(self):
        from gnautilus_ml.ocular_boundary_audit import context_ranges
        rows=[dict(trial_id='a',split='test',stream_id='x',context_start=1000,context_end=2000,start=1250,end=1750),dict(trial_id='b',split='validation',stream_id='x',context_start=2200,context_end=3000,start=2300,end=2800),dict(trial_id='c',split='test',stream_id='x',context_start=3000,context_end=4000,start=3200,end=3800)]
        result=context_ranges(rows,{'x':[[0,6000]]})
        for r in rows:
            a,b=result[r['trial_id']];self.assertLessEqual(a,r['context_start']);self.assertGreaterEqual(b,r['context_end'])
        self.assertLessEqual(result['a'][1],result['b'][0]);self.assertLessEqual(result['b'][1],result['c'][0]);self.assertEqual(result['c'][0],3000)
    def test_unknown_label_and_warmup_are_distinct(self):
        from gnautilus_ml.ocular_boundary_audit import input_status
        self.assertEqual(input_status(True,False,-1),'READY_LABEL_UNKNOWN')
        self.assertEqual(input_status(True,True,-1),'WARMUP')
        self.assertEqual(input_status(False,False,2),'QC_REJECTED')
    def test_events_do_not_use_instruction_labels(self):
        from gnautilus_ml.ocular_boundary_audit import proposals
        z=np.zeros((40,5));z[8:10,0]=9;z[23:25,1]=8
        events=proposals(z,np.arange(1,41)*25)
        self.assertEqual(len(events),2);self.assertEqual(events[0]['sample_start'],200)
        self.assertEqual(events[0]['annotation_status'],'signal_candidate_unverified')
    def test_continuity_groups_do_not_reset_at_same_role_trial_edge(self):
        from gnautilus_ml.ocular_boundary_audit import merge_intervals
        self.assertEqual(merge_intervals([[0,100],[100,200],[300,400]]),[[0,200],[300,400]])
    def test_biphasic_return_and_new_movement_are_not_one_peak(self):
        from gnautilus_ml.ocular_boundary_audit import proposals
        z=np.zeros((30,5));z[8:15,0]=[4,8,12,5,-10,-8,-4]
        events=proposals(z,np.arange(1,31)*25)
        self.assertEqual(len(events),2);self.assertLessEqual(events[0]['sample_end'],events[1]['sample_start'])
        self.assertNotEqual(events[0]['peak_sample'],events[1]['peak_sample'])
    def test_first_sample_trace_does_not_create_fake_saccade(self):
        from gnautilus_ml.ocular_boundary_audit import OcularTrace
        t=OcularTrace();a=t.step(np.ones((8,250))*25)
        self.assertLess(np.max(abs(np.asarray([r['velocity'] for r in a]))),1e-6)
    def test_context_expansion_larger_history_keeps_original_and_roles(self):
        from gnautilus_ml.ocular_boundary_audit import context_ranges
        rows=[dict(trial_id='a',split='train',stream_id='x',context_start=10000,context_end=11000,start=10250,end=10750),dict(trial_id='b',split='test',stream_id='x',context_start=11200,context_end=12000,start=11450,end=11750)]
        small=context_ranges(rows,{'x':[[0,15000]]},pre=1500,post=750);large=context_ranges(rows,{'x':[[0,15000]]},pre=3000,post=1500)
        self.assertLessEqual(large['a'][0],small['a'][0]);self.assertGreaterEqual(large['a'][1],small['a'][1]);self.assertLessEqual(large['a'][1],large['b'][0])
    def test_context_derivative_retains_raw_and_unknown_exactly(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from gnautilus_ml.common import atomic
        from gnautilus_ml.ocular_boundary_audit import load_contextual_episodes
        with TemporaryDirectory() as name:
            folder=Path(name);p=folder/'.metadata/canonical/x';p.mkdir(parents=True)
            raw=np.arange(5000*32,dtype='float32').reshape(5000,32);np.save(p/'samples.npy',raw);np.save(p/'coverage.npy',np.ones(5000,bool));np.save(p/'conflict_mask.npy',np.zeros(5000,bool))
            atomic(p/'segments.json',dict(segments=[[0,5000]]));atomic(p/'descriptor.original.json',dict(sampling_rate=250,channels=[dict(kind='EEG',physical_index=i) for i in range(32)]))
            r=dict(trial_id='a',split='test',stream_id='x',context_start=2000,context_end=3000,start=2250,end=2750,onset_in_episode=250,offset_in_episode=750)
            y=np.full(1000,-1,dtype='int8');y[250:750]=3;row=dict(id='a',x=raw[2000:3000].T.copy(),y=y,metadata=r)
            with patch('gnautilus_ml.personal_study.load_episodes',return_value=[row]):out=load_contextual_episodes(dict(folder=str(folder),episodes=[r]),'test',pre=1000,post=500)[0]
            at=2000-out['metadata']['context_start'];np.testing.assert_array_equal(out['x'][:,at:at+1000],row['x']);np.testing.assert_array_equal(out['y'][at:at+1000],y)
            self.assertTrue(np.all(out['y'][:at]==-1));self.assertGreaterEqual(out['metadata']['actual_pre_s'],4)

if __name__=='__main__':unittest.main()
