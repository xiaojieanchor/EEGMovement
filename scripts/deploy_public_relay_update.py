"""Update relay code only; retain configuration, cache, TLS and other services."""
import argparse
import hashlib
import json
import shlex
import subprocess
import tarfile
import time
import uuid
from pathlib import Path

from scripts.publish_public_release import copy, remote

ROOT = Path(__file__).resolve().parents[1]


def deploy(record):
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    folder = ROOT / 'outputs/relay_code_updates' / stamp
    folder.mkdir(parents=True)
    files = subprocess.check_output(
        ['git', 'ls-files', 'eeg_relay', 'deploy/eeg-relay'], cwd=ROOT, text=True
    ).splitlines()
    archive = folder / 'source.tar.gz'
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}
    with tarfile.open(archive, 'w:gz') as tar:
        for p in files:
            tar.add(ROOT / p, arcname=p)
    previous = remote('readlink -f /opt/eeg-relay/current').strip()
    if not previous.startswith('/opt/eeg-relay/releases/'):
        raise ValueError('Unexpected previous release path')
    baseline = remote(
        'systemctl is-active eeg-relay nginx schisto-study\n'
        'curl -fsS http://127.0.0.1:18765/study/api/health'
    )
    release = '/opt/eeg-relay/releases/' + stamp
    remote('mkdir ' + shlex.quote(release))
    copy(archive, release + '/source.tar.gz')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    check = (
        "import hashlib,pathlib; p=pathlib.Path(" + repr(release + '/source.tar.gz') + "); "
        "assert hashlib.sha256(p.read_bytes()).hexdigest()==" + repr(digest)
    )
    remote('python3 -c ' + shlex.quote(check) + '\ntar -xzf ' +
           shlex.quote(release + '/source.tar.gz') + ' -C ' + shlex.quote(release))
    verify = (
        "import hashlib,pathlib; r=pathlib.Path(" + repr(release) + "); "
        "expected=" + repr(hashes) + "; "
        "assert all(hashlib.sha256((r/p).read_bytes()).hexdigest()==h for p,h in expected.items()); "
        "assert (r/'deploy/eeg-relay/requirements.txt').read_bytes()==pathlib.Path(" +
        repr(previous + '/deploy/eeg-relay/requirements.txt') + ").read_bytes()"
    )
    remote('python3 -c ' + shlex.quote(verify) + '\n/opt/eeg-relay/venv/bin/python -m compileall -q ' +
           shlex.quote(release + '/eeg_relay') + '\ncd ' + shlex.quote(release) +
           "\n/opt/eeg-relay/venv/bin/python -c \"from eeg_relay.server import RULES; assert 'eeg_event' in RULES['eeg']\"")
    rollback = ('ln -sfn ' + shlex.quote(previous) + ' /opt/eeg-relay/current.rollback\n'
                'mv -Tf /opt/eeg-relay/current.rollback /opt/eeg-relay/current\n'
                'systemctl restart eeg-relay\n')
    rollback_file = folder / 'rollback.sh'
    rollback_file.write_text('#!/bin/sh\nset -eu\n' + rollback, encoding='utf-8', newline='\n')
    copy(rollback_file, release + '/rollback.sh')
    activate = (
        'ln -sfn ' + shlex.quote(release) + ' /opt/eeg-relay/current.next\n'
        'mv -Tf /opt/eeg-relay/current.next /opt/eeg-relay/current\n'
        'systemctl restart eeg-relay\n'
        'ready=0\nfor attempt in $(seq 1 20); do\n'
        ' if curl -fsS http://127.0.0.1:18767/eeg/health >/dev/null; then ready=1; break; fi\n'
        ' sleep 1\ndone\ntest "$ready" = 1\n'
        'systemctl is-active eeg-relay nginx schisto-study\n'
        'curl -fsS http://127.0.0.1:18767/eeg/health\n'
        'curl -fsS http://127.0.0.1:18765/study/api/health'
    )
    try:
        health = remote(activate)
    except Exception:
        remote(rollback)
        raise
    result = dict(release=release, previous=previous, source_sha256=digest,
                  files=hashes, baseline=baseline, health=health,
                  rollback=release + '/rollback.sh',
                  configuration_changed=False, cache_changed=False,
                  nginx_reloaded=False, application_installers_published=False)
    record = Path(record)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--record', required=True)
    deploy(parser.parse_args().record)
