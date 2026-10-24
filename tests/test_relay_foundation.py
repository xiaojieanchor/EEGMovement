import tempfile,unittest,hashlib,time
from pathlib import Path
from eeg_relay.protocol import pack,unpack,seal,verify,authentication
from eeg_relay.spool import Spool,CapacityError
from eeg_relay.trust import create_identity,client_context

class RelayFoundation(unittest.TestCase):
    def test_signed_payload_and_bounds(self):
        h=seal('a'*32,'b'*32,'c'*32,'d'*32,1,'eeg_block',b'data','ef'*32,stream='e'*32,epoch=0,block_seq=1)
        self.assertTrue(verify(h,b'data','ef'*32))
        self.assertFalse(verify(dict(h,to='f'*32),b'data','ef'*32))
        self.assertFalse(verify(h,b'Data','ef'*32))
        self.assertEqual(unpack(pack(h,b'data')),(h,b'data'))
        with self.assertRaises(ValueError):unpack(b'bad')
        with self.assertRaises(ValueError):seal('a'*32,'b'*32,'c'*32,'d'*32,1,'ssh',b'', 'ef'*32)

    def test_spool_restart_dedup_conflict_and_ack(self):
        with tempfile.TemporaryDirectory() as t:
            h=seal('a'*32,'b'*32,'c'*32,'d'*32,1,'eeg_block',b'RAW-EEG','ef'*32,stream='e'*32,epoch=0,block_seq=1)
            s=Spool(t,b'k'*32,reserve=0)
            self.assertFalse(s.put(h,b'RAW-EEG')['duplicate']);self.assertTrue(s.put(h,b'RAW-EEG')['duplicate'])
            with self.assertRaises(ValueError):s.put(dict(h,digest='f'*64),b'changed')
            s.close();s=Spool(t,b'k'*32,reserve=0)
            records=s.pending('a'*32,'c'*32);self.assertEqual(len(records),1);self.assertEqual(records[0][1],b'RAW-EEG')
            self.assertNotIn(b'RAW-EEG',(Path(t)/'cache.sqlite3').read_bytes())
            s.ack('a'*32,'b'*32,'e'*32,0,1);self.assertEqual(s.pending('a'*32,'c'*32),[]);s.close()

    def test_capacity_expiry_and_tamper(self):
        with tempfile.TemporaryDirectory() as t:
            h=seal('a'*32,'b'*32,'c'*32,'d'*32,1,'eeg_block',b'x'*80,'ef'*32,stream='e'*32,epoch=0,block_seq=1)
            s=Spool(t,b'k'*32,reserve=0,limit=10)
            with self.assertRaises(CapacityError):s.put(h,b'x'*80)
            s.close();s=Spool(t,b'k'*32,reserve=0,ttl=.01)
            s.put(h,b'x'*80);time.sleep(.02);self.assertEqual(s.expire(),1);self.assertEqual(s.pending('a'*32,'c'*32),[]);s.close()

    def test_dedicated_ca_and_ip_san(self):
        with tempfile.TemporaryDirectory() as t:
            create_identity(t,'106.55.251.83')
            ctx=client_context(Path(t)/'ca.pem');self.assertTrue(ctx.check_hostname)
            import ssl
            self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
            from cryptography import x509
            cert=x509.load_pem_x509_certificate((Path(t)/'server.pem').read_bytes())
            self.assertIn('106.55.251.83',str(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value))
    def test_bad_block_quarantine_retains_bytes_without_blocking_other_streams(self):
        with tempfile.TemporaryDirectory() as t:
            s=Spool(t,b'k'*32,reserve=0)
            h=seal('a'*32,'b'*32,'c'*32,'d'*32,1,'eeg_block',b'bad','ef'*32,stream='e'*32,epoch=0,block_seq=1)
            s.put(h,b'bad');s.quarantine('a'*32,'b'*32,'e'*32,0,1)
            self.assertEqual(s.pending('a'*32,'c'*32),[]);self.assertEqual(s.snapshot()['blocks'],1);s.close()

if __name__=='__main__':unittest.main()
