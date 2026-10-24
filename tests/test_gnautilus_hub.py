import collections
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from tests.test_gnautilus_transport import descriptor
from gnautilus_acquisition.contract import block_frame,validate_descriptor
from hub.acquisition import GuidedAcquisition
from hub.gnautilus_runtime import GnautilusRuntime
from hub.gnautilus import provision
from gnautilus_acquisition.client import connect_authenticated
from gnautilus_acquisition.contract import send_frame,recv_frame


class HubIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.hub=SimpleNamespace(lock=threading.RLock(),record_lock=threading.RLock(),new_device_mode=True,raw=collections.deque(),filtered=collections.deque(),notched=collections.deque(),
            source_mode='paused',last_packet=None,epoch=0,sample_index=0,recorder=None,recording_name=None,recording_samples=0,quality={},event=lambda *a:None,
            intent_control=SimpleNamespace(configure=lambda d:None),vr=SimpleNamespace(stop_test=lambda:None))
        self.runtime=GnautilusRuntime(self.hub,self.root);self.hub.gnautilus_runtime=self.runtime
        self.hub.start_recording=self.runtime.start_recording;self.hub.stop_recording=self.runtime.stop_recording
        self.hub.acquisition=GuidedAcquisition(self.hub)
    def tearDown(self):self.runtime.close();self.tmp.cleanup()
    def feed(self,d,seq=1,start=0):
        h,b=block_frame(d,seq,start,np.zeros((8,len(d['channels'])),np.float32));self.runtime.accept(d,h,np.frombuffer(b,dtype='<f4').reshape(8,-1))
    def test_32_channel_path_preserves_data_and_unknown_electrode_quality(self):
        d=validate_descriptor(descriptor('gtec_live'));self.feed(d)
        self.assertEqual(len(self.hub.raw[-1]),32)
        self.assertFalse(self.hub.quality['valid']);self.assertFalse(self.hub.quality['contact_quality_known'])
        s=self.runtime.snapshot();self.assertEqual(s['source']['nominal_sfreq'],512)
        self.assertIsNone(s['source']['sfreq_measured']);self.assertEqual(s['source']['sampling_rate_source'],'device_configuration')
        self.assertFalse(self.runtime.readiness()['model_fresh'])

    def test_runtime_preserves_remaining_freshness_instead_of_resetting_lease(self):
        d=validate_descriptor(descriptor('gtec_live'));h,b=block_frame(d,1,0,np.zeros((8,32),np.float32));x=np.frombuffer(b,dtype='<f4').reshape(8,32)
        mark=time.monotonic_ns()-180_000_000;self.runtime.accept(d,dict(h,hub_source_monotonic_ns=mark),x)
        self.assertAlmostEqual(self.hub.last_packet,mark/1e9,places=6);self.assertTrue(self.runtime.fresh())
        time.sleep(.08);self.assertFalse(self.runtime.fresh())
        self.runtime.accept(d,dict(h,seq=2,sample_start=8,hub_source_monotonic_ns=time.monotonic_ns()-300_000_000),x)
        self.assertEqual(self.hub.sample_index,8)
    def test_desktop_markers_attach_to_real_stream_and_release_is_unlabelled(self):
        d=validate_descriptor(descriptor('gtec_live'));self.feed(d)
        a=self.hub.acquisition
        s=a.start(dict(build_id='test-build',paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2',input_surface='desktop'))
        for seq,phase in [(1,'hold_start'),(2,'hold_end')]:
            a.event(dict(session_id=s['session_id'],client_token=s['client_token'],seq=seq,label='FORWARD',phase=phase,sender_monotonic_ms=time.monotonic()*1000,sender_frame=1,clock_offset_ms=0,sync_rtt_ms=1,xr_active=False,pointer_source='desktop_pointer',page_active=True))
        self.assertIsNone(a.snapshot()['label'])
        self.feed(d,2,8);path=self.hub.recorder.journal.path
        a.stop(reason='operator_stop')
        rows=[json.loads(l) for l in (path/'events.jsonl').read_text('utf-8').splitlines()]
        markers=[r for r in rows if r.get('code')=='manual_hold_start']
        self.assertEqual(markers[0]['stream_id'],d['stream_id']);self.assertEqual(len(markers[0]['channel_training_mask']),32)
        self.assertTrue((path/'closed.json').exists())
    def test_synthetic_input_cannot_start_personal_capture(self):
        self.feed(validate_descriptor(descriptor()))
        with self.assertRaises(ValueError):self.runtime.start_recording()
    def test_gap_ends_active_hold_without_reusing_labels(self):
        d=validate_descriptor(descriptor('gtec_live'));self.feed(d)
        s=self.hub.acquisition.start(dict(build_id='test-build',paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2',input_surface='desktop'))
        self.feed(d,3,16)
        self.assertFalse(self.hub.acquisition.snapshot()['active']);self.assertIsNone(self.hub.recorder)

    def test_new_device_hold_expires_at_declared_lease_and_closes(self):
        d=validate_descriptor(descriptor('gtec_live'));self.feed(d);a=self.hub.acquisition
        s=a.start(dict(build_id='test',paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2',input_surface='desktop'))
        a.event(dict(session_id=s['session_id'],client_token=s['client_token'],seq=1,label='FORWARD',phase='hold_start',sender_monotonic_ms=0,sender_frame=1,clock_offset_ms=0,sync_rtt_ms=1,xr_active=False,pointer_source='desktop_pointer',page_active=True))
        a.last_seen-=.8;a.expire()
        self.assertFalse(a.snapshot()['active']);self.assertIsNone(a.snapshot()['label']);self.assertIsNone(self.hub.recorder)

    def test_tls_runtime_active_markers_storage_and_returned_status_in_one_chain(self):
        # Synthetic matrices under an injected descriptor exercise the software
        # contract only. This is not evidence of a real vendor/EEG connection.
        e=provision(self.root,'127.0.0.1',port=0);self.runtime.service.host='127.0.0.1';self.runtime.service.port=0
        self.runtime.start();e['port']=self.runtime.service.port;s,_=connect_authenticated(e)
        try:
            d=validate_descriptor(descriptor('gtec_live'));send_frame(s,dict(kind='descriptor',descriptor=d));r,_=recv_frame(s)
            x=np.arange(256,dtype=np.float32).reshape(8,32)
            h,b=block_frame(d,1,0,x,ticket=r['ticket'],source_age_ms=0);send_frame(s,h,b);r,_=recv_frame(s)
            self.assertTrue(r['live_accepted']);self.assertEqual(r['durable_seq'],1)
            a=self.hub.acquisition;session=a.start(dict(build_id='software-chain-fixture',paradigm='self_paced_motor_imagery',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2',input_surface='desktop'))
            event=dict(session_id=session['session_id'],client_token=session['client_token'],seq=1,label='LEFT',phase='hold_start',sender_monotonic_ms=time.monotonic()*1000,sender_frame=1,clock_offset_ms=0,sync_rtt_ms=1,xr_active=False,pointer_source='desktop_pointer',page_active=True)
            a.event(event);h,b=block_frame(d,2,8,x+1,ticket=r['ticket'],source_age_ms=0);send_frame(s,h,b);r,_=recv_frame(s)
            self.assertEqual(r['hub_state']['label'],'LEFT');self.assertEqual(r['hub_state']['recording_samples'],8)
            self.assertEqual(r['hub_state']['sample_index_end'],16);self.assertEqual(r['hub_state']['motion_state'],'UNKNOWN')
            path=self.hub.recorder.journal.path;a.event(dict(event,seq=2,phase='hold_end'));self.assertIsNone(a.snapshot()['label']);a.stop(reason='software_chain_complete')
            self.assertEqual(np.fromfile(path/'raw.f32',dtype='<f4').reshape(8,32).tolist(),(x+1).tolist())
            closed=json.loads((path/'closed.json').read_text('utf-8'));self.assertEqual(closed['samples'],8)
            events=[json.loads(line) for line in (path/'events.jsonl').read_text('utf-8').splitlines()]
            marker=next(row for row in events if row.get('code')=='manual_hold_start');self.assertEqual(marker['sample_index_end'],8)
            remote=self.runtime.service.journals[(e['agent_id'],d['stream_id'])];self.assertEqual(remote.snapshot()['samples'],16)
        finally:s.close()


if __name__=='__main__':unittest.main()
