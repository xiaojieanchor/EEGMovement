"""Isolated software observations: no device, production upload, or motion."""
import collections,copy,json,threading,time
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import pytest
from hub.immersive import ImmersiveWorkspace
from gnautilus_ml.navigation_contract import CHANNELS,ELECTRICAL
from gnautilus_ml.stream_runtime import contract_from_descriptor


def fixture():
    d=dict(stream_id='a'*32,source_kind='gtec_live',device_serial='FIXTURE',sampling_rate=250,layout_verified=True,reference=None,ground=None,
        channels=[dict(name=c,kind='EEG',physical_index=i,unit='uV') for i,c in enumerate(CHANNELS)],configuration=dict(InputSignal=0,Channels=[dict(BipolarChannel=-1) for _ in CHANNELS]))
    meta=dict(model_id='m',algorithm='hybrid_navigation',runtime_schema='gnautilus.hybrid_navigation.v3',runtime_accepted=True,
        sampling_rate=250,channels=CHANNELS,input_contract=contract_from_descriptor(d),electrical_policy=ELECTRICAL)
    t=np.arange(500)/250;x=np.stack([10*np.sin(2*np.pi*10*t+i*.1) for i in range(32)],1)
    peer=dict(receiver_id='v',device_id='d',role='unity',online=True,telemetry_age_ms=5,applied_seq=4,
        telemetry=dict(actual_speed=0,actual_yaw_rate=0,state='STOP',mode='LIVE'))
    h=SimpleNamespace(lock=threading.RLock(),started=time.monotonic(),last_packet=time.monotonic(),epoch=3,sample_index=500,raw=collections.deque(x.tolist()),recorder=None,
        acquisition=SimpleNamespace(snapshot=lambda:dict(active=False)),intent=SimpleNamespace(snapshot=lambda:dict(inference_enabled=False,fresh=False,control=dict(enabled=False))),
        vr=SimpleNamespace(snapshot=Mock(return_value=dict(ready=True,clients=[peer]))),quest=SimpleNamespace(snapshot=Mock(return_value=dict(mirror_fresh=False))),
        gnautilus_runtime=SimpleNamespace(descriptor=d,fresh=Mock(return_value=True),gap_count=0,last_gap=None,active_transport='fixture'),
        ml=SimpleNamespace(updated=time.time(),error=None,cached=dict(loaded='m',models=[meta]),bridge=SimpleNamespace(status=dict(ssh_connected=True,worker_connected=True))))
    return h,ImmersiveWorkspace(h),meta,peer


def test_stopped_inference_still_has_real_per_channel_quality_and_no_motion():
    h,w,m,p=fixture();w.observe();s=w.status()
    assert s['entry_eligible'] and s['can_start'] and not s['intent']['inference_enabled']
    q=s['quality'];assert q['good_channels']==32 and q['contact_quality_known'] is False and q['device_valid'] is None
    assert q['sample_index_start']==125 and q['sample_index_end']==500
    assert all(c['peak_to_peak_uv']>0 and c['rms_uv']>0 for c in q['channels'])
    h.quest.snapshot.assert_called_with('d');json.dumps(s,allow_nan=False)


@pytest.mark.parametrize('kind',['flat','rail','line','nan'])
def test_bad_channel_does_not_block_view_but_does_block_start(kind):
    h,w,m,p=fixture();x=np.asarray(h.raw)
    x[:,4]=0 if kind=='flat' else 2000 if kind=='rail' else np.nan if kind=='nan' else 20*np.sin(2*np.pi*50*np.arange(500)/250)
    h.raw=collections.deque(x.tolist());w.observe();s=w.status()
    assert s['entry_eligible'] and not s['can_start']
    assert s['quality']['good_channels']==31 and not s['quality']['channels'][4]['valid']
    json.dumps(s,allow_nan=False)


def test_warm_stale_and_model_change_never_reuse_old_pass():
    h,w,m,p=fixture();w.observe();h.sample_index=600;h.raw=collections.deque(list(h.raw)[-100:]);w.observe()
    assert w.quality()['state']=='warming' and not w.quality()['valid']
    h.gnautilus_runtime.fresh.return_value=False;assert w.quality()['good_channels']==0
    h.gnautilus_runtime.fresh.return_value=True;m['model_id']='other';assert not w.quality()['fresh']


def test_multiple_vr_targets_require_explicit_choice_and_do_not_mix_camera():
    h,w,m,p=fixture();other=dict(p,receiver_id='other',device_id='other-device');h.vr.snapshot.return_value['clients'].append(other)
    assert not w.status()['entry_eligible']
    assert w.status('other')['vr']['target']==other
    h.quest.snapshot.assert_called_with('other-device')
    h.vr.snapshot.return_value['clients'][1]['online']=False
    assert not w.status('other')['entry_eligible']


@pytest.mark.parametrize('fault',['offline','compute','rate','replay','csharp','ack','collecting'])
def test_entry_checks_all_four_ends_and_real_contract(fault):
    h,w,m,p=fixture()
    if fault=='offline':m['runtime_accepted']=False
    if fault=='compute':h.ml.updated-=20
    if fault=='rate':h.gnautilus_runtime.descriptor['sampling_rate']=256
    if fault=='replay':h.gnautilus_runtime.descriptor['source_kind']='recording_replay'
    if fault=='csharp':p['role']='csharp_harness'
    if fault=='ack':p['applied_seq']=0
    if fault=='collecting':h.recorder=object()
    assert not w.status()['entry_eligible']


def test_signal_cursor_is_incremental_and_resets_on_epoch_or_eviction():
    h,w,m,p=fixture();a=w.signal();b=w.signal(3,475)
    assert a['reset'] and len(a['samples'])==500
    assert not b['reset'] and len(b['samples'])==25 and b['sample_index_start']==475
    assert w.signal(2,475)['reset'] and w.signal(3,-10)['reset']
    h.gnautilus_runtime.fresh.return_value=False;assert w.signal(3,475)['samples']==[]


def test_policy_telemetry_is_sample_clock_and_thresholds_match_real_evaluation():
    from gnautilus_ml.observation import SessionObservation
    from gnautilus_ml.intent_repair import IntentPolicy
    config=dict(activity=.6,direction=.6,margin=.1,confirm=3,stop=2,eye_discount=.1,other_premium=.1,hold_discount=.05,rest_activity=.2)
    policy=IntentPolicy(config);policy.eye_event(1);thresholds=[policy.thresholds(d) for d in range(1,5)]
    decision=policy.step(.9,[.8,.1,.05,.05]);assert decision['effective_thresholds']==thresholds
    o=SessionObservation();a=o.update(25,decision,1);b=o.update(75,decision,0)
    assert a['eye_event_sequence']==b['eye_event_sequence']==1 and b['cue_duration_s']==.2
    assert b['eye_events'][0]['session_sample']==25
    assert SessionObservation().update(25,dict(status='UNKNOWN',direction=0,cue_direction=0),0)['eye_events']==[]


def test_require_stopped_activation_is_enforced_on_server():
    from hub.ml_runtime import MLManager
    h,w,m,p=fixture();h.intent.inference_enabled=True;h.intent_control=SimpleNamespace(snapshot=lambda:dict(enabled=False))
    manager=MLManager(h,'.',Mock())
    with pytest.raises(ValueError,match='停止'):manager.activate(dict(id='new',require_stopped=True))
    manager.bridge.call.assert_not_called()
