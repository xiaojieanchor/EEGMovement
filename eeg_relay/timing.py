"""Host-clock sample association. Never claims hardware-trigger precision."""
import math

def associate(anchors,event_ms,event_uncertainty_ms,epoch):
    if not all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in (event_ms,event_uncertainty_ms)):return dict(status='unresolved',reason='invalid_clock_mapping')
    matches=[]
    for a in anchors:
        if a['epoch']!=epoch:continue
        low=a['read_end_ms']-(a['end']-a['start'])/a['rate']*1000;high=a['read_end_ms'];uncertainty=a['uncertainty_ms']+event_uncertainty_ms
        if event_ms+uncertainty<low or event_ms-uncertainty>high:continue
        first=max(a['start'],int(math.floor(a['end']+(event_ms-uncertainty-high)*a['rate']/1000)))
        last=min(a['end'],int(math.ceil(a['end']+(event_ms+uncertainty-high)*a['rate']/1000)))
        if first<=last:matches.append((a,first,last))
    if not matches:return dict(status='unresolved',reason='no_supported_sample_interval',sample_range=None)
    streams={a['stream'] for a,_,_ in matches}
    ordered=sorted(matches,key=lambda v:v[0]['start'])
    if len(streams)!=1 or any(b[0]['start']!=a[0]['end'] for a,b in zip(ordered,ordered[1:])):return dict(status='unresolved',reason='source_gap_or_epoch_boundary',sample_range=None)
    return dict(status='host_clock_estimate',stream_id=ordered[0][0]['stream'],source_epoch=epoch,sample_range=[min(x for _,x,_ in matches),max(y for _,_,y in matches)],uncertainty_ms=max(a['uncertainty_ms']+event_uncertainty_ms for a,_,_ in matches),hardware_synchronization=False,device_buffer_latency_known=False)
