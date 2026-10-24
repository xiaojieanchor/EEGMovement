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
    """Peak-preserving display buckets; vectorized to leave I/O threads time."""
    a=np.asarray(values);valid=np.isfinite(a)
    edges=np.flatnonzero(np.diff(np.r_[False,valid,False]));segments=[]
    stride=max(1,int(math.ceil(len(a)/max(1,pixels))))
    for start,end in zip(edges[::2],edges[1::2]):
        start,end=int(start),int(end)
        if stride==1:
            indices=np.arange(start,end)
        else:
            count=(end-start)//stride
            indices=np.empty(0,dtype=np.int64)
            if count:
                blocks=a[start:start+count*stride].reshape(count,stride)
                offsets=np.stack((np.zeros(count,dtype=np.int64),np.argmin(blocks,axis=1),np.argmax(blocks,axis=1),np.full(count,stride-1,dtype=np.int64)),axis=1)
                offsets.sort(axis=1)
                keep=np.column_stack((np.ones(count,dtype=bool),offsets[:,1:]!=offsets[:,:-1]))
                indices=(start+np.arange(count)[:,None]*stride+offsets)[keep]
            tail=start+count*stride
            if tail<end:
                part=a[tail:end]
                extra=tail+np.array(sorted({0,int(np.argmin(part)),int(np.argmax(part)),len(part)-1}))
                indices=np.r_[indices,extra]
        if len(indices):segments.append(list(zip(indices.tolist(),a[indices].astype(float).tolist())))
    return segments
