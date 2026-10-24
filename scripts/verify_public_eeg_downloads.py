"""Independent full-file SHA256 audit of the explicit download plans."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--plan', action='append', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    rows = []
    expected = 0
    for filename in args.plan:
        plan = json.loads(Path(filename).read_text())
        expected += len(plan['items'])
        for item in plan['items']:
            path = (root / item['relative_path']).resolve()
            result = dict(path=item['relative_path'], dataset=item['dataset'], status='failed')
            try:
                if not path.is_relative_to(root):
                    raise ValueError('File is outside the source root')
                receipt = json.loads(path.with_name(path.name + '.receipt.json').read_text())
                digest = hashlib.sha256()
                git_digest = hashlib.sha1() if item.get('git_blob_sha') and not item.get('annex_pointer') else None
                size = path.stat().st_size
                if git_digest is not None:
                    git_digest.update(b'blob ' + str(size).encode() + b'\0')
                with path.open('rb') as source:
                    for block in iter(lambda: source.read(8 * 1024**2), b''):
                        digest.update(block)
                        if git_digest is not None:
                            git_digest.update(block)
                actual = digest.hexdigest()
                if actual != receipt['sha256'] or size != receipt['bytes']:
                    raise ValueError('Full-file content no longer agrees with committed receipt')
                if item.get('bytes') is not None and size != item['bytes']:
                    raise ValueError('Byte count differs from the fixed source plan')
                if item.get('sha256') and actual != item['sha256']:
                    raise ValueError('SHA256 differs from the fixed source plan')
                if item.get('git_blob_sha'):
                    if item.get('annex_pointer'):
                        pointer = item['annex_pointer'].encode()
                        git_actual = hashlib.sha1(b'blob ' + str(len(pointer)).encode() + b'\0' + pointer).hexdigest()
                    else:
                        git_actual = git_digest.hexdigest()
                    if git_actual != item['git_blob_sha']:
                        raise ValueError('Pinned OpenNeuro Git blob differs')
                result.update(status='verified', bytes=size, sha256=actual,
                              publisher_md5_in_receipt=receipt.get('md5') if item.get('md5') else None,
                              pinned_git_blob_checked=bool(item.get('git_blob_sha')))
            except Exception as error:
                result['error'] = type(error).__name__ + ': ' + str(error)
            rows.append(result)
    failed = [r for r in rows if r['status'] != 'verified']
    report = dict(schema='eeg.public_independent_file_audit.v1',
                  updated_utc=datetime.now(timezone.utc).isoformat(),
                  expected_files=expected, verified_files=expected-len(failed), failed_files=len(failed),
                  verified_bytes=sum(r.get('bytes', 0) for r in rows), files=rows, training_started=False)
    target = root / 'independent-file-verification.json'
    temporary = target.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, target)
    print(json.dumps({k:v for k,v in report.items() if k != 'files'}))
    if failed:
        print(json.dumps(failed[:20]))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
