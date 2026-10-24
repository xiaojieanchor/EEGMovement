"""Identity/NAT transport regressions; synthetic packets, no device or model."""
import copy
import json
import secrets
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hub.quest import QuestService, execution_key, signature
from hub.vr import VrLink, decode_packet, encode_packet


class NatIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ready = dict(epoch=1, source_valid=False, server_fresh=False,
                          source_fresh=False, reason='model_not_configured')
        self.link = VrLink(Path(self.temp.name), lambda: self.ready, lambda *a: None)
        self.hub = SimpleNamespace(vr=self.link)
        self.service = QuestService(self.hub, self.temp.name)
        self.secret = secrets.token_bytes(32)
        self.device = 'a' * 32
        self.service.register(dict(device_id=self.device, address='192.168.0.4',
                                   secret=self.secret.hex()))

    def packet(self, rid='b' * 32, nonce='c' * 32, ticket=1, session=''):
        return dict(version=1, kind='heartbeat', receiver_id=rid, nonce=nonce,
                    session=session, ticket=ticket, role='unity', allow_test=False,
                    telemetry=dict(state='STOP', mode='LIVE', applied_seq=0))

    def exchange(self, packet, address=('10.0.0.9', 42000), secret=None):
        key = execution_key(secret or self.secret)
        response = self.link.process_datagram(encode_packet(packet, key), address, self.ready)
        return decode_packet(response, key) if response is not None else None

    def test_http_identity_survives_nat_without_weakening_signature(self):
        boot = 'd' * 32
        headers = {'X-EEG-Device': self.device, 'X-EEG-Boot': boot, 'X-EEG-Seq': '1',
                   'X-EEG-MAC': signature(self.secret, 'GET', '/v1/pairing',
                                          self.device, boot, 1, b'')}
        result = self.service.authenticate('GET', '/v1/pairing', headers, b'', '10.0.0.9')
        self.assertEqual(result[0], self.device)
        self.assertEqual(self.service.devices[self.device]['observed_address'], '10.0.0.9')
        with self.assertRaises(ValueError):
            self.service.authenticate('GET', '/v1/pairing', headers, b'', '10.0.0.10')

    def test_two_devices_behind_same_nat_have_independent_keys_and_routes(self):
        other = secrets.token_bytes(32)
        self.service.register(dict(device_id='e' * 32, address='192.168.0.5', secret=other.hex()))
        a = self.exchange(self.packet())
        b = self.exchange(self.packet('f' * 32), ('10.0.0.9', 42001), other)
        self.assertEqual(a['kind'], 'welcome')
        self.assertEqual(b['kind'], 'welcome')
        self.assertEqual(self.link.clients['b' * 32]['identity'], self.device)
        self.assertEqual(self.link.clients['f' * 32]['identity'], 'e' * 32)

    def test_foreign_paired_device_cannot_claim_an_existing_receiver(self):
        self.exchange(self.packet())
        other = secrets.token_bytes(32)
        self.service.register(dict(device_id='e' * 32, address='192.168.0.5', secret=other.hex()))
        with self.assertRaises(ValueError):
            self.exchange(self.packet(nonce='f' * 32), secret=other)
        self.assertEqual(self.link.clients['b' * 32]['identity'], self.device)

    def test_enrolled_identity_cannot_create_two_simultaneous_receivers(self):
        self.exchange(self.packet())
        with self.assertRaises(ValueError):
            self.exchange(self.packet(rid='8' * 32), ('10.0.0.9', 42001))

    def test_identical_keys_cannot_ambiguously_identify_two_devices(self):
        with self.assertRaises(ValueError):
            self.link.register_lan('e' * 32, execution_key(self.secret))

    def test_changed_nat_mapping_stops_then_requires_new_nonce(self):
        p = self.packet()
        welcome = self.exchange(p)
        p.update(session=welcome['session'], ticket=2)
        command = self.exchange(p)
        self.assertFalse(command['active'])
        before = self.link.clients[p['receiver_id']]['last_seen']
        p['ticket'] = 3
        changed = ('10.0.0.9', 43000)
        response = self.exchange(p, changed)
        self.assertEqual(response['kind'], 'rebind')
        self.assertEqual(before, self.link.clients[p['receiver_id']]['last_seen'])
        self.assertFalse(self.link.clients[p['receiver_id']]['command']['active'])
        p.update(nonce='9' * 32, session='', ticket=4)
        self.assertEqual(self.exchange(p, changed)['kind'], 'welcome')
        self.assertEqual(self.link.clients[p['receiver_id']]['address'], changed)

    def test_retired_nonce_replay_cannot_replace_new_handshake(self):
        old = self.packet()
        self.exchange(old)
        newer = self.packet(nonce='9' * 32)
        self.exchange(newer)
        with self.assertRaises(ValueError):
            self.exchange(old)
        self.assertEqual(self.link.clients[old['receiver_id']]['nonce'], newer['nonce'])

    def test_duplicate_datagram_does_not_renew_freshness(self):
        p = self.packet()
        self.exchange(p)
        before = self.link.clients[p['receiver_id']]['last_seen']
        self.assertIsNone(self.exchange(p))
        self.assertEqual(before, self.link.clients[p['receiver_id']]['last_seen'])

    def test_wrong_key_cannot_poison_receiver_or_renew_freshness(self):
        p=self.packet();self.exchange(p)
        before=copy.deepcopy(self.link.clients[p['receiver_id']])
        with self.assertRaises(ValueError):
            self.exchange(self.packet(nonce='9'*32,ticket=1000),secret=secrets.token_bytes(32))
        self.assertEqual(before,self.link.clients[p['receiver_id']])

    def test_credential_rotation_revokes_old_udp_identity(self):
        p = self.packet()
        self.exchange(p)
        other = secrets.token_bytes(32)
        self.service.register(dict(device_id=self.device, address='192.168.0.4', secret=other.hex()))
        self.assertNotIn(p['receiver_id'], self.link.clients)
        with self.assertRaises(ValueError):
            self.exchange(p)

    def test_enrollment_address_change_keeps_identity_key(self):
        self.service.register(dict(device_id=self.device, address='192.168.5.4', secret=self.secret.hex()))
        self.assertEqual(self.exchange(self.packet())['kind'], 'welcome')

    def test_dynamic_status_preserves_all_columns_without_private_fields(self):
        channels = [dict(name='CH%02d' % (i+1), kind='EEG', unit='uV',
                         quality_label='未测量', training_eligible=False,
                         raw='PRIVATE') for i in range(32)]
        channels += [dict(name='COUNTER', kind='COUNTER', unit='count')]
        self.hub.snapshot = lambda: dict(source=dict(device='g.Nautilus', fresh=True,
            sfreq_measured=511.8, nominal_sfreq=512), quality=dict(channels=channels,
            valid=False, state='contact_unknown', good_channels=None), server={},
            gnautilus=dict(password='PRIVATE'))
        result = self.service.dispatch(self.device, 'GET', '/v1/status', b'')
        self.assertEqual(len(result['source']['channels']), 33)
        self.assertEqual(result['source']['eeg_channel_count'], 32)
        self.assertEqual(result['source']['nominal_rate'], 512)
        self.assertFalse(result['source']['contact_quality_known'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_udp_key_registry_is_bounded_by_four_enrolled_identities(self):
        for char in '234':
            self.link.register_lan(char * 32, secrets.token_bytes(32))
        with self.assertRaises(ValueError):
            self.link.register_lan('5' * 32, secrets.token_bytes(32))

    def test_removed_enrollment_revokes_udp_and_http_identity(self):
        self.exchange(self.packet())
        self.service.reload_enrollments([])
        self.assertFalse(self.link.clients)
        self.assertFalse(self.link.lan_keys)
        self.assertFalse(self.service.devices)
        with self.assertRaises(ValueError):
            self.exchange(self.packet())

    def test_invalid_reload_is_atomic_and_keeps_current_pairing(self):
        with self.assertRaises(ValueError):
            self.service.reload_enrollments([dict(device_id='e' * 32,
                address='0.0.0.0', secret=self.secret.hex())])
        self.assertIn(self.device, self.service.devices)
        self.assertEqual(self.exchange(self.packet())['kind'], 'welcome')

    def test_actual_udp_replies_to_observed_port_and_signed_rebind_handshakes(self):
        self.link.port=0;self.link.start();self.addCleanup(self.link.close)
        endpoint=('127.0.0.1',self.link.port);key=execution_key(self.secret)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as first, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as changed:
            first.bind(('127.0.0.1',0));changed.bind(('127.0.0.1',0))
            first.settimeout(2);changed.settimeout(2)
            p=self.packet();first.sendto(encode_packet(p,key),endpoint)
            raw,address=first.recvfrom(4096);welcome=decode_packet(raw,key)
            self.assertEqual(address[1],self.link.port);self.assertEqual(welcome['kind'],'welcome')
            p.update(ticket=2,session=welcome['session']);changed.sendto(encode_packet(p,key),endpoint)
            rebind=decode_packet(changed.recvfrom(4096)[0],key)
            self.assertEqual(rebind['kind'],'rebind')
            p.update(nonce='9'*32,ticket=3,session='');changed.sendto(encode_packet(p,key),endpoint)
            self.assertEqual(decode_packet(changed.recvfrom(4096)[0],key)['kind'],'welcome')


if __name__ == '__main__':
    unittest.main()
