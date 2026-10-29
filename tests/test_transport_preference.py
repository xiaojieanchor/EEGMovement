"""Route display is derived from the actual authenticated data path."""
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hub.vr import VrLink, encode_packet, decode_packet
from hub.public_relay import HubRelay


def test_signed_udp_probe_does_not_replace_active_peer_or_change_control():
    provider=Mock(side_effect=AssertionError('Reachability probe must not evaluate actions'))
    link=VrLink(Path('.'),provider,lambda *args:None)
    key=bytes.fromhex('ab'*32);link.register_lan('a'*32,key)
    link.clients['b'*32]={'identity':'a'*32,'nonce':'c'*32,'last_seen':time.monotonic(),'address':('192.168.0.2',4444)}
    generation=link.control_generation
    request=encode_packet(dict(version=1,kind='route_probe',nonce='d'*32),key)
    reply=decode_packet(link.process_datagram(request,('192.168.0.2',4567)),key)
    assert reply==dict(version=1,kind='route_probe_reply',nonce='d'*32,session=link.session)
    assert link.clients['b'*32]['nonce']=='c'*32
    assert link.control_generation==generation
    provider.assert_not_called()
    with pytest.raises(ValueError):link.process_datagram(encode_packet(dict(version=1,kind='route_probe',nonce='d'*32),b'0'*32),('192.168.0.3',4568))


@pytest.mark.parametrize('address,expected',[(('127.0.0.1',1234),'loopback'),(('192.168.0.2',1234),'lan'),(('relay:'+'a'*32,0),'public_relay')])
def test_receiver_route_is_observed_not_reported_by_untrusted_telemetry(address,expected):
    link=VrLink(Path('.'),lambda:None,lambda *args:None)
    readiness=dict(epoch=1,source_valid=False,server_fresh=False,reason='fixture')
    packet=dict(version=1,kind='heartbeat',receiver_id='b'*32,nonce='c'*32,ticket=1,role='unity',allow_test=False,telemetry=dict(state='STOP',mode='LIVE',transport='forged'))
    link.heartbeat(packet,address,readiness)
    peer=link.snapshot()['clients'][0]
    assert peer['transport']==expected
    assert ('公网' if expected=='public_relay' else '内网') in peer['transport_label']


def test_backup_cloud_reconnect_does_not_disarm_healthy_lan_receiver():
    control=Mock();runtime=SimpleNamespace(active_transport='lan_ws',source_lost=Mock())
    hub=SimpleNamespace(vr=SimpleNamespace(snapshot=lambda:dict(clients=[dict(online=True,role='unity',transport='lan')])),intent_control=SimpleNamespace(configure=control),gnautilus_runtime=runtime,event=Mock())
    relay=HubRelay(hub,dict(peers={}))
    relay.connection_state(dict(connected=False))
    control.assert_not_called();runtime.source_lost.assert_not_called()
    hub.vr.snapshot=lambda:dict(clients=[dict(online=True,role='unity',transport='public_relay')])
    relay.connection_state(dict(connected=False))
    control.assert_called_once_with({'enabled':False})


def test_lan_pairing_hints_contain_no_keys_or_admin_endpoint(tmp_path):
    from hub.quest import QuestService
    hub=SimpleNamespace(vr=SimpleNamespace(port=19771,session='a'*32))
    result=QuestService(hub,tmp_path).dispatch('b'*32,'GET','/v1/pairing',b'')
    assert result['prefer_lan'] is True and result['lan_http_port']==8766
    assert 'key' not in result and 'secret' not in result
    assert all(not host.startswith('127.') for host in result['lan_hosts'])
