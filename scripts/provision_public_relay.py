"""Local provisioning only. Cloud gets authentication keys, never action/E2E keys."""
import hashlib,json,os,secrets,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eeg_relay.trust import create_identity
from gnautilus_acquisition.storage import atomic_json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

def provision():
    private=ROOT/'hub/runtime/relay_private';private.mkdir(parents=True,exist_ok=True);create_identity(private/'tls','106.55.251.83')
    state_path=private/'provision.json'
    if state_path.exists():return json.loads(state_path.read_text('utf-8'))
    workspace,hub_id=secrets.token_hex(16),secrets.token_hex(16);hub_auth=secrets.token_hex(32)
    ca=(private/'tls/ca.pem').read_text('ascii');url='wss://106.55.251.83:443/eeg/v1/ws'
    cloud={hub_id:dict(workspace=workspace,role='hub',auth_secret=hub_auth,hub_id=hub_id)}
    peers={};exports=[]
    agents_path=ROOT/'hub/runtime/gnautilus/agents.json';agents=json.loads(agents_path.read_text('utf-8')) if agents_path.exists() else {}
    if not agents:agents[secrets.token_hex(16)]=secrets.token_hex(32)
    quest_path=ROOT/'hub/runtime/quest_devices.json';quest=json.loads(quest_path.read_text('utf-8')) if quest_path.exists() else dict(devices=[])
    if not quest['devices']:quest['devices'].append(dict(device_id=secrets.token_hex(16),secret=secrets.token_hex(32),address='127.0.0.1'))
    atomic_json(quest_path,quest)
    for role,records in [('eeg',[dict(device_id=i,secret=s) for i,s in agents.items()]),('quest',quest['devices'])]:
        for r in records:
            identity=r['device_id'];auth=secrets.token_hex(32);secret=r['secret'];cloud[identity]=dict(workspace=workspace,role=role,auth_secret=auth,hub_id=hub_id)
            peers[identity]=dict(role=role,secret=secret)
            enrollment=dict(schema='gnautilus.enrollment.v2' if role=='eeg' else 'eegmovement.quest.enrollment.v2',version=2,mode='relay',url=url,workspace=workspace,device_id=identity,agent_id=identity,hub_id=hub_id,secret=secret,auth_secret=auth,ca_pem=ca,proxy_mode='auto')
            path=ROOT/'hub/runtime/relay_enrollments'/f'{role}-{identity}.json';atomic_json(path,enrollment);os.chmod(path,0o600);exports.append(str(path))
    config=dict(url=url,workspace=workspace,device_id=hub_id,auth_secret=hub_auth,ca_pem=ca,proxy_mode='auto',peers=peers)
    atomic_json(ROOT/'hub/runtime/public_relay.json',config)
    cache=private/'cache.key';cache.write_bytes(secrets.token_bytes(32));os.chmod(cache,0o600)
    release_key=rsa.generate_private_key(65537,3072);keypath=private/'release-signing.key';keypath.write_bytes(release_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));os.chmod(keypath,0o600)
    public=release_key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    public_config=dict(base_url='https://106.55.251.83:443/eeg/',ca_pem=ca,release_public_key=public)
    for p in [ROOT/'gnautilus_acquisition/relay_public.json',ROOT/'unity/EEGMovement/Assets/Resources/relay_public.json']:atomic_json(p,public_config)
    atomic_json(private/'cloud-config.json',dict(devices=cloud,cache_key='/etc/eeg-relay/cache.key',cache_root='/var/lib/eeg-relay/cache',limit=8*1024**3,reserve=10*1024**3,ttl=86400))
    state=dict(workspace=workspace,hub_id=hub_id,exports=exports,public_endpoint=url,created_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat())
    atomic_json(state_path,state);return state

if __name__=='__main__':
    s=provision();print(json.dumps(dict(endpoint=s['public_endpoint'],enrollment_files=s['exports'],credentials_printed=False),ensure_ascii=False))
