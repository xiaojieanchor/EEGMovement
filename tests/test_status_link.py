import json,ssl,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from gnautilus_acquisition.status_link import StatusLink
from gnautilus_acquisition.telemetry import build,sanitize
from eeg_relay.server import RelayServer
from eeg_relay.trust import create_identity
from hub.public_relay import HubRelay


class StatusConnection(unittest.TestCase):
    def test_details_do_not_export_paths_credentials_or_invent_a_rate(self):
        row=build(dict(serial='selected',sdk_dir='PRIVATE_PATH',secret='PRIVATE_KEY'),dict(state='idle'))
        self.assertIsNone(row['sampling_rate']);self.assertFalse(row['contact_quality_known'])
        self.assertNotIn('PRIVATE',json.dumps(row))
        self.assertNotIn('secret',sanitize(dict(secret='x',configuration=dict(secret='x'))))

    def test_control_connection_outlives_probe_and_releases_role_on_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);create_identity(root/'tls','127.0.0.1')
            workspace='a'*32;hub_id='b'*32;device='c'*32
            cloud=RelayServer({hub_id:dict(workspace=workspace,role='hub',auth_secret='ab'*32),device:dict(workspace=workspace,role='eeg',auth_secret='ab'*32,hub_id=hub_id)},root/'spool',b'k'*32,reserve=0)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'tls/server.pem',root/'tls/server.key')
            server=cloud.start('127.0.0.1',0,ssl_context=context)
            common=dict(url=f'wss://127.0.0.1:{server.socket.getsockname()[1]}/eeg/v1/ws',workspace=workspace,auth_secret='ab'*32,ca_pem=(root/'tls/ca.pem').read_text(),proxy_mode='direct')
            service=SimpleNamespace(lock=threading.RLock(),status={})
            runtime=SimpleNamespace(feedback=lambda:dict(recording_active=False,motion_state='STOP',vr_online=1,source_fresh=False),source_lost=lambda *a:None,service=service)
            hub=SimpleNamespace(intent_control=SimpleNamespace(configure=lambda *a:None),gnautilus_runtime=runtime,event=lambda *a:None)
            relay=HubRelay(hub,dict(common,device_id=hub_id,peers={device:dict(role='eeg',secret='ef'*32)})).start()
            config=dict(common,device_id=device,hub_id=hub_id,secret='ef'*32)
            link=StatusLink(config);next_link=None
            try:
                self.assertTrue(link.probe()['connected'])
                link.update_health(dict(sdk_state='not_started',disk_state='not_recording',client_status=build({},dict(state='idle'))))
                end=time.monotonic()+6
                while time.monotonic()<end:
                    self.assertTrue(link.snapshot()['hub_state_fresh']);time.sleep(.25)
                report=relay.snapshot()['acquisition_clients'][0]
                self.assertTrue(report['status_fresh']);self.assertEqual(report['reported']['state'],'idle')
                self.assertIsNone(report['reported']['sampling_rate']);self.assertFalse(runtime.feedback()['source_fresh'])
                link.stop();next_link=StatusLink(config);self.assertTrue(next_link.probe()['connected'])
                relay.close();time.sleep(1.1)
                self.assertFalse(next_link.snapshot()['hub_state_fresh']);self.assertEqual(next_link.snapshot()['hub'],{})
            finally:
                link.stop()
                if next_link:next_link.stop()
                relay.close();cloud.close()

if __name__=='__main__':unittest.main()
