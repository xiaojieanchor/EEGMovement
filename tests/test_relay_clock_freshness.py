"""Authenticated host clock intervals tighten freshness without renewing old data."""
import time
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from hub.public_relay import HubRelay
from hub.gnautilus import IngestService
from gnautilus_acquisition.contract import block_frame
from tests.test_gnautilus_transport import descriptor


def relay_with_clock():
    relay=HubRelay(SimpleNamespace(),dict(peers={}))
    relay.clock_maps['a'*32]=dict(offset_ms=0.,uncertainty_ms=10.,measured=999950.)
    return relay


def test_old_ticket_does_not_charge_its_roundtrip_to_a_new_host_read(tmp_path):
    seen=[];service=IngestService(tmp_path,lambda *args:seen.append(args));relay=relay_with_clock()
    d=descriptor('gtec_live');block,raw=block_frame(d,1,0,np.zeros((8,32),np.float32),read_monotonic_ns=999980000000)
    try:
        with patch('hub.public_relay.time.monotonic',return_value=1000.):
            assert not service.ingest_external('a'*32,d,block,raw,999.7)['live_accepted']
            mark=relay.source_mark('a'*32,block,999.7)
            assert abs(mark-999.96)<1e-9
            assert service.ingest_external('a'*32,d,block,raw,mark)['live_accepted']
            assert not service.ingest_external('a'*32,d,block,raw,mark)['live_accepted']
        assert len(seen)==1
    finally:service.close()


def test_expired_sample_remains_expired_with_a_valid_clock(tmp_path):
    seen=[];service=IngestService(tmp_path,lambda *args:seen.append(args));relay=relay_with_clock()
    d=descriptor('gtec_live');block,raw=block_frame(d,1,0,np.zeros((8,32),np.float32),read_monotonic_ns=999600000000)
    try:
        with patch('hub.public_relay.time.monotonic',return_value=1000.):
            mark=relay.source_mark('a'*32,block,999.7)
            assert not service.ingest_external('a'*32,d,block,raw,mark)['live_accepted']
        assert not seen
    finally:service.close()


def test_ticket_and_recent_clock_are_required_and_future_bounds_fail_closed():
    relay=relay_with_clock();block=dict(read_monotonic_ns=999980000000)
    with patch('hub.public_relay.time.monotonic',return_value=1000.):
        assert relay.source_mark('a'*32,block,None) is None
        relay.clock_maps['a'*32]['measured']=980000.
        assert relay.source_mark('a'*32,block,999.7)==999.7
        relay.clock_maps['a'*32].update(measured=999950.,offset_ms=1000.)
        assert relay.source_mark('a'*32,block,999.7)==999.7
        assert relay.source_mark('b'*32,block,999.7)==999.7


def test_clock_uncertainty_is_subtracted_not_presented_as_extra_freshness():
    relay=relay_with_clock();relay.clock_maps['a'*32]['uncertainty_ms']=240.
    with patch('hub.public_relay.time.monotonic',return_value=1000.):
        assert relay.source_mark('a'*32,dict(read_monotonic_ns=999980000000),999.7)<=999.73
