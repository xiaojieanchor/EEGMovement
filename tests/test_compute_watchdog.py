import threading,time
from unittest.mock import Mock
import pytest


def test_channel_open_failure_updates_worker_state_and_keeps_reason(tmp_path):
    from gnautilus_ml.ssh import ComputeBridge
    from gnautilus_ml.common import atomic
    atomic(tmp_path/'configs/gnautilus_compute.json',dict(host='fixture',port=5522,service_port=18871))
    b=ComputeBridge(tmp_path);b.client=Mock();b.token='fixture';b.status.update(ssh_connected=True,worker_connected=True)
    b.client.get_transport().is_active.return_value=True;b.client.get_transport().open_channel.side_effect=TimeoutError('forwarding timeout fixture')
    with pytest.raises(TimeoutError):b.call('GET','/health')
    assert b.status['worker_connected'] is False and b.status['last_error']=='forwarding timeout fixture'


def test_http_progress_watchdog_reports_stall_and_does_not_kill_a_healthy_loop(tmp_path):
    from gnautilus_ml.watchdog import ProgressWatchdog
    recovered=threading.Event();w=ProgressWatchdog(tmp_path,limit=.12,interval=.01,terminate=lambda code:recovered.set())
    w.start()
    for _ in range(6):time.sleep(.03);w.pulse()
    assert not recovered.is_set()
    assert recovered.wait(1)
    w.close();assert len(list(tmp_path.glob('service-stall-*.log')))==1


def test_watchdog_close_prevents_shutdown_from_becoming_a_stall(tmp_path):
    from gnautilus_ml.watchdog import ProgressWatchdog
    terminated=Mock();w=ProgressWatchdog(tmp_path,limit=.05,interval=.01,terminate=terminated);w.start();w.close();time.sleep(.08)
    terminated.assert_not_called()
