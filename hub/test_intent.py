import unittest,time,threading,tempfile
from pathlib import Path
from types import SimpleNamespace
from hub.intent import effective_mask,IntentMonitor
from unittest.mock import patch
class IntentTests(unittest.TestCase):
    def test_ch3_only_explicit_exception(self):
        q=dict(training_mask=[True]*7,channels=[{} for _ in range(7)]);q['training_mask'][2]=False
        self.assertFalse(effective_mask(q)[2]);q['channels'][2]=dict(native_code=1,reasons=['50Hz干扰'])
        self.assertTrue(effective_mask(q)[2]);q['training_mask'][0]=False;self.assertFalse(effective_mask(q)[2])
    def test_late_and_stale_scores_hidden(self):
        m=IntentMonitor(SimpleNamespace(),Path('.'));m.inference_enabled=True;r=dict(eligible=True,probabilities=[.2]*5,reason='eligible')
        m.publish(r,time.monotonic()-1,'LIVE');self.assertIsNone(m.snapshot()['latest'])
        m.publish(r,time.monotonic(),'LIVE');self.assertTrue(m.snapshot()['fresh'])
        m.latest_at-=1;self.assertIsNone(m.snapshot()['latest'])
    def test_live_payload_preserves_samples_when_polling_jitters(self):
        from hub.intent import make_live_payload
        raw=[[float(i)]*7 for i in range(100,180)]
        q=dict(kind='channel_quality',policy='neuradock_native_green_v1',source_epoch=2,sample_index_start=100,sample_index_end=170,training_mask=[True]*7,channels=[])
        p=make_live_payload(raw,180,2,[q],(2,130),'live-test')
        self.assertEqual(p['sample_index_end'],170);self.assertEqual(len(p['raw']),40)
        self.assertEqual(p['raw'][0][0],130);self.assertEqual(p['raw'][-1][0],169)
        self.assertTrue(all(all(row) for row in p['quality']))
    def test_live_payload_unknown_quality_excluded(self):
        from hub.intent import make_live_payload
        q=dict(kind='channel_quality',policy='neuradock_native_green_v1',source_epoch=2,sample_index_start=145,sample_index_end=170,training_mask=[True]*7,channels=[])
        p=make_live_payload([[0.]*7]*80,180,2,[q],(2,130),'live-test')
        self.assertEqual(p['quality'][:15],[[False]*7]*15)
        self.assertIsNone(make_live_payload([[0.]*7]*80,180,3,[q],None,'live-test'))
    def test_live_scores_expire_when_source_stops(self):
        hub=SimpleNamespace(lock=threading.RLock(),epoch=3,source_mode='live',last_packet=time.monotonic()-1,remote=SimpleNamespace(status={'ssh_connected':True}))
        m=IntentMonitor(hub,Path('.'));m.inference_enabled=True;r=dict(eligible=True,probabilities=[.2]*5,reason='eligible',source_epoch=3)
        m.publish(r,time.monotonic(),'LIVE');self.assertFalse(m.snapshot()['fresh'])
        hub.last_packet=time.monotonic();self.assertTrue(m.snapshot()['fresh'])
    def test_readonly_health_reconnects_once_after_worker_restart(self):
        class Connection:
            def __init__(self,broken=False):self.broken=broken
            def request(self,*args,**kwargs):
                if self.broken:raise ConnectionError('worker restarted')
            def getresponse(self):return SimpleNamespace(status=200,read=lambda:b'{"weights_loaded":true}')
            def close(self):pass
        transport=SimpleNamespace(is_active=lambda:True)
        client=SimpleNamespace(get_transport=lambda:transport)
        hub=SimpleNamespace(remote=SimpleNamespace(client=client))
        m=IntentMonitor(hub,Path('.'));m.client=client;m.token='test';m.connection=Connection(True)
        with patch('hub.intent.ChannelHTTP',return_value=Connection()):self.assertTrue(m.request('GET','/health')['weights_loaded'])
if __name__=='__main__':unittest.main()
