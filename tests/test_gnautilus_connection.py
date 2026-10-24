import socket
import unittest
from unittest.mock import patch
from gnautilus_acquisition.client import connect_authenticated
from tests.test_gnautilus_gui import enrollment


class ConnectionDiagnosticsTests(unittest.TestCase):
    def test_tcp_attempt_logs_explicit_target_and_localizes_refusal(self):
        stages=[]
        with patch('socket.create_connection',side_effect=ConnectionRefusedError(10061,'fixture refused')):
            with self.assertRaisesRegex(ValueError,r'TCP.*192\.168\.1\.10:8767.*拒绝'):
                connect_authenticated(enrollment(),on_stage=lambda *args:stages.append(args))
        self.assertEqual(stages[0][0],'TCP');self.assertIn('192.168.1.10:8767',stages[0][1])
    def test_tcp_timeout_explains_routing_and_firewall_without_claiming_tls(self):
        stages=[]
        with patch('socket.create_connection',side_effect=socket.timeout('fixture timeout')):
            with self.assertRaisesRegex(ValueError,r'TCP.*超时'):
                connect_authenticated(enrollment(),on_stage=lambda *args:stages.append(args))
        self.assertFalse(any(stage=='TLS' for stage,_ in stages))


if __name__=='__main__':unittest.main()
