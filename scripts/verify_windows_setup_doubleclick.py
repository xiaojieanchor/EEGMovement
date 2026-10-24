"""Real native upgrade: installed/running 1.1.0 -> Setup with no command-line flags."""
import ctypes,json,os,subprocess,sys,time
from ctypes import wintypes
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_public_release import APP_VERSION


def titles(pid):
    user=ctypes.WinDLL('user32',use_last_error=True);found=[]
    kind=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.EnumWindows.argtypes=[kind,wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    @kind
    def visit(window,_):
        value=wintypes.DWORD();user.GetWindowThreadProcessId(window,ctypes.byref(value))
        if value.value==pid:
            text=ctypes.create_unicode_buffer(1024);user.GetWindowTextW(window,text,1024)
            if text.value:found.append(text.value)
        return True
    user.EnumWindows(visit,0);return found


def receipt(target,version):
    for path in sorted((target/'updates').glob('health-*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        value=json.loads(path.read_text())
        if value.get('ready') and value.get('version')==version:
            process=psutil.Process(value['pid'])
            if Path(process.exe()).resolve().is_relative_to(target.resolve()):return process,value
    raise RuntimeError('No matching real native health receipt '+version)


def main():
    target=ROOT/('outputs/relay_20261023/doubleclick-check-'+str(time.time_ns()));target.mkdir()
    profile=target/'profile';(profile/'Desktop').mkdir(parents=True)
    env=dict(os.environ,EEGMOVEMENT_INSTALL_ROOT=str(target),LOCALAPPDATA=str(profile),USERPROFILE=str(profile),QT_QPA_PLATFORM='windows')
    release=ROOT/'outputs/relay_20261023/release';processes=[]
    try:
        subprocess.run([str(release/'EEGMovement-Setup-1.1.0.exe'),'--install','--no-shortcut'],env=env,check=True,timeout=120)
        old,old_health=receipt(target,'1.1.0');processes.append(old)
        before=titles(old.pid);assert any('v1.1.0' in name for name in before),before
        marker=profile/'EEGMovement/gNautilus/recordings/preserve.txt';marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('software-only preservation fixture')
        # Exactly how a double-click starts Setup: no --install or other flags.
        subprocess.run([str(release/f'EEGMovement-Setup-{APP_VERSION}.exe')],env=env,check=True,timeout=150)
        new,new_health=receipt(target,APP_VERSION);processes.append(new)
        after=titles(new.pid);assert any('v'+APP_VERSION in name for name in after),after
        assert not old.is_running(),'Old process must gracefully exit before switching'
        active=json.loads((target/'current.json').read_text());assert active['version']==APP_VERSION
        assert active['previous']['version']=='1.1.0'
        assert marker.read_text()=='software-only preservation fixture'
        shortcut=profile/'Desktop/EEGMovement采集工作台.cmd';assert shortcut.exists()
        # A second double-click repairs/reinstalls the same signed version safely.
        subprocess.run([str(release/f'EEGMovement-Setup-{APP_VERSION}.exe')],env=env,check=True,timeout=150)
        repeated,repeated_health=receipt(target,APP_VERSION);processes.append(repeated)
        assert repeated.pid!=new.pid and not new.is_running()
        repeat_titles=titles(repeated.pid);assert any('v'+APP_VERSION in name for name in repeat_titles)
        result=dict(scope='real native Setup without arguments, existing running old version',
                    old_version='1.1.0',new_version=APP_VERSION,old_window_titles=before,new_window_titles=after,
                    old_process_closed_gracefully=True,health_version_matches_manifest=True,
                    current_pointer_updated=True,repeated_double_click_passed=True,recording_directory_preserved=True,
                    shortcut_created_in_isolated_profile=True,real_eeg=False,other_pc=False)
        out=ROOT/'docs/gnautilus/setup_upgrade_fix_20261023';(out/'doubleclick-native.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True))
    finally:
        for process in processes:
            try:
                if process.is_running() and Path(process.exe()).resolve().is_relative_to(target.resolve()):process.terminate();process.wait(10)
            except psutil.NoSuchProcess:pass


if __name__=='__main__':main()
