import os,time,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
from gnautilus_acquisition.qt_gui import Workbench

class UpdateIdle(unittest.TestCase):
    def test_missing_stale_or_busy_hub_cannot_grant_install(self):
        state=SimpleNamespace(controller=SimpleNamespace(running=False),recovery=None,busy=False)
        self.assertFalse(Workbench.update_idle(state))
        state.update_hub=dict(recording_active=False,motion_state='STOP');state.update_hub_at=time.monotonic()
        self.assertTrue(Workbench.update_idle(state))
        state.update_hub_at-=6;self.assertFalse(Workbench.update_idle(state))
        state.update_hub_at=time.monotonic();state.update_hub['recording_active']=True
        self.assertFalse(Workbench.update_idle(state))
        state.update_hub.update(recording_active=False,motion_state='UNKNOWN');self.assertFalse(Workbench.update_idle(state))
        state.update_hub['motion_state']='STOP';state.controller.running=True;self.assertFalse(Workbench.update_idle(state))

if __name__=='__main__':unittest.main()
