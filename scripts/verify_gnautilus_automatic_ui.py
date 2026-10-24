"""Own Qt application to the real relay/Hub, with no identity supplied by the operator."""
import json,os,sys,tempfile,time
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    from PySide6 import QtWidgets
    from gnautilus_acquisition.qt_gui import Workbench
    from gnautilus_acquisition import __version__
    dest=ROOT/'docs/gnautilus/pairing_ui_fix_20261023'
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory() as folder:
        w=Workbench(settings_path=Path(folder)/'settings.json');w.resize(1420,900);w.show();app.processEvents()
        assert 'pairing_path' not in w.fields
        assert not w.identity_path.exists()
        w.probe();deadline=time.monotonic()+45
        try:
            while not w.probed_at and time.monotonic()<deadline:
                app.processEvents();w.refresh();time.sleep(.01)
            assert w.probed_at and w.last_probe.get('connected'), w.enrollment_state
            assert w.identity_path.is_file()
            credentials=json.loads(w.identity_path.read_text())
            content=json.dumps(w.events.snapshot(),ensure_ascii=False)
            assert credentials['secret'] not in content and credentials['auth_secret'] not in content
            assert '复制' not in content and '请选择' not in content
            w.grab().save(str(dest/'automatic-live.png'))
            result=dict(version=__version__,public_connection_test=True,automatic_internal_identity=True,
                        manual_file_supplied=False,pairing_file_widgets=0,legacy_ui_present=False,
                        end_to_end_hub_reply=True,credential_in_log=False,hardware_accessed=False,
                        other_pc_verified=False,elapsed_ms=w.last_probe['elapsed_ms'])
            (dest/'automatic-live.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
        finally:
            w.close();app.processEvents()
            if w.enrollment_thread:w.enrollment_thread.join(10)

if __name__=='__main__':main()
