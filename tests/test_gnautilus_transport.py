import hashlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
import numpy as np

from gnautilus_acquisition.contract import validate_descriptor, block_frame, send_frame, recv_frame
from gnautilus_acquisition.storage import StreamJournal


def descriptor(source='synthetic', width=32, fs=512):
    config={'InputSignal':0,'SamplingRate':fs}
    return dict(schema='gnautilus.stream.v1',stream_id='a'*32,epoch=0,
                source_kind=source,device_serial='TEST-N32',sampling_rate=fs,
                channels=[dict(id=f'CH{i+1}',name=f'CH{i+1}',kind='EEG',unit='uV',physical_index=i) for i in range(width)],
                configuration=config,configuration_sha256=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest(),layout_verified=False)


class ContractTests(unittest.TestCase):
    def test_dynamic_columns_and_auxiliary_units_are_preserved(self):
        d=descriptor();d['channels'].append(dict(id='counter',name='Counter',kind='COUNTER',unit='count',physical_index=None))
        self.assertEqual(len(validate_descriptor(d)['channels']),33)
        bad=descriptor();bad['sampling_rate']=float('nan')
        with self.assertRaises(ValueError):validate_descriptor(bad)
        bad=descriptor();bad['channels'][0]['unit']='V'
        with self.assertRaises(ValueError):validate_descriptor(bad)

    def test_float32_frames_roundtrip_without_rescaling_and_detect_corruption(self):
        d=validate_descriptor(descriptor());x=np.arange(96,dtype=np.float32).reshape(3,32);x[1,0]=np.nan
        h,b=block_frame(d,1,0,x)
        a,z=socket.socketpair()
        try:
            send_frame(a,h,b);r,body=recv_frame(z)
            self.assertEqual(r['sample_count'],3);self.assertEqual(body,b)
            self.assertEqual(np.frombuffer(body,dtype='<f4')[33],x.flat[33])
        finally:a.close();z.close()
        with self.assertRaises(ValueError):block_frame(d,1,0,np.zeros((4,7),dtype=np.float32))

    def test_frame_limits_and_nonfinite_json_are_rejected(self):
        a,z=socket.socketpair()
        try:
            a.sendall((5_000_000).to_bytes(4,'big'))
            with self.assertRaises(ValueError):recv_frame(z)
            with self.assertRaises(ValueError):send_frame(a,{'bad':float('nan')})
        finally:a.close();z.close()


class JournalTests(unittest.TestCase):
    def test_durable_duplicate_out_of_order_and_conflict(self):
        d=validate_descriptor(descriptor());x=np.arange(64,dtype=np.float32).reshape(2,32)
        with tempfile.TemporaryDirectory() as t:
            j=StreamJournal(Path(t),d)
            h2,b2=block_frame(d,2,2,x+1);r=j.append(h2,b2)
            self.assertEqual(r['durable_seq'],0)
            h1,b1=block_frame(d,1,0,x);r=j.append(h1,b1)
            self.assertEqual(r['durable_seq'],2)
            self.assertTrue(j.append(h1,b1)['duplicate'])
            _,bad=block_frame(d,1,0,x+3)
            with self.assertRaises(ValueError):j.append(h1,bad)
            j.event(dict(kind='marker',label=None,sample_index_end=4))
            report=j.close();self.assertEqual(report['samples'],4)
            self.assertEqual(report['missing_sequences'],[])
            recovered=StreamJournal(Path(t),d)
            self.assertEqual(recovered.durable_seq,2)
            self.assertEqual(recovered.read(2)[1],b2);recovered.close()

    def test_crash_tail_is_trimmed_and_committed_corruption_is_refused(self):
        d=validate_descriptor(descriptor());x=np.zeros((2,32),dtype=np.float32)
        with tempfile.TemporaryDirectory() as t:
            j=StreamJournal(Path(t),d);h,b=block_frame(d,1,0,x);j.append(h,b);j.close()
            with (Path(t)/'raw.f32').open('ab') as f:f.write(b'bad-tail')
            with (Path(t)/'blocks.jsonl').open('ab') as f:f.write(b'{incomplete')
            with (Path(t)/'events.jsonl').open('ab') as f:f.write(b'{incomplete')
            k=StreamJournal(Path(t),d);self.assertEqual(k.durable_seq,1);k.close()
            self.assertEqual((Path(t)/'events.jsonl').read_bytes(),b'')
            self.assertEqual((Path(t)/'raw.f32').stat().st_size,len(b))
            with (Path(t)/'raw.f32').open('r+b') as f:f.write(b'1234')
            with self.assertRaises(ValueError):StreamJournal(Path(t),d)

    def test_overlapping_sample_ranges_and_changed_descriptor_are_refused(self):
        d=validate_descriptor(descriptor());x=np.zeros((2,32),dtype=np.float32)
        with tempfile.TemporaryDirectory() as t:
            j=StreamJournal(Path(t),d);j.append(*block_frame(d,1,0,x))
            with self.assertRaises(ValueError):j.append(*block_frame(d,2,1,x))
            j.close();other=descriptor(fs=250)
            with self.assertRaises(ValueError):StreamJournal(Path(t),other)


if __name__=='__main__':unittest.main()
