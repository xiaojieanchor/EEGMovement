"""Immutable upload first, signed manifests last. Never publishes pairing or private keys."""
import argparse,hashlib,json,shlex,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_public_release import APP_VERSION,QUEST_VERSION
OPTIONS=['-i',str(Path.home()/'.ssh/kizunaframe_deploy_ed25519'),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=3','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-o','ProxyCommand=C:/Python313/python.exe '+str(ROOT/'scripts/ssh_physical_pipe.py').replace('\\','/')+' %h %p']
TARGET='root@106.55.251.83'
def remote(command):
    p=subprocess.run(['ssh',*OPTIONS,TARGET,'set -e\n'+command],capture_output=True,timeout=180)
    if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-1000:])
    return p.stdout.decode('utf-8')
def copy(source,dest):subprocess.run(['scp',*OPTIONS,str(source),TARGET+':'+dest],check=True,timeout=600)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def activate(temp,dest,digest):
    # Keep the complete shell expansion quoted; hash check must precede activation.
    return remote('actual=$(sha256sum '+shlex.quote(temp)+')\n'
                  'test "${actual%% *}" = '+shlex.quote(digest)+'\n'
                  'chmod 644 '+shlex.quote(temp)+'\n'
                  'mv '+shlex.quote(temp)+' '+shlex.quote(dest))

def release_selection(quest_only=False,windows_only=False):
    if quest_only and windows_only:raise ValueError('Select one release platform or neither')
    names=[];platforms=[]
    if not quest_only:
        names += [f'EEGMovement-Windows-x64-{APP_VERSION}.zip',f'EEGMovement-Setup-{APP_VERSION}.exe',f'EEGMovement-Launcher-{APP_VERSION}.exe'];platforms.append('windows-x64')
    if not windows_only:names.append(f'EEGMovement-Quest-{QUEST_VERSION}.apk');platforms.append('quest-arm64')
    return names,platforms

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage-only',action='store_true');g=p.add_mutually_exclusive_group();g.add_argument('--quest-only',action='store_true');g.add_argument('--windows-only',action='store_true');p.add_argument('--record');args=p.parse_args()
    folder=ROOT/'outputs/relay_20261023/release';token=uuid.uuid4().hex;published={}
    names,platforms=release_selection(args.quest_only,args.windows_only)
    for name in names:
        source=folder/name
        if not source.exists():raise FileNotFoundError(source)
        digest=sha(source);dest='/srv/www/eeg-releases/'+name;temp=dest+'.incoming-'+token
        exists=remote('if test -f '+shlex.quote(dest)+'; then sha256sum '+shlex.quote(dest)+'; fi').strip()
        if exists:
            if exists.split()[0]!=digest:raise ValueError('Published immutable artifact differs: '+name)
        else:
            copy(source,temp);activate(temp,dest,digest)
        published[name]=dict(sha256=digest,bytes=source.stat().st_size)
    if not args.stage_only:
        for platform in platforms:
            source=folder/(platform+'.json');dest='/srv/www/eeg-updates/'+source.name;temp=dest+'.incoming-'+token
            copy(source,temp);activate(temp,dest,sha(source))
            published[source.name]=dict(sha256=sha(source),bytes=source.stat().st_size)
    record=dict(published_utc=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),base_url='https://106.55.251.83:443/eeg/',immutable_first=True,manifests_activated=not args.stage_only,files=published)
    target=Path(args.record) if args.record else ROOT/'docs/gnautilus/relay_20261023_followup/publication.json';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(record,indent=2),encoding='utf-8');print(json.dumps(record))
if __name__=='__main__':main()
