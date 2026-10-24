import unittest
from hub.intent_control import IntentControl
class ControlTests(unittest.TestCase):
    def setUp(self):
        self.now=10.;self.c=IntentControl(True,lambda:self.now)
    def result(self,end=1000,p=None,source='LIVE'):
        return dict(eligible=True,source=source,probabilities=p or [.03,.9,.02,.03,.02],source_epoch=1,sample_index_end=end,model_id='test',input_origin_monotonic=self.now-.02,usable_channels=[0,1,2,3,4,5,6])
    def test_distinct_supported_updates_start_and_invalid_stops(self):
        self.assertFalse(self.c.update(self.result())['active']);self.now+=.1
        self.assertTrue(self.c.update(self.result(1025))['active'])
        self.assertFalse(self.c.update(None)['active'])
    def test_replay_and_collection_never_move(self):
        self.assertFalse(self.c.update(self.result(source='PERSONAL_REPLAY'))['active'])
        self.assertFalse(self.c.update(self.result(),True)['active'])
    def test_training_test_needs_explicit_current_lease_authorization(self):
        r=self.result(source='TRAINING_TEST')
        self.assertFalse(self.c.update(r)['active'])
        self.c.update(r,training_test_authorized=True);self.now+=.1
        self.assertTrue(self.c.update(self.result(1025,source='TRAINING_TEST'),training_test_authorized=True)['active'])
    def test_duplicate_cannot_confirm_and_quality_change_resets(self):
        self.c.update(self.result());self.now+=.1
        self.assertFalse(self.c.update(self.result())['active'])
        r=self.result(1050);r['usable_channels']=[0,1,3,4]
        self.assertFalse(self.c.update(r)['active'])
    def test_invalid_config_is_atomic_and_history_retained(self):
        self.c.update(self.result());self.now+=.1;d=self.c.update(self.result(1025,p=[.04,.85,.04,.03,.04]))
        self.assertNotEqual(d['raw_probabilities'],d['probabilities'])
        with self.assertRaises(ValueError):self.c.configure(dict(enabled=False,speed=100))
        self.assertTrue(self.c.enabled)
    def test_stale_input_and_idle_stop(self):
        r=self.result();r['input_origin_monotonic']-=1;self.assertFalse(self.c.update(r)['active'])
        self.c.update(self.result());self.now+=.1;self.c.update(self.result(1025));self.now+=.4
        self.assertFalse(self.c.update(self.result(1125,p=[.99,.0025,.0025,.0025,.0025]))['active'])
    def test_compatible_axes_combine_and_equal_opponents_do_not(self):
        for linear,turn,p in [(1,-1,[.04,.43,.05,.43,.05]),(1,1,[.04,.43,.05,.05,.43]),(-1,-1,[.04,.05,.43,.43,.05]),(-1,1,[.04,.05,.43,.05,.43])]:
            self.c.configure({'enabled':True});self.c.update(self.result(p=p));self.now+=.1;d=self.c.update(self.result(1025,p=p))
            self.assertEqual((d['linear_axis'],d['turn_axis']),(linear,turn));self.assertEqual(d['motion_schema'],2)
            self.assertGreater(d['linear_strength'],0);self.assertGreater(d['turn_strength'],0)
        self.c.configure({'enabled':True});p=[.04,.24,.24,.24,.24];self.c.update(self.result(p=p));self.now+=.1
        self.assertFalse(self.c.update(self.result(1025,p=p))['active'])
    def test_negligible_axis_mass_cannot_make_spurious_second_axis(self):
        p=[.02,.002,.0001,.95,.0279];self.c.update(self.result(p=p));self.now+=.1;d=self.c.update(self.result(1025,p=p))
        self.assertEqual(d['linear_axis'],0);self.assertEqual(d['turn_axis'],-1)
    def test_reverse_is_confirmed_and_history_is_continuous(self):
        p=[.02,.9,.02,.03,.03];self.c.update(self.result(p=p));self.now+=.1;self.c.update(self.result(1025,p=p))
        seen=[]
        for k in range(8):
            self.now+=.1;d=self.c.update(self.result(1050+25*k,p=[.02,.02,.9,.03,.03]));seen.append(d['linear_axis'])
        self.assertIn(0,seen);self.assertEqual(seen[-1],-1)
