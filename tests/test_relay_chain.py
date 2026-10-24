import tempfile,unittest,ssl,time,threading,collections,json,hashlib
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
from gnautilus_acquisition.contract import block_frame,fingerprint
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
            hub.start_recording=runtime.start_recording;hub.stop_recording=runtime.stop_recording
            relay=HubRelay(hub,config).start();enrollment=dict(config,schema='gnautilus.enrollment.v2',mode='relay',device_id=agent,agent_id=agent,hub_id=hid,secret='ef'*32);enrollment.pop('peers')
            # Isolated test only: synthetic arrays under an injected live contract.
            # This never uses a vendor device or a production Hub.
            stream=descriptor('gtec_live',fs=250)
            stream['channels'] += [dict(id='LINK',name='LinkQualityInformation',kind='LINK',unit='%',physical_index=None),dict(id='DIGITAL',name='DigitalIOs',kind='DIGITAL',unit='bits',physical_index=None)]
            stream['configuration'].update(LinkQualityInformation=True,DigitalIOs=True)
            stream['configuration_sha256']=fingerprint(stream['configuration'])
            journal=StreamJournal(root/'original',stream);rep=None
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
                    h,b=block_frame(journal.descriptor,n,(n-1)*8,np.zeros((8,34),np.float32));journal.append(h,b);time.sleep(.03)
                end=time.monotonic()+8
                while rep.snapshot()['durable_seq']<32 and time.monotonic()<end:time.sleep(.04)
                self.assertEqual(rep.snapshot()['durable_seq'],32,rep.snapshot())
                self.assertGreater(rep.snapshot()['live_accepted'],0,rep.snapshot());self.assertGreater(hub.sample_index,0)
                self.assertEqual(journal.snapshot()['blocks'],32)
                # Carry tags and durable session storage through the same WSS
                # path, and verify the status/logs returned to the acquisition UI.
                session=hub.acquisition.start(dict(build_id='SOFTWARE-WSS-TEST',paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2',input_surface='desktop'))
                event=dict(session_id=session['session_id'],client_token=session['client_token'],seq=1,label='LEFT',phase='hold_start',sender_monotonic_ms=time.monotonic()*1000,sender_frame=1,clock_offset_ms=0,sync_rtt_ms=1,xr_active=False,pointer_source='desktop_pointer',page_active=True)
                hub.acquisition.event(event);record_path=hub.recorder.journal.path
                for n in range(33,41):
                    h,b=block_frame(journal.descriptor,n,(n-1)*8,np.full((8,34),n,np.float32));journal.append(h,b);time.sleep(.03)
                end=time.monotonic()+5
                while time.monotonic()<end:
                    feedback=rep.snapshot().get('hub',{})
                    if feedback.get('label')=='LEFT' and feedback.get('recording_samples',0)>0 and rep.snapshot()['durable_seq']>=40:break
                    time.sleep(.02)
                self.assertEqual(feedback.get('label'),'LEFT',rep.snapshot())
                self.assertGreater(feedback.get('recording_samples',0),0)
                self.assertGreater(feedback.get('events_saved',0),0)
                hub.acquisition.event(dict(event,seq=2,phase='hold_end'));self.assertIsNone(hub.acquisition.snapshot()['label'])
                hub.acquisition.stop(reason='software_wss_test_complete')
                raw=np.fromfile(record_path/'raw.f32',dtype='<f4').reshape(-1,34)
                closed=json.loads((record_path/'closed.json').read_text('utf-8'))
                self.assertEqual(closed['samples'],len(raw));self.assertGreater(len(raw),0)
                np.testing.assert_array_equal(raw[:,0],raw[:,-1])
                self.assertTrue(np.isin(raw[:,0],np.arange(33,41)).all())
                self.assertEqual(hashlib.sha256((record_path/'raw.f32').read_bytes()).hexdigest(),closed['files']['raw.f32']['sha256'])
                self.assertEqual(hashlib.sha256((record_path/'events.jsonl').read_bytes()).hexdigest(),closed['files']['events.jsonl']['sha256'])
                events=[json.loads(row) for row in (record_path/'events.jsonl').read_text('utf-8').splitlines()]
                self.assertTrue(any(row.get('code')=='manual_hold_start' and row.get('label')=='LEFT' for row in events))
                end=time.monotonic()+3
                while time.monotonic()<end:
                    feedback=rep.snapshot().get('hub',{})
                    if any('记录已关闭' in row.get('message','') for row in feedback.get('logs',[])):break
                    time.sleep(.02)
                self.assertFalse(feedback.get('recording_active'))
                self.assertTrue(any('记录已关闭' in row.get('message','') for row in feedback.get('logs',[])))
            finally:
                if rep:rep.stop()
                relay.close();runtime.close();journal.close();cloud.close()

    def test_client_active_hold_capture_syncs_to_hub_and_passes_ml_record_validation(self):
        from gnautilus_acquisition.client import Collector
        from gnautilus_ml.data import read_native,check_record
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);create_identity(root/'tls','127.0.0.1');w='a'*32;hid='b'*32;agent='c'*32
            records={i:dict(workspace=w,role=r,auth_secret='ab'*32,hub_id=hid) for i,r in [(hid,'hub'),(agent,'eeg')]}
            cloud=RelayServer(records,root/'spool',b'k'*32,reserve=0);ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(root/'tls/server.pem',root/'tls/server.key')
            server=cloud.start('127.0.0.1',0,ssl_context=ctx);url=f'wss://127.0.0.1:{server.socket.getsockname()[1]}/eeg/v1/ws'
            config=dict(url=url,workspace=w,device_id=hid,auth_secret='ab'*32,ca_pem=(root/'tls/ca.pem').read_text(),proxy_mode='direct',peers={agent:dict(role='eeg',secret='ef'*32)})
            hub=SimpleNamespace(lock=threading.RLock(),record_lock=threading.RLock(),new_device_mode=True,raw=collections.deque(),filtered=collections.deque(),notched=collections.deque(),source_mode='paused',last_packet=None,epoch=0,sample_index=0,recorder=None,recording_name=None,recording_samples=0,quality={},event=lambda *a:None,vr=SimpleNamespace(stop_test=lambda:None),intent_control=SimpleNamespace(configure=lambda d:None))
            runtime=GnautilusRuntime(hub,root/'hub_store');hub.gnautilus_runtime=runtime;hub.acquisition=GuidedAcquisition(hub)
            hub.start_recording=runtime.start_recording;hub.stop_recording=runtime.stop_recording
            relay=HubRelay(hub,config).start();enrollment=dict(config,schema='gnautilus.enrollment.v2',mode='relay',device_id=agent,agent_id=agent,hub_id=hid,secret='ef'*32);enrollment.pop('peers')
            stream=descriptor('gtec_live',fs=250)
            stream['layout_verified']=True;stream['reference']='Fpz'
            stream['channels'] += [dict(id='LINK',name='LinkQualityInformation',kind='LINK',unit='%',physical_index=None),dict(id='DIGITAL',name='DigitalIOs',kind='DIGITAL',unit='bits',physical_index=None)]
            stream['configuration'].update(LinkQualityInformation=True,DigitalIOs=True)
            stream['configuration_sha256']=fingerprint(stream['configuration'])
            collector=Collector(dict(),enrollment,root/'client_store')
            try:
                end=time.monotonic()+8
                while not relay.endpoint.snapshot()['connected'] and time.monotonic()<end:time.sleep(.02)
                collector._message(dict(kind='descriptor',descriptor=stream))
                end=time.monotonic()+8
                while not collector.network.ticket and time.monotonic()<end:time.sleep(.02)
                self.assertIsNotNone(collector.network.ticket)
                def push_blocks(count):
                    for _ in range(count):
                        now_ns=time.monotonic_ns();utc_ns=time.time_ns()
                        arr=np.ones((8,34),np.float32)
                        collector._message(dict(kind='block',samples=arr,read_monotonic_ns=now_ns,utc_ns=utc_ns,read_completed_monotonic_ns=now_ns,read_completed_utc_ns=utc_ns))
                        time.sleep(.02)
                push_blocks(6)
                end=time.monotonic()+5
                while hub.sample_index<48 and time.monotonic()<end:time.sleep(.02)
                cap=collector.start_capture(allow_combos=False,experiment_mode='navigation_mi_gaze')
                self.assertTrue(cap['active'])
                end=time.monotonic()+5
                while not (hub.recorder and collector.capture_snapshot()['hub_synced']) and time.monotonic()<end:
                    push_blocks(1)
                self.assertTrue(collector.capture_snapshot()['hub_synced'])
                hub_record_path=hub.recorder.journal.path
                collector.hold_start('FORWARD',pointer_source='desktop_pointer',page_active=True)
                push_blocks(8)
                collector.capture_heartbeat(page_active=True)
                push_blocks(4)
                # Transient 300ms stream jitter should invalidate live motion intent
                # without aborting the active capture session on the Hub.
                runtime.source_lost('eeg_stream_stale')
                self.assertEqual(hub.acquisition.snapshot()['phase'],'waiting_input')
                self.assertTrue(hub.acquisition.snapshot()['active'])
                self.assertIsNotNone(hub.recorder)
                collector.hold_end('FORWARD',pointer_source='desktop_pointer',page_active=True)
                push_blocks(4)
                collector.hold_start('REST',pointer_source='desktop_keyboard',page_active=True)
                push_blocks(8)
                collector.hold_end('REST',pointer_source='desktop_keyboard',page_active=True)
                push_blocks(4)
                collector.stop_capture('completed')
                end=time.monotonic()+5
                while (hub.recorder is not None or collector.network.snapshot()['backlog']>0) and time.monotonic()<end:
                    time.sleep(.03)
                self.assertIsNone(hub.recorder)
                collector.blocks.put(None);collector.writer.join(5)
                collector.network.stop();collector.journal.close()
                attrs=dict(subject='S01',date='2026-10-24',wearing_id='W01',role='train',experiment_mode='navigation_mi_gaze',reference='Fpz',prospective=True,previously_inspected=False,electrical_policy=dict(min_std_uv=0.0,max_abs_uv=1000.0))
                local_native=read_native(collector.journal.path)
                self.assertEqual([i['label'] for i in local_native['intervals']],['FORWARD','REST'])
                local_check=check_record(collector.journal.path,attrs)
                self.assertTrue(local_check['training_ready'],local_check)
                hub_native=read_native(hub_record_path)
                self.assertEqual([i['label'] for i in hub_native['intervals']],['FORWARD','REST'])
                hub_check=check_record(hub_record_path,attrs)
                self.assertTrue(hub_check['training_ready'],hub_check)
            finally:
                if collector.network:collector.network.stop()
                if collector.journal and not collector.journal.closed:
                    try:collector.blocks.put(None);collector.writer.join(2);collector.journal.close()
                    except BaseException:pass
                relay.close();runtime.close();cloud.close()

    def test_lan_ws_direct_hotspot_discovery_streaming_and_active_hold_capture(self):
        from gnautilus_acquisition.client import Collector,probe_hub
        from gnautilus_acquisition.lan_client import build_lan_enrollment,discover_lan_hub
        from gnautilus_acquisition.status_link import StatusLink
        from gnautilus_ml.data import read_native,check_record
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            hub=SimpleNamespace(lock=threading.RLock(),record_lock=threading.RLock(),new_device_mode=True,raw=collections.deque(),filtered=collections.deque(),notched=collections.deque(),source_mode='paused',last_packet=None,epoch=0,sample_index=0,recorder=None,recording_name=None,recording_samples=0,quality={},event=lambda *a:None,vr=SimpleNamespace(stop_test=lambda:None),intent_control=SimpleNamespace(configure=lambda d:None))
            runtime=GnautilusRuntime(hub,root/'hub_store',port=0,lan_ws_port=0);hub.gnautilus_runtime=runtime;hub.acquisition=GuidedAcquisition(hub)
            hub.start_recording=runtime.start_recording;hub.stop_recording=runtime.stop_recording
            runtime.start()
            lan_port=runtime.lan_ws.port
            self.assertGreater(lan_port,0)
            stream=descriptor('gtec_live',fs=250)
            stream['layout_verified']=True;stream['reference']='Fpz'
            stream['channels'] += [dict(id='LINK',name='LinkQualityInformation',kind='LINK',unit='%',physical_index=None),dict(id='DIGITAL',name='DigitalIOs',kind='DIGITAL',unit='bits',physical_index=None)]
            stream['configuration'].update(LinkQualityInformation=True,DigitalIOs=True)
            stream['configuration_sha256']=fingerprint(stream['configuration'])
            found=discover_lan_hub(f'127.0.0.1:{lan_port}',default_port=lan_port)
            self.assertIsNotNone(found)
            self.assertEqual(found['port'],lan_port)
            enrollment=build_lan_enrollment(f'127.0.0.1:{lan_port}')
            link=StatusLink(enrollment)
            collector=Collector(dict(),enrollment,root/'client_store')
            try:
                probe_res=probe_hub(enrollment,status_link=link)
                self.assertTrue(probe_res['connected'])
                self.assertEqual(link.snapshot()['transport'],'lan_ws')
                link.stop()
                collector._message(dict(kind='descriptor',descriptor=stream))
                end=time.monotonic()+5
                while not collector.network.snapshot()['connected'] and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(collector.network.snapshot()['connected'])
                def push_blocks(count):
                    for _ in range(count):
                        now_ns=time.monotonic_ns();utc_ns=time.time_ns()
                        arr=np.ones((8,34),np.float32)
                        collector._message(dict(kind='block',samples=arr,read_monotonic_ns=now_ns,utc_ns=utc_ns,read_completed_monotonic_ns=now_ns,read_completed_utc_ns=utc_ns))
                        time.sleep(.015)
                push_blocks(6)
                end=time.monotonic()+5
                while hub.sample_index<48 and time.monotonic()<end:time.sleep(.02)
                cap=collector.start_capture(allow_combos=False,experiment_mode='navigation_mi_gaze')
                self.assertTrue(cap['active'])
                end=time.monotonic()+5
                while not (hub.recorder and collector.capture_snapshot()['hub_synced']) and time.monotonic()<end:
                    push_blocks(1)
                self.assertTrue(collector.capture_snapshot()['hub_synced'])
                hub_record_path=hub.recorder.journal.path
                collector.hold_start('FORWARD',pointer_source='desktop_pointer',page_active=True)
                push_blocks(8)
                collector.capture_heartbeat(page_active=True)
                push_blocks(4)
                collector.hold_end('FORWARD',pointer_source='desktop_pointer',page_active=True)
                push_blocks(4)
                collector.hold_start('REST',pointer_source='desktop_keyboard',page_active=True)
                push_blocks(8)
                collector.hold_end('REST',pointer_source='desktop_keyboard',page_active=True)
                push_blocks(4)
                collector.stop_capture('completed')
                end=time.monotonic()+5
                while (hub.recorder is not None or collector.network.snapshot()['backlog']>0) and time.monotonic()<end:
                    time.sleep(.02)
                self.assertIsNone(hub.recorder)
                collector.blocks.put(None);collector.writer.join(5)
                collector.network.stop();collector.journal.close()
                attrs=dict(subject='S01',date='2026-10-24',wearing_id='W01',role='train',experiment_mode='navigation_mi_gaze',reference='Fpz',prospective=True,previously_inspected=False,electrical_policy=dict(min_std_uv=0.0,max_abs_uv=1000.0))
                local_native=read_native(collector.journal.path)
                self.assertEqual([i['label'] for i in local_native['intervals']],['FORWARD','REST'])
                self.assertTrue(check_record(collector.journal.path,attrs)['training_ready'])
                hub_native=read_native(hub_record_path)
                self.assertEqual([i['label'] for i in hub_native['intervals']],['FORWARD','REST'])
                self.assertTrue(check_record(hub_record_path,attrs)['training_ready'])
            finally:
                link.stop()
                if collector.network:collector.network.stop()
                if collector.journal and not collector.journal.closed:
                    try:collector.blocks.put(None);collector.writer.join(2);collector.journal.close()
                    except BaseException:pass
                runtime.close()

if __name__=='__main__':unittest.main()
