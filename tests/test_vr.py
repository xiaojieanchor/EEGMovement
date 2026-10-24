import copy,json,time,unittest
from pathlib import Path
from hub.vr import encode_packet,decode_packet,VrLink

class WireTests(unittest.TestCase):
    def test_authenticated_roundtrip(self):
        key=bytes(range(32));p={'version':1,'kind':'heartbeat','value':'signed data'}
        self.assertEqual(decode_packet(encode_packet(p,key),key),p)
    def test_wrong_key_and_oversize_rejected(self):
        data=encode_packet({'version':1},bytes(32))
        with self.assertRaises(ValueError):decode_packet(data,bytes([1])*32)
        with self.assertRaises(ValueError):decode_packet(b'x'*4097,bytes(32))
    def test_no_nonfinite_payload(self):
        with self.assertRaises(ValueError):encode_packet({'version':1,'x':float('nan')},bytes(32))
    def test_unsupported_version_rejected(self):
        with self.assertRaises(ValueError):decode_packet(encode_packet({'version':2},bytes(32)),bytes(32))

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.link=VrLink(Path('.'),lambda:None,lambda *a:None)
        self.packet=dict(version=1,kind='heartbeat',receiver_id='a'*32,nonce='b'*32,session='',ticket=1,role='csharp_harness',allow_test=True,telemetry=dict(state='STOP',mode='LIVE',applied_seq=0))
        self.readiness=dict(epoch=2,source_valid=False,server_fresh=True,reason='quality_invalid',input_origin=time.monotonic()-.02)
        self.address=('127.0.0.1',12345)
    def command(self):
        welcome=self.link.heartbeat(self.packet,self.address,self.readiness)
        self.packet.update(ticket=2,session=welcome['session'])
        return self.link.heartbeat(self.packet,self.address,self.readiness)
    def test_live_always_requires_model_calibration(self):
        p=self.command();self.assertFalse(p['active']);self.assertFalse(p['input_valid']);self.assertFalse(p['model_ready']);self.assertEqual(p['mode'],'LIVE')
    def test_explicit_model_decision_reaches_unity_and_collection_blocks(self):
        self.packet['role']='unity';self.readiness.update(source_valid=True,model_fresh=True,decision_id=5,
            model_decision=dict(enabled=True,model_ready=True,active=True,direction=1,p_active=.95,confidence=.92,speed=.4,yaw_rate=35.,reason='experimental_model_active'))
        p=self.command();self.assertTrue(p['active']);self.assertEqual(p['mode'],'LIVE');self.assertTrue(p['model_ready']);self.assertEqual(p['decision_id'],5)
        self.readiness['acquisition_active']=True;self.packet['ticket']+=1
        p=self.link.heartbeat(self.packet,self.address,self.readiness);self.assertFalse(p['active']);self.assertFalse(p['model_ready'])
    def test_axes_require_capable_receiver_and_both_axes_are_transmitted(self):
        from hub.intent_control import IntentControl
        control=IntentControl(True,clock=lambda:100.)
        result=dict(source='LIVE',eligible=True,input_origin_monotonic=99.99,probabilities=[.04,.43,.05,.43,.05],source_epoch=1,sample_index_end=1000)
        control.update(result);control.clock=lambda:100.1;result['sample_index_end']=1025;decision=control.update(result)
        self.packet['role']='unity';self.readiness.update(model_fresh=True,source_valid=True,model_decision=decision,decision_id=decision['decision_id'])
        p=self.command();self.assertFalse(p['active']);self.assertEqual(p['reason'],'receiver_update_required')
        self.packet['telemetry']['motion_schema']=2;self.packet['ticket']+=1;p=self.link.heartbeat(self.packet,self.address,self.readiness)
        self.assertTrue(p['active']);self.assertEqual((p['linear_axis'],p['turn_axis']),(1,-1));self.assertEqual(p['motion_schema'],2)
    def test_test_is_explicit_separate_source(self):
        self.command();self.link.set_test({'action':'FORWARD','duration_s':2,'control_generation':self.link.control_generation})
        self.packet['ticket']=3;p=self.link.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(p['mode'],'TEST');self.assertTrue(p['active']);self.assertTrue(p['input_valid']);self.assertTrue(p['model_ready'])
    def test_stale_server_cannot_move_even_test(self):
        self.command();self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation});self.readiness['server_fresh']=False;self.packet['ticket']=3
        self.assertFalse(self.link.heartbeat(self.packet,self.address,self.readiness)['input_valid'])
    def test_stop_invalidates_delayed_test_post(self):
        self.command()
        generation=self.link.snapshot().get('control_generation',0)
        pending=dict(action='FORWARD',control_generation=generation)
        self.link.stop_test()
        with self.assertRaises(ValueError):self.link.set_test(pending)
        self.assertFalse(self.link.snapshot()['test_active'])
    def test_test_without_current_generation_is_refused(self):
        self.command()
        with self.assertRaises(ValueError):self.link.set_test({'action':'FORWARD'})
    def test_stop_generation_is_public_and_current_requests_work(self):
        self.command();before=self.link.snapshot().get('control_generation')
        self.assertIs(type(before),int)
        self.link.stop_test();after=self.link.snapshot()['control_generation']
        self.assertGreater(after,before)
        self.link.set_test({'action':'FORWARD','control_generation':after})
        self.assertTrue(self.link.snapshot()['test_active'])
    def test_server_stale_revokes_test_and_fresh_recovery_stays_live_stop(self):
        self.command();self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation})
        before=self.link.control_generation
        self.readiness['server_fresh']=False;self.packet['ticket']=3
        self.link.heartbeat(self.packet,self.address,self.readiness)
        self.readiness['server_fresh']=True;self.packet['ticket']=4
        command=self.link.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(command['mode'],'LIVE');self.assertFalse(command['active'])
        self.assertGreater(self.link.control_generation,before)
        generation=self.link.control_generation
        self.readiness['server_fresh']=False;self.packet['ticket']=5
        self.link.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(self.link.control_generation,generation)
    def test_source_stale_revokes_test_without_treating_invalid_qc_as_stale_transport(self):
        self.command();self.readiness['source_fresh']=True
        self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation})
        self.packet['ticket']=3
        self.assertEqual(self.link.heartbeat(self.packet,self.address,self.readiness)['mode'],'TEST')
        self.readiness['source_fresh']=False;self.packet['ticket']=4
        self.link.heartbeat(self.packet,self.address,self.readiness)
        self.readiness['source_fresh']=True;self.packet['ticket']=5
        self.assertEqual(self.link.heartbeat(self.packet,self.address,self.readiness)['mode'],'LIVE')
    def test_receiver_lease_gap_revokes_test_before_reconnect(self):
        self.command();self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation})
        self.link.clients[self.packet['receiver_id']]['last_seen']=time.monotonic()-.2
        self.packet['ticket']=3
        command=self.link.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(command['mode'],'LIVE');self.assertFalse(command['active'])
    def test_source_switch_revokes_test_even_without_manual_stop(self):
        from hub.app import Hub,ROOT
        h=Hub(json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8')))
        h.vr=self.link;self.command()
        self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation})
        before=self.link.control_generation;h.set_source('paused')
        self.assertFalse(self.link.snapshot()['test_active']);self.assertGreater(self.link.control_generation,before)
    def test_receiver_must_explicitly_allow_test(self):
        self.packet['allow_test']=False;self.command()
        with self.assertRaises(ValueError):self.link.set_test({'action':'FORWARD'})
    def test_duplicate_heartbeat_cannot_extend_connection(self):
        self.command();before=self.link.clients[self.packet['receiver_id']]['last_seen']
        self.assertIsNone(self.link.heartbeat(self.packet,self.address,self.readiness));self.assertEqual(before,self.link.clients[self.packet['receiver_id']]['last_seen'])
    def test_foreign_endpoint_cannot_take_receiver(self):
        self.command();self.packet['ticket']=3
        self.assertIsNone(self.link.heartbeat(self.packet,('127.0.0.1',12346),self.readiness))
    def test_expired_test_falls_back_live_stop(self):
        self.command();self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation});self.link.test['until']=time.monotonic()-1;self.packet['ticket']=3
        p=self.link.heartbeat(self.packet,self.address,self.readiness);self.assertEqual(p['mode'],'LIVE');self.assertFalse(p['active'])
    def test_ack_must_follow_sent_command(self):
        self.command();self.packet['ticket']=3;self.packet['telemetry']['applied_seq']=999
        with self.assertRaises(ValueError):self.link.heartbeat(self.packet,self.address,self.readiness)
    def test_hub_restart_welcomes_old_session_without_importing_ack_or_motion(self):
        command=self.command()
        self.packet['ticket']=3
        self.packet['telemetry'].update(applied_seq=command['seq'],state='MOVING',actual_speed=.4)
        restarted=VrLink(Path('.'),lambda:None,lambda *args:None)
        self.assertNotEqual(self.packet['session'],restarted.session)
        welcome=restarted.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(welcome['kind'],'welcome');self.assertEqual(welcome['session'],restarted.session)
        peer=restarted.clients[self.packet['receiver_id']]
        self.assertEqual((peer['seq'],peer['applied_seq']),(0,0));self.assertEqual(peer['telemetry'],{})
        self.assertEqual(peer['sent'],{});self.assertIsNone(peer['command']);self.assertIsNone(peer['ack_ms'])
        self.assertEqual(len(restarted.ack_times),0);self.assertEqual(len(restarted.fullchain_times),0)
        self.assertEqual(restarted.stats['commands_sent'],0)
        self.assertIsNone(restarted.heartbeat(self.packet,self.address,self.readiness))
        # The receiver resets its applied sequence only after the new welcome.
        self.packet.update(session=welcome['session'],ticket=4)
        self.packet['telemetry'].update(applied_seq=0,state='STOP',actual_speed=0.)
        next_command=restarted.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(next_command['seq'],1);self.assertEqual(next_command['ttl_ms'],180)
        self.assertFalse(next_command['active'])
        self.packet['ticket']=5;self.packet['telemetry']['applied_seq']=1
        restarted.heartbeat(self.packet,self.address,self.readiness)
        self.packet['ticket']=6;self.packet['telemetry']['applied_seq']=0
        with self.assertRaisesRegex(ValueError,'invalid applied acknowledgement'):
            restarted.heartbeat(self.packet,self.address,self.readiness)
    def test_wrong_session_never_consumes_sent_ack_or_retains_moving_telemetry(self):
        command=self.command();peer=self.link.clients[self.packet['receiver_id']]
        self.link.set_test({'action':'FORWARD','control_generation':self.link.control_generation})
        peer['telemetry']=dict(state='MOVING',actual_speed=.4)
        sent=copy.deepcopy(peer['sent']);generation=self.link.control_generation
        self.packet.update(ticket=3,session='f'*32)
        self.packet['telemetry'].update(applied_seq=command['seq'],state='MOVING',actual_speed=.4)
        welcome=self.link.heartbeat(self.packet,self.address,self.readiness)
        self.assertEqual(welcome['kind'],'welcome')
        self.assertEqual((peer['seq'],peer['applied_seq']),(1,0));self.assertEqual(peer['sent'],sent)
        self.assertEqual(peer['telemetry'],{});self.assertIsNone(peer['command'])
        self.assertEqual(len(self.link.ack_times),0);self.assertEqual(len(self.link.fullchain_times),0)
        self.assertFalse(self.link.snapshot()['test_active']);self.assertGreater(self.link.control_generation,generation)
    def test_nonfinite_pose_rejected(self):
        self.packet['telemetry']['x']=float('nan')
        with self.assertRaises(ValueError):self.link.heartbeat(self.packet,self.address,self.readiness)
    def test_optional_render_health_is_preserved(self):
        health=dict(app_version='0.3',build_id='digest',ui_state='experience',frame_ms=16.0,
                    frame_p95_ms=20,rendered_frames=40,focused=True,paused=False,xr_active=False)
        self.packet['telemetry'].update(health);self.command()
        telemetry=self.link.snapshot()['clients'][0]['telemetry']
        for key,value in health.items():self.assertEqual(telemetry.get(key),value)
    def test_invalid_render_health_rejected(self):
        for key,invalid in [('frame_ms',-1),('frame_ms',10001),('frame_p95_ms',float('inf')),
                            ('rendered_frames',True),('rendered_frames',-1),('rendered_frames',1.5),
                            ('focused',1),('paused','false'),('xr_active',None),('build_id','x'*121)]:
            with self.subTest(key=key,value=invalid):
                state=dict(state='STOP',mode='LIVE',**{key:invalid})
                with self.assertRaises(ValueError):VrLink.valid_telemetry(state)
    def test_execution_uses_fresh_unity_actual_motion_then_expires(self):
        self.packet['role']='unity';self.packet['telemetry'].update(actual_speed=.4,actual_yaw_rate=3,state='MOVING',mode='TEST')
        self.command();snapshot=self.link.snapshot()
        self.assertEqual(snapshot.get('execution',{}).get('actual_speed'),.4)
        self.assertEqual(snapshot.get('execution',{}).get('source'),'unity')
        self.link.clients[self.packet['receiver_id']]['last_seen']=time.monotonic()-1
        snapshot=self.link.snapshot()
        self.assertIsNone(snapshot.get('execution',{}).get('actual_speed'))
        self.assertEqual(snapshot.get('execution',{}).get('status'),'offline')
    def test_foreign_noise_does_not_consume_paired_heartbeat_budget(self):
        self.command();now=time.monotonic()
        for _ in range(500):self.link.rate_allows(('127.0.0.1',55555),now)
        self.assertTrue(all(self.link.rate_allows(self.address,now) for _ in range(20)))
        self.assertFalse(self.link.rate_allows(('127.0.0.1',55555),now))
    def test_old_source_is_not_live_eligible(self):
        from hub.app import Hub,ROOT
        config=json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8'));h=Hub(config);h.accept([[20.0]*7]*5,1,'x');h.quality['valid']=True;h.source_mode='replay'
        self.assertFalse(h.execution_readiness()['source_valid'])
    def test_execution_source_gate_is_stricter_than_display(self):
        from hub.app import Hub,ROOT
        h=Hub(json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8')));h.preparation.snapshot({});h.accept([[20.0]*7]*5,1,'x');h.quality['valid']=True;h.last_packet=time.monotonic()-.12
        self.assertTrue(h.snapshot()['source']['fresh']);self.assertFalse(h.execution_readiness()['source_valid'])
    def test_fresh_response_with_old_input_is_not_execution_fresh(self):
        from hub.app import Hub,ROOT
        h=Hub(json.loads((ROOT/'hub/config.json').read_text(encoding='utf-8')));h.accept([[20.0]*7]*5,1,'x');h.last_result_time=time.monotonic();h.last_result={'input_ready_to_return_ms':400,'request_id':1};h.remote.status.update(ssh_connected=True,worker_connected=True)
        self.assertFalse(h.execution_readiness()['server_fresh'])

if __name__=='__main__':unittest.main()
