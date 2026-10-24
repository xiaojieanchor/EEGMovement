"""Per-user installer, version launcher and signed updater. No vendor changes."""
import argparse,json,os,shutil,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from eeg_relay.update import verify_manifest,file_hash,extract_version,activate,atomic

def bundle(name):return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/name
def install_root():return Path(os.environ.get('EEGMOVEMENT_INSTALL_ROOT') or Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'EEGMovement/gNautilus-app')
def config():return json.loads(bundle('relay_public.json').read_text('utf-8'))
def launch(root,dir,health_path=None):
    args=[str(root/dir/'EEGMovement-gNautilus.exe')]
    if health_path:args+=['--health-file',str(health_path)]
    return subprocess.Popen(args,cwd=root/dir,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
def health(root,candidate,expected_version=None):
    report=root/'updates'/('health-'+uuid.uuid4().hex+'.json');report.parent.mkdir(parents=True,exist_ok=True)
    process=launch(root,str(candidate.relative_to(root)),report)
    for _ in range(150):
        if report.exists():
            try:
                value=json.loads(report.read_text('utf-8'))
                if value.get('ready') and value.get('pid')==process.pid and (expected_version is None or value.get('version')==expected_version) and process.poll() is None:return True
            except Exception:pass
        if process.poll() is not None:break
        time.sleep(.2)
    if process.poll() is None:process.terminate();process.wait(timeout=10)
    return False
def close_installed_app(root):
    """Ask only this installed app to close and finish saving; never terminate it."""
    if os.name!='nt':return
    import ctypes
    from ctypes import wintypes
    import psutil
    active=[]
    for process in psutil.process_iter(['pid','name','exe']):
        if (process.info.get('name') or '').lower()!='eegmovement-gnautilus.exe':continue
        executable=process.info.get('exe')
        if not executable or not Path(executable).resolve().is_relative_to(root.resolve()):
            raise RuntimeError('另一个采集工作台正在运行。请结束接收并关闭旧窗口后重新运行安装器；尚未切换版本。')
        active.append(process)
    if not active:return
    pids={p.pid for p in active};user=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];user.EnumWindows.restype=wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    @callback_type
    def close_window(window,_):
        pid=wintypes.DWORD();user.GetWindowThreadProcessId(window,ctypes.byref(pid))
        if pid.value in pids:user.PostMessageW(window,0x10,0,0)  # WM_CLOSE: Qt drains its own collector.
        return True
    user.EnumWindows(close_window,0)
    _,alive=psutil.wait_procs(active,timeout=30)
    if alive:raise RuntimeError('旧采集工作台仍在保存或退出，未强制终止。请等待保存完成并关闭旧窗口后再安装。')

def apply(root,archive,envelope,allow_same=False):
    cfg=config();state=root/'updates/state.json';minimum=json.loads(state.read_text('utf-8')).get('installed_release_seq',0) if state.exists() else 0
    manifest=verify_manifest(Path(envelope).read_bytes(),cfg['release_public_key'],'windows-x64',max(0,minimum-1) if allow_same else minimum)
    archive=Path(archive)
    if archive.stat().st_size!=manifest['size'] or file_hash(archive)!=manifest['sha256']:raise ValueError('安装包身份不符')
    close_installed_app(root)
    directory='versions/'+manifest['version']+'-'+uuid.uuid4().hex[:8];target=root/directory
    if shutil.disk_usage(root).free<manifest['size']*4+256*1024**2:raise OSError('安装磁盘空间不足')
    extract_version(archive,target)
    try:activate(root,manifest['version'],directory,lambda p:health(root,p,manifest['version']))
    except RuntimeError:
        previous=json.loads((root/'current.json').read_text('utf-8')) if (root/'current.json').exists() else None
        if previous:launch(root,previous['directory'])
        raise
    atomic(state,dict(installed_release_seq=manifest['release_seq'],release_seq=manifest['release_seq'],version=manifest['version']))
def main():
    p=argparse.ArgumentParser();p.add_argument('--apply');p.add_argument('--manifest');p.add_argument('--wait-pid',type=int);p.add_argument('--install',action='store_true');p.add_argument('--no-shortcut',action='store_true');args=p.parse_args()
    root=install_root();root.mkdir(parents=True,exist_ok=True)
    if args.wait_pid and os.name=='nt':
        import ctypes
        from ctypes import wintypes
        api=ctypes.windll.kernel32
        api.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];api.OpenProcess.restype=wintypes.HANDLE
        api.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];api.WaitForSingleObject.restype=wintypes.DWORD
        api.CloseHandle.argtypes=[wintypes.HANDLE];api.CloseHandle.restype=wintypes.BOOL
        h=api.OpenProcess(0x100000,False,args.wait_pid)
        if h:
            result=ctypes.windll.kernel32.WaitForSingleObject(h,60000);ctypes.windll.kernel32.CloseHandle(h)
            if result!=0:raise RuntimeError('主程序尚未退出，保留当前版本')
    if args.apply:
        if not args.manifest:raise ValueError('Signed manifest required')
        return apply(root,args.apply,args.manifest)
    current=root/'current.json'
    embedded=bundle('bootstrap.zip')
    # A full Setup contains bootstrap.zip. Double-clicking it always enters the
    # installer path, even when current.json points to an older installation.
    if args.install or embedded.exists() or not current.exists():
        if not embedded.exists():raise ValueError('请运行完整安装器')
        updater=bundle('EEGMovement-Launcher.exe')
        if updater.exists():shutil.copy2(updater,root/'EEGMovement-Launcher.exe')
        apply(root,embedded,bundle('bootstrap.manifest.json'),allow_same=True)
        if os.name=='nt' and not args.no_shortcut:
            desktop=Path.home()/'Desktop/EEGMovement采集工作台.cmd';desktop.write_text('@start "" "'+str(root/'EEGMovement-Launcher.exe')+'"\r\n',encoding='utf-8')
    else:
        directory=json.loads(current.read_text('utf-8'))['directory'];candidate=(root/directory).resolve()
        if not candidate.is_relative_to(root.resolve()):raise ValueError('Invalid active version')
        launch(root,directory)
if __name__=='__main__':
    try:main()
    except Exception as e:
        root=install_root();root.mkdir(parents=True,exist_ok=True);atomic(root/'update-error.json',dict(error=str(e),time=time.time()))
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(e),'EEGMovement 安装／更新需要检查',0x10)
        raise
