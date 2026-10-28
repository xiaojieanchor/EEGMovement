import unittest
import numpy as np
from gnautilus_ml.ocular_channel_selection import channel_candidates
from gnautilus_ml.navigation_contract import CHANNELS

class OcularChannelSelectionTests(unittest.TestCase):
    def records(self):
        rng=np.random.default_rng(7);rows=[]
        for i,d in enumerate(['UP','DOWN','LEFT','RIGHT']):
            x=rng.normal(0,.1,(32,700)).astype('float32');x[i,500:]+=np.linspace(0,15,200)
            rows.append(dict(split='train',class_label=d,x=x,event_start=500,segment_id=d))
        return rows

    def test_high_contrast_channels_selected_and_weights_bounded(self):
        r=channel_candidates(self.records());self.assertEqual(set(r['global_ranking'][:4]),set(CHANNELS[:4]))
        self.assertEqual(len(r['candidates']),5)
        for q in r['candidates']:
            w=np.asarray(q['input_weights']);self.assertEqual(w.shape,(32,));self.assertTrue(((w>=0)&(w<=1)).all())
            self.assertEqual(q['channels'],[CHANNELS[i] for i in np.flatnonzero(w)])
        self.assertEqual(r['candidates'][1]['channels'],CHANNELS[:4])

    def test_no_heldout_or_no_eye_data_used_to_rank(self):
        rows=self.records();rows[0]['split']='validation'
        with self.assertRaises(ValueError):channel_candidates(rows)

    def test_same_trial_reference_is_not_fabricated_no_eye(self):
        r=channel_candidates(self.records());self.assertEqual(r['fit_role'],'train')
        self.assertFalse(r['reference_regions_labelled_no_eye'])
        self.assertEqual(set(r['per_direction']),{'UP','DOWN','LEFT','RIGHT'})

if __name__=='__main__':unittest.main()
