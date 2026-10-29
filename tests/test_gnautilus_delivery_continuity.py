import time
from unittest.mock import patch

import numpy as np
import pytest

from gnautilus_acquisition.contract import block_frame
from tests.test_gnautilus_mode_recovery import capture_fixture,manual_event,inference_fixture
from tests.test_gnautilus_transport import descriptor


def feed(runtime,d,seq,age=0):
    h,b=block_frame(d,seq,(seq-1)*8,np.ones((8,32),np.float32))
    runtime.accept(d,dict(h,ingest_transport='lan_ws',hub_source_monotonic_ns=time.monotonic_ns()-int(age*1e9)),np.frombuffer(b,dtype='<f4').reshape(-1,32))


def test_late_contiguous_delivery_keeps_labels_buffer_and_epoch(tmp_path):
    hub,runtime=capture_fixture(tmp_path);d=descriptor('gtec_live')
    try:
        feed(runtime,d,1)
        hub.acquisition.start_manual(dict(build_id='TEST',input_surface='acquisition_client',session_id='a'*32,
            client_token='token',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2'))
        manual_event(hub,1,'hold_start','FORWARD');epoch=hub.epoch;recorder=hub.recorder
        for seq in range(2,7):
            feed(runtime,d,seq,age=.45)
            runtime.check_input_health()
            assert not runtime.fresh()
            assert hub.epoch==epoch and runtime.recovery_count==0
            assert hub.acquisition.snapshot()['label']=='FORWARD'
            assert len(hub.raw)==seq*8 and hub.recorder is recorder
            assert len(runtime.model_input(require_fresh=False)['samples'])==seq*8
            with pytest.raises(ValueError):runtime.model_input()
        feed(runtime,d,7)
        assert runtime.fresh() and hub.epoch==epoch and runtime.last_seq==7
        assert hub.recording_samples==48
    finally:runtime.close()


def test_short_no_arrival_does_not_clear_history_or_create_new_epoch(tmp_path):
    hub,runtime=capture_fixture(tmp_path);d=descriptor('gtec_live')
    try:
        feed(runtime,d,1);epoch=hub.epoch
        with patch('hub.gnautilus_runtime.time.monotonic',return_value=runtime.last_accept_monotonic+.8):
            runtime.check_input_health()
            assert not runtime.fresh()
        assert not runtime.input_suspended and len(hub.raw)==8 and hub.epoch==epoch
        with patch('hub.gnautilus_runtime.time.monotonic',return_value=runtime.last_accept_monotonic+3.1):
            runtime.check_input_health()
        assert runtime.input_suspended and len(hub.raw)==8
        feed(runtime,d,2)
        assert not runtime.input_suspended and len(hub.raw)==8
    finally:runtime.close()


def test_late_inference_preserves_consumed_sample_cursor_and_lease():
    hub,intent=inference_fixture();original=intent.manager.bridge.call
    def call(method,path,data=None,**kw):
        result=original(method,path,data,**kw)
        return result
    intent.manager.bridge.call=call
    payload=hub.gnautilus_runtime.model_input()
    payload['input_origin_monotonic']=time.monotonic()-.5
    intent.loop()
    assert intent.inference_enabled and intent.state=='delayed'
    assert intent.remote_generation==7 and intent.last_sample==16
    assert intent.latest is not None and not intent.snapshot()['fresh']


def test_delayed_lan_live_is_delivered_but_backfill_never_is(tmp_path):
    from unittest.mock import Mock
    from hub.gnautilus import IngestService
    d=descriptor('gtec_live');receive=Mock();service=IngestService(tmp_path,receive)
    h,b=block_frame(d,1,0,np.ones((8,32),np.float32))
    try:
        result=service.ingest_external('c'*32,d,h,b,time.monotonic()-.6,transport='lan_ws')
        assert result['live_accepted'] and receive.call_count==1
        h,b=block_frame(d,2,8,np.ones((8,32),np.float32),mode='backfill')
        result=service.ingest_external('c'*32,d,h,b,time.monotonic(),transport='lan_ws')
        assert not result['live_accepted'] and receive.call_count==1
    finally:service.close()
