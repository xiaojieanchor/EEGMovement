"""Physical device exclusions for personal recordings, never public EEG layouts."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def disabled_channels(root=ROOT):
    path=Path(root)/'configs/device_channels.json'
    if not path.exists():return ()
    values=json.loads(path.read_text(encoding='utf-8')).get('disabled_channels_1based',[])
    if not isinstance(values,list) or any(type(v) is not int or not 1<=v<=7 for v in values):raise ValueError('Invalid device channel exclusion policy')
    return tuple(sorted({v-1 for v in values}))

def exclude_quality(quality,disabled):
    q=dict(quality);channels=[dict(c) for c in q.get('channels',[])];mask=list(q.get('training_mask',[False]*7))
    while len(channels)<7:channels.append(dict(name='Ch'+str(len(channels)+1),valid=False,training_eligible=False,reasons=['等待数据'],color='gray'))
    for i in disabled:
        mask[i]=False
        if i<len(channels):channels[i].update(disabled=True,valid=False,training_eligible=False,line_repaired=False,repair_validated=False,color='gray',quality_label='硬件损坏 · 永久停用',reasons=['用户停用'],signal_used='disabled')
    active=[i for i in range(7) if i not in disabled]
    q.update(channels=channels,training_mask=mask,disabled_channels_1based=[i+1 for i in disabled],available_channel_count=len(active),
        training_good_channels=sum(mask),good_channels=sum(mask),valid=bool(active) and all(mask[i] for i in active),
        reasons=sorted({r for i in active for r in channels[i].get('reasons',[])}))
    if isinstance(q.get('notch50'),dict):q['notch50']=dict(q['notch50'],repaired_channels=[n for n in q['notch50'].get('repaired_channels',[]) if n-1 not in disabled])
    return q
