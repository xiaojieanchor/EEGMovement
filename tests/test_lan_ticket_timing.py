"""Elapsed-ticket bounds remove idle wait, never the true sample age."""
from unittest.mock import patch

from gnautilus_acquisition.lan_client import LanWsReplicator
from hub import lan_ws


def lan_source_mark(*args):
    assert hasattr(lan_ws, 'lan_source_mark'), 'LAN freshness must account for elapsed ticket wait'
    return lan_ws.lan_source_mark(*args)


def test_fresh_read_after_old_ticket_retains_conservative_network_bound():
    mark = lan_source_mark(999.7, 1000., 10., 280.)
    assert abs(mark - 999.968) < 1e-9
    assert mark <= 1000. - .010


def test_old_samples_and_impossible_elapsed_time_fail_closed():
    assert lan_source_mark(999.7, 1000., 800., 280.) < 999.2
    assert lan_source_mark(999.7, 1000., 10., 400.) is None
    for elapsed in (-1., float('nan'), float('inf'), True):
        assert lan_source_mark(999.7, 1000., 10., elapsed) is None
    assert lan_source_mark(None, 1000., 10., 0.) is None
    assert lan_source_mark(999.7, 1000., 10.) == 999.69


def test_sender_uses_matching_ticket_receipt_and_same_timestamp_for_ages(tmp_path):
    from gnautilus_acquisition.storage import StreamJournal
    from tests.test_gnautilus_transport import descriptor
    journal = StreamJournal(tmp_path, descriptor('gtec_live'))
    rep = LanWsReplicator(journal, dict(agent_id='c' * 32))
    rep.ticket = 'new-ticket'
    rep.ticket_received_ns = 720_000_000
    try:
        assert hasattr(rep, '_wire_block'), 'Sender must carry paired ticket elapsed time'
        with patch('gnautilus_acquisition.lan_client.time.monotonic_ns', return_value=1_000_000_000):
            h = rep._wire_block(dict(read_monotonic_ns=990_000_000), 'live')
        assert h['ticket'] == 'new-ticket'
        assert h['ticket_elapsed_ms'] == 280.
        assert h['source_age_ms'] == 10.
    finally:
        journal.close()
