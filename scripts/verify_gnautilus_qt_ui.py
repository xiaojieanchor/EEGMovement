"""Render our own widgets offscreen using marked software fixtures, without hardware."""
from pathlib import Path
import os,sys,json,tempfile,time,statistics
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PySide6 import QtWidgets
from gnautilus_acquisition.qt_gui import Workbench
from tests.test_gnautilus_qt import FixtureController

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
    dest=Path(args.output) if args.output else ROOT/'docs/gnautilus/integration_20261022/verification/ui';dest.mkdir(parents=True,exist_ok=True)
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        idle=Workbench(settings_path=Path(temp)/'idle.json');idle.resize(1420,900);idle.show();app.processEvents();idle.grab().save(str(dest/'idle.png'));idle.close()
        c=FixtureController();w=Workbench(c,Path(temp)/'fixture.json');w.resize(1420,900);w.show();app.processEvents();w.refresh_preview()
        w.subtitle.setText('软件界面验收 · 合成波形夹具 · 无真实EEG / 无设备连接');app.processEvents();w.grab().save(str(dest/'software-fixture.png'))
        times=[]
        for _ in range(100):
            start=time.perf_counter();w.wave.grab();times.append((time.perf_counter()-start)*1000)
        w.resize(1024,720);app.processEvents();w.grab().save(str(dest/'compact.png'))
        c.running=False;w.close()
    result=dict(mode='Qt offscreen software fixture',waveform_channels_visible=8,seconds_visible=5,paint_ms_median=round(statistics.median(times),3),paint_ms_p95=round(sorted(times)[94],3),native_desktop_performance_verified=False,hardware_verified=False,images=['idle.png','software-fixture.png','compact.png'])
    (dest/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
