"""Monitor only this worker's HTTP accept loop; never touch other processes."""
import faulthandler,os,threading,time
from pathlib import Path


class ProgressWatchdog:
    def __init__(self,log_root,limit=30.,interval=2.,terminate=os._exit):
        self.log_root=Path(log_root);self.limit=limit;self.interval=interval;self.terminate=terminate
        self.last_progress=time.monotonic();self.closed=threading.Event();self.thread=None
    def pulse(self):self.last_progress=time.monotonic()
    def start(self):
        self.pulse();self.thread=threading.Thread(target=self._run,daemon=True,name='compute-http-watchdog');self.thread.start()
    def _run(self):
        while not self.closed.wait(self.interval):
            if time.monotonic()-self.last_progress<=self.limit:continue
            try:
                self.log_root.mkdir(parents=True,exist_ok=True)
                with (self.log_root/('service-stall-'+str(time.time_ns())+'.log')).open('w') as f:
                    f.write('Own worker HTTP accept loop made no progress; supervisor restart required.\n');f.flush()
                    faulthandler.dump_traceback(file=f,all_threads=True);f.flush();os.fsync(f.fileno())
            finally:self.terminate(70)
            return
    def close(self):
        self.closed.set()
        if self.thread:self.thread.join(timeout=max(1,self.interval*2))
