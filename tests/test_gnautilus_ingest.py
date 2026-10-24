import hashlib
import datetime
import hmac
import json
import socket
import ssl
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
import numpy as np
from tests.test_gnautilus_transport import descriptor
from gnautilus_acquisition.contract import send_frame,recv_frame,block_frame
from gnautilus_acquisition.client import connect_authenticated,probe_hub
from hub.gnautilus import IngestService,provision


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.live=[]
        self.enrollment=provision(self.root,'127.0.0.1',port=0)
        self.server=IngestService(self.root,lambda d,h,x:self.live.append((d,h,x.copy())),port=0,host='127.0.0.1')
        self.server.start();self.enrollment['port']=self.server.port
    def tearDown(self):
        self.server.close();self.tmp.cleanup()
    def open_stream(self):
        s,reply=connect_authenticated(self.enrollment);d=descriptor()
        send_frame(s,dict(kind='descriptor',descriptor=d));r,_=recv_frame(s)
        return s,d,r
    def test_tls_pairing_durable_ack_and_live_backfill_separation(self):
        s,d,r=self.open_stream()
        try:
            h,b=block_frame(d,2,2,np.ones((2,32),np.float32),ticket=r['ticket'],source_age_ms=0)
            send_frame(s,h,b);r,_=recv_frame(s)
            self.assertEqual(r['ack_seq'],2);self.assertEqual(r['durable_seq'],0)
            self.assertEqual(len(self.live),1)
            h,b=block_frame(d,1,0,np.zeros((2,32),np.float32),mode='backfill')
            send_frame(s,h,b);r,_=recv_frame(s)
            self.assertEqual(r['durable_seq'],2);self.assertEqual(len(self.live),1)
            send_frame(s,h,b);r,_=recv_frame(s);self.assertTrue(r['duplicate'])
        finally:s.close()
    def test_expired_freshness_ticket_is_stored_but_never_live(self):
        s,d,r=self.open_stream()
        try:
            time.sleep(.3)
            h,b=block_frame(d,1,0,np.zeros((2,32),np.float32),ticket=r['ticket'],source_age_ms=0)
            send_frame(s,h,b);r,_=recv_frame(s)
            self.assertEqual(r['durable_seq'],1);self.assertFalse(r['live_accepted']);self.assertEqual(self.live,[])
        finally:s.close()

    def test_slow_durable_commit_cannot_promote_an_expired_block_to_realtime(self):
        s,d,r=self.open_stream();journal=self.server.journals[(self.enrollment['agent_id'],d['stream_id'])];append=journal.append
        def slow_commit(*args):time.sleep(.35);return append(*args)
        try:
            with patch.object(journal,'append',side_effect=slow_commit):
                h,b=block_frame(d,1,0,np.zeros((2,32),np.float32),ticket=r['ticket'],source_age_ms=0)
                send_frame(s,h,b);r,_=recv_frame(s)
            self.assertEqual(r['durable_seq'],1);self.assertFalse(r['live_accepted']);self.assertEqual(self.live,[])
        finally:s.close()

    def test_out_of_order_live_block_cannot_move_realtime_cursor_backwards(self):
        s,d,r=self.open_stream()
        try:
            h,b=block_frame(d,2,2,np.zeros((2,32),np.float32),ticket=r['ticket'],source_age_ms=0)
            send_frame(s,h,b);r,_=recv_frame(s)
            h,b=block_frame(d,1,0,np.zeros((2,32),np.float32),ticket=r['ticket'],source_age_ms=0)
            send_frame(s,h,b);r,_=recv_frame(s)
            self.assertFalse(r['live_accepted']);self.assertEqual(len(self.live),1)
        finally:s.close()
    def test_wrong_pin_or_key_rejected_before_data_acceptance(self):
        e=dict(self.enrollment,certificate_sha256='0'*64)
        with self.assertRaisesRegex(ValueError,'TLS.*证书'):connect_authenticated(e)
        e=dict(self.enrollment,secret='0'*64)
        with self.assertRaises(ValueError):connect_authenticated(e)
        self.assertEqual(self.live,[])

    def test_failed_authentication_cannot_disconnect_a_valid_agent(self):
        states=[];self.server.on_state=states.append
        s,d,r=self.open_stream()
        try:
            with self.assertRaises(ValueError):connect_authenticated(dict(self.enrollment,secret='0'*64))
            self.assertTrue(self.server.snapshot()['connected']);self.assertEqual(states,[])
        finally:s.close()
    def test_reconnect_gets_durable_prefix_without_promoting_old_blocks(self):
        s,d,r=self.open_stream();h,b=block_frame(d,1,0,np.zeros((2,32),np.float32),mode='backfill')
        send_frame(s,h,b);recv_frame(s);s.close();time.sleep(.03)
        s,d,r=self.open_stream()
        try:self.assertEqual(r['durable_seq'],1);self.assertEqual(self.live,[])
        finally:s.close()

    def test_overnight_service_restart_reuses_original_stream_journal(self):
        class FirstDay(datetime.date):
            @classmethod
            def today(cls):return cls(2026,10,22)
        class NextDay(datetime.date):
            @classmethod
            def today(cls):return cls(2026,10,23)
        with patch('hub.gnautilus.datetime.date',FirstDay):
            s,d,r=self.open_stream();h,b=block_frame(d,1,0,np.zeros((2,32),np.float32),mode='backfill')
            send_frame(s,h,b);reply,_=recv_frame(s);self.assertEqual(reply['durable_seq'],1);s.close();self.server.close()
        self.server=IngestService(self.root,lambda *args:self.live.append(args),host='127.0.0.1',port=0);self.server.start();self.enrollment['port']=self.server.port
        with patch('hub.gnautilus.datetime.date',NextDay):
            s,d,r=self.open_stream()
            try:
                self.assertEqual(r['durable_seq'],1);send_frame(s,h,b);reply,_=recv_frame(s);self.assertTrue(reply['duplicate'])
                paths=list((self.root/'data/gnautilus').glob('*/streams/*/*/descriptor.json'));self.assertEqual(len(paths),1)
            finally:s.close()

    def test_probe_returns_actual_hub_components_without_opening_sdk_or_data(self):
        self.server.get_hub_state=lambda:dict(recording_active=True,recording_samples=123,label='LEFT',phase='holding',vr_online=2,model_state='not_configured',motion_state='STOP')
        logs=[];result=probe_hub(self.enrollment,lambda *r:logs.append(r))
        self.assertEqual(result['hub']['label'],'LEFT');self.assertEqual(result['hub']['vr_online'],2)
        self.assertEqual(result['hub']['recording_samples'],123);self.assertEqual(self.live,[])
        self.assertTrue(any('TLS' in r[2] for r in logs));self.assertTrue(any('身份' in r[2] for r in logs))


if __name__=='__main__':unittest.main()
