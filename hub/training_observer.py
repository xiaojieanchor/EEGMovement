"""Task-independent local telemetry worker; credentials use the existing private pipe."""
import os,sys
from pathlib import Path

class ObserverLock:
    def __init__(self,root):self.path=Path(root)/'hub/runtime/retraining_observer.lock';self.handle=None
    def acquire(self):
        if self.handle is not None:return True
        self.path.parent.mkdir(parents=True,exist_ok=True);handle=self.path.open('a+b')
        if self.path.stat().st_size==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:handle.close();return False
        self.handle=handle;return True
    def close(self):
        if self.handle is None:return
        handle=self.handle;self.handle=None
        try:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:handle.close()

def observer_running(root):
    probe=ObserverLock(root)
    if not probe.acquire():return True
    probe.close();return False

def ensure_background_observer(root,password):
    root=Path(root)
    if observer_running(root):return None
    if not isinstance(password,str) or not password:raise ValueError('In-memory server credential required')
    from scripts.windows_background import spawn_with_secret
    python=Path(sys.executable).with_name('pythonw.exe')
    if not python.exists():python=Path(sys.executable)
    return spawn_with_secret([str(python),str(root/'scripts/watch_retraining.py'),'--worker'],root,password)
