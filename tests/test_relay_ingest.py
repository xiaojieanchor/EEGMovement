import tempfile,unittest,time
from pathlib import Path
import numpy as np
from tests.test_gnautilus_transport import descriptor
from gnautilus_acquisition.contract import block_frame
from hub.gnautilus import IngestService

class RelayIngest(unittest.TestCase):
    def test_bad_vr_wire_isolated_from_other_relay_lanes(self):
        from hub.public_relay import HubRelay
        from types import SimpleNamespace
        import collections
        def invalid(*args):raise ValueError('invalid applied acknowledgement')
        vr=SimpleNamespace(process_datagram=invalid,stats=collections.Counter())
        relay=HubRelay(SimpleNamespace(vr=vr),dict(peers={'a'*32:dict(role='quest',secret='ef'*32)}))
        relay.accept_message('control',{'from':'a'*32,'kind':'vr_wire'},b'fixture')
        self.assertEqual(vr.stats['invalid_wire'],1)
        self.assertIn('invalid applied acknowledgement',relay.state['vr_last_rejection'])

    def test_unknown_headset_operation_is_rejected_without_breaking_connection(self):
        from hub.public_relay import HubRelay
        from eeg_relay.protocol import encoded
        from types import SimpleNamespace
        relay=HubRelay(SimpleNamespace(),dict(peers={'a'*32:dict(role='quest',secret='ef'*32)}));sent=[]
        relay.send=lambda *args,**kwargs:sent.append(args)
        relay.rpc('a'*32,{},encoded(dict(request_id='b'*32,path='/v1/unavailable',method='GET',body='')))
        self.assertEqual(sent[0][1],'rpc_reply');self.assertEqual(sent[0][2]['status'],409)

    def test_malformed_inner_blocks_are_rejected_without_reconnect(self):
        from hub.public_relay import HubRelay
        from eeg_relay.protocol import pack
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as t:
            relay=HubRelay(SimpleNamespace(root=Path(t)),dict(peers={'a'*32:dict(role='eeg',secret='ef'*32)}));sent=[]
            relay.send=lambda *args,**kwargs:sent.append((args,kwargs))
            d=descriptor();b,raw=block_frame(d,1,0,np.zeros((8,32),np.float32));b.pop('read_monotonic_ns');b['read_completed_monotonic_ns']=5
            envelope=dict(kind='eeg_block',workspace='f'*32,**{'from':'a'*32},stream=d['stream_id'],epoch=0,block_seq=1)
            for value,payload in [({},b''),({'descriptor':d},b''),({'descriptor':None,'block':{}},b''),({'descriptor':d,'block':[]},b''),({'descriptor':d,'block':b},raw)]:
                relay.message('backfill',envelope,pack(value,payload))
            self.assertEqual(len(sent),5);self.assertTrue(all(item[0][1]=='hub_reject' for item in sent))

    def test_external_journal_historical_does_not_refresh_live(self):
        with tempfile.TemporaryDirectory() as t:
            seen=[];described=[];service=IngestService(Path(t),lambda *args:seen.append(args),on_descriptor=described.append)
            d=descriptor();h,b=block_frame(d,1,0,np.zeros((8,32),np.float32))
            r=service.ingest_external('a'*32,d,h,b,None);self.assertEqual(r['durable_seq'],1);self.assertEqual(seen,[]);self.assertEqual(described,[])
            h,b=block_frame(d,2,8,np.ones((8,32),np.float32))
            r=service.ingest_external('a'*32,d,h,b,time.monotonic());self.assertTrue(r['live_accepted']);self.assertEqual(len(seen),1)
            self.assertEqual(len(described),1)
            r=service.ingest_external('a'*32,d,h,b,time.monotonic());self.assertFalse(r['live_accepted'])
            service.close()

if __name__=='__main__':unittest.main()
