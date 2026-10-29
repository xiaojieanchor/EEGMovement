"""Measured reception statistics; no device-clock or contact-quality claims."""
import collections


OVERVIEW_ELECTRICAL_POLICY = dict(schema='gnautilus.electrical.v1',rail_uv=1000.,flat_ptp_uv=.1,
    max_ptp_uv=500.,max_rms_uv=100.,max_line_ratio=.5,
    semantics='Overview gross electrical observation; not electrode impedance or training qualification')


class ReceptionObservations:
    def __init__(self):
        self.identity=None
        self.reset(None)

    def reset(self,identity):
        self.identity=identity;self.points=collections.deque(maxlen=4096)
        self.received=0;self.missing=0;self.missing_blocks=0;self.duplicates=0;self.blocks=0
        self.last_end=None;self.last_seq=None;self.last_arrival=None;self.hz=None;self.measured_at=None

    def accept(self,identity,start,count,seq,now):
        if identity!=self.identity:self.reset(identity)
        if self.last_end is not None and (seq<=self.last_seq or start<self.last_end):
            self.duplicates+=1;return
        if self.last_end is not None:
            self.missing+=max(0,start-self.last_end);self.missing_blocks+=max(0,seq-self.last_seq-1)
        self.received+=count;self.blocks+=1;self.last_end=start+count;self.last_seq=seq;self.last_arrival=now
        self.points.append((now,self.received))
        while len(self.points)>2 and self.points[1][0]<now-8:self.points.popleft()
        elapsed=now-self.points[0][0]
        if elapsed>=2 and (self.measured_at is None or now-self.measured_at>=1):
            self.hz=(self.received-self.points[0][1])/elapsed;self.measured_at=now

    def snapshot(self,now):
        age=(now-self.last_arrival)*1000 if self.last_arrival is not None else None
        state='unknown' if age is None else 'retained' if age>=3000 else 'warming' if self.hz is None else 'measured'
        denominator=self.received+self.missing
        return dict(state=state,measured_hz=round(self.hz,1) if self.hz is not None else None,
            measurement='accepted live sample count / Hub monotonic reception time; rolling 2–8s',
            window_s=round(self.points[-1][0]-self.points[0][0],2) if len(self.points)>1 else 0,
            age_ms=round(age,1) if age is not None else None,received_samples=self.received,
            missing_samples=self.missing,missing_blocks=self.missing_blocks,duplicate_blocks=self.duplicates,
            loss_pct=round(100*self.missing/denominator,4) if self.blocks>=2 and denominator else None,
            continuity_scope='live sample-index gaps after first observed block; excludes upstream device/radio loss and disk backfill')
