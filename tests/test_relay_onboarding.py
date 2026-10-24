import base64,json,tempfile,unittest
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from eeg_relay.onboarding import EnrollmentBroker,seal_identity

class Onboarding(unittest.TestCase):
    def test_hub_requests_never_follow_redirect_with_authentication_headers(self):
        import io,urllib.request,urllib.response,email.message
        from unittest.mock import patch
        from eeg_relay.onboarding import hub_call
        seen=[]
        class Transport(urllib.request.BaseHandler):
            handler_order=50
            def https_open(self,request):
                seen.append(request.full_url);headers=email.message.Message()
                if request.host=='trusted.example':headers['Location']='https://redirect.example/enroll';code=302
                else:code=200
                response=urllib.response.addinfourl(io.BytesIO(b'{}'),headers,request.full_url,code);response.msg='Found' if code==302 else 'OK';return response
        config=dict(url='wss://trusted.example/eeg/v1/ws',device_id='a'*32,auth_secret='ef'*32)
        with patch('eeg_relay.network.paths',return_value=[('fixture',None)]),patch('eeg_relay.update.opener',side_effect=lambda *args:urllib.request.build_opener(urllib.request.ProxyHandler({}),Transport())):
            with self.assertRaises(ConnectionError):hub_call(config,'list')
        self.assertEqual(seen,['https://trusted.example/eeg/v1/enroll/list'])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.key=rsa.generate_private_key(65537,3072)
        self.public=self.key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.occupied=False;self.broker=EnrollmentBroker(Path(self.tmp.name),lambda role:self.occupied)
        self.request=dict(request_id='a'*32,token='ef'*32,role='eeg',public_key=self.public,name='Acquisition test')
    def tearDown(self):self.broker.close();self.tmp.cleanup()
    def test_automatic_role_flow_encrypted_end_to_end_and_idempotency(self):
        self.assertEqual(self.broker.start(self.request)['state'],'waiting_hub')
        pending=self.broker.pending();self.assertEqual(len(pending),1);self.assertNotIn('token',pending[0])
        identity=dict(secret='ab'*32,auth_secret='cd'*32,mode='relay');sealed=seal_identity(identity,self.public,'a'*32)
        self.broker.complete('a'*32,'eeg',sealed)
        reply=self.broker.poll(dict(request_id='a'*32,token='ef'*32));self.assertEqual(reply['state'],'ready')
        key=self.key.decrypt(base64.b64decode(reply['sealed']['key']),padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),algorithm=hashes.SHA256(),label=None))
        raw=AESGCM(key).decrypt(base64.b64decode(sealed['nonce']),base64.b64decode(sealed['ciphertext']),b'a'*32)
        self.assertEqual(json.loads(raw),identity)
        self.assertEqual(self.broker.start(self.request)['state'],'ready')
        self.assertEqual(self.broker.start(dict(self.request,request_id='b'*32,token='01'*32))['state'],'waiting_hub')
        for p in Path(self.tmp.name).iterdir():self.assertNotIn(b'ab'*32,p.read_bytes())
    def test_occupied_wrong_token_conflict_and_expiry(self):
        self.occupied=True
        with self.assertRaisesRegex(ValueError,'role_occupied'):self.broker.start(self.request)
        self.occupied=False;self.broker.start(self.request)
        with self.assertRaisesRegex(ValueError,'identity_conflict'):self.broker.start(dict(self.request,public_key='changed'))
        with self.assertRaisesRegex(ValueError,'request_rejected'):self.broker.poll(dict(request_id='a'*32,token='00'*32))
        with self.broker.lock:self.broker.db.execute('UPDATE requests SET created=0');self.broker.db.commit()
        with self.assertRaisesRegex(ValueError,'expired'):self.broker.poll(dict(request_id='a'*32,token='ef'*32))

if __name__=='__main__':unittest.main()
