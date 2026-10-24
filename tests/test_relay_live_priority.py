"""Synthetic lane contention and replay ordering; no production device or cloud."""
import threading,time
from types import SimpleNamespace
import numpy as np
import pytest
from gnautilus_acquisition.contract import block_frame
from gnautilus_acquisition.storage import StreamJournal
from gnautilus_acquisition.relay_client import RelayReplicator
from tests.test_gnautilus_transport import descriptor
from hub.gnautilus import IngestService


def test_fresh_live_copy_can_publish_once_after_backfill_won_the_disk_race(tmp_path):
    blocks=[];service=IngestService(tmp_path,lambda *a:blocks.append(a));d=descriptor('gtec_live')
    h,raw=block_frame(d,1,0,np.ones((8,32),np.float32));h['mode']='backfill'
    assert not service.ingest_external('c'*32,d,h,raw,None)['live_accepted']
    live=dict(h,mode='live');result=service.ingest_external('c'*32,d,live,raw,time.monotonic())
    assert result['duplicate'] and result['live_accepted'] and len(blocks)==1
    assert not service.ingest_external('c'*32,d,live,raw,time.monotonic())['live_accepted']
    assert len(blocks)==1
    h2,raw2=block_frame(d,2,8,np.ones((8,32),np.float32));h2['mode']='backfill'
    service.ingest_external('c'*32,d,h2,raw2,None)
    assert not service.ingest_external('c'*32,d,dict(h2,mode='live'),raw2,time.monotonic()-1)['live_accepted']
    assert len(blocks)==1 # Disk promotion never renews an expired input timestamp.
    changed=dict(live,read_monotonic_ns=live['read_monotonic_ns']+1)
    with pytest.raises(ValueError,match='Duplicate block changed'):
        service.ingest_external('c'*32,d,changed,raw,time.monotonic())
    service.close()


def make_rep(tmp_path):
    journal=StreamJournal(tmp_path,descriptor('gtec_live'))
    for i in range(3):
        h,b=block_frame(journal.descriptor,i+1,i*8,np.ones((8,32),np.float32));journal.append(h,b)
    rep=RelayReplicator(journal,dict(workspace='a'*32,device_id='b'*32,hub_id='c'*32,secret='ef'*32))
    rep.ticket='ticket';return rep,journal


def test_live_sends_oldest_fresh_unsent_block_in_order(tmp_path):
    rep,journal=make_rep(tmp_path);sent=[]
    rep.endpoint=SimpleNamespace(send=lambda *a,**kw:sent.append(kw),snapshot=lambda:{'connected':True})
    rep._send_next_live();rep._send_next_live();rep._send_next_live()
    assert [v['block_seq'] for v in sent]==[1,2,3] and all(v['lane']=='data' for v in sent)
    journal.close()


def test_blocked_backfill_cannot_stall_live_lane(tmp_path):
    rep,journal=make_rep(tmp_path);entered=threading.Event();release=threading.Event();live=threading.Event()
    # Keep one explicitly old item for backfill; newer blocks remain live eligible.
    journal.records[1]['header']['read_monotonic_ns']=time.monotonic_ns()-10**9
    def send(*args,**kw):
        if kw['lane']=='backfill':entered.set();release.wait(2)
        else:live.set()
    rep.endpoint=SimpleNamespace(send=send,snapshot=lambda:{'connected':True})
    t=threading.Thread(target=rep._send_next_backfill);t.start()
    try:
        assert entered.wait(.5);rep._send_next_live();assert live.wait(.05)
        assert t.is_alive()
    finally:release.set();t.join(2);journal.close()


def test_quick_cloud_ack_does_not_reintroduce_an_inflight_slot(tmp_path):
    rep,journal=make_rep(tmp_path)
    def send(*a,**kw):rep.inflight.pop(kw['block_seq'],None)
    rep.endpoint=SimpleNamespace(send=send,snapshot=lambda:{'connected':True})
    rep._send_next_live();assert not rep.inflight
    journal.close()
