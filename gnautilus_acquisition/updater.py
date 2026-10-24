"""Background update monitor. Applies only when this receiver and the Hub are stopped."""
import json,os,subprocess,threading,time
from pathlib import Path
from eeg_relay.update import verify_manifest,fetch,download,atomic
from . import __version__

def public_config():return json.loads(Path(__file__).with_name('relay_public.json').read_text('utf-8'))

class UpdateMonitor:
    def __init__(self,idle,on_event,on_ready,root=None,idle_refresh=lambda:None):
        self.idle,self.on_event,self.on_ready=idle,on_event,on_ready;self.stop_event=threading.Event();self.wake=threading.Event();self.state='waiting'
        self.root=Path(root or Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'EEGMovement/gNautilus-app');self.state_path=self.root/'updates/state.json'
        self.applying=False;self.idle_refresh=idle_refresh
    def is_idle(self):
        self.idle_refresh();return self.idle()
    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='Signed application updates');self.thread.start();return self
    def check(self):self.wake.set()
    def run(self):
        if self.stop_event.wait(5):return
        while not self.stop_event.is_set():
            try:
                config=public_config();state=json.loads(self.state_path.read_text('utf-8')) if self.state_path.exists() else {}
                raw,proxy=fetch(config,'updates/windows-x64.json');m=verify_manifest(raw,config['release_public_key'],'windows-x64',max(0,state.get('release_seq',0)-1))
                if tuple(map(int,m['version'].split('.')))<=tuple(map(int,__version__.split('.'))):
                    atomic(self.state_path,dict(state,release_seq=m['release_seq'],version=__version__));self.state='current'
                else:
                    self.state='found';self.on_event('更新','INFO','发现 '+m['version']+'；等待采集与动作停止后下载')
                    while not self.is_idle() and not self.stop_event.wait(1):self.state='waiting_idle'
                    if self.stop_event.is_set():return
                    path=download(config,m,self.root/'updates',proxy=proxy,busy=lambda:not self.is_idle(),stopped=self.stop_event.is_set,on_state=self.progress)
                    manifest=path.with_suffix('.manifest.json');manifest.write_bytes(raw);self.state='verified';self.on_event('更新','INFO','安装包签名与SHA256已验证，等待安全安装')
                    while not self.is_idle() and not self.stop_event.wait(1):self.state='waiting_idle'
                    if self.stop_event.is_set():return
                    launcher=self.root/'EEGMovement-Launcher.exe'
                    if launcher.exists():self.applying=True;self.on_ready(path,manifest)
                    else:self.on_event('更新','WARN','当前为便携运行：请先运行公网版安装器以启用自动安装；下载包已保留')
            except InterruptedError:self.state='waiting_idle'
            except Exception as error:
                self.state='check_failed';self.on_event('更新','WARN',str(error)[:220]+'；当前程序继续使用')
            self.wake.wait(600);self.wake.clear()
    def progress(self,state,done,total):self.state=state
    def stop(self):self.stop_event.set();self.wake.set()
