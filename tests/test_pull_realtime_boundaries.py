"""Update regressions: persisted history cannot acquire a live timestamp."""
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from eeg_relay.protocol import pack
from gnautilus_acquisition.contract import block_frame
from hub.gnautilus import IngestService
from hub.lan_ws import LanHubServer
from tests.test_gnautilus_transport import descriptor


def test_backfill_hole_is_not_replayed_as_live(tmp_path):
    seen = []
    service = IngestService(tmp_path, lambda d, h, x: seen.append(h))
    d = descriptor('gtec_live')
    try:
        for seq in (1, 2, 3):
            h, raw = block_frame(d, seq, (seq - 1) * 8, np.ones((8, 32), np.float32))
            history = seq == 2
            h['mode'] = 'backfill' if history else 'live'
            service.ingest_external('c' * 32, d, h, raw, None if history else time.monotonic())
        assert [h['seq'] for h in seen] == [1, 3]
    finally:
        service.close()


def test_lan_ticket_is_a_conservative_age_bound():
    captured = []
    service = SimpleNamespace(selected_agent='c' * 32, lock=__import__('threading').RLock())
    service.ingest_external = lambda agent, d, b, raw, mark: captured.append(mark) or dict(durable_seq=1, duplicate=False, live_accepted=False)
    runtime = SimpleNamespace(service=service, feedback=lambda: {})
    server = LanHubServer(runtime, port=-1)
    server.tickets[('c' * 32, 'ticket')] = 100.0
    d = descriptor('gtec_live')
    h, raw = block_frame(d, 1, 0, np.ones((8, 32), np.float32))
    h.update(mode='live', ticket='ticket', source_age_ms=1000.0)

    class Socket:
        def __iter__(self):
            return iter([pack(dict(kind='block', agent_id='c' * 32, descriptor=d, block=h), raw)])
        def send(self, frame):
            pass

    with patch('hub.lan_ws.time.monotonic', return_value=100.1):
        server._handle_ws(Socket())
    assert captured == [99.0]
