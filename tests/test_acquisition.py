import io
import unittest
from hub.acquisition import GuidedAcquisition


class FakeHub:
    def __init__(self):
        import threading
        self.lock=threading.RLock();self.record_lock=threading.RLock();self.recorder=None
        self.source_mode='live';self.last_packet=100.;self.epoch=7;self.sample_index=500
        self.quality={'valid':False,'state':'invalid'};self.recording_name='fixture'
    def start_recording(self):self.recorder=io.StringIO()
    def stop_recording(self):self.saved=self.recorder.getvalue();self.recorder=None


class AcquisitionTests(unittest.TestCase):
    def test_v5_continuous_instructions_have_preparation_and_minimum_eight_second_actions(self):
        import json
        session=self.a.start(dict(build_id='generated-ui-test',paradigm='guided_motor_imagery',
            presentation_protocol='white_continuous_countdown_axes_v5',target_seconds=780,blocks=2))
        self.assertTrue(session['countdown_displayed'])
        self.assertEqual(session['instruction_visibility'],'continuous_during_task')
        self.assertGreaterEqual(session['planned_seconds'],780)
        self.assertLess(session['planned_seconds'],798)
        self.assertEqual(set(t['label'] for t in session['trials']),set(session['labels']))
        for trial in session['trials']:
            self.assertEqual(trial['prepare_ms'],2000)
            self.assertIn(trial['task_ms'],[8000,10000,12000])
        header=json.loads(self.hub.recorder.getvalue().splitlines()[0])
        self.assertEqual(header['presentation_protocol'],'white_continuous_countdown_axes_v5')
        self.assertFalse(header['formal_training_ready'])

    def test_new_collection_records_joint_gaze_intent_without_invented_eye_truth(self):
        import hashlib,json
        from pathlib import Path
        session=self.start();header=json.loads(self.hub.recorder.getvalue().splitlines()[0])
        policy=Path(__file__).resolve().parents[1]/'configs/joint_control_policy.json'
        self.assertEqual(header.get('control_intent'),'imagery_and_or_directional_gaze')
        self.assertEqual(header.get('control_policy_sha256'),hashlib.sha256(policy.read_bytes()).hexdigest())
        self.assertIs(header.get('eye_tracking_measured'),False)
        self.assertFalse(session['intention_verified'])

    def test_v3_longer_schedule_and_old_protocol_durations_preserved(self):
        from collections import Counter
        session=self.a.start(dict(build_id='test',paradigm='guided_motor_imagery',blocks=2,
                                  presentation_protocol='white_centered_instruction_v3'))
        for trial in session['trials']:
            self.assertEqual((trial['prepare_ms'],trial['cue_ms'],trial['task_ms']),(2000,2000,6000))
            self.assertIn(trial['rest_ms'],[3000,3500,4000])
        self.assertEqual(Counter(t['label'] for t in session['trials']),
                         Counter({'REST':2,'FORWARD':2,'BACKWARD':2,'LEFT':2,'RIGHT':2}))
        duration=sum(t[k] for t in session['trials'] for k in ['prepare_ms','cue_ms','task_ms','rest_ms'])
        self.assertTrue(130000<=duration<=140000)
        self.a.event(self.event(session))
        import json
        self.assertEqual(json.loads(self.hub.recorder.getvalue().splitlines()[-1])['presentation_protocol'],
                         'white_centered_instruction_v3')
        self.a.stop(session['session_id'])
        for protocol in ['legacy_fixation_v1','white_centered_instruction_v2']:
            old=self.a.start(dict(build_id='old',paradigm='guided_motor_imagery',blocks=1,presentation_protocol=protocol))
            for trial in old['trials']:
                self.assertEqual((trial['prepare_ms'],trial['cue_ms'],trial['task_ms']),(1000,1000,3000))
                self.assertIn(trial['rest_ms'],[1500,2000,2500])
            self.a.stop(old['session_id'])

    def test_presentation_protocol_is_saved_and_unknown_protocol_rejected(self):
        session=self.a.start(dict(build_id='test',paradigm='guided_motor_imagery',blocks=1,
                                  presentation_protocol='white_centered_instruction_v2'))
        self.assertEqual(session['presentation_protocol'],'white_centered_instruction_v2')
        self.assertIn('white_centered_instruction_v2',self.hub.recorder.getvalue())
        with self.assertRaisesRegex(ValueError,'Unsupported presentation'):
            self.a.start(dict(build_id='test',paradigm='guided_motor_imagery',blocks=1,presentation_protocol='unknown'))
        self.a.event(self.event(session))
        import json
        self.assertEqual(json.loads(self.hub.recorder.getvalue().splitlines()[-1])['presentation_protocol'],'white_centered_instruction_v2')

    def setUp(self):
        self.hub=FakeHub();self.now=100.;self.a=GuidedAcquisition(self.hub,clock=lambda:self.now)
    def start(self):return self.a.start({'build_id':'eeg-test','paradigm':'guided_motor_imagery','blocks':2})
    def event(self,session,seq=1,**extra):
        return dict(session_id=session['session_id'],seq=seq,trial=0,label=session['trials'][0]['label'],phase='prepare',sender_monotonic_ms=1000.,sender_frame=10,clock_offset_ms=99000.,sync_rtt_ms=4.,**extra)
    def test_explicit_start_records_unverified_metadata_and_raw_session(self):
        session=self.start();self.assertTrue(session['active']);self.assertIsNotNone(self.hub.recorder)
        self.assertFalse(session['formal_training_ready']);self.assertIn('guided_session',self.hub.recorder.getvalue())
    def test_replay_stale_and_existing_recording_refused(self):
        self.hub.source_mode='replay'
        with self.assertRaises(ValueError):self.start()
        self.hub.source_mode='live';self.now=101
        with self.assertRaises(ValueError):self.start()
        self.now=100;self.hub.recorder=io.StringIO()
        with self.assertRaises(ValueError):self.start()
    def test_ack_idempotent_and_conflicting_duplicate_refused(self):
        session=self.start();event=self.event(session);one=self.a.event(event);before=self.hub.recorder.getvalue()
        self.assertEqual(one,self.a.event(event));self.assertEqual(before,self.hub.recorder.getvalue())
        with self.assertRaises(ValueError):self.a.event(dict(event,sender_frame=11))
    def test_future_sequence_wrong_session_nonfinite_and_unknown_label_refused(self):
        session=self.start()
        for change in [dict(seq=2),dict(session_id='wrong'),dict(sender_monotonic_ms=float('nan')),dict(label='guessed_intent')]:
            event=self.event(session);event.update(change)
            with self.assertRaises(ValueError):self.a.event(event)
    def test_markers_anchor_received_sample_epoch_qc_and_software_timing(self):
        import json
        session=self.start();self.a.event(self.event(session));row=json.loads(self.hub.recorder.getvalue().splitlines()[-1])
        self.assertEqual(row['sample_index_end'],500);self.assertEqual(row['source_epoch'],7)
        self.assertFalse(row['quality_valid']);self.assertEqual(row['trigger_type'],'software_frame_end')
        self.assertFalse(row['intention_verified'])
    def test_timeout_stops_and_retains_abort_marker(self):
        self.start();self.now=111;self.a.expire()
        self.assertFalse(self.a.snapshot()['active']);self.assertIsNone(self.hub.recorder);self.assertIn('sender_timeout',self.hub.saved)
    def test_finish_is_idempotent_and_does_not_close_new_manual_recording(self):
        session=self.start();self.a.stop(session['session_id'],'operator_stop');self.hub.start_recording()
        self.a.stop(session['session_id'],'operator_stop');self.assertIsNotNone(self.hub.recorder)
    def test_schedule_balances_all_five_labels_in_each_block(self):
        from collections import Counter
        session=self.start()
        self.assertEqual(len(session['trials']),10)
        self.assertEqual([t['trial'] for t in session['trials']],list(range(10)))
        for block in range(2):
            self.assertEqual(Counter(t['label'] for t in session['trials'] if t['block']==block),
                             Counter({'REST':1,'FORWARD':1,'BACKWARD':1,'LEFT':1,'RIGHT':1}))
    def test_marker_cannot_claim_label_outside_randomized_schedule(self):
        session=self.start();event=self.event(session)
        event['label']='LEFT' if event['label']!='LEFT' else 'RIGHT'
        with self.assertRaises(ValueError):self.a.event(event)
    def test_skipped_phases_and_early_completion_are_rejected(self):
        session=self.start();event=self.event(session);event['phase']='task'
        with self.assertRaises(ValueError):self.a.event(event)
        with self.assertRaises(ValueError):self.a.stop(session['session_id'],'completed')
    def test_complete_balanced_schedule_can_be_saved(self):
        session=self.start();seq=0
        for trial in session['trials']:
            for phase in ['prepare','cue','task','rest']:
                seq+=1;event=self.event(session,seq);event.update(trial=trial['trial'],label=trial['label'],phase=phase)
                self.a.event(event)
        result=self.a.stop(session['session_id'],'completed')
        self.assertEqual(result['phase'],'completed');self.assertEqual(result['events_saved'],40)
