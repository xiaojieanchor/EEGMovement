"""Real public network with synthetic samples; never accesses GDS or model server."""
import base64,hashlib,json,os,secrets,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from eeg_relay.protocol import seal,pack,unpack,verify,encoded
from gnautilus_acquisition.gui import load_enrollment
from gnautilus_acquisition.client import Replicator,probe_hub
from gnautilus_acquisition.storage import StreamJournal
from gnautilus_acquisition.contract import block_frame
from tests.test_gnautilus_transport import descriptor

def main():
    report=dict(scope='public software integration with synthetic EEG, not hardware acceptance',device_executed=False,private_model_server_accessed=False)
    enrollment=load_enrollment(next((ROOT/'hub/runtime/relay_enrollments').glob('eeg-*.json')))
    report['probe']=probe_hub(enrollment)
    with tempfile.TemporaryDirectory() as t:
        d=descriptor();d['stream_id']=secrets.token_hex(16);journal=StreamJournal(Path(t)/'raw',d);rep=Replicator(journal,enrollment).start()
        try:
            end=time.monotonic()+20
            while not rep.snapshot()['connected'] and time.monotonic()<end:time.sleep(.05)
            for n in range(1,97):
                h,raw=block_frame(journal.descriptor,n,(n-1)*16,np.zeros((16,32),np.float32));journal.append(h,raw);time.sleep(.04)
            end=time.monotonic()+30
            while rep.snapshot()['durable_seq']<96 and time.monotonic()<end:time.sleep(.1)
            status=rep.snapshot()
            if status['durable_seq']!=96:raise AssertionError('Hub durable receipt missing: '+str(status))
            report['eeg']=dict(original_blocks=96,original_samples=1536,hub_durable_seq=status['durable_seq'],live_accepted=status['live_accepted'],cloud_receipts=status['cloud_blocks'],historical_separated=True,local_original_retained=journal.snapshot()['blocks']==96)
        finally:rep.stop();journal.close()
    quest=json.loads(next((ROOT/'hub/runtime/relay_enrollments').glob('quest-*.json')).read_text('utf-8'));boot=secrets.token_hex(16)
    request_id=secrets.token_hex(16);body=encoded(dict(request_id=request_id,method='GET',path='/v1/status',body=''))
    h=seal(quest['workspace'],quest['device_id'],quest['hub_id'],boot,1,'rpc',body,quest['secret']);request=base64.b64encode(pack(h,body)).decode()
    java=Path('G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/OpenJDK/bin')
    classes=ROOT/'outputs/relay_20261023/java-check'
    subprocess.run([str(java/'javac.exe'),'-encoding','UTF-8','-d',str(classes),str(ROOT/'unity/EEGMovement/Assets/Plugins/Android/org/eegmovement/relay/RelayBridge.java'),str(ROOT/'tests/RelayProbe.java')],check=True)
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp);(p/'ca.pem').write_text(quest['ca_pem'],encoding='ascii')
        props=dict(url=quest['url'],workspace=quest['workspace'],device=quest['device_id'],auth=quest['auth_secret'],boot=boot,ca=str(p/'ca.pem').replace('\\','/'),request=request)
        (p/'probe.properties').write_text('\n'.join(k+'='+v for k,v in props.items()),encoding='ascii')
        result=subprocess.run([str(java/'java.exe'),'-cp',str(classes),'RelayProbe',str(p/'probe.properties'),str(p/'reply.txt')],capture_output=True,text=True,timeout=40)
        if result.returncode:raise RuntimeError(result.stderr[-2000:])
        rh,rb=unpack(base64.b64decode((p/'reply.txt').read_text()));reply=json.loads(rb)
        if not verify(rh,rb,quest['secret']) or reply.get('request_id')!=request_id or reply.get('status')!=200:raise ValueError('Native transport Hub response validation failed')
        status=json.loads(base64.b64decode(reply['body']));report['quest_java']=dict(dedicated_ca_tls=True,authenticated_lanes=4,hub_e2e_signature=True,status_schema=status['status_schema'],hardware_rendering_verified=False)
    (ROOT/'docs/gnautilus/relay_20261023/public-integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
