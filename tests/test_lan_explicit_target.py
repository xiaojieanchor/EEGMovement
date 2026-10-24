"""An operator's explicit hotspot target cannot be replaced by VPN discovery."""
from unittest.mock import patch
from gnautilus_acquisition.lan_client import build_lan_enrollment


def test_explicit_hotspot_does_not_discover_or_switch_to_another_interface():
    with patch('gnautilus_acquisition.lan_client.discover_lan_hub',return_value=dict(host='198.18.0.1',port=8768)) as discover:
        e=build_lan_enrollment('192.168.137.1:8768',agent_id='a'*32)
    assert e['host']=='192.168.137.1' and e['port']==8768
    assert not e['discovered'];discover.assert_not_called()


def test_empty_target_still_supports_automatic_lan_discovery():
    with patch('gnautilus_acquisition.lan_client.discover_lan_hub',return_value=dict(host='192.168.137.1',port=9000)):
        e=build_lan_enrollment('',agent_id='a'*32)
    assert e['host']=='192.168.137.1' and e['port']==9000 and e['discovered']


def test_status_and_stream_reconnect_preserve_explicit_target():
    from types import SimpleNamespace
    from gnautilus_acquisition.lan_client import LanStatusLink,LanWsReplicator
    config=dict(host_hint='192.168.137.1:8768',host='192.168.137.1',port=8768,
                url='ws://192.168.137.1:8768/eeg/v1/lan',agent_id='a'*32)
    status=LanStatusLink(config)
    stream=LanWsReplicator(SimpleNamespace(),config)
    with patch('gnautilus_acquisition.lan_client.discover_lan_hub',return_value=dict(host='198.18.0.1',port=8768)) as discover:
        assert status._resolve_url()==config['url']
        assert stream._resolve_url()==config['url']
        discover.assert_not_called()


def test_explicit_probe_connects_with_broken_system_proxy(tmp_path):
    import os
    from tests.test_gnautilus_mode_recovery import capture_fixture
    from gnautilus_acquisition.lan_client import build_lan_enrollment,probe_lan
    hub,runtime=capture_fixture(tmp_path)
    runtime.lan_ws.requested_port=runtime.lan_ws.port=0
    runtime.start()
    target=f'127.0.0.1:{runtime.lan_ws.port}'
    try:
        proxy=dict(http_proxy='http://127.0.0.1:1',https_proxy='http://127.0.0.1:1',all_proxy='http://127.0.0.1:1',
                   HTTP_PROXY='http://127.0.0.1:1',HTTPS_PROXY='http://127.0.0.1:1',ALL_PROXY='http://127.0.0.1:1',NO_PROXY='',no_proxy='')
        with patch.dict(os.environ,proxy):
            config=build_lan_enrollment(target,agent_id='a'*32)
            assert probe_lan(config)['connected']
    finally:runtime.close()
