"""Deploy only isolated EEG paths; check coexistence and retain a rollback script."""
import hashlib,json,shlex,subprocess,tarfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    private=ROOT/'hub/runtime/relay_private';stamp=time.strftime('%Y%m%d-%H%M%S');out=ROOT/'outputs/relay_20261023';out.mkdir(parents=True,exist_ok=True)
    archive=out/f'relay-{stamp}.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for folder in ['eeg_relay','deploy/eeg-relay']:
            for p in (ROOT/folder).rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts:tar.add(p,arcname=p.relative_to(ROOT))
    options=['-i',str(Path.home()/'.ssh/kizunaframe_deploy_ed25519'),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=3','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-o','ProxyCommand=C:/Python313/python.exe '+str(ROOT/'scripts/ssh_physical_pipe.py').replace('\\','/')+' %h %p'];target='root@106.55.251.83'
    def run(command):
        r=subprocess.run(['ssh',*options,target,'set -e\n'+command],capture_output=True,timeout=240)
        text=r.stdout.decode('utf-8',errors='replace')+r.stderr.decode('utf-8',errors='replace')
        if r.returncode:raise RuntimeError(str(r.returncode)+' '+text[-2000:])
        return text
    class Copy:
        def put(self,local,remote):subprocess.run(['scp',*options,local,target+':'+remote],check=True,timeout=120)
        def chmod(self,path,mode):run('chmod '+oct(mode)[2:]+' '+shlex.quote(path))
        def close(self):pass
    baseline=run("systemctl is-active schisto-study nginx; curl -fsS http://127.0.0.1:18765/study/api/health")
    sftp=Copy();remote='/opt/eeg-relay/releases/'+stamp
    run('mkdir -p '+shlex.quote(remote)+" /etc/eeg-relay /var/lib/eeg-relay/cache /srv/www/eeg-updates /srv/www/eeg-releases; id eeg-relay >/dev/null 2>&1 || useradd --system --home /var/lib/eeg-relay --shell /sbin/nologin eeg-relay")
    sftp.put(str(archive),remote+'/source.tar.gz');run('tar -xzf '+remote+'/source.tar.gz -C '+remote)
    for local,dest in [('cloud-config.json','config.json'),('cache.key','cache.key'),('tls/server.pem','server.pem'),('tls/server.key','server.key')]:
        sftp.put(str(private/local),'/etc/eeg-relay/'+dest);sftp.chmod('/etc/eeg-relay/'+dest,0o600)
    run("chown -R eeg-relay:eeg-relay /etc/eeg-relay /var/lib/eeg-relay; python3 -m venv /opt/eeg-relay/venv; /opt/eeg-relay/venv/bin/pip install -q -r "+remote+'/deploy/eeg-relay/requirements.txt')
    previous=run('readlink /opt/eeg-relay/current || true').strip()
    rollback=f'''#!/bin/sh
set -eu
systemctl stop eeg-relay
rm -f /etc/nginx/conf.d/eeg-relay.conf
nginx -t
systemctl reload nginx
'''
    rollback_local=out/('rollback-'+stamp+'.sh');rollback_local.write_text(rollback,encoding='utf-8',newline='\n');sftp.put(str(rollback_local),'/opt/eeg-relay/rollback-'+stamp+'.sh')
    sftp.chmod('/opt/eeg-relay/rollback-'+stamp+'.sh',0o700)
    run('ln -sfn '+remote+' /opt/eeg-relay/current; cp '+remote+'/deploy/eeg-relay/eeg-relay.service /etc/systemd/system/; cp '+remote+'/deploy/eeg-relay/nginx.conf /etc/nginx/conf.d/eeg-relay.conf; nginx -t; systemctl daemon-reload; systemctl enable --now eeg-relay; systemctl restart eeg-relay; systemctl reload nginx')
    health=run('sleep 1; curl -fsS http://127.0.0.1:18767/eeg/health; curl --cacert /etc/eeg-relay/server.pem --connect-to 106.55.251.83:18443:127.0.0.1:18443 -fsS https://106.55.251.83:18443/eeg/health') if False else run('sleep 1; curl -fsS http://127.0.0.1:18767/eeg/health; systemctl is-active eeg-relay nginx schisto-study')
    after=run('curl -fsS http://127.0.0.1:18765/study/api/health; ss -lnt | grep -E "18443|18767"')
    record=dict(release=remote,previous=previous,source_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),baseline=baseline,health=health,after=after,rollback='/opt/eeg-relay/rollback-'+stamp+'.sh',cloud_firewall_external_test=False)
    (ROOT/'docs/gnautilus/relay_20261023/deployment.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(record,ensure_ascii=False))
    sftp.close()

if __name__=='__main__':main()
