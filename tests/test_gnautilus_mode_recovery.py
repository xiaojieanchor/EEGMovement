"""Requested modes survive temporary input loss; labels/results never bridge it."""
import collections
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from gnautilus_acquisition.contract import block_frame
from hub.acquisition import GuidedAcquisition
from hub.gnautilus_runtime import GnautilusRuntime
from hub.ml_runtime import MLIntent
from tests.test_gnautilus_transport import descriptor


def capture_fixture(tmp_path):
    hub = SimpleNamespace(lock=threading.RLock(), record_lock=threading.RLock(), new_device_mode=True,
        raw=collections.deque(), filtered=collections.deque(), notched=collections.deque(), source_mode='paused',
        last_packet=None, epoch=0, sample_index=0, recorder=None, recording_name=None, recording_samples=0,
        quality={}, event=Mock(), vr=SimpleNamespace(stop_test=Mock()), intent_control=SimpleNamespace(configure=Mock()))
    runtime = GnautilusRuntime(hub, tmp_path, port=0, lan_ws_port=-1)
    hub.gnautilus_runtime = runtime
    hub.acquisition = GuidedAcquisition(hub)
    hub.start_recording = runtime.start_recording
    hub.stop_recording = runtime.stop_recording
    return hub, runtime


def manual_event(hub, seq, phase, label):
    return hub.acquisition.manual_event(dict(session_id='a'*32, client_token='token', seq=seq,
        phase=phase, label=label, sender_monotonic_ms=time.monotonic()*1000, sender_frame=seq,
        clock_offset_ms=0, sync_rtt_ms=1, xr_active=False, page_active=True, pointer_source='desktop_pointer'))


def test_capture_pauses_resumes_same_session_and_closes_unknown_gap(tmp_path):
    hub, runtime = capture_fixture(tmp_path)
    d = descriptor('gtec_live')
    def feed(seq):
        h, b = block_frame(d, seq, (seq-1)*8, np.ones((8,32),np.float32))
        runtime.accept(d, dict(h, ingest_transport='lan_ws'), np.frombuffer(b,dtype='<f4').reshape(-1,32))
    try:
        feed(1)
        hub.acquisition.start_manual(dict(build_id='TEST', input_surface='acquisition_client', session_id='a'*32,
            client_token='token', presentation_protocol='self_paced_axes_v2', task_guidance_version='gaze_navigation_v2'))
        manual_event(hub,1,'hold_start','FORWARD')
        feed(2)
        recorder = hub.recorder
        runtime.source_lost('transport_error')
        state = hub.acquisition.snapshot()
        assert state['active'] and state['phase']=='waiting_input' and state['label'] is None
        assert hub.recorder is recorder
        ack = manual_event(hub,2,'heartbeat','FORWARD')
        assert ack['waiting_input'] and hub.acquisition.snapshot()['label'] is None
        feed(3)
        manual_event(hub,3,'heartbeat','FORWARD')
        assert hub.acquisition.snapshot()['phase']=='held'
        feed(4)
        manual_event(hub,4,'hold_end','FORWARD')
        assert hub.acquisition.snapshot()['session_id']=='a'*32
        path = hub.recorder.journal.path
        hub.acquisition.stop(reason='completed')
        from gnautilus_ml.data import read_native
        native = read_native(path)
        assert [(x['start'],x['end'],x['label']) for x in native['intervals']]==[(0,8,'FORWARD'),(16,24,'FORWARD')]
        runtime.source_lost('transport_error')
        feed(5)
        assert not hub.acquisition.snapshot()['active'] and hub.recorder is None
    finally:
        runtime.close()


def inference_fixture():
    control = SimpleNamespace(configure=Mock(), update=Mock(), snapshot=lambda:{'enabled':False})
    hub = SimpleNamespace(intent_control=control, vr=SimpleNamespace(stop_test=Mock()), epoch=2,
        stop_event=Mock(), acquisition=SimpleNamespace(snapshot=lambda:{'active':False}), recorder=None)
    d=dict(channels=[dict(kind='EEG',name='C3')],sampling_rate=8)
    payload=dict(sample_index_end=16,descriptor=d,samples=np.ones((8,1)),source_epoch=2,stream_id='d'*32,input_origin_monotonic=time.monotonic())
    hub.gnautilus_runtime=SimpleNamespace(model_input=lambda **kw:payload, fresh=lambda:True)
    def call(method,path,data=None,**kwargs):
        if path=='/inference/start':return dict(generation=7,model_id='model')
        if path=='/inference/predict':return dict(source_epoch=2,sample_index_end=16,model_id='model',probabilities=[.8,.05,.05,.05,.05],eligible=True)
        return {}
    manager=SimpleNamespace(cached=dict(loaded='model',models=[dict(model_id='model',window_s=1.,hop_s=.1,algorithm='bandpower_lda')]),bridge=SimpleNamespace(call=call))
    intent=MLIntent(hub,manager)
    intent.model_id='model';intent.inference_enabled=True
    hub.stop_event.wait.side_effect=[False,True]
    return hub,intent


def test_inference_enabled_survives_loss_and_reacquires_same_model_lease():
    hub,intent=inference_fixture()
    intent.invalidate('transport_error',preserve_mode=True)
    assert intent.inference_enabled and intent.latest is None and intent.state=='waiting_input'
    intent.loop()
    assert intent.state=='running' and intent.remote_generation==7
    hub.intent_control.configure.assert_called_with({'enabled':False})


def test_explicit_start_without_eeg_waits_without_predicting_or_arming_motion():
    hub,intent=inference_fixture()
    intent.manager.inference_readiness=lambda:dict(can_start=True)
    hub.gnautilus_runtime.model_input=Mock(side_effect=ValueError('Fresh verified-origin input required'))
    original=intent.manager.bridge.call
    intent.manager.bridge.call=Mock(side_effect=original)
    intent.snapshot=Mock(return_value={})
    intent.start()
    assert intent.inference_enabled and intent.state=='waiting_input'
    intent.loop()
    assert intent.inference_enabled and intent.latest is None and intent.state=='waiting_input'
    assert not any(c.args[1]=='/inference/predict' for c in intent.manager.bridge.call.call_args_list)
    hub.intent_control.configure.assert_called_with({'enabled':False})
    intent.stop();assert not intent.inference_enabled


def test_waiting_mode_cannot_start_an_unready_model():
    import pytest
    hub,intent=inference_fixture()
    intent.manager.inference_readiness=lambda:dict(can_start=False,message='Model contract not ready')
    intent.manager.bridge.call=Mock()
    with pytest.raises(ValueError,match='Model contract not ready'):intent.start()
    intent.manager.bridge.call.assert_not_called()


def test_manual_stop_during_recovery_cannot_be_undone_by_late_reply():
    hub,intent=inference_fixture()
    intent.invalidate('transport_error',preserve_mode=True)
    previous=intent.manager.bridge.call
    def call(method,path,data=None,**kwargs):
        if path=='/inference/start':intent.stop()
        return previous(method,path,data,**kwargs)
    intent.manager.bridge.call=call
    intent.loop()
    assert not intent.inference_enabled and intent.latest is None


def test_new_stream_rotates_recording_but_preserves_logical_capture(tmp_path):
    hub,runtime=capture_fixture(tmp_path)
    d=descriptor('gtec_live')
    def feed(d,seq):
        h,b=block_frame(d,seq,(seq-1)*8,np.ones((8,32),np.float32))
        runtime.accept(d,dict(h,ingest_transport='lan_ws'),np.frombuffer(b,dtype='<f4').reshape(-1,32))
    try:
        feed(d,1)
        hub.acquisition.start_manual(dict(build_id='TEST',input_surface='acquisition_client',session_id='a'*32,
            client_token='token',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2'))
        first=hub.recorder.journal.path
        other=dict(d,stream_id='f'*32)
        feed(other,1)
        assert hub.acquisition.snapshot()['active']
        assert hub.acquisition.snapshot()['session_id']=='a'*32
        assert hub.recorder.journal.path != first
        assert (first/'closed.json').exists()
        assert hub.recorder.journal.descriptor['stream_id']=='f'*32
        hub.acquisition.stop(reason='completed')
    finally:runtime.close()


def test_local_hotspot_boundaries_do_not_use_public_clock_association(tmp_path):
    hub,runtime=capture_fixture(tmp_path)
    d=descriptor('gtec_live');h,b=block_frame(d,1,0,np.ones((8,32),np.float32))
    try:
        runtime.accept(d,dict(h,ingest_transport='lan_ws'),np.frombuffer(b,dtype='<f4').reshape(-1,32))
        hub.public_relay=SimpleNamespace(associate_event=Mock(return_value={'status':'no_clock_mapping'}))
        hub.acquisition.start_manual(dict(build_id='TEST',input_surface='acquisition_client',session_id='a'*32,
            client_token='token',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2'))
        manual_event(hub,1,'hold_start','FORWARD')
        hub.public_relay.associate_event.assert_not_called()
        hub.acquisition.stop(reason='completed')
    finally:runtime.close()
