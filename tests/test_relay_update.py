import base64,hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives import serialization,hashes
from eeg_relay.update import verify_manifest,extract_version,activate

class SignedUpdates(unittest.TestCase):
    def setUp(self):
        self.key=rsa.generate_private_key(65537,2048);self.public=self.key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.value=dict(schema='eegmovement.update.v1',platform='windows-x64',version='1.1.0',release_seq=2,protocol=1,path='releases/test.zip',size=10,sha256='a'*64)
    def sign(self,value):
        body=json.dumps(value).encode();return json.dumps(dict(payload=base64.b64encode(body).decode(),signature=base64.b64encode(self.key.sign(body,padding.PKCS1v15(),hashes.SHA256())).decode())).encode()
    def test_signature_platform_and_downgrade(self):
        self.assertEqual(verify_manifest(self.sign(self.value),self.public,'windows-x64',1)['version'],'1.1.0')
        for value in [dict(self.value,path='../private'),dict(self.value,platform='quest-arm64'),dict(self.value,release_seq=0)]:
            with self.assertRaises(ValueError):verify_manifest(self.sign(value),self.public,'windows-x64',1)
        wrapper=json.loads(self.sign(self.value));wrapper['payload']=base64.b64encode(b'{}').decode()
        with self.assertRaises(ValueError):verify_manifest(json.dumps(wrapper).encode(),self.public,'windows-x64',1)
    def test_zip_bounds_path_escape_and_rollback(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);z=root/'a.zip'
            with zipfile.ZipFile(z,'w') as f:f.writestr('../outside','no')
            with self.assertRaises(ValueError):extract_version(z,root/'version')
            with zipfile.ZipFile(z,'w') as f:f.writestr('EEGMovement-gNautilus/EEGMovement-gNautilus.exe',b'fixture')
            extract_version(z,root/'version');self.assertTrue((root/'version/EEGMovement-gNautilus.exe').exists())
            (root/'old').mkdir();(root/'current.json').write_text(json.dumps(dict(version='1.0.0',directory='old')))
            with self.assertRaises(RuntimeError):activate(root,'1.1.0','version',lambda _:False)
            self.assertEqual(json.loads((root/'current.json').read_text())['version'],'1.0.0')
            def cannot_start(_):raise OSError('invalid executable')
            with self.assertRaises(RuntimeError):activate(root,'1.1.0','version',cannot_start)
            self.assertEqual(json.loads((root/'current.json').read_text())['version'],'1.0.0')

if __name__=='__main__':unittest.main()
