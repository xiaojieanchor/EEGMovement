import tempfile
import unittest
from pathlib import Path
import numpy as np
from gnautilus_acquisition.audit import audit_journal,audit_and_compare,file_hash
from gnautilus_acquisition.storage import StreamJournal
from gnautilus_acquisition.contract import block_frame
from tests.test_gnautilus_transport import descriptor


class AuditTests(unittest.TestCase):
    def write(self,path,order=(1,2)):
        d=descriptor();j=StreamJournal(path,d)
        for seq in order:
            h,b=block_frame(d,seq,(seq-1)*2,np.full((2,32),seq,np.float32));j.append(h,b)
        j.close()
    def test_two_ends_compare_logical_blocks_despite_different_physical_order(self):
        with tempfile.TemporaryDirectory() as t:
            a,b=Path(t)/'a',Path(t)/'b';self.write(a);self.write(b,(2,1))
            self.assertNotEqual(file_hash(a/'raw.f32'),file_hash(b/'raw.f32'))
            report=audit_and_compare(a,b);self.assertTrue(report['comparison_match']);self.assertEqual(report['samples'],4)
    def test_audit_detects_uncommitted_tails_without_truncating_or_rewriting(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.write(p);(p/'closed.json').unlink()
            with (p/'raw.f32').open('ab') as f:f.write(b'abcd')
            before={f.name:file_hash(f) for f in p.iterdir()};r=audit_journal(p)
            self.assertEqual(r['uncommitted_tail_bytes'],4);self.assertFalse(r['complete_closed']);self.assertTrue(r['committed_integrity_passed'])
            self.assertEqual(before,{f.name:file_hash(f) for f in p.iterdir()})
    def test_committed_corruption_is_an_error(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);self.write(p)
            with (p/'raw.f32').open('r+b') as f:f.write(b'xxxx')
            with self.assertRaises(ValueError):audit_journal(p)


if __name__=='__main__':unittest.main()
