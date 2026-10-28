"""Sample-clock telemetry only: it never contributes to a model decision."""
from collections import deque

class SessionObservation:
    def __init__(self):
        self.events=deque(maxlen=24);self.sequence=0;self.cue=0;self.cue_start=0;self.state=None;self.state_start=0
    def update(self,sample,decision,event,fs=250):
        cue=decision.get('cue_direction',0);state=(decision.get('status','UNKNOWN'),decision.get('direction',0))
        if event:
            self.sequence+=1;self.events.append(dict(sequence=self.sequence,direction=int(event),session_sample=int(sample)))
        if cue!=self.cue:self.cue=cue;self.cue_start=sample
        if state!=self.state:self.state=state;self.state_start=sample
        return dict(session_samples=int(sample),eye_events=list(self.events),eye_event_sequence=self.sequence,
                    cue_duration_s=(sample-self.cue_start)/fs if cue else 0.,state_duration_s=(sample-self.state_start)/fs,
                    sampling_rate=fs,semantics='sample_clock_model_state_not_measured_gaze')
