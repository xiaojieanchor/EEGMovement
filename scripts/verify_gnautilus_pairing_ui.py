"""Exercise our own Qt widgets against the explicitly provisioned local hub."""
from pathlib import Path
import argparse,json,os,sys,tempfile,time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def main():
    from PySide6 import QtWidgets
    from gnautilus_acquisition.qt_gui import Workbench
    from gnautilus_acquisition.gui import load_enrollment
    from gnautilus_acquisition import __version__
    parser=argparse.ArgumentParser();parser.add_argument('--pairing',required=True);args=parser.parse_args()
    pairing=load_enrollment(args.pairing);dest=ROOT/'docs/gnautilus/integration_20261022/connection_fix_v101'
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        w=Workbench(settings_path=Path(temp)/'settings.json');w.resize(1420,1000);w.show();app.processEvents()
        w.probe();w.refresh();missing=[row['message'] for row in w.events.snapshot()]
        assert any('未选择中枢配对文件' in m for m in missing)
        assert not any('开始测试 TCP' in m for m in missing)
        w.fields['pairing_path'].setText(args.pairing);w.probe();deadline=time.monotonic()+10
        while w.busy and time.monotonic()<deadline:app.processEvents();w.refresh();time.sleep(.01)
        assert not w.busy and w.last_probe['connected'] is True
        assert w.settings.load()['pairing_path']==args.pairing
        logs=w.events.snapshot();content=json.dumps(logs,ensure_ascii=False);assert pairing['secret'] not in content
        app.processEvents();w.grab().save(str(dest/'ui-paired-local-hub.png'));w.close()
    result=dict(version=__version__,mode='Qt offscreen to local WLAN hub',missing_pairing_rejected_before_tcp=True,explicit_target=f"{pairing['host']}:{pairing['port']}",
        authenticated_hub_probe=True,selected_path_persisted=True,credentials_absent_from_logs=True,log_events=logs,remote_pc_verified=False,device_accessed=False)
    (dest/'ui-connection-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k!='log_events'},ensure_ascii=False))

if __name__=='__main__':main()
