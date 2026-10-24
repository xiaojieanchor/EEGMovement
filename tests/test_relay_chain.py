import tempfile,unittest,ssl,time,threading,collections,json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from eeg_relay.server import RelayServer
from eeg_relay.trust import create_identity
from hub.public_relay import HubRelay
from hub.gnautilus_runtime import GnautilusRuntime
from hub.acquisition import GuidedAcquisition
from gnautilus_acquisition.client import Replicator,probe_hub
from gnautilus_acquisition.storage import StreamJournal
from gnautilus_acquisition.contract import block_frame
from tests.test_gnautilus_transport import descriptor

class PublicChain(unittest.TestCase):
    def test_cloud_hub_probe_upload_and_two_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);create_identity(root/'tls','127.0.0.1');w='a'*32;hid='b'*32;agent='c'*32
            records={i:dict(workspace=w,role=r,auth_secret='ab'*32,hub_id=hid) for i,r in [(hid,'hub'),(agent,'eeg')]}
            cloud=RelayServer(records,root/'spool',b'k'*32,reserve=0);ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(root/'tls/server.pem',root/'tls/server.key')
            server=cloud.start('127.0.0.1',0,ssl_context=ctx);url=f'wss://127.0.0.1:{server.socket.getsockname()[1]}/eeg/v1/ws'
            config=dict(url=url,workspace=w,device_id=hid,auth_secret='ab'*32,ca_pem=(root/'tls/ca.pem').read_text(),proxy_mode='direct',peers={agent:dict(role='eeg',secret='ef'*32)})
            hub=SimpleNamespace(lock=threading.RLock(),record_lock=threading.RLock(),new_device_mode=True,raw=collections.deque(),filtered=collections.deque(),notched=collections.deque(),source_mode='paused',last_packet=None,epoch=0,sample_index=0,recorder=None,recording_name=None,recording_samples=0,quality={},event=lambda *a:None,vr=SimpleNamespace(stop_test=lambda:None),intent_control=SimpleNamespace(configure=lambda d:None))
            runtime=GnautilusRuntime(hub,root);hub.gnautilus_runtime=runtime;hub.acquisition=GuidedAcquisition(hub)
            relay=HubRelay(hub,config).start();enrollment=dict(config,schema='gnautilus.enrollment.v2',mode='relay',device_id=agent,agent_id=agent,hub_id=hid,secret='ef'*32);enrollment.pop('peers')
            journal=StreamJournal(root/'original',descriptor());rep=None
            try:
                end=time.monotonic()+8
                while not relay.endpoint.snapshot()['connected'] and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(relay.endpoint.snapshot()['connected'],relay.state)
                self.assertTrue(probe_hub(enrollment)['connected'])
                rep=Replicator(journal,enrollment).start()
                end=time.monotonic()+8
                while not rep.ticket and time.monotonic()<end:time.sleep(.02)
                self.assertIsNotNone(rep.ticket,'Live tickets must arrive before timed fixture samples')
                for n in range(1,33):
                    h,b=block_frame(journal.descriptor,n,(n-1)*8,np.zeros((8,32),np.float32));journal.append(h,b);time.sleep(.03)
                end=time.monotonic()+8
                while rep.snapshot()['durable_seq']<32 and time.monotonic()<end:time.sleep(.04)
                self.assertEqual(rep.snapshot()['durable_seq'],32,rep.snapshot())
                self.assertGreater(rep.snapshot()['live_accepted'],0,rep.snapshot());self.assertGreater(hub.sample_index,0)
                self.assertEqual(journal.snapshot()['blocks'],32)
            finally:
                if rep:rep.stop()
                relay.close();runtime.close();journal.close();cloud.close()

if __name__=='__main__':unittest.main()
