import tempfile
import unittest
from pathlib import Path

from hub.device_transition import transition_state, retired_action, annotate_html, background_targets


class DeviceTransitionTests(unittest.TestCase):
    def test_archived_model_actions_cannot_be_restarted(self):
        for path in ['/api/models/activate', '/api/models/refresh', '/api/intent/start',
                     '/api/intent/replay', '/api/intent/test', '/api/calibration/start',
                     '/api/acquisition/start', '/api/record/start']:
            self.assertTrue(retired_action(path, {}, True), path)
        self.assertTrue(retired_action('/api/intent/control', {'enabled': True}, True))
        self.assertTrue(retired_action('/api/source', {'mode': 'live'}, True))
        self.assertTrue(retired_action('/api/source', {'mode': 'recording'}, True))

    def test_stop_and_vr_transport_remain_available(self):
        for path in ['/api/intent/stop', '/api/vr/stop', '/api/vr/test',
                     '/api/server/disconnect', '/api/acquisition/stop', '/api/record/stop',
                     '/api/vr/receiver/start', '/api/quest/reload']:
            self.assertFalse(retired_action(path, {}, True), path)
        self.assertFalse(retired_action('/api/intent/control', {'enabled': False}, True))
        self.assertFalse(retired_action('/api/source', {'mode': 'paused'}, True))
        self.assertFalse(retired_action('/api/intent/start', {}, False))

    def test_phase_is_explicit_and_html_explains_pending_integration(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            self.assertFalse(transition_state(r)['legacy_archived'])
            (r/'configs').mkdir()
            (r/'configs/device_transition.json').write_text(
                '{"legacy_archived": true, "target_device": "g.tec g.Nautilus", "new_device_integrated": false}',
                encoding='utf-8')
            self.assertTrue(transition_state(r)['legacy_archived'])
        b = annotate_html(b'<html><body class="studio"><main>kept</main></body></html>', True)
        self.assertIn(b'data-device-transition="gnautilus"', b)
        self.assertIn(b'class="studio"', b)
        self.assertNotIn(b'<aside', b) # The existing workspace owns visible notices.
        self.assertIn(b'<main>kept</main>', b)
        self.assertEqual(annotate_html(b'plain', False), b'plain')

    def test_archived_start_has_no_model_or_training_observers(self):
        names = ['acquire', 'analyze', 'infer', 'intent.loop', 'observe_training', 'refactor.loop', 'model_library.loop']
        self.assertEqual(background_targets(names, True), ['acquire', 'analyze', 'infer'])
        self.assertEqual(background_targets(names, False), names)

    def test_archived_quest_still_accepts_binary_mirror_and_blocks_old_model(self):
        from hub.quest import QuestService
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'configs').mkdir()
            (r/'configs/device_transition.json').write_text('{"legacy_archived":true}',encoding='utf-8')
            s=QuestService(SimpleNamespace(),r)
            jpeg=b'\xff\xd8'+b'a'*30+b'\xff\xd9'
            s.dispatch('test-device','POST','/v1/mirror',jpeg)
            self.assertEqual(s.frame()['jpeg'],jpeg)
            with self.assertRaisesRegex(ValueError,'已归档'):
                s.dispatch('test-device','POST','/v1/intent/start',b'{}')


if __name__ == '__main__':
    unittest.main()
