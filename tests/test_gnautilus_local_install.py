import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from scripts.gnautilus_launcher import health
from scripts.install_local_acquisition import install


class LocalInstallTests(unittest.TestCase):
    def test_local_build_keeps_signed_release_state_and_previous_version(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'build';source.mkdir();(source/'_internal').mkdir()
            (source/'EEGMovement-gNautilus.exe').write_bytes(b'test-only')
            (source/'_internal/library').write_bytes(b'fixture')
            root=base/'installed';(root/'updates').mkdir(parents=True)
            (root/'current.json').write_text(json.dumps(dict(version='old',directory='old')))
            state=root/'updates/state.json';state.write_text('{"installed_release_seq":11005}')
            home=base/'home';(home/'Desktop').mkdir(parents=True)
            with patch('scripts.install_local_acquisition.close_installed_app'),patch('scripts.install_local_acquisition.health',return_value=True) as check,patch('pathlib.Path.home',return_value=home):
                result=install(source,root,start_receiving=True,status_path=base/'status.json')
            current=json.loads((root/'current.json').read_text())
            self.assertEqual(current['previous']['version'],'old')
            self.assertEqual(state.read_text(),'{"installed_release_seq":11005}')
            self.assertEqual(result['files_verified'],2)
            self.assertFalse(result['signed_update_state_modified'])
            self.assertTrue(check.call_args.kwargs['start_receiving'])
            self.assertTrue((home/'Desktop/EEGMovement采集工作台.cmd').exists())

    def test_failed_startup_restores_previous_active_version(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);source=base/'build';source.mkdir();(source/'_internal').mkdir()
            (source/'EEGMovement-gNautilus.exe').write_bytes(b'test-only')
            root=base/'installed';root.mkdir();old=dict(version='old',directory='old')
            (root/'current.json').write_text(json.dumps(old))
            with patch('scripts.install_local_acquisition.close_installed_app'),patch('scripts.install_local_acquisition.health',return_value=False):
                with self.assertRaises(RuntimeError):install(source,root)
            self.assertEqual(json.loads((root/'current.json').read_text()),old)

    def test_singleton_exit_code_is_preserved_for_health_failure_diagnosis(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);candidate=root/'candidate';candidate.mkdir()
            process=Mock(pid=123);process.poll.return_value=2
            with patch('scripts.gnautilus_launcher.launch',return_value=process):
                self.assertFalse(health(root,candidate,'1.1.8'))
            failure=json.loads((root/'updates/last-health-failure.json').read_text())
            self.assertEqual(failure['reason'],'another_workbench_running')
            self.assertEqual(failure['exit_code'],2)
            process.terminate.assert_not_called()


if __name__=='__main__':unittest.main()
