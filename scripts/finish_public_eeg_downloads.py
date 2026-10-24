"""Finite completion stage for the current downloads; no service or scheduler."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--plan', action='append', required=True)
    parser.add_argument('--wait-seconds', type=int, default=21600)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    deadline = time.monotonic() + args.wait_seconds
    plans = [json.loads(Path(p).read_text()) for p in args.plan]
    while True:
        progress = {}
        for plan in plans:
            state_path = root / ('download-status-' + plan['id'] + '.json')
            state = json.loads(state_path.read_text()) if state_path.exists() else dict(items={})
            rows = [state['items'].get(item['relative_path'], dict(state='not_started')) for item in plan['items']]
            verified = sum(v['state'] == 'verified' for v in rows)
            failures = [v for v in rows if v['state'] == 'failed']
            progress[plan['id']] = dict(verified=verified, expected=len(rows), failures=len(failures))
            if state.get('completed_utc') and failures:
                raise RuntimeError('Download plan has completed with failures: ' + plan['id'])
        print(json.dumps(dict(phase='waiting_for_downloads', progress=progress)), flush=True)
        if all(v['verified'] == v['expected'] for v in progress.values()):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError('Completion wait expired; resumable download processes are left intact')
        time.sleep(30)
    plan_args = [argument for p in args.plan for argument in ['--plan', str(Path(p).resolve())]]
    sources = root / '_sources/20261023'
    source_rows = [dict(path=str(p.relative_to(root)), bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                   for p in sorted(sources.iterdir()) if p.is_file() and p.name != 'source-index.json']
    (sources / 'source-index.json').write_text(json.dumps(dict(files=source_rows), indent=2), encoding='utf-8')
    for script, python in [('inspect_public_eeg.py', str(root.parent.parent / '.venv/bin/python')),
                           ('verify_public_eeg_downloads.py', 'python3'),
                           ('catalog_public_eeg_downloads.py', 'python3')]:
        print(json.dumps(dict(phase='running', script=script)), flush=True)
        subprocess.run([python, str(root / script), '--root', str(root), *plan_args], check=True)
    audit = json.loads((root / 'independent-file-verification.json').read_text())
    content = json.loads((root / 'content-inspection.json').read_text())
    errors = [dict(path=k, error=v['error']) for k,v in content['files'].items() if v['status'] == 'error']
    files = ['public-catalog.json', 'original-file-index.json', 'content-inspection.json', 'recording-index.json', 'recording-index.csv',
             'independent-file-verification.json', '公开数据下载清单与状态.md', '_sources/20261023/source-index.json']
    receipt = dict(schema='eeg.public_download_completion.v1',
                   completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   download_complete=not audit['failed_files'], expected_files=audit['expected_files'],
                   verified_files=audit['verified_files'], verified_bytes=audit['verified_bytes'],
                   structure_errors=errors, training_admission='pending_protocol_and_content_review', training_started=False,
                   files=[dict(path=name, bytes=(root/name).stat().st_size,
                               sha256=hashlib.sha256((root/name).read_bytes()).hexdigest()) for name in files])
    (root / 'download-completion.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
