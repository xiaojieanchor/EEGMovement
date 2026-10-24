"""Conservative retrospective mask from sample-anchored QC decisions."""
import numpy as np


def channel_sample_mask(events,start,end,epoch):
    if type(start) is not int or type(end) is not int or not 0<=start<end or end-start>2_000_000:
        raise ValueError('Invalid bounded quality interval')
    covered=np.zeros(end-start,dtype=bool)
    veto=np.zeros((end-start,7),dtype=bool)
    for row in events:
        if row.get('kind')!='channel_quality' or row.get('source_epoch')!=epoch:continue
        if row.get('policy') not in ['neuradock_native_green_v1','neuradock_notch50_rechecked_v2']:continue
        a,b=row.get('sample_index_start'),row.get('sample_index_end');mask=row.get('training_mask')
        if (type(a) is not int or type(b) is not int or b<=a or b-a>1250 or not isinstance(mask,list)
                or len(mask)!=7 or any(type(x) is not bool for x in mask)):
            raise ValueError('Invalid quality event')
        if row.get('policy')=='neuradock_notch50_rechecked_v2':
            channels=row.get('channels',[])
            if len(channels)!=7:raise ValueError('Rechecked quality requires per-channel provenance')
            for i,c in enumerate(channels):
                if mask[i] and (c.get('training_eligible') is not True or c.get('valid') is not True or c.get('native_code') not in [0,1]):
                    raise ValueError('Rechecked mask contradicts per-channel validity')
                if mask[i] and c.get('native_code')==0 and c.get('line_repaired') is not True:
                    raise ValueError('Raw line-noise channel requires a verified notch repair')
                if mask[i] and c.get('line_repaired'):
                    notch=row.get('notch50') or {}
                    if not (notch.get('applied') is True and notch.get('causal') is True and notch.get('frequency_hz')==50 and notch.get('q')==30
                        and c.get('repair_validated') is True and c.get('raw_safety_passed') is True and c.get('native_filtered_code')==1
                        and c.get('post_filter',{}).get('valid') is True and c.get('line_suppression_db',-1)>=10):
                        raise ValueError('Filtered channel missing valid recheck evidence')
        lo,hi=max(start,a)-start,min(end,b)-start
        if hi<=lo:continue
        covered[lo:hi]=True
        veto[lo:hi]|=~np.asarray(mask,dtype=bool)
    return covered[:,None]&~veto


def online_channel_mask(events,start,end,epoch,max_age_samples=375):
    """Use decisions only AFTER they became available; never backfill history.

    Old recordings without availability provenance cannot certify online QC.
    The retrospective export function above intentionally keeps its semantics.
    """
    if not 0<=start<end or end-start>2_000_000:raise ValueError('Invalid interval')
    output=np.zeros((end-start,7),bool)
    rows=[r for r in events if r.get('source_epoch')==epoch and type(r.get('available_sample_index_end')) is int]
    rows.sort(key=lambda r:r['available_sample_index_end'])
    for i,row in enumerate(rows):
        a=row['available_sample_index_end'];b=min(end,a+max_age_samples,rows[i+1]['available_sample_index_end'] if i+1<len(rows) else end)
        if row.get('sample_index_end',a+1)>a:raise ValueError('QC available before source support ends')
        # Reuse strict electrical-repair provenance validation.
        channel_sample_mask([row],row['sample_index_start'],row['sample_index_end'],epoch)
        lo,hi=max(start,a)-start,b-start
        if hi>lo:output[lo:hi]=row['training_mask']
    return output
