"""Automatic identity UX with a cancellable worker; no SDK or real network."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6 import QtWidgets

from gnautilus_acquisition.gui import AcquisitionController
from gnautilus_acquisition.qt_gui import Workbench
from tests.test_relay_enrollment_client import IDENTITY


class PendingEnrollment:
    def __init__(self, config, directory):
        self.identity_path=Path(directory)/'relay-identity.json'
        self.ready=threading.Event()
        self.entered=threading.Event()
        self.config=config
        self.occupied=False
    def run(self, *, stop_event, on_state):
        on_state({'state':'waiting_hub'});self.entered.set()
        while not self.ready.wait(.005):
            if stop_event.is_set():raise InterruptedError('cancelled')
        if self.occupied:
            from eeg_relay.enrollment_client import EnrollmentError
            raise EnrollmentError('role_occupied','已有采集端在线')
        if stop_event.is_set():raise InterruptedError('cancelled')
        self.identity_path.write_text(json.dumps(IDENTITY),encoding='utf-8')
        return dict(IDENTITY)


class AutomaticConnectionQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.controller=AcquisitionController()
        self.w=Workbench(self.controller,self.root/'settings.json')
        self.w.show();self.app.processEvents()
    def tearDown(self):
        self.w.close();self.app.processEvents()
        thread=getattr(self.w,'enrollment_thread',None)
        if thread:thread.join(1)
        self.tmp.cleanup()
    def wait_for(self, predicate):
        deadline=time.monotonic()+2
        while not predicate() and time.monotonic()<deadline:
            self.app.processEvents();self.w.refresh();time.sleep(.005)
        self.assertTrue(predicate())
    def start_pending(self):
        with patch('eeg_relay.enrollment_client.EnrollmentClient',PendingEnrollment):self.w.probe()
        self.wait_for(lambda:getattr(self.w,'enrollment_state',None)=='waiting_hub')
        return self.w.enrollment_client
    def test_offscreen_has_no_pairing_file_widgets_or_legacy_entry(self):
        self.assertIsNone(self.w.enrollment_thread)
        self.assertEqual(Path(self.w.profile()['pairing_path']),self.root/'relay-identity.json')
        self.assertNotIn('pairing_path',self.w.fields)
        self.assertFalse(any('配对文件' in field.accessibleName() for field in self.w.findChildren(QtWidgets.QLineEdit)))
        self.assertFalse(any('局域网' in field.text() for field in self.w.findChildren(QtWidgets.QCheckBox)))
        self.assertNotIn('复制',self.w.pairing_help.text())
    def test_waiting_worker_does_not_lock_other_controls_and_can_be_cancelled(self):
        pending=self.start_pending()
        self.assertTrue(pending.entered.is_set())
        self.assertFalse(self.w.busy)
        self.assertTrue(self.w.discover_button.isEnabled())
        self.assertTrue(self.w.fields['storage_root'].isEnabled())
        self.assertTrue(self.w.cancel_enrollment_button.isVisible())
        self.w.cancel_enrollment()
        self.wait_for(lambda:not self.w.enrollment_thread.is_alive())
        self.assertIn('已取消',self.w.pairing_status.text())
        self.assertFalse(pending.identity_path.exists())
    def test_ready_identity_is_saved_internally_and_uses_existing_probe(self):
        pending=self.start_pending()
        with patch('gnautilus_acquisition.client.probe_hub',return_value=dict(connected=True,elapsed_ms=1,hub={})):
            pending.ready.set()
            self.wait_for(lambda:self.w.probed_at>0)
        self.assertEqual(self.w.settings.load()['pairing_path'],str(pending.identity_path))
        self.assertEqual(self.w.update_enrollment['device_id'],'a'*32)
        self.assertNotIn('pairing_path',self.w.fields)
    def test_role_occupied_stays_unassigned_and_reconnect_is_available(self):
        pending=self.start_pending();pending.occupied=True;pending.ready.set()
        self.wait_for(lambda:self.w.enrollment_state=='role_occupied')
        self.assertIn('已有采集端在线',self.w.pairing_status.text())
        self.assertFalse(pending.identity_path.exists())
        self.assertTrue(self.w.probe_button.isEnabled())
    def test_close_cancels_enrollment_without_waiting_for_hub(self):
        self.start_pending()
        began=time.monotonic();self.w.close();self.app.processEvents()
        self.assertLess(time.monotonic()-began,.2)
        self.assertTrue(self.w.enrollment_stop.is_set())
    def test_normal_startup_enrolls_without_pressing_connect(self):
        with patch.dict(os.environ,{'QT_QPA_PLATFORM':'minimal'}),patch('gnautilus_acquisition.updater.UpdateMonitor.start'),patch('eeg_relay.enrollment_client.EnrollmentClient',PendingEnrollment):
            other=Workbench(AcquisitionController(),self.root/'settings2.json')
            try:
                self.wait_for(lambda:getattr(other,'enrollment_state',None)=='waiting_hub')
                self.assertTrue(other.enrollment_client.entered.is_set())
                self.assertFalse(other.busy)
            finally:
                other.close();self.app.processEvents()
                if other.enrollment_thread:other.enrollment_thread.join(1)
    def test_old_profile_with_pairing_file_migrates_to_automatic_role(self):
        settings=self.root/'old-profile.json'
        settings.write_text(json.dumps({'pairing_path':'C:/old-machine/pairing.json','serial':'actual-serial','storage_root':'G:/raw-recordings'}))
        other=Workbench(AcquisitionController(),settings)
        try:
            self.assertTrue(other.automatic_connection())
            self.assertEqual(other.fields['serial'].text(),'actual-serial')
            self.assertEqual(other.fields['storage_root'].text(),'G:/raw-recordings')
            self.assertEqual(other.profile()['connection_mode'],'automatic')
        finally:other.close()
    def test_legacy_profile_also_migrates_without_external_file(self):
        settings=self.root/'legacy-profile.json'
        settings.write_text(json.dumps({'pairing_path':'C:/selected/lan.json','connection_mode':'legacy'}))
        other=Workbench(AcquisitionController(),settings)
        try:
            self.assertTrue(other.automatic_connection())
            self.assertEqual(other.profile()['connection_mode'],'automatic')
            self.assertEqual(other.profile()['pairing_path'],str(self.root/'relay-identity.json'))
        finally:other.close()
    def test_broken_internal_identity_never_prompts_for_a_file(self):
        self.w.identity_path.write_text('{broken',encoding='utf-8')
        self.w.update_pairing_status(report_error=True)
        self.assertNotIn('选择',self.w.pairing_help.text())
        self.assertNotIn('JSON',self.w.pairing_help.text())
        self.assertIn('自动',self.w.pairing_status.text())
    def test_broken_internal_identity_is_backed_up_and_replaced_automatically(self):
        self.w.identity_path.write_text('{broken',encoding='utf-8')
        def receive(client,**kwargs):
            client.identity_path.write_text(json.dumps(IDENTITY),encoding='utf-8')
            return dict(IDENTITY)
        with patch('eeg_relay.enrollment_client.EnrollmentClient.run',receive),patch('gnautilus_acquisition.client.probe_hub',return_value=dict(connected=True,elapsed_ms=1,hub={})):
            self.w.probe();self.wait_for(lambda:self.w.probed_at>0)
        backups=list(self.root.glob('relay-identity.invalid-*.json'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_text(),'{broken')
        self.assertEqual(json.loads(self.w.identity_path.read_text())['role'],'eeg')
    def test_cancel_after_worker_finishes_prevents_queued_ready_from_connecting(self):
        pending=self.start_pending();pending.ready.set()
        self.w.enrollment_thread.join(1)
        self.assertFalse(self.w.enrollment_thread.is_alive())
        with patch('gnautilus_acquisition.client.probe_hub',return_value=dict(connected=True,elapsed_ms=1,hub={})) as probe:
            self.w.cancel_enrollment();self.w.refresh()
            self.assertTrue(self.w.enrollment_stop.is_set())
            self.assertFalse(self.w.busy)
            probe.assert_not_called()


if __name__=='__main__':unittest.main()
