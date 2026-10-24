"""Read-only display state. Decimation never changes recorded or transmitted data."""
import collections
import datetime
import math
import threading
import time
import numpy as np


class PreviewBuffer:
    def __init__(self,seconds=5):self.seconds=seconds;self.lock=threading.RLock();self.descriptor=None;self.parts=collections.deque();self.rows=0;self.last_read=None
    def configure(self,descriptor):
        with self.lock:self.descriptor=descriptor;self.parts.clear();self.rows=0;self.last_read=None
    def push(self,samples,read_monotonic_ns):
        with self.lock:
            if not self.descriptor:return
            a=np.asarray(samples,dtype=np.float32).copy()
            self.parts.append(a);self.rows+=len(a);self.last_read=read_monotonic_ns
            capacity=max(1,int(self.seconds*self.descriptor['sampling_rate']))
            while self.rows>capacity:
                excess=self.rows-capacity;first=self.parts.popleft()
                if len(first)>excess:self.parts.appendleft(first[excess:]);self.rows-=excess
                else:self.rows-=len(first)
    def snapshot(self):
        with self.lock:
            a=np.concatenate(list(self.parts)) if self.parts else np.empty((0,len(self.descriptor['channels']) if self.descriptor else 0),np.float32)
            return dict(descriptor=self.descriptor,samples=a.copy(),read_monotonic_ns=self.last_read,seconds=self.seconds)


class EventLog:
    def __init__(self,capacity=500):self.records=collections.deque(maxlen=capacity);self.lock=threading.RLock();self.seq=0
    def add(self,stage,level,message):
        with self.lock:
            self.seq+=1;self.records.append(dict(seq=self.seq,time=datetime.datetime.now().astimezone().isoformat(timespec='seconds'),stage=stage,level=level,message=str(message)[:1200]))
    def snapshot(self):
        with self.lock:return [dict(r) for r in self.records]


def envelope_points(values,pixels):
    """Min/max per pixel bucket in time order; nonfinite runs break the trace."""
    a=np.asarray(values);valid=np.isfinite(a)
    edges=np.flatnonzero(np.diff(np.r_[False,valid,False]));segments=[]
    stride=max(1,int(math.ceil(len(a)/max(1,pixels))))
    for start,end in zip(edges[::2],edges[1::2]):
        points=[]
        for begin in range(int(start),int(end),stride):
            part=a[begin:min(begin+stride,end)];lo=int(np.argmin(part));hi=int(np.argmax(part))
            for offset in sorted({0,lo,hi,len(part)-1}):points.append((begin+offset,float(part[offset])))
        if points:segments.append(points)
    return segments
