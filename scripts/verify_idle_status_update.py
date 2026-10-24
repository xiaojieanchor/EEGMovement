"""Actual public idle status persists while checking signed software updates."""
import json,os,sys,tempfile,time,urllib.request
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    from PySide6 import QtWidgets
    from gnautilus_acquisition.qt_gui import Workbench
    from gnautilus_acquisition import __version__
    out=ROOT/'docs/gnautilus/status_link_fix_20261023'
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory() as folder:
        window=Workbench(settings_path=Path(folder)/'settings.json');window.show();app.processEvents();window.probe()
        deadline=time.monotonic()+30
        try:
            while not window.probed_at and time.monotonic()<deadline:
                app.processEvents();window.refresh();time.sleep(.02)
            assert window.probed_at,'No initial Hub acknowledgement'
            window.updater.start();window.update_button.click()
            samples=[];end=time.monotonic()+15
            while time.monotonic()<end:
                app.processEvents();window.refresh()
                link=window.status_link.snapshot()
                samples.append(dict(connected=link['connected'],hub_fresh=link['hub_state_fresh'],transport=window.feedback_labels['transport'].text(),update=window.updater.state))
                time.sleep(.1)
            assert all(s['connected'] and s['hub_fresh'] for s in samples)
            assert window.updater.state=='current',window.updater.state
            with urllib.request.urlopen('http://127.0.0.1:8765/api/relay/status',timeout=5) as response:relay=json.load(response)
            report=next(c for c in relay['acquisition_clients'] if c['reported']['app_version']==__version__)
            assert report['status_fresh'] and report['reported']['state']=='idle'
            assert report['reported']['sampling_rate'] is None and report['reported']['channels']==[]
            window.resize(1420,900);app.processEvents();window.grab().save(str(out/'idle-after-update.png'))
            result=dict(version=__version__,observation_seconds=15,update_button_clicked=True,update_check_completed=True,
                        persistent_signed_hub_status=True,no_manual_pairing=True,snapshots=len(samples),all_fresh=True,
                        hub_report=report,real_eeg=False,other_pc=False)
            (out/'live-idle-update.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=True))
        finally:
            window.updater.stop()
            if window.status_link:window.status_link.stop()
            window.close();app.processEvents()

if __name__=='__main__':main()
