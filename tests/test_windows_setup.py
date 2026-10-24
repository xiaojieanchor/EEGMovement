"""Installer entry point tests: the user double-clicks without --install."""
import json,tempfile,sys,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from scripts import gnautilus_launcher as launcher

class SetupEntry(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.assets=self.root/'assets';self.assets.mkdir()
        (self.root/'versions/old').mkdir(parents=True)
        self.old=dict(version='1.1.0',directory='versions/old')
        (self.root/'current.json').write_text(json.dumps(self.old))
        self.manifest=dict(version='1.1.4',release_seq=11004)
    def tearDown(self):self.temp.cleanup()
    def test_double_click_setup_upgrades_existing_install(self):
        (self.assets/'bootstrap.zip').write_bytes(b'fixture')
        with patch.object(sys,'argv',['Setup.exe']),patch.object(launcher,'install_root',return_value=self.root),patch.object(launcher,'bundle',side_effect=lambda n:self.assets/n),patch.object(launcher,'apply') as apply,patch.object(launcher,'launch') as launch:
            launcher.main()
        apply.assert_called_once()
        launch.assert_not_called()
    def test_plain_launcher_keeps_launching_installed_version(self):
        with patch.object(sys,'argv',['Launcher.exe']),patch.object(launcher,'install_root',return_value=self.root),patch.object(launcher,'bundle',side_effect=lambda n:self.assets/n),patch.object(launcher,'launch') as launch:
            launcher.main()
        launch.assert_called_once_with(self.root,'versions/old')
    def test_health_requires_expected_version_not_only_pid(self):
        process=Mock(pid=1234);process.poll.return_value=None
        def launched(root,directory,report):
            report.write_text(json.dumps(dict(ready=True,pid=1234,version='1.1.0')))
            return process
        with patch.object(launcher,'launch',side_effect=launched),patch.object(launcher.time,'sleep'):
            self.assertFalse(launcher.health(self.root,self.root/'versions/old','1.1.4'))
        process.terminate.assert_called_once()

if __name__=='__main__':unittest.main()
