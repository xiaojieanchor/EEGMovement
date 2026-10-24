"""Crash-recoverable append journal. Acknowledgement follows both data and index fsync."""
import datetime
import bisect
import hashlib
import json
import os
import threading
from pathlib import Path
from .contract import json_bytes,validate_descriptor,validate_block


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('wb') as f:f.write(json_bytes(value));f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


class StreamJournal:
    def __init__(self,path,descriptor):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        self.descriptor=validate_descriptor(descriptor);self.lock=threading.RLock();self.records={};self.intervals=[];self.closed=False
        p=self.path/'descriptor.json'
        if p.exists():
            if json_bytes(json.loads(p.read_text('utf-8')))!=json_bytes(self.descriptor):raise ValueError('Stored descriptor changed')
        else:atomic_json(p,self.descriptor)
        self.raw=(self.path/'raw.f32').open('a+b');self.index=(self.path/'blocks.jsonl').open('a+b')
        self.events=(self.path/'events.jsonl').open('a+b')
        try:self._recover()
        except BaseException:
            self.raw.close();self.index.close();self.events.close();raise

    def _recover(self):
        self.index.seek(0);offset=0;index_end=0
        for line in self.index:
            if not line.endswith(b'\n'):break
            r=json.loads(line);h=r['header'];length=r['length']
            if r['offset']!=offset or length<1:raise ValueError('Corrupt committed index')
            self.raw.seek(offset);raw=self.raw.read(length);validate_block(h,raw,self.descriptor)
            if h['seq'] in self.records:raise ValueError('Duplicate committed index')
            self._check_overlap(h)
            self.records[h['seq']]=r;offset+=length;index_end+=len(line)
            bisect.insort(self.intervals,(h['sample_start'],h['sample_start']+h['sample_count']))
        self.raw.seek(0,2);raw_size=self.raw.tell();self.index.seek(0,2);index_size=self.index.tell()
        if offset>raw_size:raise ValueError('Committed samples are missing')
        self.recovered_tail_bytes=raw_size-offset+index_size-index_end
        self.events.seek(0);events_end=0
        for line in self.events:
            if not line.endswith(b'\n'):break
            json.loads(line);events_end+=len(line)
        self.events.seek(0,2);self.recovered_tail_bytes+=self.events.tell()-events_end
        self.events.truncate(events_end);self.events.seek(0,2)
        self.raw.truncate(offset);self.index.truncate(index_end)
        self.raw.seek(0,2);self.index.seek(0,2)
        self._durable_seq=0
        while self._durable_seq+1 in self.records:self._durable_seq+=1

    @property
    def durable_seq(self):return self._durable_seq

    def _check_overlap(self,h):
        start,end=h['sample_start'],h['sample_start']+h['sample_count']
        at=bisect.bisect_left(self.intervals,(start,))
        for a,b in self.intervals[max(0,at-1):at+1]:
            if start<b and a<end:raise ValueError('Conflicting sample range')

    def append(self,header,raw):
        validate_block(header,raw,self.descriptor)
        h={k:v for k,v in header.items() if k not in ['ticket','source_age_ms']}
        with self.lock:
            if self.closed:raise ValueError('Journal closed')
            seq=h['seq'];old=self.records.get(seq)
            if old:
                stable=['stream_id','epoch','seq','sample_start','sample_count','columns','dtype','sha256','read_monotonic_ns','utc_ns','read_completed_monotonic_ns','read_completed_utc_ns']
                if any(old['header'].get(k)!=h.get(k) for k in stable):raise ValueError('Duplicate block changed')
                return dict(received_seq=seq,durable_seq=self.durable_seq,duplicate=True)
            self._check_overlap(h);self.raw.seek(0,2);offset=self.raw.tell()
            r=dict(offset=offset,length=len(raw),header=h)
            self.raw.write(raw);self.raw.flush();os.fsync(self.raw.fileno())
            self.index.write(json_bytes(r)+b'\n');self.index.flush();os.fsync(self.index.fileno())
            self.records[seq]=r
            bisect.insort(self.intervals,(h['sample_start'],h['sample_start']+h['sample_count']))
            while self._durable_seq+1 in self.records:self._durable_seq+=1
            return dict(received_seq=seq,durable_seq=self.durable_seq,duplicate=False)

    def read(self,seq):
        with self.lock:
            r=self.records[seq];self.raw.seek(r['offset']);raw=self.raw.read(r['length'])
            return dict(r['header']),raw

    def event(self,event):
        with self.lock:
            if self.closed:raise ValueError('Journal closed')
            self.events.write(json_bytes(event)+b'\n');self.events.flush();os.fsync(self.events.fileno())

    def snapshot(self):
        with self.lock:
            maximum=max(self.records,default=0)
            # Missing ranges instead of an unbounded list for sparse sequence attacks.
            gaps=[];expected=1
            for seq in sorted(self.records):
                if seq>expected:gaps.append([expected,seq-1])
                expected=seq+1
            return dict(stream_id=self.descriptor['stream_id'],blocks=len(self.records),samples=sum(r['header']['sample_count'] for r in self.records.values()),
                        durable_seq=self.durable_seq,last_seq=maximum,missing_sequences=gaps,recovered_tail_bytes=self.recovered_tail_bytes,closed=self.closed)

    def close(self):
        with self.lock:
            if not self.closed:
                self.raw.flush();self.index.flush();self.events.flush()
                for f in [self.raw,self.index,self.events]:os.fsync(f.fileno());f.close()
                self.closed=True
            report=self.snapshot();report['closed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
            report['files']={p.name:dict(bytes=p.stat().st_size,sha256=self._file_hash(p)) for p in self.path.iterdir() if p.name in ['descriptor.json','raw.f32','blocks.jsonl','events.jsonl']}
            atomic_json(self.path/'closed.json',report)
            return report

    @staticmethod
    def _file_hash(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        return h.hexdigest()
