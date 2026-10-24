"""Auditable instructed-task results; never grants runtime permission."""
import hashlib
import json
from pathlib import Path
import numpy as np
from .session_calibration import LABELS
from .intent_control import IntentControl


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


class CheckMetrics:
    def __init__(self,classes,policy):
        self.classes=classes;self.total=np.zeros(5);self.valid=np.zeros(5);self.correct=np.zeros(5)
        self.confusion=np.zeros((5,classes+1));self.events={};self.frames=0;self.rejected=0
        self.now=10.;self.controller=IntentControl(True,lambda:self.now)
        self.controller.configure({k:v for k,v in policy.items() if k in ['active_threshold','direction_threshold','axis_mass_threshold','speed','yaw_rate']})
        self.false_starts=0;self.was_active=False;self.rest_seconds=0.

    def add(self,row):
        if not row['scored']:return
        self.frames+=1;label=LABELS.index(row['instructed_label']);dt=row['scored_seconds'];self.total[label]+=dt
        event=self.events.setdefault(row['cursor'],dict(label=label,seconds=0.,valid_seconds=0.,sum=np.zeros(self.classes)))
        event['seconds']+=dt
        p=row['probabilities'];eligible=row['eligible'];self.now+=dt
        if eligible:
            prediction=int(np.argmax(p));self.valid[label]+=dt;self.correct[label]+=dt*(prediction==label)
            self.confusion[label,prediction]+=dt;event['valid_seconds']+=dt;event['sum']+=np.asarray(p)*dt
        else:self.confusion[label,self.classes]+=dt;self.rejected+=1
        if label==0:
            self.rest_seconds+=dt
            result=dict(eligible=eligible,source='LIVE',probabilities=p,source_epoch=row['source_epoch'],sample_index_end=row['sample_index_end'],
                usable_channels=row['usable_channels'],model_id='offline-current-policy-check',input_origin_monotonic=self.now-.001)
            moving=bool(self.controller.update(result if eligible else None).get('active'))
            if moving and not self.was_active:self.false_starts+=1
            self.was_active=moving
        else:self.controller.update(None);self.was_active=False

    def summary(self):
        total=float(self.total.sum());valid=float(self.valid.sum());supported=np.flatnonzero(self.total>0).tolist()
        recalls=[float(self.correct[i]/self.total[i]) for i in supported]
        events=[]
        for cursor,e in self.events.items():
            prediction=int(e['sum'].argmax()) if e['valid_seconds'] else None
            events.append(dict(cursor=cursor,label=LABELS[e['label']],prediction=prediction,correct=prediction==e['label'],
                seconds=round(e['seconds'],3),valid_seconds=round(e['valid_seconds'],3)))
        return dict(scored_seconds=round(total,3),eligible_seconds=round(valid,3),coverage=valid/total if total else None,
            accuracy_including_rejection=float(self.correct.sum()/total) if total else None,
            accuracy_on_eligible=float(self.correct.sum()/valid) if valid else None,
            balanced_accuracy_supported_classes=float(np.mean(recalls)) if recalls else None,
            supported_classes=[LABELS[i] for i in supported],full_direction_support=len(supported)==5,
            classes=[dict(label=LABELS[i],instructed_seconds=round(float(self.total[i]),3),eligible_seconds=round(float(self.valid[i]),3),
                recall_including_rejection=float(self.correct[i]/self.total[i]) if self.total[i] else None) for i in range(5)],
            confusion_seconds=self.confusion.tolist(),prediction_columns=list(range(self.classes))+['REJECTED'],
            events=events,event_correct=sum(e['correct'] for e in events),event_count=len(events),
            frames=self.frames,rejected_frames=self.rejected,frames_are_independent_trials=False,
            rest_seconds=round(self.rest_seconds,3),rest_false_starts=self.false_starts,
            rest_false_starts_per_minute=self.false_starts/(self.rest_seconds/60) if self.rest_seconds else None,
            false_start_semantics='offline replay of current control policy; no avatar command sent',
            independent_test=False,score_semantics='instructed task agreement; intention not independently verified')


def write_report(directory,report,raw_path):
    raw=Path(raw_path) if raw_path else None
    report['raw_recording']=dict(path=str(raw),sha256=digest(raw),bytes=raw.stat().st_size) if raw and raw.is_file() else None
    report['data_files']={name:dict(path=str(Path(directory)/name),sha256=digest(Path(directory)/name)) for name in ['frames.jsonl','events.jsonl']}
    path=Path(directory)/'report.json';temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');temporary.replace(path)
    return report
