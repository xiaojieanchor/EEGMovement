import json,secrets,tempfile,time,unittest,urllib.request,urllib.error,hmac
from pathlib import Path
from types import SimpleNamespace
from hub.quest import QuestService,execution_key,private_ipv4,signature

class QuestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.keys={};vr=SimpleNamespace(register_lan=lambda address,key:self.keys.update({address:key}),port=19771,session='a'*32)
        self.service=QuestService(SimpleNamespace(vr=vr),Path(self.temp.name))
        self.device='b'*32;self.secret=secrets.token_bytes(32);self.address='172.29.15.220';self.boot='c'*32
        self.service.register(dict(device_id=self.device,address=self.address,secret=self.secret.hex()))
    def headers(self,seq=1,path='/v1/pairing',method='GET',body=b''):
        return {'X-EEG-Device':self.device,'X-EEG-Boot':self.boot,'X-EEG-Seq':str(seq),'X-EEG-MAC':signature(self.secret,method,path,self.device,self.boot,seq,body)}
    def test_private_literal_only(self):
        for x in ['172.29.15.220','10.1.2.3','192.168.0.2']:self.assertTrue(private_ipv4(x))
        for x in ['8.8.8.8','example.com','198.18.0.1','::1']:self.assertFalse(private_ipv4(x))
    def test_key_derivation_no_secret_in_pairing_response(self):
        self.assertEqual(self.keys[self.device],execution_key(self.secret))
        response=self.service.dispatch(self.device,'GET','/v1/pairing',b'')
        self.assertNotIn('key',response);self.assertNotIn(self.secret.hex(),json.dumps(response))
    def test_request_auth_and_response_domain_separation(self):
        self.assertEqual(self.service.authenticate('GET','/v1/pairing',self.headers(),b'',self.address)[0],self.device)
        self.assertNotEqual(signature(self.secret,'GET','/v1/pairing',self.device,self.boot,1,b''),signature(self.secret,'200','response',self.device,self.boot,1,b''))
    def test_wrong_signature_body_and_unknown_identity_rejected(self):
        for address,headers,body in [(self.address,{**self.headers(),'X-EEG-Device':'e'*32},b''),(self.address,{**self.headers(),'X-EEG-MAC':'0'*64},b''),(self.address,self.headers(),b'x')]:
            with self.assertRaises(ValueError):self.service.authenticate('GET','/v1/pairing',headers,body,address)
    def test_replay_and_old_window_rejected_out_of_order_once(self):
        for seq in [2,1,100]:self.service.authenticate('GET','/v1/pairing',self.headers(seq),b'',self.address)
        for seq in [100,1,3]:
            with self.assertRaises(ValueError):self.service.authenticate('GET','/v1/pairing',self.headers(seq),b'',self.address)
    def test_mirror_latest_only_and_expiry(self):
        jpeg=b'\xff\xd8'+b'a'*30+b'\xff\xd9'
        self.service.dispatch(self.device,'POST','/v1/mirror',jpeg)
        self.assertTrue(self.service.snapshot()['mirror_fresh']);self.assertEqual(self.service.frame()['jpeg'],jpeg)
        self.service.frames[self.device]['time']-=3
        self.assertFalse(self.service.snapshot()['mirror_fresh']);self.assertIsNone(self.service.frame())
    def test_invalid_jpeg_and_oversized_rejected(self):
        for body in [b'x'*100,b'\xff\xd8'+b'a'*262144+b'\xff\xd9']:
            with self.assertRaises(ValueError):self.service.dispatch(self.device,'POST','/v1/mirror',body)
    def test_no_admin_endpoint_exposed(self):
        for path in ['/api/config','/api/server/connect','/api/shutdown','/v1/vr/test']:
            with self.assertRaises(LookupError):self.service.dispatch(self.device,'POST',path,b'{}')
    def test_headset_status_is_small_read_only_and_has_no_private_state(self):
        self.service.hub.snapshot=lambda:dict(source=dict(fresh=True,sfreq_measured=250),
            server=dict(worker_connected=True,health_age_ms=20,last_health=dict(weights_loaded=False,device='cuda:0',secret='PRIVATE')),
            result=dict(mode='diagnostics',model_state='No intent weights'),waveforms=['PRIVATE_RAW'],config=dict(password='PRIVATE'))
        value=self.service.dispatch(self.device,'GET','/v1/status',b'')
        self.assertEqual(value['model']['status'],'not_loaded');self.assertFalse(value['training']['formal_ready'])
        self.assertNotIn('PRIVATE',json.dumps(value));self.assertFalse(value['execution']['known'])
        with self.assertRaises(LookupError):self.service.dispatch(self.device,'POST','/v1/status',b'{}')
    def test_signed_panel_uses_live_model_and_channel_quality_without_raw_data(self):
        self.service.hub.snapshot=lambda:dict(source=dict(fresh=True),quality=dict(channels=[dict(name='Ch1',color='green',quality_label='通过',training_eligible=True,raw='PRIVATE')]),server={})
        self.service.hub.intent=SimpleNamespace(snapshot=lambda:dict(inference_enabled=True,inference_state='warming',health=dict(weights_loaded=True,model_id='new-fit',parameters=70087769,training_fit={'accuracy':.96},development={'accuracy':.15})))
        value=self.service.dispatch(self.device,'GET','/v1/status',b'')
        self.assertTrue(value['model']['inference_enabled']);self.assertEqual(value['model']['inference_state'],'warming');self.assertEqual(value['model']['parameters'],70087769)
        self.assertEqual(value['source']['channels'][0]['name'],'Ch1');self.assertNotIn('PRIVATE',json.dumps(value))
    def test_stale_worker_reports_unknown_weights_not_a_fresh_model(self):
        self.service.hub.snapshot=lambda:dict(server=dict(worker_connected=True,health_age_ms=6000,last_health=dict(weights_loaded=True)))
        value=self.service.dispatch(self.device,'GET','/v1/status',b'')
        self.assertEqual(value['model']['status'],'unknown');self.assertFalse(value['server']['connected'])
    def test_broken_pair_remains_excluded_in_headset_status_without_renumbering(self):
        from training.channel_policy import exclude_quality
        quality=exclude_quality(dict(training_mask=[True]*7,channels=[dict(name='Ch'+str(i+1),color='green',quality_label='通过',training_eligible=True) for i in range(7)]),(0,5))
        self.service.hub.snapshot=lambda:dict(source=dict(fresh=True),quality=quality,server={})
        source=self.service.dispatch(self.device,'GET','/v1/status',b'')['source']
        self.assertEqual(source['good_channels'],5)
        self.assertEqual([c['name'] for c in source['channels'] if c['training_eligible']],['Ch2','Ch3','Ch4','Ch5','Ch7'])
        for i in (0,5):self.assertIn('永久停用',source['channels'][i]['quality_label'])
    def test_actual_http_response_signature_and_replay(self):
        self.service.devices[self.device]['address']='127.0.0.1'
        self.service.port=0;self.service.start();self.addCleanup(self.service.close)
        port=self.service.server.server_address[1]
        request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/v1/pairing',headers=self.headers())
        with urllib.request.urlopen(request,timeout=2) as response:
            body=response.read();self.assertTrue(hmac.compare_digest(response.headers['X-EEG-MAC'],signature(self.secret,'200','response',self.device,self.boot,1,body)))
        with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(request,timeout=2)
        self.assertEqual(e.exception.code,403)
    def test_device_address_change_preserves_identity_but_key_rotation_revokes_it(self):
        revoked=[];self.service.hub.vr.unregister_lan=lambda identity:revoked.append(identity)
        self.service.register(dict(device_id=self.device,address='172.29.15.221',secret=self.secret.hex()))
        self.assertEqual(revoked,[])
        self.service.register(dict(device_id=self.device,address='172.29.15.221',secret=secrets.token_hex(32)))
        self.assertEqual(revoked,[self.device])

if __name__=='__main__':unittest.main()
