import unittest
import numpy as np

class ContextualReadoutTests(unittest.TestCase):
    def rows(self):
        source=np.random.default_rng(1).normal(size=(32,1800)).astype('float32')
        result=[]
        for tid,a,b,on,off,c in [('a',0,1200,500,800,1),('b',900,1800,1300,1600,2)]:
            y=np.full(b-a,-1,dtype='int8');y[on-a:off-a]=c
            result.append(dict(id=tid,x=source[:,a:b],y=y,metadata=dict(trial_id=tid,stream_id='s',split='train',context_start=a,context_end=b,start=on,end=off,onset_in_episode=on-a,offset_in_episode=off-a)))
        return result
    def test_contiguous_same_role_context_merges_without_duplicate_samples(self):
        from gnautilus_ml.contextual_readout import merge_rows
        rows=merge_rows(self.rows());self.assertEqual(len(rows),1);r=rows[0]
        self.assertEqual(r['x'].shape,(32,1800));self.assertEqual(np.sum(r['y']>=0),600);self.assertTrue(np.all(r['y'][800:1300]==-1));self.assertEqual(len(r['metadata']['anchors']),2)
    def test_overlapping_roles_and_changed_source_are_rejected(self):
        from gnautilus_ml.contextual_readout import merge_rows
        rows=self.rows();rows[1]['metadata']['split']='validation'
        with self.assertRaises(ValueError):merge_rows(rows)
        rows=self.rows();rows[1]['x']=rows[1]['x'].copy();rows[1]['x'][0,0]+=1
        with self.assertRaises(ValueError):merge_rows(rows)
    def test_anchor_assignment_never_invents_unknown_direction(self):
        from gnautilus_ml.contextual_readout import metadata_at
        r=self.rows()[0]
        self.assertEqual(metadata_at(r,300),('','UNKNOWN'))
        self.assertEqual(metadata_at(r,600),('a','onset_0_2s'))
        self.assertEqual(metadata_at(r,900),('','UNKNOWN'))
    def test_equal_support_comparison_includes_unready_rejections(self):
        from gnautilus_ml.contextual_readout import summarize
        rows=[dict(label=1,trial_id='a',phase='onset_0_2s'),dict(label=1,trial_id='a',phase='onset_0_2s'),dict(label=-1,trial_id='',phase='UNKNOWN')]
        p=np.array([[0,0,0,0,0],[0,1,0,0,0],[0,1,0,0,0]],float)
        r=summarize(rows,p);self.assertEqual(r['full_labelled_updates'],2);self.assertEqual(r['ready_labelled_updates'],1);self.assertEqual(r['recall_with_rejections'][1],.5);self.assertEqual(r['unknown_updates'],1)
    def test_source_exposure_overlap_detected(self):
        from gnautilus_ml.contextual_readout import overlaps
        x=[dict(stream_id='s',start=20,end=40)];y=[dict(stream_id='s',start=39,end=50)]
        self.assertEqual(len(overlaps(x,y)),1);y[0]['start']=40;self.assertEqual(overlaps(x,y),[])
    def test_temporal_average_respects_sequence_and_qc_not_trial_labels(self):
        from gnautilus_ml.contextual_readout import causal_average
        frames=[dict(sequence_id='s',reset=0),dict(sequence_id='s',reset=0),dict(sequence_id='s',reset=1)]
        p=np.eye(5)[[1,2,3]];q=causal_average(p,frames,1.)
        self.assertGreater(q[1,1],q[1,2]);np.testing.assert_array_equal(q[2],p[2])
    def test_decision_state_continues_across_trial_anchor_without_losing_trial_count(self):
        from gnautilus_ml.eye_joint_probe import decision_report
        p=np.tile(np.array([.0025,.99,.0025,.0025,.0025]),(6,1));y=np.ones(6,dtype=int)
        frames=[dict(sequence_id='one-real-stream',reset=0,trial_id='a' if i<3 else 'b',source_sample_end=25*(i+1)) for i in range(6)]
        r=decision_report(y,p,frames)
        self.assertEqual(r['responded_direction_trials'],2);self.assertEqual(r['accepted_correct_updates'],4)

if __name__=='__main__':unittest.main()
