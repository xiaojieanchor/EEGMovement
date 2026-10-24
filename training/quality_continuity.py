"""Bounded delayed confirmation of native-status flicker, never electrical failures."""
import copy
import numpy as np

POLICY='native_status_continuity_200ms_v1'
LIMIT=50  # actual 250 Hz samples; also bounded by wall time

def possible_status_flicker(row,index):
    channels=row.get('channels',[])
    if index in [0,5] or len(channels)!=7 or row.get('native_error') or row.get('notch50_error'):return False
    c=channels[index]
    return c.get('valid') is True and c.get('reasons')==[] and c.get('native_code') in [None,-1,3] and not c.get('line_repaired')

def proof(row):
    keys=['kind','policy','source_epoch','sample_index_start','sample_index_end','available_sample_index_end','available_at_monotonic','training_mask','channels','notch50','native_error','notch50_error']
    return copy.deepcopy(dict({k:row[k]for k in keys if k in row},kind='channel_quality'))

class QualityContinuity:
    def __init__(self):self.epoch=None;self.previous=None;self.pending={};self.confirmed_intervals=0;self.confirmed_channel_samples=0
    def observe(self,row):
        row=copy.deepcopy(row);epoch=row['source_epoch'];end=row['available_sample_index_end'];now=row['available_at_monotonic']
        if epoch!=self.epoch:self.epoch=epoch;self.previous=None;self.pending={}
        resolutions=[]
        for i in range(7):
            current=row['training_mask'][i];pending=self.pending.get(i)
            if pending:
                within=0<=end-pending['start']<=LIMIT and 0<=now-pending['time']<=.2
                if current and within and end>pending['start']:
                    resolution=dict(kind='quality_resolution',policy=POLICY,source_epoch=epoch,sample_index_start=pending['start'],sample_index_end=end,
                        available_sample_index_end=end,available_at_monotonic=now,channels_1based=[i+1],
                        before=pending['before'],observations=pending['observations'],after=proof(row),max_gap_samples=LIMIT)
                    validate_resolution(resolution);resolutions.append(resolution);self.pending.pop(i)
                    self.confirmed_intervals+=1;self.confirmed_channel_samples+=end-pending['start']
                elif current or not within or not possible_status_flicker(row,i):self.pending.pop(i)
                else:pending['observations'].append(proof(row))
            elif (not current and possible_status_flicker(row,i) and self.previous and self.previous['training_mask'][i]
                    and 0<=end-self.previous['available_sample_index_end']<=75):
                self.pending[i]=dict(start=end,time=now,before=proof(self.previous),observations=[proof(row)])
        row['continuity_policy']=POLICY;row['continuity_pending_start']=min((p['start']for p in self.pending.values()),default=None)
        row['continuity_pending_channels_1based']=[i+1 for i in self.pending]
        row['continuity_confirmed_intervals']=self.confirmed_intervals;row['continuity_confirmed_channel_samples']=self.confirmed_channel_samples
        for i in self.pending:
            c=row['channels'][i];c.update(measured_color=c.get('color'),measured_quality_label=c.get('quality_label'),color='amber',quality_label='短暂状态波动 · 200ms复核中')
        self.previous=proof(row)
        return row,resolutions

def validate_resolution(row):
    from training.quality import channel_sample_mask
    if row.get('kind')!='quality_resolution' or row.get('policy')!=POLICY:raise ValueError('Unknown quality resolution')
    a,b=row['sample_index_start'],row['sample_index_end'];epoch=row['source_epoch'];before=row['before'];after=row['after'];observations=row['observations'];channels=row['channels_1based']
    if type(a)is not int or type(b)is not int or not 0<=a<b or b-a>LIMIT or row.get('available_sample_index_end')!=b:raise ValueError('Invalid quality resolution interval')
    if not observations or len(observations)>8 or not channels or any(type(i)is not int or i in [1,6]or not 1<=i<=7 for i in channels):raise ValueError('Invalid quality resolution evidence')
    if after['available_sample_index_end']!=b or observations[0]['available_sample_index_end']!=a or not 0<=a-before['available_sample_index_end']<=75:raise ValueError('Missing bracketing quality decisions')
    times=[r['available_at_monotonic']for r in observations]+[after['available_at_monotonic']]
    if any(y<x for x,y in zip(times,times[1:])) or not 0<=times[-1]-times[0]<=.2:raise ValueError('Quality resolution expired')
    for r in [before,*observations,after]:
        if r.get('source_epoch')!=epoch or r['sample_index_end']>r['available_sample_index_end']:raise ValueError('Cross-epoch or future quality evidence')
    for r in [before,after]:channel_sample_mask([r],r['sample_index_start'],r['sample_index_end'],epoch)
    for index in [i-1 for i in channels]:
        if not before['training_mask'][index]or not after['training_mask'][index]:raise ValueError('Unqualified bracketing channel')
        if any(r['channels'][index].get('valid')is not True or r['channels'][index].get('training_eligible')is not True for r in [before,after]):raise ValueError('Invalid bracketing electrode state')
        for r in observations:
            if not a<=r['available_sample_index_end']<b or r['training_mask'][index]or not possible_status_flicker(r,index):raise ValueError('Electrical failure cannot be bridged')
    return a,b,[i-1 for i in channels]

def resolved_channel_mask(events,start,end,epoch,available_end):
    """A delayed request may consume only resolutions already known at request time."""
    from training.quality import online_channel_mask
    measured=[r for r in events if r.get('kind')=='channel_quality' and r.get('available_sample_index_end',available_end+1)<=available_end]
    mask=online_channel_mask(measured,start,end,epoch)
    for r in events:
        if r.get('kind')!='quality_resolution' or r.get('source_epoch')!=epoch or r.get('available_sample_index_end',available_end+1)>available_end:continue
        a,b,indices=validate_resolution(r);lo,hi=max(a,start)-start,min(b,end)-start
        if hi>lo:
            # No later/hard veto may hide in the resolved interval.
            for q in measured:
                at=q['available_sample_index_end']
                if a<=at<b:
                    for i in indices:
                        if not q['training_mask'][i] and not possible_status_flicker(q,i):raise ValueError('Contradictory electrical veto')
            mask[lo:hi,indices]=True
    return mask
