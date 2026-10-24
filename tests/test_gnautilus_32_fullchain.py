"""Isolated 32-EEG software chain. Synthetic fixtures, no hardware or production server."""
import collections,json,threading,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import pytest
from tests.test_gnautilus_ml_e2e import fixture_record

_layout=json.loads((Path(__file__).resolve().parents[1]/'configs/gnautilus_layout32_hybrid_candidate.example.json').read_text(encoding='utf-8'))
CHANNELS=[_layout['channels'][str(i)] for i in range(32)]


def test_32_float32_ingest_registered_model_inference_decision_and_signed_vr_packet(tmp_path):
    from gnautilus_ml.service import ModelService
    from gnautilus_ml.common import read_json
    from gnautilus_acquisition.contract import block_frame
    from hub.gnautilus_runtime import GnautilusRuntime
    from hub.ml_runtime import MLIntent
    from hub.intent_control import IntentControl
    from hub.vr import VrLink,encode_packet,decode_packet
    import gnautilus_ml
    remote=tmp_path/'isolated-compute';service=ModelService(remote,{},Path(gnautilus_ml.__file__).resolve().parents[1]);records=[]
    for i in range(4):
        uid=f'{i+1:064x}';fixture_record(remote/'data'/uid,i+1,channels=CHANNELS,fs=512,mode='navigation_mi_gaze')
        records.append(dict(upload_id=uid,attributes=dict(subject='SYNTHETIC_FIXTURE',date=f'2026-10-{i+1:02}',wearing_id=f'synthetic-{i}',role='train' if i<3 else 'development',experiment_mode='navigation_mi_gaze',reference='right_ear',electrical_policy=dict(rail_uv=20000,flat_ptp_uv=.001,max_rms_uv=5000,max_line_ratio=.8))))
    dataset=service.post('/datasets/import',{'records':records});job=service.post('/jobs',dict(dataset_id=dataset['dataset_id'],algorithm='bandpower_lda',max_epochs=1))
    # This mock is restricted to this synthetic unit test, never a production flag.
    with patch('gnautilus_ml.governance.check_runtime',return_value='cpu'):service.runner.run(job['id'])
    actual=service.registry.job(job['id']);assert actual['state']=='completed',service.registry.events(job['id'])[-2:]
    service.post('/models/activate',{'id':actual['model_id']})
    root=remote/'data'/records[0]['upload_id'];d=read_json(root/'descriptor.json');raw=np.fromfile(root/'raw.f32',dtype='<f4').reshape(-1,32)
    control=IntentControl(False)
    hub=SimpleNamespace(lock=threading.RLock(),record_lock=threading.RLock(),new_device_mode=True,raw=collections.deque(),filtered=collections.deque(),notched=collections.deque(),source_mode='paused',last_packet=None,epoch=0,sample_index=0,recorder=None,recording_name=None,recording_samples=0,quality={},event=lambda *a:None,intent_control=control,acquisition=SimpleNamespace(snapshot=lambda:{'active':False}),stop_event=Mock())
    manager=SimpleNamespace(cached=service.get('/snapshot'),bridge=SimpleNamespace(call=lambda method,path,data=None,**kw:service.post(path,data)),switching=lambda:False)
    hub.intent=MLIntent(hub,manager);hub.ml=manager;hub.vr=VrLink(tmp_path,lambda:hub.intent.readiness(),lambda *a:None)
    runtime=GnautilusRuntime(hub,tmp_path,port=0);hub.gnautilus_runtime=runtime
    heartbeat=dict(version=1,kind='heartbeat',receiver_id='a'*32,nonce='b'*32,session='',ticket=1,role='unity',allow_test=False,
                   telemetry=dict(state='STOP',mode='LIVE',applied_seq=0,motion_schema=3))
    def packet():
        authenticated=decode_packet(encode_packet(heartbeat,hub.vr.key),hub.vr.key)
        reply=hub.vr.heartbeat(authenticated,('127.0.0.1',12345),hub.intent.readiness())
        assert reply is not None
        return decode_packet(encode_packet(reply,hub.vr.key),hub.vr.key)
    start=seq=0;n=768;directions=[]
    def feed(values,mode='live'):
        nonlocal start,seq
        h,b=block_frame(d,seq+1,start,values);h['mode']=mode
        runtime.accept(d,h,np.frombuffer(b,dtype='<f4').reshape(-1,32))
        if mode=='live':seq+=1;start+=len(values)
    try:
        for label in range(1,5):
            hub.intent.stop();values=raw[label*1536:label*1536+n];feed(values)
            hub.intent.start();assert not control.enabled # inference does not arm movement
            control.configure({'enabled':True})
            for i in range(3):
                feed(values);hub.stop_event.wait.side_effect=[False,True];hub.intent.loop()
                assert hub.intent.latest is not None,hub.intent.error
                assert len(hub.intent.latest['probabilities'])==5 and len(hub.intent.latest['usable_channels'])==32
                heartbeat['ticket']+=1;reply=packet()
                if reply['kind']=='welcome':heartbeat['session']=reply['session']
                if i<2:time.sleep(.09)
            assert reply['kind']=='command' and reply['active'] and reply['direction']==label,reply
            assert reply['ttl_ms']==180 and reply['motion_schema']==3
            directions.append(label)
        assert directions==[1,2,3,4]
        old=json.loads(json.dumps(d));old['channels'][15]['name'],old['channels'][18]['name']=old['channels'][18]['name'],old['channels'][15]['name']
        with pytest.raises(ValueError,match='montage'):
            service.post('/inference/predict',dict(model_id=hub.intent.model_id,generation=hub.intent.remote_generation,descriptor=old,
                stream_id=d['stream_id'],source_epoch=hub.epoch,sample_index_end=start+1,samples=values.T.tolist(),device_valid=True))
        origin=hub.last_packet;before=hub.sample_index;feed(values,mode='backfill')
        assert hub.last_packet==origin and hub.sample_index==before
        # Late return cannot become fresh just because the worker replied now.
        feed(values);hub.last_packet=time.monotonic()-.2
        previous=manager.bridge.call
        def late(method,path,data=None,**kw):
            out=previous(method,path,data,**kw)
            if path=='/inference/predict':time.sleep(.08)
            return out
        manager.bridge.call=late;hub.stop_event.wait.side_effect=[False,True];hub.intent.loop()
        assert hub.intent.latest is None and 'late_return' in hub.intent.error
        heartbeat['ticket']+=1;assert not packet()['active']
        runtime.source_lost('transport_error');assert not hub.intent.inference_enabled and not control.enabled
        heartbeat['ticket']+=1;assert not packet()['active']
    finally:
        hub.intent.stop();runtime.close();hub.vr.close()
