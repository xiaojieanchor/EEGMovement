"""Bounded, explicit client diagnostics. Never forwards paths, secrets or raw configuration."""
import math,platform,socket
from . import __version__

TEXT=('app_version','computer_name','platform','state','sdk_state','disk_state','selected_serial','device_serial','stream_id','configuration_sha256','source_kind','network_path','update_state','error')
NUMBERS=('samples','blocks','queue_blocks','backlog','durable_seq','cloud_blocks','sampling_rate','epoch')

def sanitize(value):
    if not isinstance(value,dict):raise ValueError('Client status must be an object')
    out={key:value[key][:400 if key=='error' else 128] for key in TEXT if isinstance(value.get(key),str)}
    for key in NUMBERS:
        number=value.get(key)
        if number is None:out[key]=None
        elif type(number) in (int,float) and math.isfinite(number) and 0<=number<=2**53:out[key]=number
    out['channels']=[{k:c[k][:120] for k in ('id','name','kind','unit') if isinstance(c.get(k),str)} for c in value.get('channels',[])[:80] if isinstance(c,dict)] if isinstance(value.get('channels'),list) else []
    out['contact_quality_known']=False
    return out

def build(profile,state,network=None,update_state=None):
    network=network or {};d=state.get('descriptor') or {}
    return sanitize(dict(app_version=__version__,computer_name=socket.gethostname(),platform=platform.platform(),
        state=state.get('state','idle'),sdk_state=state.get('sdk_state','not_started'),disk_state=state.get('disk_state','not_recording' if not d else 'unknown'),
        selected_serial=profile.get('serial',''),device_serial=d.get('device_serial',''),stream_id=d.get('stream_id',''),epoch=d.get('epoch'),
        configuration_sha256=d.get('configuration_sha256',''),source_kind=d.get('source_kind',''),sampling_rate=d.get('sampling_rate'),channels=d.get('channels',[]),
        samples=state.get('samples',0),blocks=state.get('blocks',0),queue_blocks=state.get('queue_blocks',0),
        backlog=network.get('backlog',0),durable_seq=network.get('durable_seq',0),cloud_blocks=network.get('cloud_blocks',0),
        network_path=network.get('path',''),update_state=update_state or '',error=state.get('error') or network.get('error') or ''))
