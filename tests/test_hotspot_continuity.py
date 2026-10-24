"""Synthetic hotspot scheduling and independent-transport regressions."""
import collections
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from eeg_relay.protocol import pack, unpack
from gnautilus_acquisition.contract import block_frame
from gnautilus_acquisition.lan_client import LanWsReplicator
from gnautilus_acquisition.storage import StreamJournal
from hub.public_relay import HubRelay
from tests.test_gnautilus_transport import descriptor


def test_burst_is_ordered_and_pipelined_before_ack(tmp_path):
    journal = StreamJournal(tmp_path, descriptor('gtec_live'))
    for seq in range(1, 7):
        h, raw = block_frame(journal.descriptor, seq, (seq - 1) * 8, np.ones((8, 32), np.float32))
        journal.append(h, raw)
    replies = queue.Queue()
    sent = []
    all_sent = threading.Event()

    class Socket:
        def send(self, frame):
            h, _ = unpack(frame)
            kind = h['kind']
            if kind == 'descriptor':
                replies.put(pack(dict(kind='descriptor_ok', durable_seq=0, ticket='t')))
            elif kind == 'status':
                replies.put(pack(dict(kind='status_ok', ticket='t')))
            elif kind == 'block':
                sent.append(h['block'])
                if len(sent) == 6:
                    for b in sent:
                        replies.put(pack(dict(kind='ack', ack_seq=b['seq'], durable_seq=b['seq'], live_accepted=True, ticket='t')))
                    all_sent.set()
            elif kind == 'close':
                replies.put(pack(dict(kind='closed')))
        def recv(self, timeout=None):
            try:
                return replies.get(timeout=min(timeout or .1, .1))
            except queue.Empty:
                raise TimeoutError()
        def close(self):
            pass

    rep = LanWsReplicator(journal, dict(agent_id='c' * 32, host='127.0.0.1', port=8768))
    rep._resolve_url = lambda: 'ws://127.0.0.1:8768/eeg/v1/lan'
    with patch('gnautilus_acquisition.lan_client.connect', return_value=Socket()):
        rep.start()
        try:
            assert all_sent.wait(1), 'sender waited for an ACK or skipped intermediate fresh blocks'
            assert [b['seq'] for b in sent] == list(range(1, 7))
            assert all(b['mode'] == 'live' for b in sent)
        finally:
            rep.stop()
            journal.close()


def test_public_reconnect_does_not_reset_active_hotspot_capture():
    runtime = SimpleNamespace(active_transport='lan_ws', source_lost=Mock())
    hub = SimpleNamespace(gnautilus_runtime=runtime, intent_control=SimpleNamespace(configure=Mock()), event=Mock())
    relay = HubRelay(hub, dict(peers={}))
    relay.connection_state(dict(connected=False, error='public unavailable'))
    relay.connection_state(dict(connected=True, error=None))
    runtime.source_lost.assert_not_called()


def test_public_source_still_resets_when_its_connection_changes():
    runtime = SimpleNamespace(active_transport='public_relay', source_lost=Mock())
    hub = SimpleNamespace(gnautilus_runtime=runtime, intent_control=SimpleNamespace(configure=Mock()), event=Mock())
    relay = HubRelay(hub, dict(peers={}))
    relay.connection_state(dict(connected=False))
    runtime.source_lost.assert_called_once_with('public_relay_disconnected')


def test_pending_window_is_bounded_and_history_never_becomes_live(tmp_path):
    journal = StreamJournal(tmp_path, descriptor('gtec_live'))
    for seq in range(1, 13):
        h, raw = block_frame(journal.descriptor, seq, (seq - 1) * 8, np.ones((8, 32), np.float32))
        if seq == 1:
            h['read_monotonic_ns'] -= 2_000_000_000
        journal.append(h, raw)
    rep = LanWsReplicator(journal, dict(agent_id='c' * 32))
    rep.ticket = 't'
    rep.pending = {}
    rep.live_scan_seq = 0
    try:
        sent = [rep._next_block() for _ in range(8)]
        assert [h['seq'] for h, _, mode in sent] == list(range(2, 10))
        assert all(mode == 'live' for _, _, mode in sent)
        assert rep._next_block() is None
        assert len(rep.pending) == 8
        rep.pending.clear()
        sent = [rep._next_block() for _ in range(3)]
        assert [(h['seq'], mode) for h, _, mode in sent] == [(10, 'live'), (11, 'live'), (12, 'live')]
        rep.pending.clear()
        h, _, mode = rep._next_block()
        assert (h['seq'], mode) == (1, 'backfill')
    finally:
        journal.close()
