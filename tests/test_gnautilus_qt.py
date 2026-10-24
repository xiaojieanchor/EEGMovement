"""Offscreen widget checks with explicitly synthetic fixtures; no SDK or network."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
import unittest
import json
from unittest.mock import patch
from pathlib import Path
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets
from gnautilus_acquisition.qt_gui import Workbench
from gnautilus_acquisition.client import CAPTURE_LABELS,capture_task_spec
from gnautilus_acquisition.display import PreviewBuffer,EventLog
from tests.test_gnautilus_transport import descriptor
from tests.test_gnautilus_gui import enrollment


class FixtureController:
    def __init__(self):
        self.running=True;self.collector=self
        self.descriptor=descriptor();self.descriptor['device_serial']='SOFTWARE-FIXTURE'
        self.preview=PreviewBuffer();self.preview.configure(self.descriptor)
        t=np.arange(2560)/512
        a=np.stack([28*np.sin(2*np.pi*(8+i*.17)*t)+10*np.sin(2*np.pi*1.3*t) for i in range(32)],axis=1).astype(np.float32)
        a[1100:1130,2]=np.nan;self.preview.push(a,time.monotonic_ns());self.log=EventLog()
        self.capture=dict(active=False,session_id=None,held=None,held_duration_s=0.0,allow_combos=False,labels=list(CAPTURE_LABELS[:5]),instructions={l:capture_task_spec(l)['instruction'] for l in CAPTURE_LABELS},events_saved=0,periods_completed=0,hub_synced=True,hub_ack_seq=0,hub_recording='hub-session-01',last_recording=None,error=None)
        for stage,message in [('采集','软件测试夹具：32路合成波形，未访问设备'),('写盘','软件夹具：本地保存计数检查'),('中枢','软件夹具：TLS回执状态展示'),('中枢·标签','软件夹具：LEFT / holding'),('中枢·VR','软件夹具：2个身份在线'),('异常','软件夹具：接触质量仍未验证')]:self.log.add(stage,'INFO',message)
    def start_capture(self,allow_combos=False):
        labels=list(CAPTURE_LABELS) if allow_combos else list(CAPTURE_LABELS[:5])
        instructions={l:capture_task_spec(l)['instruction'] for l in CAPTURE_LABELS}
        self.capture.update(active=True,session_id='cap-session-1234',held=None,held_duration_s=0.0,allow_combos=bool(allow_combos),labels=labels,instructions=instructions,events_saved=1,periods_completed=0,hub_ack_seq=1)
        return dict(self.capture)
    def hold_start(self,action_label,pointer_source='desktop_pointer',page_active=True):
        self.capture.update(held=action_label,held_duration_s=0.4,events_saved=self.capture['events_saved']+1,hub_ack_seq=self.capture['hub_ack_seq']+1)
        self.log.add('标签','INFO',f'长按开始：{action_label} ({pointer_source})')
        return dict(self.capture)
    def capture_heartbeat(self,page_active=True):
        if self.capture['held']:
            self.capture['held_duration_s']=round(self.capture['held_duration_s']+0.3,1)
            self.capture['events_saved']+=1
        return dict(self.capture)
    def hold_end(self,action_label=None,pointer_source=None,reason='key_up',page_active=True):
        prev=self.capture['held']
        if prev:
            self.capture.update(held=None,held_duration_s=0.0,events_saved=self.capture['events_saved']+1,periods_completed=self.capture['periods_completed']+1,hub_ack_seq=self.capture['hub_ack_seq']+1)
            self.log.add('标签','INFO',f'松开结束：{prev} -> UNKNOWN ({reason})')
        return dict(self.capture)
    def stop_capture(self,reason='completed'):
        if self.capture['held']:self.hold_end(reason=reason)
        self.capture.update(active=False,held=None,held_duration_s=0.0,last_recording='session_fixture_01')
        self.remote_stopped=True
        return dict(self.capture)
    def capture_snapshot(self):
        return dict(self.capture)
    def snapshot(self):
        hub_active=not self.capture['active'] and not getattr(self,'remote_stopped',False)
        return dict(state='receiving',samples=25600,descriptor=self.descriptor,path='TEST-ONLY / raw.f32',capture=dict(self.capture),network=dict(connected=True,backlog=2,durable_seq=998,hub=dict(recording_active=hub_active or self.capture['active'],recording_samples=15360,label=self.capture['held'] if self.capture['active'] else ('LEFT' if hub_active else None),phase=(('holding' if self.capture['held'] else 'released') if self.capture['active'] else ('holding' if hub_active else 'completed')),vr_online=2,model_state='not_configured',motion_state='STOP')))
    def stop(self):self.running=False


class QtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=FixtureController();self.w=Workbench(self.c,Path(self.tmp.name)/'settings.json');self.w.resize(1420,900);self.w.show();self.app.processEvents();self.w.refresh_preview()
    def tearDown(self):self.c.running=False;self.w.close();self.app.processEvents();self.tmp.cleanup()
    def test_dynamic_page_fixed_gain_freeze_does_not_mutate_data(self):
        self.assertEqual(self.w.channel_page.count(),4);self.w.channel_page.setCurrentIndex(3);self.assertEqual(self.w.wave.page,3)
        self.assertEqual(self.w.wave.paths[0][0],'CH25');self.w.scale.setCurrentIndex(2);self.assertEqual(self.w.wave.gain,200)
        self.w.freeze.setChecked(True);last=self.w.wave.last_read;self.c.preview.push(np.ones((20,32),np.float32),time.monotonic_ns());self.w.refresh_preview()
        self.assertEqual(self.w.wave.last_read,last);self.assertEqual(self.c.preview.snapshot()['samples'][-1,0],1)
        self.w.freeze.setChecked(False);self.w.refresh_preview();self.assertNotEqual(self.w.wave.last_read,last)
    def test_hub_feedback_and_stage_filter_are_observed_values(self):
        self.assertEqual(self.w.feedback_labels['vr'].text(),'2个在线');self.assertEqual(self.w.feedback_labels['label'].text(),'向左')
        self.w.filter.setCurrentText('中枢');self.assertIn('LEFT / holding',self.w.log_view.toPlainText());self.assertNotIn('本地保存计数',self.w.log_view.toPlainText())
    def test_stale_probe_expires_and_reduced_motion_keeps_data_refresh(self):
        self.c.running=False;self.w.controller.collector=None;self.w.controller.snapshot=lambda:dict(state='idle',network={})
        self.w.last_probe=dict(connected=True,hub=dict(vr_online=2));self.w.probed_at=time.monotonic()-10;self.w.refresh()
        self.assertEqual(self.w.feedback_labels['transport'].text(),'等待连接')
        self.w.reduce.setChecked(True);self.assertEqual(self.w.wave.timer.interval(),100);self.assertTrue(self.w.start_button.reduce_motion)

    def test_update_check_does_not_expire_an_idle_live_status_link(self):
        from types import SimpleNamespace
        self.c.running=False;self.w.controller.collector=None;self.w.controller.snapshot=lambda:dict(state='idle',network={})
        self.w.probed_at=time.monotonic()-60
        self.w.status_link=SimpleNamespace(snapshot=lambda:dict(connected=True,transport='public_relay',hub_online=True,hub_state_fresh=True,hub=dict(vr_online=1,motion_state='STOP',model_state='not_configured',recording_active=False)))
        self.w.updater.check();self.w.refresh();self.w.update_idle_refresh()
        self.assertEqual(self.w.feedback_labels['vr'].text(),'1个在线')
        self.assertIn('已确认',self.w.feedback_labels['transport'].text())
        self.assertTrue(self.w.update_idle())
        self.w.status_link=None

    def test_idle_hub_status_cannot_erase_previous_stream_pending_blocks(self):
        from types import SimpleNamespace
        state=self.c.snapshot();self.c.running=False
        self.w.status_link=SimpleNamespace(snapshot=lambda:dict(connected=True,status_only=True,backlog=0,durable_seq=0,hub=dict(label=None,recording_active=False)))
        combined=self.w.idle_network(state)
        self.assertEqual(combined['backlog'],2);self.assertEqual(combined['durable_seq'],998)
        self.assertIsNone(combined['hub']['label']);self.assertFalse(combined['status_only'])
        self.w.status_link=None

    def test_missing_identity_starts_automatic_connection_without_file_prompt(self):
        self.c.running=False;self.w.controller.collector=None
        with patch.object(self.w,'start_enrollment') as enroll,patch('gnautilus_acquisition.client.probe_hub') as probe:
            self.w.probe();self.app.processEvents();self.w.refresh();probe.assert_not_called();enroll.assert_called_once()
        messages='\n'.join(row['message'] for row in self.w.events.snapshot())
        self.assertNotIn('复制',messages);self.assertNotIn('开始测试 TCP',messages)
        self.assertFalse(self.w.busy);self.assertIn('自动',self.w.pairing_status.text())

    def test_internal_public_identity_shows_target_and_is_saved_after_probe(self):
        from tests.test_relay_enrollment_client import IDENTITY
        self.c.running=False;self.w.controller.collector=None;p=self.w.identity_path;p.write_text(json.dumps(IDENTITY),encoding='utf-8')
        with patch('gnautilus_acquisition.client.probe_hub',return_value=dict(connected=True,elapsed_ms=1,hub={'vr_online':0})) as probe:
            self.w.probe();end=time.monotonic()+2
            while self.w.busy and time.monotonic()<end:self.app.processEvents();self.w.refresh();time.sleep(.01)
            probe.assert_called_once()
        self.assertIn('relay.example:443',self.w.pairing_status.text());self.assertEqual(self.w.settings.load()['pairing_path'],str(p))
        self.assertIn('relay.example:443','\n'.join(row['message'] for row in self.w.events.snapshot()))

    def discover_result(self, groups, selected=''):
        from gnautilus_acquisition.diagnostics import describe_devices
        self.c.running=False;self.w.controller.collector=None
        self.w.fields['serial'].setText(selected)
        report=describe_devices(groups, selected or None)
        with patch('gnautilus_acquisition.diagnostics.diagnose_sdk',return_value=report):
            self.w.discover();end=time.monotonic()+2
            while self.w.busy and time.monotonic()<end:
                self.app.processEvents();self.w.refresh();time.sleep(.005)
        self.assertFalse(self.w.busy)
        return report

    def test_visible_idle_device_is_displayed_and_can_be_selected(self):
        from tests.test_gnautilus_diagnostics import group
        self.discover_result([group('PHYSICAL-TEST',False)])
        self.assertIn('PHYSICAL-TEST',self.w.device_status.text())
        self.assertIn('未建立原厂会话',self.w.device_status.text())
        self.assertEqual(self.w.fields['serial'].text(),'PHYSICAL-TEST')
        self.assertFalse(self.w.device_diagnosis['stream_verified'])

    def test_shared_session_is_reported_without_claiming_samples(self):
        from tests.test_gnautilus_diagnostics import group
        self.discover_result([group('SHARED-TEST',True)])
        self.assertIn('尚未验证',self.w.device_status.text())
        self.assertEqual(self.w.fields['serial'].text(),'SHARED-TEST')
        self.assertFalse(self.w.device_diagnosis['stream_verified'])

    def test_missing_selected_serial_is_not_silently_replaced(self):
        from tests.test_gnautilus_diagnostics import group
        self.discover_result([group('OTHER',True)],'SELECTED')
        self.assertEqual(self.w.fields['serial'].text(),'SELECTED')
        self.assertIn('未找到所选序列号',self.w.device_status.text())

    def test_discovery_error_is_shown_as_device_diagnosis(self):
        self.c.running=False;self.w.controller.collector=None
        with patch('gnautilus_acquisition.diagnostics.diagnose_sdk',return_value=dict(code='service_query_failed',message='服务查询被拒绝，状态未知',ready_for_participant=False)):
            self.w.discover();end=time.monotonic()+2
            while self.w.busy and time.monotonic()<end:
                self.app.processEvents();self.w.refresh();time.sleep(.005)
        self.assertIn('状态未知',self.w.device_status.text())

    def test_public_gui_entry_accepts_start_request_and_writes_runtime_status(self):
        # Run the public QApplication entry in a separate process. Re-entering
        # the suite's shared QApplication can let another fixture veto quit.
        import subprocess,sys
        root=Path(__file__).resolve().parents[1]
        status=Path(self.tmp.name)/'entry-status.json';marker=Path(self.tmp.name)/'begin-requested'
        code=f'''
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from unittest.mock import patch
from PySide6 import QtCore,QtWidgets
from gnautilus_acquisition.gui import run_gui
from gnautilus_acquisition.qt_gui import Workbench
app=QtWidgets.QApplication([])
status=Path({str(status)!r});marker=Path({str(marker)!r})
def make_window():
    window=Workbench(settings_path=status.with_name('settings.json'));window.busy=True
    QtCore.QTimer.singleShot(2400,lambda:setattr(window,'busy',False))
    return window
def begin(window):marker.write_text('explicit request')
with patch('gnautilus_acquisition.qt_gui.Workbench',side_effect=make_window),patch.object(Workbench,'begin',begin):
    QtCore.QTimer.singleShot(4000,app.quit)
    run_gui(start_receiving=True,status_path=status)
assert marker.exists() and status.exists()
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=root,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(marker.read_text(),'explicit request')
        self.assertEqual(json.loads(status.read_text('utf-8'))['schema'],'gnautilus.runtime.v1')

    def test_runtime_snapshot_never_exports_configuration_or_transport_secrets(self):
        self.c.snapshot=lambda:dict(state='receiving',samples=42,descriptor=dict(self.c.descriptor,configuration={'private':'DO-NOT-EXPORT'}),network=dict(connected=True,secret='DO-NOT-EXPORT',hub=dict(sample_index_end=42,secret='DO-NOT-EXPORT')))
        result=self.w.runtime_snapshot()
        self.assertEqual(result['client']['samples'],42)
        self.assertEqual(result['hub']['sample_index_end'],42)
        self.assertNotIn('DO-NOT-EXPORT',json.dumps(result))

    def test_capture_pad_press_hold_and_release_marks_periods_and_reverts_to_unknown(self):
        for lbl in ('FORWARD','LEFT','REST','RIGHT','BACKWARD'):
            self.assertTrue(self.w.capture_buttons[lbl].isVisible())
        for lbl in ('FORWARD_LEFT','FORWARD_RIGHT','BACKWARD_LEFT','BACKWARD_RIGHT'):
            self.assertFalse(self.w.capture_buttons[lbl].isVisible())
        self.w.capture_combos.setChecked(True);self.app.processEvents()
        for lbl in ('FORWARD_LEFT','FORWARD_RIGHT','BACKWARD_LEFT','BACKWARD_RIGHT'):
            self.assertTrue(self.w.capture_buttons[lbl].isVisible())
        self.w.capture_combos.setChecked(False);self.app.processEvents()
        self.w.start_capture();self.app.processEvents()
        self.assertTrue(self.c.capture['active'])
        self.assertIn('松开 · 未标注',self.w.capture_state.text())
        fwd_btn=self.w.capture_buttons['FORWARD']
        pos=QtCore.QPointF(fwd_btn.width()/2,fwd_btn.height()/2)
        press=QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,pos,pos,QtCore.Qt.LeftButton,QtCore.Qt.LeftButton,QtCore.Qt.NoModifier)
        fwd_btn.mousePressEvent(press);self.app.processEvents()
        self.assertEqual(self.c.capture['held'],'FORWARD')
        self.assertTrue(fwd_btn.held)
        self.assertIn('正在标注：向前',self.w.capture_state.text())
        release=QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,pos,pos,QtCore.Qt.LeftButton,QtCore.Qt.NoButton,QtCore.Qt.NoModifier)
        fwd_btn.mouseReleaseEvent(release);self.app.processEvents()
        self.assertIsNone(self.c.capture['held'])
        self.assertFalse(fwd_btn.held)
        self.assertEqual(self.c.capture['periods_completed'],1)
        self.assertIn('松开 · 未标注',self.w.capture_state.text())
        key_down=QtGui.QKeyEvent(QtCore.QEvent.KeyPress,QtCore.Qt.Key_Space,QtCore.Qt.NoModifier,' ',False,1)
        self.w.keyPressEvent(key_down);self.app.processEvents()
        self.assertEqual(self.c.capture['held'],'REST')
        self.assertTrue(self.w.capture_buttons['REST'].held)
        self.assertIn('正在标注：静止',self.w.capture_state.text())
        key_up=QtGui.QKeyEvent(QtCore.QEvent.KeyRelease,QtCore.Qt.Key_Space,QtCore.Qt.NoModifier,' ',False,1)
        self.w.keyReleaseEvent(key_up);self.app.processEvents()
        self.assertIsNone(self.c.capture['held'])
        self.assertEqual(self.c.capture['periods_completed'],2)
        esc=QtGui.QKeyEvent(QtCore.QEvent.KeyPress,QtCore.Qt.Key_Escape,QtCore.Qt.NoModifier,'',False,1)
        self.w.keyPressEvent(esc);self.app.processEvents()
        self.assertFalse(self.c.capture['active'])
        self.assertIn('已保存',self.w.capture_state.text())
        self.w.filter.setCurrentText('标签');self.app.processEvents()
        self.assertIn('FORWARD',self.w.log_view.toPlainText())

    def test_connection_mode_switches_between_public_relay_and_lan_ws_hotspot(self):
        self.c.running=False;self.w.controller.collector=None
        self.w.fields['lan_host'].setText('192.168.137.1:8768')
        idx=self.w.connection_mode_box.findData('lan_ws')
        self.w.connection_mode_box.setCurrentIndex(idx);self.app.processEvents()
        self.assertIsNotNone(self.w.update_enrollment)
        self.assertEqual(self.w.update_enrollment['mode'],'lan_ws')
        self.assertEqual(self.w.update_enrollment['host'],'192.168.137.1')
        self.assertIn('192.168.137.1:8768',self.w.pairing_status.text())
        self.assertIn('WebSocket',self.w.pairing_status.text())
        idx_auto=self.w.connection_mode_box.findData('automatic')
        self.w.connection_mode_box.setCurrentIndex(idx_auto);self.app.processEvents()
        self.assertEqual(self.w.profile()['connection_mode'],'automatic')


if __name__=='__main__':unittest.main()
