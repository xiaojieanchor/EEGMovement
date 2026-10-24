import unittest

import numpy as np

from hub.channel_quality import green_channels,NativeQuality,pending_quality
from unittest.mock import Mock
from pathlib import Path


class ChannelQualityTests(unittest.TestCase):
    def test_slow_native_startup_does_not_block_or_kill_a_live_initializing_engine(self):
        from unittest.mock import patch
        clock=[100.];worker=NativeQuality(Path('.'),clock=lambda:clock[0]);worker.process=Mock();worker.process.poll.return_value=None
        process=worker.process;worker.ready=False;worker.starting_at=100.
        q={'channels':[dict(valid=True,reasons=[]) for _ in range(7)]}
        with patch('hub.channel_quality.time.monotonic',side_effect=lambda:clock[0]):
            first=worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
            self.assertFalse(any(first['training_mask']));process.kill.assert_not_called()
            clock[0]=110.;worker.lines.put('READY');worker.lines.put('Q [1,1,1,1,1,1,1]')
            resumed=worker.evaluate([[0.]*7]*256,q,sample_index_end=525,source_epoch=1)
            self.assertTrue(resumed['native_updated']);self.assertFalse(any(resumed['training_mask']))
            clock[0]=111.01
            stable=worker.evaluate([[0.]*7]*256,q,sample_index_end=550,source_epoch=1)
            self.assertTrue(all(stable['training_mask']));worker.process.kill.assert_not_called()

    def probe(self):
        clock=[100.]
        worker=NativeQuality(Path('.'),clock=lambda:clock[0])
        worker._detect=Mock(return_value=[1]*7)
        quality={'channels':[dict(valid=True,reasons=[]) for _ in range(7)]}
        return worker,clock,quality

    def test_vendor_receives_new_batches_not_repeated_overlapping_windows(self):
        worker,clock,q=self.probe()
        first=worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        self.assertFalse(any(first['training_mask']))
        for n in range(1,11):
            clock[0]=100+n*.1
            result=worker.evaluate([[0.]*7]*256,q,sample_index_end=500+25*n,source_epoch=1)
        self.assertEqual(worker._detect.call_count,1)
        self.assertTrue(all(result['training_mask']))
        clock[0]=101.1
        worker.evaluate([[0.]*7]*256,q,sample_index_end=775,source_epoch=1)
        self.assertEqual(worker._detect.call_count,2)

    def test_brief_native_status_has_one_fast_recheck_and_no_second_recovery_wait(self):
        worker,clock,q=self.probe();bad=[1]*7;bad[2]=3
        worker._detect.side_effect=[[1]*7,bad,[1]*7]
        worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        clock[0]=101.01;self.assertTrue(all(worker.evaluate([[0.]*7]*256,q,sample_index_end=750,source_epoch=1)['training_mask']))
        clock[0]=101.11;self.assertFalse(worker.evaluate([[0.]*7]*256,q,sample_index_end=775,source_epoch=1)['training_mask'][2])
        clock[0]=101.21;r=worker.evaluate([[0.]*7]*256,q,sample_index_end=800,source_epoch=1)
        self.assertTrue(r['native_status_recheck']);self.assertTrue(r['training_mask'][2]);self.assertEqual(worker._detect.call_count,3)

    def test_electrical_failure_keeps_the_full_recovery_confirmation(self):
        worker,clock,q=self.probe();worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        clock[0]=101.01;worker.evaluate([[0.]*7]*256,q,sample_index_end=750,source_epoch=1)
        q['channels'][2]=dict(valid=False,reasons=['平线']);clock[0]=101.11
        self.assertFalse(worker.evaluate([[0.]*7]*256,q,sample_index_end=775,source_epoch=1)['training_mask'][2])
        q['channels'][2]=dict(valid=True,reasons=[]);clock[0]=101.21
        self.assertFalse(worker.evaluate([[0.]*7]*256,q,sample_index_end=800,source_epoch=1)['training_mask'][2])

    def test_fast_safety_guard_excludes_one_bad_channel_between_vendor_batches(self):
        worker,clock,q=self.probe()
        worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        clock[0]=101.01
        result=worker.evaluate([[0.]*7]*256,q,sample_index_end=750,source_epoch=1)
        self.assertTrue(all(result['training_mask']))
        q['channels'][4]=dict(valid=False,reasons=['幅度过高'])
        clock[0]=101.02
        result=worker.evaluate([[0.]*7]*256,q,sample_index_end=755,source_epoch=1)
        self.assertEqual(result['training_mask'],[True,True,True,True,False,True,True])
        self.assertEqual(worker._detect.call_count,1)

    def test_cached_native_status_expires_and_epoch_never_inherits_green_lease(self):
        worker,clock,q=self.probe()
        worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        clock[0]=101.6
        expired=worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        self.assertFalse(any(expired['training_mask']))
        fresh=worker.evaluate([[0.]*7]*256,q,sample_index_end=525,source_epoch=2)
        self.assertFalse(any(fresh['training_mask']))
        self.assertTrue(fresh['native_updated'])

    def test_process_death_invalidates_cache_without_waiting_for_next_batch(self):
        worker,clock,q=self.probe()
        worker.evaluate([[0.]*7]*256,q,sample_index_end=500,source_epoch=1)
        clock[0]=101.01
        self.assertTrue(all(worker.evaluate([[0.]*7]*256,q,sample_index_end=750,source_epoch=1)['training_mask']))
        worker.process=Mock();worker.process.poll.return_value=1
        failed=worker.evaluate([[0.]*7]*256,q,sample_index_end=755,source_epoch=1)
        self.assertFalse(any(failed['training_mask']))
        self.assertIn('process exited',failed['native_error'])

    def test_gap_keeps_measured_electrode_identity_but_never_training_permission(self):
        previous=green_channels({'channels':[dict(valid=True,reasons=[]) for _ in range(7)]},[1]*7,[True]*7)
        pending=pending_quality(previous,'数据断点后复核')
        self.assertFalse(any(pending['training_mask']))
        self.assertTrue(all(c['measurement_held'] and not c['training_eligible'] for c in pending['channels']))
        self.assertTrue(all(c['color']=='green' and c['quality_label'].startswith('上次：') for c in pending['channels']))
        self.assertTrue(all(previous['training_mask']))

    def test_real_hub_gap_brakes_training_without_erasing_electrode_measurements(self):
        import json,time
        from hub.app import Hub,ROOT
        h=Hub(json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8')))
        h.accept([[10.]*7]*5,1,'12:00:00')
        h.quality=dict(green_channels({'channels':[dict(valid=True,reasons=[]) for _ in range(7)]},[1]*7,[True]*7),
                       valid=True,state='usable',label='通过',checked_monotonic=time.monotonic())
        h.accept([[20.]*7]*5,3,'12:00:00.040',gap=1,discontinuity=True)
        state=h.snapshot()
        self.assertEqual(len(h.raw),5)
        self.assertFalse(any(state['quality']['training_mask']))
        self.assertFalse(state['control']['enabled'])
        self.assertEqual(len(state['quality']['channels']),7)
        self.assertTrue(state['quality']['measurement_held'])
    def test_only_native_one_plus_software_pass_is_green(self):
        q={'channels':[dict(valid=True,reasons=[]) for _ in range(7)]}
        result=green_channels(q,[1,0,2,-1,1,1,1],stable=[True]*7)
        self.assertEqual(result['training_mask'],[True,False,False,False,True,True,True])
        self.assertEqual([c['color'] for c in result['channels'][:4]],['green','red','cyan','gray'])

    def test_software_failure_or_missing_native_never_accepted(self):
        q={'channels':[dict(valid=True,reasons=[]) for _ in range(7)]}
        q['channels'][2]['valid']=False
        result=green_channels(q,[1]*7,stable=[True]*7)
        self.assertFalse(result['training_mask'][2])
        self.assertEqual(green_channels(q,None)['training_mask'],[False]*7)

    def test_recovering_channels_are_not_yet_training_green(self):
        q={'channels':[dict(valid=True,reasons=[]) for _ in range(7)]}
        result=green_channels(q,[1]*7,stable=[False,True,True,True,True,True,True])
        self.assertFalse(result['training_mask'][0])
        self.assertEqual(result['channels'][0]['color'],'amber')

    def test_quality_expiry_cannot_remain_green_while_data_keeps_arriving(self):
        import json,time
        from hub.app import Hub,ROOT
        h=Hub(json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8')))
        h.last_packet=time.monotonic()
        h.quality=dict(valid=True,state='usable',good_channels=7,channels=[dict(training_eligible=True,color='green')]*7,
                       training_mask=[True]*7,checked_monotonic=time.monotonic()-3)
        q=h.snapshot()['quality']
        self.assertFalse(q['valid']);self.assertEqual(q['training_mask'],[False]*7)

    def test_native_unknown_or_flat_code_only_excludes_that_channel(self):
        q={'channels':[dict(valid=True,reasons=[]) for _ in range(7)]}
        result=green_channels(q,[3,99,1,1,1,1,1],stable=[True]*7)
        self.assertEqual(result['training_mask'],[False,False,True,True,True,True,True])


if __name__=='__main__':unittest.main()
