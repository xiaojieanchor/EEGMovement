import copy,unittest
import numpy as np
from gnautilus_ml.navigation_channel_audit import strict_intervals,rank_channels,select_candidates,similarity_audit,structure_audit
from gnautilus_ml.navigation_contract import CHANNELS

def catalog():
    rows=[]
    for i,(role,label) in enumerate([('train','FORWARD'),('train','BACKWARD'),('train','LEFT'),('validation','RIGHT'),('test','RIGHT')]):
        a=10000*i+1000;b=a+5000
        phases=dict(onset=dict(status='marked',sample_start=a,sample_end=a+200),offset=dict(status='marked',sample_start=b-100,sample_end=b))
        if label=='BACKWARD':phases['offset']=dict(status='absent')
        if role=='validation':phases['onset']=dict(status='absent')
        rows.append(dict(trial_id=str(i)*64,stream_id='s',split=role,label=label,task_start=a,task_end=b,annotation=dict(phases=phases)))
    return dict(channels=CHANNELS,sampling_rate=250,trials=rows)

class NavigationChannelAuditTests(unittest.TestCase):
    def test_uses_marked_eye_end_start_and_never_test(self):
        result=strict_intervals(catalog(),guard=250);self.assertEqual(len(result['included']),2)
        self.assertEqual(result['included'][0]['start'],1450);self.assertEqual(result['included'][0]['end'],5650)
        self.assertEqual(len(result['excluded']),2);self.assertTrue(all(q['split']!='test' for q in result['included']+result['excluded']))

    def test_montage_cannot_silently_swap_cz_c5(self):
        c=catalog();c['channels']=list(CHANNELS);c['channels'][15],c['channels'][18]=c['channels'][18],c['channels'][15]
        with self.assertRaises(ValueError):strict_intervals(c)

    def features(self):
        rng=np.random.default_rng(9);rows=[]
        for i,label in enumerate(['FORWARD','LEFT','RIGHT']):
            for window in range(6):
                x=rng.normal(0,.2,(32,2));x[15]+=i*6;x[18]-=i*4
                rows.append(dict(split='train',trial_id=label,label=label,features=x,spectrum=rng.normal(size=(32,23)),correlation=np.eye(32)))
        return rows

    def test_channel_scores_are_train_only_and_detect_separation(self):
        rows=self.features();r=rank_channels(rows);self.assertEqual(set(r['ranking'][:2]),{'Cz','C5'})
        bad=copy.deepcopy(rows);bad[0]['split']='validation'
        with self.assertRaises(ValueError):rank_channels(bad)
        self.assertFalse(r['independent_same_direction_repeats'])

    def test_candidates_preserve_physical_ids_and_motor_pool(self):
        rows=self.features();r=rank_channels(rows);s=select_candidates(r,np.eye(32));self.assertEqual(len(s),6)
        for c in s:
            self.assertTrue(set(c['channels'])<=set(CHANNELS[8:]));self.assertEqual(c['channels'],[CHANNELS[i] for i in c['physical_indices_zero_based']])

    def test_similarity_missing_same_direction_is_unknown(self):
        train=self.features();valid=[dict(train[0],split='validation',trial_id='b',label='BACKWARD')]
        r=similarity_audit(train,valid);self.assertEqual(r['matched_directions'],[]);self.assertIsNone(r['channels'][0]['mean_same_direction_similarity'])

    def test_filter_and_first_window_cannot_read_later_or_ocular_samples(self):
        from unittest.mock import patch
        from gnautilus_ml.navigation_channel_audit import extract
        rng=np.random.default_rng(18);x=rng.normal(0,1,(32,2200));meta=dict(context_start=0)
        interval=dict(trial_id='a',stream_id='s',label='FORWARD',split='train',guard_samples=250,start=350,end=1850,raw_filter_start=100,raw_filter_end=2100)
        with patch('gnautilus_ml.personal_study.load_episodes',return_value=[dict(id='a',metadata=meta,x=x)]):a,_=extract({},[interval],'train')
        y=x.copy();y[:,:100]=8000;y[:,850:]=20
        with patch('gnautilus_ml.personal_study.load_episodes',return_value=[dict(id='a',metadata=meta,x=y)]):b,_=extract({},[interval],'train')
        np.testing.assert_array_equal(a[0]['features'],b[0]['features']);self.assertEqual(a[0]['raw_filter_support_start'],100)
        with self.assertRaises(ValueError):extract({},[interval],'test')

    def test_joint_structure_uses_off_diagonal_and_preserves_matches(self):
        train=self.features()
        for row in train:
            row['correlation']=np.eye(32);v={'FORWARD':.7,'LEFT':-.4,'RIGHT':.1}[row['label']]
            row['correlation'][15,18]=row['correlation'][18,15]=v
        val=[dict(train[0],trial_id='v',split='validation')]
        plan=[dict(name='Cz_C5',physical_indices_zero_based=[15,18],channels=['Cz','C5'])]
        r=structure_audit(train,val,plan);self.assertEqual(r[0]['same_is_nearest_count'],1)
        self.assertAlmostEqual(r[0]['matched_trials'][0]['same_distance'],0.,places=12)

if __name__=='__main__':unittest.main()
