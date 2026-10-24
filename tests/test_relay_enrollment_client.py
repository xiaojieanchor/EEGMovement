"""Automatic participant enrollment, using simulated HTTPS and real crypto."""
import base64
import io
import json
import ssl
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from eeg_relay import enrollment_client as enrollment


CONFIG = {'base_url': 'https://relay.example/eeg/', 'ca_pem': 'fixture CA', 'proxy_mode': 'direct'}
IDENTITY = dict(schema='gnautilus.enrollment.v2', mode='relay', role='eeg',
                device_id='a'*32, hub_id='b'*32, workspace='c'*32,
                secret='d'*64, auth_secret='e'*64,
                url='wss://relay.example/eeg/v1/ws', ca_pem='fixture CA')


class Response(io.BytesIO):
    def __init__(self, value, url):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.url = url
    def geturl(self): return self.url


class SimulatedHTTPS:
    def __init__(self, occupied=False, waiting=False, mutate=None):
        self.occupied, self.waiting, self.mutate = occupied, waiting, mutate
        self.requests = []
        self.handlers = []
    def add_handler(self, handler): self.handlers.append(handler)
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        payload = json.loads(base64.b64decode(request.get_header('X-eeg-request')))
        if request.full_url.endswith('/start'):
            self.start = payload
            if self.occupied:
                raise urllib.error.HTTPError(request.full_url, 409, 'occupied', {},
                    Response({'state':'role_occupied', 'message':'other EEG is online'}, request.full_url))
            value = {'state':'waiting_hub', 'request_id':payload['request_id']}
        elif self.waiting:
            value = {'state':'waiting_hub'}
        else:
            public = serialization.load_pem_public_key(self.start['public_key'].encode())
            key = b'k'*32
            nonce = b'n'*12
            plain = dict(IDENTITY)
            if self.mutate: self.mutate(plain)
            ciphertext = AESGCM(key).encrypt(nonce, json.dumps(plain).encode(), payload['request_id'].encode())
            wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA256(), label=None))
            value = {'state':'ready', 'sealed':{k:base64.b64encode(v).decode() for k,v in
                     [('key',wrapped), ('nonce',nonce), ('ciphertext',ciphertext)]}}
        return Response(value, request.full_url)


class EnrollmentClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = enrollment.EnrollmentClient(CONFIG, self.root)
    def tearDown(self): self.tmp.cleanup()
    def run_client(self, transport, **kwargs):
        with patch.object(enrollment, 'opener', return_value=transport), patch.object(enrollment, 'paths', return_value=[('direct',None)]):
            return self.client.run(poll_interval=.001, **kwargs)
    def test_get_header_enrollment_decrypts_and_persists_internal_identity(self):
        server = SimulatedHTTPS()
        states = []
        result = self.run_client(server, on_state=lambda state:states.append(state['state']))
        self.assertEqual(result['secret'], 'd'*64)
        self.assertEqual(json.loads(self.client.identity_path.read_text())['device_id'], 'a'*32)
        self.assertFalse(self.client.pending_path.exists())
        self.assertEqual(states[-1], 'ready')
        self.assertEqual(server.start['role'], 'eeg')
        self.assertEqual(len(server.start['request_id']), 32)
        self.assertEqual(len(server.start['token']), 64)
        self.assertEqual(serialization.load_pem_public_key(server.start['public_key'].encode()).key_size, 3072)
        for request, timeout in server.requests:
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertNotIn(server.start['token'], request.full_url)
            self.assertLessEqual(timeout, 8)
    def test_cancel_keeps_pending_private_key_for_same_request_resume(self):
        cancel = threading.Event()
        server = SimulatedHTTPS(waiting=True)
        with self.assertRaises(InterruptedError):
            self.run_client(server, stop_event=cancel,
                on_state=lambda state:cancel.set() if state['state']=='waiting_hub' else None)
        pending = self.client.pending_path.read_bytes()
        resumed = SimulatedHTTPS()
        result = self.run_client(resumed)
        self.assertEqual(server.start['request_id'], resumed.start['request_id'])
        self.assertEqual(server.start['public_key'], resumed.start['public_key'])
        self.assertNotIn(result['secret'].encode(), pending)
    def test_role_occupied_never_saves_or_replaces_identity(self):
        server = SimulatedHTTPS(occupied=True)
        with self.assertRaises(enrollment.EnrollmentError) as error:
            self.run_client(server)
        self.assertEqual(error.exception.state, 'role_occupied')
        self.assertFalse(self.client.identity_path.exists())
        self.assertEqual(len(server.requests), 1)
    def test_total_deadline_is_bounded_and_cancelled_before_network(self):
        with self.assertRaises(TimeoutError):
            self.run_client(SimulatedHTTPS(waiting=True), timeout=.01)
        cancel = threading.Event(); cancel.set()
        server = SimulatedHTTPS()
        with self.assertRaises(InterruptedError): self.run_client(server, stop_event=cancel)
        self.assertEqual(server.requests, [])
    def test_wrong_role_or_relay_trust_cannot_be_installed(self):
        for mutate in [lambda p:p.update(role='quest'), lambda p:p.update(url='wss://foreign.example/eeg/v1/ws'), lambda p:p.update(ca_pem='other')]:
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.run_client(SimulatedHTTPS(mutate=mutate))
            self.assertFalse(self.client.identity_path.exists())
    def test_tampered_sealed_reply_cannot_be_installed(self):
        server = SimulatedHTTPS()
        original = server.open
        def tampered(request, timeout):
            response = original(request, timeout)
            value = json.loads(response.read())
            if value['state']=='ready':value['sealed']['nonce']=base64.b64encode(b'x'*12).decode()
            return Response(value, request.full_url)
        server.open = tampered
        with self.assertRaises(ValueError): self.run_client(server)
        self.assertFalse(self.client.identity_path.exists())
    def test_response_size_and_redirect_are_rejected(self):
        for body,url in [(b'x'*16385,CONFIG['base_url']+'v1/enroll/start'), ({'state':'waiting_hub'},'https://foreign.example/start')]:
            server = SimulatedHTTPS()
            server.open = lambda request,timeout:Response(body,url)
            with self.subTest(url=url),self.assertRaises(ValueError): self.run_client(server)
            self.assertFalse(self.client.identity_path.exists())
    def test_network_errors_never_include_header_secrets(self):
        server = SimulatedHTTPS()
        def failed(request,timeout):raise OSError(request.get_header('X-eeg-request'))
        server.open = failed
        with self.assertRaises(ConnectionError) as error:self.run_client(server)
        pending = json.loads(self.client.pending_path.read_text())
        self.assertNotIn(pending['token'], str(error.exception))
        self.assertNotIn('PRIVATE KEY', str(error.exception))
    def test_existing_identity_is_loaded_without_new_request(self):
        self.client.identity_path.write_text(json.dumps(IDENTITY))
        server = SimulatedHTTPS()
        self.assertEqual(self.run_client(server)['device_id'], 'a'*32)
        self.assertEqual(server.requests, [])
    def test_plain_http_public_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            enrollment.EnrollmentClient(dict(CONFIG,base_url='http://relay.example/eeg/'),self.root)
    def test_request_timeout_tries_next_configured_network_path(self):
        server=SimulatedHTTPS()
        failure=SimulatedHTTPS()
        def timed_out(request,timeout):raise TimeoutError('network fixture')
        failure.open=timed_out
        with patch.object(enrollment,'paths',return_value=[('physical','interface:7'),('direct',None)]),patch.object(enrollment,'opener',side_effect=[failure,server,server]):
            value=self.client.run(poll_interval=.001)
        self.assertEqual(value['device_id'],'a'*32)
    def test_https_redirect_does_not_forward_pending_token(self):
        from eeg_relay.trust import create_identity
        create_identity(self.root/'tls','127.0.0.1')
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append((self.path,self.headers.get('X-EEG-Request')))
                if self.path.endswith('/start'):
                    self.send_response(302);self.send_header('Location',f'https://127.0.0.1:{self.server.server_port}/leak');self.end_headers()
                else:
                    self.send_response(200);self.end_headers();self.wfile.write(b'{"state":"waiting_hub"}')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.root/'tls/server.pem',self.root/'tls/server.key')
        server.socket=context.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        config=dict(CONFIG,base_url=f'https://127.0.0.1:{server.server_port}/eeg/',ca_pem=(self.root/'tls/ca.pem').read_text())
        try:
            with self.assertRaises(ValueError):enrollment.EnrollmentClient(config,self.root).run(timeout=2)
            self.assertEqual([path for path,_ in received],['/eeg/v1/enroll/start'])
        finally:server.shutdown();server.server_close();thread.join(1)


if __name__=='__main__':unittest.main()
