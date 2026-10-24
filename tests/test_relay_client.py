import json,tempfile,unittest
from pathlib import Path
from gnautilus_acquisition.gui import load_enrollment,SettingsStore
from gnautilus_acquisition.client import Replicator
from tests.test_gnautilus_transport import descriptor
from gnautilus_acquisition.storage import StreamJournal

class RelayClient(unittest.TestCase):
    def test_v2_enrollment_and_replicator_selection(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'pair.json';p.write_text(json.dumps(dict(schema='gnautilus.enrollment.v2',mode='relay',device_id='a'*32,agent_id='a'*32,hub_id='b'*32,workspace='c'*32,secret='ef'*32,auth_secret='ab'*32,url='wss://106.55.251.83:18443/eeg/v1/ws',ca_pem='test')))
            d=load_enrollment(p);self.assertEqual(d['host'],'106.55.251.83')
            journal=StreamJournal(Path(t)/'raw',descriptor());client=Replicator(journal,d)
            from gnautilus_acquisition.relay_client import RelayReplicator
            self.assertIsInstance(client,RelayReplicator);self.assertTrue(hasattr(client,'endpoint'));journal.close()
    def test_proxy_settings_are_persisted_without_other_secrets(self):
        with tempfile.TemporaryDirectory() as t:
            store=SettingsStore(Path(t)/'s.json');store.save(dict(proxy_mode='manual',proxy_url='http://127.0.0.1:7890',secret='no'))
            self.assertEqual(store.load()['proxy_mode'],'manual');self.assertNotIn('secret',store.load())

if __name__=='__main__':unittest.main()
