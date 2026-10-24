import tempfile,unittest,threading,ssl,secrets,time
from pathlib import Path
from eeg_relay.server import RelayServer
from eeg_relay.network import Connection
from eeg_relay.trust import create_identity
from eeg_relay.protocol import seal,encoded,unpack

class RelayService(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);create_identity(self.root/'tls','127.0.0.1')
        self.w='a'*32;self.hub='b'*32;self.agent='c'*32;self.quest='d'*32;self.e2e='ef'*32
        self.records={n:dict(workspace=self.w,role=role,auth_secret='ab'*32,hub_id=self.hub) for n,role in [(self.hub,'hub'),(self.agent,'eeg'),(self.quest,'quest')]}
        self.relay=RelayServer(self.records,self.root/'spool',b'k'*32,reserve=0)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(self.root/'tls/server.pem',self.root/'tls/server.key')
        self.server=self.relay.start('127.0.0.1',0,ssl_context=ctx);self.url=f'wss://127.0.0.1:{self.server.socket.getsockname()[1]}/eeg/v1/ws';self.connections=[]
    def tearDown(self):
        for c in self.connections:c.close()
        self.relay.close();self.tmp.cleanup()
    def connect(self,device,lane='control',boot=None):
        c=Connection(dict(url=self.url,workspace=self.w,device_id=device,auth_secret='ab'*32,ca_pem=(self.root/'tls/ca.pem').read_text(),proxy_mode='direct'),lane=lane,boot=boot)
        c.connect();self.connections.append(c);return c
    def test_cache_then_hub_receipt_and_no_cross_role(self):
        c=self.connect(self.agent,'data');h=seal(self.w,self.agent,self.hub,c.boot,1,'eeg_block',b'raw',self.e2e,stream='e'*32,epoch=0,block_seq=1)
        c.send(h,b'raw');receipt=c.receive(2);self.assertEqual(receipt[0]['notice'],'cloud_durable')
        self.assertEqual(self.relay.spool.snapshot()['blocks'],1)
        hub=self.connect(self.hub,'backfill');replay=hub.receive(2);self.assertEqual(replay[0]['delivery'],'backfill');self.assertEqual(replay[1],b'raw')
        control=self.connect(self.hub,boot=hub.boot)
        ack=encoded(dict(ack_seq=1,durable_seq=1));a=seal(self.w,self.hub,self.agent,hub.boot,1,'hub_ack',ack,self.e2e,stream='e'*32,epoch=0,block_seq=1)
        control.send(a,ack);time.sleep(.1);self.assertEqual(self.relay.spool.snapshot()['blocks'],0)
        vr=self.connect(self.quest);bad=seal(self.w,self.quest,self.hub,vr.boot,1,'hub_ack',ack,self.e2e)
        vr.send(bad,ack)
        with self.assertRaises(Exception):vr.receive(2)
    def test_authentication_and_single_active_hub(self):
        h=self.connect(self.hub)
        with self.assertRaises(Exception):self.connect(self.hub)
        config=dict(url=self.url,workspace=self.w,device_id=self.agent,auth_secret='00'*32,ca_pem=(self.root/'tls/ca.pem').read_text(),proxy_mode='direct')
        with self.assertRaises(Exception):Connection(config).connect()
    def test_live_lane_is_separate_from_replay(self):
        hub=self.connect(self.hub,'data');agent=self.connect(self.agent,'data')
        h=seal(self.w,self.agent,self.hub,agent.boot,1,'eeg_block',b'raw',self.e2e,stream='e'*32,epoch=0,block_seq=1)
        agent.send(h,b'raw');agent.receive(2);received=hub.receive(2)
        self.assertEqual(received[0]['delivery'],'live')
        self.assertEqual(received[1],b'raw')

    def test_release_notice_only_triggers_manifest_check(self):
        control=self.connect(self.agent)
        releases=self.root/'updates';releases.mkdir()
        self.relay.update_root=releases
        (releases/'windows-x64.json').write_text('{"fixture":"signed wrapper"}')
        notice,_=control.receive(3)
        self.assertEqual(notice['notice'],'update_available')
        self.assertEqual(notice['platform'],'windows-x64')
        self.assertNotIn('command',notice)

if __name__=='__main__':unittest.main()
