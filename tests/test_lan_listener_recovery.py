"""Fixed LAN port remains discoverable after transient startup contention."""
import socket
import time
from types import SimpleNamespace
from unittest.mock import patch

from hub.lan_ws import LanHubServer


def test_heartbeat_cannot_recreate_closed_capture_session():
    from unittest.mock import Mock
    from hub.gnautilus_runtime import GnautilusRuntime
    acquisition = SimpleNamespace(snapshot=lambda: dict(active=False, session_id='a' * 32, phase='aborted'),
                                  start_manual=Mock(), manual_event=Mock())
    runtime = object.__new__(GnautilusRuntime)
    runtime.hub = SimpleNamespace(acquisition=acquisition)
    runtime.service = SimpleNamespace(selected_agent='b' * 32)
    result = runtime.client_capture_event('b' * 32, dict(op='event', session_id='a' * 32,
        session_info=dict(session_id='a' * 32, client_token='token'),
        event=dict(session_id='a' * 32, seq=10, phase='heartbeat', label=None)))
    acquisition.start_manual.assert_not_called()
    acquisition.manual_event.assert_not_called()
    assert result['ok'] is False and result['session_closed'] is True


def test_closed_capture_ack_clears_misleading_hub_synced_state(tmp_path):
    from gnautilus_acquisition.client import Collector
    collector = Collector({}, {}, tmp_path)
    collector.capture.update(active=True, hub_synced=True)
    collector._on_capture_ack(dict(ok=False, session_closed=True, error='session closed'))
    assert collector.capture_snapshot()['hub_synced'] is False


def test_busy_port_is_reported_and_retried_without_random_fallback():
    blocker = socket.socket()
    blocker.bind(('127.0.0.1', 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    service = LanHubServer(SimpleNamespace(), host='127.0.0.1', port=port)
    try:
        service.start()
        assert service.server is None, 'An occupied fixed port must not silently become a random one'
        assert service.port == port
        status = service.snapshot()
        assert not status['listening'] and status['error']
        blocker.close()
        deadline = time.monotonic() + 4
        while service.server is None and time.monotonic() < deadline:
            time.sleep(.03)
        assert service.server is not None
        assert service.server.socket.getsockname()[1] == port
        assert service.snapshot()['listening'] and service.snapshot()['error'] is None
    finally:
        blocker.close()
        service.close()


def test_explicit_ephemeral_test_port_still_works():
    service = LanHubServer(SimpleNamespace(), host='127.0.0.1', port=0)
    try:
        service.start()
        assert service.port > 0 and service.server is not None
    finally:
        service.close()
