"""Non-widget acquisition-controller tests; no EEG or vendor libraries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gnautilus_acquisition.gui import AcquisitionController, SettingsStore, load_enrollment


def enrollment():
    return {'host': '192.168.1.10', 'port': 8767, 'agent_id': 'a' * 32,
            'secret': 'b' * 64, 'certificate_sha256': 'c' * 64}


class FakeCollector:
    def __init__(self, settings, pairing, storage_root):
        self.settings, self.pairing, self.storage_root = settings, pairing, storage_root
        self.running = False
        self.stops = 0

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        self.stops += 1

    def snapshot(self):
        return {'state': 'receiving' if self.running else 'closed', 'samples': 123,
                'network': {'connected': False}, 'descriptor': None}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.pairing_path = self.root / 'pairing.json'
        self.pairing_path.write_text(json.dumps(enrollment()), encoding='utf-8')
        self.profile = {'sdk_dir': str(self.root), 'serial': 'NA-123',
                        'pairing_path': str(self.pairing_path), 'storage_root': str(self.root / 'data')}
        self.controller = AcquisitionController(collector_factory=FakeCollector)

    def tearDown(self):
        self.controller.stop()
        self.directory.cleanup()

    def test_begin_passes_only_explicit_config_paths_and_no_filter_settings(self):
        self.controller.begin(self.profile)
        worker = self.controller.collector
        self.assertEqual(worker.settings, {'sdk_dir': str(self.root), 'serial': 'NA-123', 'layout': {}})
        self.assertEqual(worker.pairing, enrollment())
        self.assertEqual(self.controller.snapshot()['samples'], 123)
        with self.assertRaisesRegex(ValueError, '正在'):
            self.controller.begin(self.profile)

    def test_stop_is_explicit_and_idempotent(self):
        self.controller.begin(self.profile)
        worker = self.controller.collector
        self.controller.stop()
        self.controller.stop()
        self.assertEqual(worker.stops, 1)
        self.assertEqual(self.controller.snapshot()['state'], 'closed')

    def test_invalid_or_wrong_pairing_never_creates_collector(self):
        self.pairing_path.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.controller.begin(self.profile)
        self.assertIsNone(self.controller.collector)

    def test_layout_uses_explicit_file_and_keeps_unverified_status(self):
        layout = self.root / 'layout.json'
        layout.write_text(json.dumps({'verified': False, 'channels': {'0': 'Fp1'}}), encoding='utf-8')
        self.profile['layout_path'] = str(layout)
        self.controller.begin(self.profile)
        self.assertIs(self.controller.collector.settings['layout']['verified'], False)

    def test_settings_store_saves_paths_but_never_secret_values(self):
        path = self.root / 'settings.json'
        store = SettingsStore(path)
        store.save(dict(self.profile, secret=enrollment()['secret'], enrollment=enrollment()))
        saved = store.load()
        self.assertEqual(saved, self.profile)
        self.assertNotIn('b' * 64, path.read_text('utf-8'))

    def test_pairing_validation_is_bounded_and_checks_identity(self):
        values = enrollment()
        values['agent_id'] = 'not an id'
        self.pairing_path.write_text(json.dumps(values), encoding='utf-8')
        with self.assertRaises(ValueError):
            load_enrollment(self.pairing_path)

    def test_empty_pairing_is_reported_before_current_directory_is_opened(self):
        for path in ['', '   ', None]:
            with self.subTest(path=path),self.assertRaisesRegex(ValueError,'未选择中枢配对文件'):
                load_enrollment(path)
    def test_directory_missing_and_unreadable_pairing_have_actionable_errors(self):
        with self.assertRaisesRegex(ValueError,'文件夹'):load_enrollment(self.root)
        with self.assertRaisesRegex(ValueError,'不存在'):load_enrollment(self.root/'missing.json')
        with patch.object(Path,'open',side_effect=PermissionError('fixture denied')):
            with self.assertRaisesRegex(ValueError,'无法读取配对文件'):load_enrollment(self.pairing_path)
    def test_pasted_quoted_file_path_is_supported(self):
        self.assertEqual(load_enrollment(' "'+str(self.pairing_path)+'" '),enrollment())
    def test_non_object_invalid_json_and_large_files_are_pairing_errors(self):
        for value in ['[]','null','{"secret":','x'*8200]:
            self.pairing_path.write_text(value,encoding='utf-8')
            with self.subTest(value=value[:20]),self.assertRaisesRegex(ValueError,'配对文件'):
                load_enrollment(self.pairing_path)


if __name__ == '__main__':
    unittest.main()
