"""Loopback-only cadence, recovery and backfill soak; no EEG SDK or production Hub."""
import json
import os
import socket
import time

import numpy as np

from gnautilus_acquisition.client import Replicator
from gnautilus_acquisition.contract import block_frame, fingerprint
from gnautilus_acquisition.storage import StreamJournal
from tests.test_gnautilus_mode_recovery import capture_fixture, manual_event
from tests.test_gnautilus_transport import descriptor


def test_32_eeg_aux_real_time_cadence_history_reconnect_and_capture(tmp_path):
    hub, runtime = capture_fixture(tmp_path/'hub')
    runtime.lan_ws.requested_port = runtime.lan_ws.port = 0
    runtime.start()
    agent='c'*32
    enrollment=dict(mode='lan_ws',agent_id=agent,host='127.0.0.1',port=runtime.lan_ws.port,
                    host_hint=f'127.0.0.1:{runtime.lan_ws.port}')
    d=descriptor('gtec_live',fs=250)
    d['channels'] += [dict(id=f'ACC_{axis}',name=f'ACC_{axis}',kind='ACC',unit='g',physical_index=None) for axis in 'XYZ']
    d['channels'] += [dict(id='LINK',name='LINK',kind='LINK',unit='%',physical_index=None),dict(id='DIGITAL',name='DIGITAL',kind='DIGITAL',unit='bits',physical_index=None)]
    d['configuration'].update(accelerometer=True)
    d['configuration_sha256']=fingerprint(d['configuration'])
    live=StreamJournal(tmp_path/'live',d)
    old=StreamJournal(tmp_path/'history',dict(d,stream_id='e'*32))
    for seq in range(1,97):
        h,b=block_frame(old.descriptor,seq,(seq-1)*8,np.full((8,37),-seq,np.float32),read_monotonic_ns=time.monotonic_ns()-20_000_000_000)
        old.append(h,b)
    rep=Replicator(live,enrollment).start();history=None
    received=[];seq=0;event_seq=0;capture_started=False;record_path=None;steady_gaps=None
    started=time.monotonic()
    def send():
        nonlocal seq
        seq+=1
        h,b=block_frame(d,seq,(seq-1)*8,np.full((8,37),seq,np.float32))
        live.append(h,b)
    def event(phase,label):
        nonlocal event_seq
        event_seq+=1
        return manual_event(hub,event_seq,phase,label)
    try:
        deadline=time.monotonic()+5
        while (not rep.snapshot()['connected'] or not runtime.fresh()) and time.monotonic()<deadline:
            send();time.sleep(.032)
        assert rep.snapshot()['connected'] and runtime.fresh()
        baseline=runtime.gap_count
        for i in range(int(os.environ.get('HOTSPOT_TEST_BLOCKS','180'))):
            cycle=time.monotonic()
            send()
            if i==12:
                hub.acquisition.start_manual(dict(build_id='SOAK',input_surface='acquisition_client',session_id='a'*32,
                    client_token='token',presentation_protocol='self_paced_axes_v2',task_guidance_version='gaze_navigation_v2'))
                event('hold_start','LEFT');capture_started=True
                record_path=hub.recorder.journal.path
            if i==35:
                history=Replicator(old,enrollment,backfill_only=True).start()
            if capture_started and i%6==0:
                event('heartbeat','LEFT')
            received.append((runtime.gap_count,hub.epoch,runtime.fresh()))
            if i and i%1000==0:
                print(json.dumps(dict(progress_blocks=i,elapsed_s=round(time.monotonic()-started,1),
                                      gaps=runtime.gap_count-baseline,durable_seq=rep.snapshot()['durable_seq'])),flush=True)
            time.sleep(max(0.,.032-(time.monotonic()-cycle)))
        steady_gaps=runtime.gap_count-baseline
        assert runtime.descriptor['stream_id']==d['stream_id']
        assert steady_gaps==0, runtime.last_gap
        assert len(hub.raw)==1250
        assert hub.acquisition.snapshot()['active']
        # A real transport gap: stop sending >250ms, then resume through a new
        # connection. Requested capture must persist and motion stay disabled.
        if rep.socket is not None:
            rep.socket.shutdown(socket.SHUT_RDWR)
        time.sleep(.4)
        assert hub.acquisition.snapshot()['active']
        assert hub.acquisition.snapshot()['input_paused']
        for i in range(100):
            send();time.sleep(.032)
            if i%6==0:event('heartbeat','LEFT')
        assert runtime.fresh(), rep.snapshot()
        assert hub.acquisition.snapshot()['active']
        assert hub.recorder.journal.path==record_path
        event('hold_end','LEFT')
        hub.acquisition.stop(reason='completed')
        assert (record_path/'closed.json').exists()
        deadline=time.monotonic()+5
        while rep.snapshot()['durable_seq']<seq and time.monotonic()<deadline:time.sleep(.02)
        assert rep.snapshot()['durable_seq']==seq, rep.snapshot()
        stored=runtime.service.journals[(agent,d['stream_id'])]
        assert len(stored.records)==len(live.records)
        for n in live.sequences:
            assert stored.records[n]['header']['sha256']==live.records[n]['header']['sha256']
        from gnautilus_ml.data import read_native
        native=read_native(record_path)
        assert native['intervals'] and all(r['label']=='LEFT' for r in native['intervals'])
        report=dict(software_only=True,elapsed_s=round(time.monotonic()-started,2),columns=37,eeg=32,
                    sampling_rate=250,blocks=seq,samples=seq*8,steady_gap_count=steady_gaps,
                    total_gaps=runtime.gap_count,history_blocks=96,recording_closed=True,
                    durable_seq=rep.snapshot()['durable_seq'],labels=len(native['intervals']))
        (tmp_path/'soak-report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report))
    finally:
        rep.stop()
        if history:history.stop()
        runtime.close();live.close();old.close()
