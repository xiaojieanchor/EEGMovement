"""Verify live signed manifests, exact HTTPS ranges, and passive release notification."""
import hashlib,json,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from eeg_relay.update import fetch,verify_manifest,opener,file_hash
from eeg_relay.network import Connection

def main():
    config=json.loads((ROOT/'gnautilus_acquisition/relay_public.json').read_text());folder=ROOT/'outputs/relay_20261023/release';results={}
    for platform in ('windows-x64','quest-arm64'):
        raw,proxy=fetch(config,'updates/'+platform+'.json');manifest=verify_manifest(raw,config['release_public_key'],platform)
        if raw!=(folder/(platform+'.json')).read_bytes():raise ValueError('Public manifest differs from signed local publication')
        artifact=folder/Path(manifest['path']).name
        if file_hash(artifact)!=manifest['sha256']:raise ValueError('Local release changed')
        segments=[]
        for start in (0,manifest['size']-65536):
            end=start+65535
            request=urllib.request.Request(config['base_url']+manifest['path'],headers={'Range':f'bytes={start}-{end}'})
            with opener(config,proxy).open(request,timeout=15) as response:
                data=response.read(65537)
                if response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {start}-{end}/'):raise ValueError('Public range mismatch')
            with artifact.open('rb') as f:f.seek(start);expected=f.read(65536)
            if data!=expected:raise ValueError('Public bytes differ')
            segments.append(dict(start=start,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
        results[platform]=dict(signature_valid=True,public_manifest_exact=True,version=manifest['version'],release_seq=manifest['release_seq'],artifact_sha256=manifest['sha256'],https_range_segments=segments,full_http_download=False)
    enrollment=json.loads(next((ROOT/'hub/runtime/relay_enrollments').glob('eeg-*.json')).read_text());connection=Connection(enrollment).connect()
    try:
        deadline=time.monotonic()+5;notice=None
        while time.monotonic()<deadline:
            h,_=connection.receive(3)
            if h.get('notice')=='update_available':notice=h;break
        if not notice or notice.get('platform')!='windows-x64':raise ValueError('No passive release notification')
        if notice.get('revision')!=hashlib.sha256((folder/'windows-x64.json').read_bytes()).hexdigest():raise ValueError('Release notice revision differs')
    finally:connection.close()
    result=dict(scope='live public signed manifests and exact partial downloads; full package upload hash verified separately',end_to_end_update_on_other_pc=False,quest_installation_checked_by_this_script=False,results=results,release_notification=True,certificate_validation=True)
    (ROOT/'docs/gnautilus/relay_20261023_followup/update-download-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
