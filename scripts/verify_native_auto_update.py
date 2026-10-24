"""This PC's idle installed 1.1.4 updates through the actual HTTPS updater."""
import json,os,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts import gnautilus_launcher
from scripts.verify_windows_setup_doubleclick import titles
from scripts.build_public_release import APP_VERSION

def main():
    root=Path(os.environ['LOCALAPPDATA'])/'EEGMovement/gNautilus-app'
    before=json.loads((root/'current.json').read_text())
    if before['version']!='1.1.4':raise ValueError('This check requires the actual existing idle 1.1.4 installation')
    began=time.monotonic();launcher=root/'EEGMovement-Launcher.exe'
    subprocess.run([str(launcher)],check=True,timeout=20)
    result=None
    try:
        while time.monotonic()-began<150:
            current=json.loads((root/'current.json').read_text())
            if current['version']==APP_VERSION:
                for path in sorted((root/'updates').glob('health-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
                    value=json.loads(path.read_text())
                    if value.get('version')==APP_VERSION and value.get('ready'):
                        names=titles(value['pid'])
                        if any('v'+APP_VERSION in name for name in names):
                            result=dict(previous_version=before['version'],version=APP_VERSION,actual_window_titles=names,installed_release_seq=json.loads((root/'updates/state.json').read_text()).get('installed_release_seq'),seconds=round(time.monotonic()-began,2),manual_setup_used=False,real_eeg=False,other_pc=False)
                            break
            if result:break
            time.sleep(1)
        if result is None:raise TimeoutError('Native automatic update not completed')
        time.sleep(3)
        with urllib.request.urlopen('http://127.0.0.1:8765/api/relay/status',timeout=5) as response:relay=json.load(response)
        report=next(row for row in relay['acquisition_clients'] if row['reported']['app_version']==APP_VERSION and row['status_fresh'])
        result['new_version_live_status']=True;result['state']=report['reported']['state']
        (ROOT/'docs/gnautilus/status_link_fix_20261023/native-auto-update.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=True))
    finally:
        # Leave the single EEG role free for the user's actual acquisition PC.
        gnautilus_launcher.close_installed_app(root)

if __name__=='__main__':main()
