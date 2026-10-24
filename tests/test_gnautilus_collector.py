import tempfile
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from tests.test_gnautilus_transport import descriptor
from gnautilus_acquisition.contract import validate_descriptor
from gnautilus_acquisition.client import Collector,Replicator
from hub.gnautilus import IngestService,provision
from gnautilus_acquisition.storage import StreamJournal


class FakeWorker:
    def __init__(self,settings):self.n=0;self.alive=True;self.forced_termination=False
    def start(self):return self
    def receive(self,timeout):
        self.n+=1
        if self.n==1:return dict(kind='descriptor',descriptor=descriptor())
        if self.n<=8:
            time.sleep(.03)
            return dict(kind='block',samples=np.full((8,32),self.n,dtype=np.float32),read_monotonic_ns=time.monotonic_ns(),utc_ns=time.time_ns())
        time.sleep(.03);return None
    def stop(self,on_message=None):self.alive=False

class CloseErrorWorker(FakeWorker):
    def stop(self,on_message=None):
        self.alive=False
        if on_message:on_message(dict(kind='state',state='close_errors',errors=['StopStreaming failure fixture']))


class CollectorTests(unittest.TestCase):
    def test_sdk_to_local_journal_to_tls_and_recovery_no_fake_live(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);e=provision(root,'127.0.0.1',port=0);live=[]
            service=IngestService(root,lambda *args:live.append(args),host='127.0.0.1',port=0);service.start();e['port']=service.port
            c=Collector({},e,root/'local',worker_factory=FakeWorker).start()
            try:
                end=time.monotonic()+4
                while time.monotonic()<end:
                    s=c.snapshot()
                    if s['blocks']==7 and s['network']['durable_seq']==7:break
                    time.sleep(.02)
                self.assertEqual(s['blocks'],7);self.assertEqual(s['network']['durable_seq'],7);self.assertGreater(len(live),0)
                result=c.stop();self.assertEqual(result['state'],'stopped')
                self.assertEqual(service.snapshot().get('agent_health',{}).get('sdk_state'),'closed')
                self.assertTrue((Path(result['path'])/'closed.json').exists())
                self.assertEqual(service.journals[(e['agent_id'],'a'*32)].snapshot()['samples'],56)
            finally:c.stop();service.close()

    def test_network_failure_keeps_local_record_and_later_backfills(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);e=provision(root,'127.0.0.1',port=1)
            c=Collector({},e,root/'local',worker_factory=FakeWorker).start()
            try:
                end=time.monotonic()+3
                while c.snapshot()['blocks']<7 and time.monotonic()<end:time.sleep(.02)
                self.assertEqual(c.snapshot()['blocks'],7);self.assertFalse(c.snapshot()['network']['connected'])
                c.stop()
            finally:c.stop()
            live=[];service=IngestService(root,lambda *a:live.append(a),host='127.0.0.1',port=0);service.start();e['port']=service.port
            j=StreamJournal(Path(c.snapshot()['path']),validate_descriptor(descriptor()))
            rep=Replicator(j,e,backfill_only=True).start()
            try:
                end=time.monotonic()+3
                while rep.snapshot()['durable_seq']<7 and time.monotonic()<end:time.sleep(.02)
                self.assertEqual(rep.snapshot()['durable_seq'],7);self.assertEqual(live,[])
            finally:rep.stop();j.close();service.close()

    def test_disk_failure_stops_sdk_and_reports_error(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);e=provision(root,'127.0.0.1',port=1)
            with patch('gnautilus_acquisition.storage.StreamJournal.append',side_effect=OSError('disk full')):
                c=Collector({},e,root/'local',worker_factory=FakeWorker).start()
                end=time.monotonic()+3
                while time.monotonic()<end and c.snapshot()['state']!='error':time.sleep(.02)
                result=c.stop()
            self.assertEqual(result['state'],'error');self.assertIn('disk full',result['error']);self.assertFalse(c.worker.alive)
            checkpoints=[json.loads(line) for line in (Path(result['path'])/'events.jsonl').read_text('utf-8').splitlines()]
            self.assertTrue(any(row.get('state')=='error' and 'disk full' in row.get('error','') for row in checkpoints))

    def test_sdk_release_failure_is_not_reported_as_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);e=provision(root,'127.0.0.1',port=1);c=Collector({},e,root/'local',worker_factory=CloseErrorWorker).start()
            try:
                end=time.monotonic()+3
                while c.snapshot()['blocks']<1 and time.monotonic()<end:time.sleep(.02)
                result=c.stop();self.assertEqual(result['state'],'error');self.assertIn('StopStreaming failure',result['error'])
            finally:c.stop()


if __name__=='__main__':unittest.main()
