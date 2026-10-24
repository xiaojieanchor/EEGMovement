"""Installed NeuraDock native QC, isolated from acquisition and GUI state."""
import json
import os
import queue
import subprocess
import threading
import time
import tempfile
from pathlib import Path


def green_channels(quality, native, stable=None):
    states = {1: ('green','厂家引擎通过'), 0: ('red','50Hz干扰'),
              2: ('cyan','肌电伪迹'), -1: ('gray','无有效信号'), 3: ('gray','无有效信号')}
    channels=[];mask=[]
    for i in range(7):
        base=dict(quality.get('channels',[])[i]) if len(quality.get('channels',[]))==7 else dict(valid=False,reasons=['质量窗口不足'])
        code=native[i] if isinstance(native,list) and len(native)==7 and type(native[i]) is int else None
        color,label=states.get(code,('gray','厂家引擎状态未知'))
        allowed=code==1 and base.get('valid') is True
        if code==1 and not allowed:color,label='amber','软件补充检查未通过'
        if allowed and (stable is None or stable[i] is not True):allowed=False;color,label='amber','恢复确认中'
        base.update(name='Ch'+str(i+1),native_code=code,color=color,quality_label=label,training_eligible=allowed)
        channels.append(base);mask.append(allowed)
    return dict(channels=channels,training_mask=mask,training_good_channels=sum(mask),
                native_method='installed NeuraDock JNI engine; recomputed, not screen state')


def pending_quality(previous,label):
    """Keep the last measured electrode state visible, never its training lease."""
    result=dict(previous)
    result.update(valid=False,state='warming',label=label,good_channels=0,
                  training_mask=[False]*7,training_good_channels=0,measurement_held=True)
    origin=previous.get('measurement_origin_monotonic') or previous.get('native_checked_monotonic') or previous.get('checked_monotonic')
    result['measurement_origin_monotonic']=origin
    result['measurement_age_ms']=round(max(0,time.monotonic()-origin)*1000,1) if origin is not None else None
    result['channels']=[dict(c,training_eligible=False,measurement_held=True,
        quality_label='上次：'+c.get('quality_label','未知').removeprefix('上次：')) for c in previous.get('channels',[])]
    return result


class NativeQualityPending(RuntimeError):
    """Startup is still in progress; keep the process, exclude all channels."""


class NativeQuality:
    def __init__(self,root,clock=time.monotonic):
        self.root=Path(root);self.process=None;self.lines=queue.Queue(maxsize=32);self.error=None
        self.last_attempt=-100;self.eligible_since=[None]*7
        self.last_evaluated=-100
        self.home=None
        self.clock=clock;self.native=None;self.native_at=None;self.native_end=None;self.native_epoch=None
        self.calls=0
        self.ready=False;self.starting_at=None
        self.certified=[False]*7;self.status_pending=[None]*7;self.status_retry=False

    def _reader(self,stream,lines):
        try:
            for line in stream:
                try:lines.put_nowait(line.strip())
                except queue.Full:break
        except (OSError,ValueError):pass
        finally:
            try:lines.put_nowait('EOF')
            except queue.Full:pass

    def close(self):
        p=self.process;self.process=None;self.eligible_since=[None]*7
        self.ready=False;self.starting_at=None
        self.native=None;self.native_at=None;self.native_end=None
        self.certified=[False]*7;self.status_pending=[None]*7;self.status_retry=False
        if p is not None:
            p.kill()
            try:p.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
            for s in [p.stdin,p.stdout,p.stderr]:
                if s:s.close()
        if self.home is not None:
            folder=Path(self.home.name).resolve()
            if not folder.is_relative_to((self.root/'hub/runtime/native-quality').resolve()):raise RuntimeError('QC scratch path escaped workspace')
            try:self.home.cleanup()
            except OSError:pass
            self.home=None

    def _start(self):
        self.last_attempt=time.monotonic()
        java=Path('G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/OpenJDK/bin/java.exe')
        classes=self.root/'hub/runtime/native-quality'
        source=self.root/'scripts/native_quality/NeuraQualityBridge.java'
        binary=classes/'NeuraQualityBridge.class'
        if not java.is_file() or not source.is_file():raise RuntimeError('Native QC bridge prerequisites missing')
        classes.mkdir(parents=True,exist_ok=True)
        if not binary.is_file() or binary.stat().st_mtime_ns<source.stat().st_mtime_ns:
            result=subprocess.run([str(java.with_name('javac.exe')),'-J-Xmx128m','-cp',str(self.root/'NeuraDock/app/*'),'-d',str(classes),str(source)],
                capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise RuntimeError('Native QC bridge compilation failed')
        self.lines=queue.Queue(maxsize=32)
        self.home=tempfile.TemporaryDirectory(prefix='worker-',dir=classes)
        self.process=subprocess.Popen([str(java),'-Xmx96m','-Duser.home='+self.home.name,'-cp',str(classes)+os.pathsep+str(self.root/'NeuraDock/app/*'),'NeuraQualityBridge'],
            cwd=classes,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.ready=False;self.starting_at=time.monotonic()
        threading.Thread(target=self._reader,args=(self.process.stdout,self.lines),daemon=True).start()

    def _ensure_ready(self):
        if self.ready:return
        for _ in range(24):
            try:line=self.lines.get_nowait()
            except queue.Empty:break
            if line=='READY':self.ready=True;return
            if line=='EOF':raise RuntimeError('Native QC exited during initialization')
        if self.starting_at is None or time.monotonic()-self.starting_at>30:raise TimeoutError('Native QC initialization exceeded30s')
        raise NativeQualityPending('厂家质量引擎初始化中；就绪前排除全部通道')

    def _detect(self,raw):
        if self.process is None:
            if time.monotonic()-self.last_attempt<5:raise NativeQualityPending('厂家质量引擎正在等待重试；就绪前排除全部通道')
            self._start()
        self._ensure_ready()
        self.process.stdin.write(str(len(raw))+','+','.join(str(float(v)) for row in raw for v in row)+'\n');self.process.stdin.flush()
        deadline=time.monotonic()+1
        for _ in range(24):
            line=self.lines.get(timeout=max(.001,deadline-time.monotonic()))
            if line.startswith('Q '):break
            if line=='EOF' or time.monotonic()>deadline:raise RuntimeError('Native QC invalid response')
        else:raise RuntimeError('Native QC excessive diagnostics')
        native=json.loads(line[2:])
        if len(native)!=7 or any(type(v) is not int for v in native):raise RuntimeError('Native QC invalid states')
        return native

    def evaluate(self,raw,quality,continuity_ok=True,sample_index_end=None,source_epoch=None):
        now=self.clock();updated=False
        if source_epoch!=self.native_epoch:
            self.native=None;self.native_at=None;self.native_end=None;self.eligible_since=[None]*7;self.native_epoch=source_epoch
            self.certified=[False]*7;self.status_pending=[None]*7;self.status_retry=False
        # Vendor UI waits 1024 ms and consumes its accumulated batch. Do not
        # repeatedly classify 90%-overlapping windows as independent evidence.
        due=sample_index_end is None or self.native_end is None or sample_index_end-self.native_end>=256
        # One explicit overlap recheck can disprove a status-only flicker. It is
        # not an independent new QC batch and never bypasses the software guard.
        status_recheck=bool(not due and not self.status_retry and any(p is not None for p in self.status_pending)
            and sample_index_end is not None and self.native_end is not None and sample_index_end-self.native_end>=25)
        if status_recheck:due=True;self.status_retry=True
        try:
            if self.process is not None and self.process.poll() is not None:raise RuntimeError('Native QC process exited')
            if due:
                self.native=self._detect(raw);self.native_at=self.clock();self.native_end=sample_index_end
                self.calls+=1;updated=True;self.error=None
        except NativeQualityPending as error:
            # Never stall the 100ms quality/event timeline while a cold JVM is
            # launching, and never kill a healthy initializing process at8s.
            self.error=str(error)
        except Exception as error:
            self.error=type(error).__name__+': '+str(error)[:140];self.close()
        now=self.clock();native=self.native if self.native_at is not None and 0<=now-self.native_at<1.5 else None
        stable=[]
        if now-self.last_evaluated>1.5:self.eligible_since=[None]*7;self.certified=[False]*7;self.status_pending=[None]*7
        self.last_evaluated=now
        for i in range(7):
            candidate=(native is not None and native[i]==1 and continuity_ok and len(quality.get('channels',[]))==7 and quality['channels'][i].get('valid') is True)
            pending=self.status_pending[i]
            if not candidate:
                status_only=bool(native is not None and native[i] in [-1,3] and continuity_ok and not self.error and len(quality.get('channels',[]))==7 and quality['channels'][i].get('valid') is True and quality['channels'][i].get('reasons')==[])
                if status_only and self.certified[i] and (pending is None or 0<=now-pending<=.2):
                    if pending is None:self.status_pending[i]=now
                else:self.certified[i]=False;self.status_pending[i]=None
                self.eligible_since[i]=None
            else:
                if pending is not None and self.certified[i] and 0<=now-pending<=.2:self.eligible_since[i]=now-1
                elif self.eligible_since[i] is None:self.eligible_since[i]=now
                self.status_pending[i]=None
            allowed=bool(candidate and now-self.eligible_since[i]>=1)
            if allowed:self.certified[i]=True
            stable.append(allowed)
        if not any(p is not None for p in self.status_pending):self.status_retry=False
        return dict(green_channels(quality,native,stable),native_error=self.error,
                    native_updated=updated,native_calls=self.calls,native_checked_monotonic=self.native_at,
                    native_window_end=self.native_end,native_window_samples=256,native_hop_samples=256,
                    native_age_ms=round((now-self.native_at)*1000,1) if self.native_at is not None else None,
                    native_status_recheck=status_recheck,policy_revision='vendor_batch_256_status_recheck_200ms_v3')
