"""Freeze the verified Android inputs without private enrollment or runtime files."""
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def main():
    build = json.loads(Path('G:/Unity/android-build-status.json').read_text(encoding='utf-8-sig'))
    if not (build.get('success') and build.get('source_snapshot_unchanged')
            and build.get('phase') == 'succeeded' and build.get('exit_code') == 0):
        raise ValueError('A completed, stable successful Android build is required')
    apk = Path(build['output']).resolve()
    if not apk.is_relative_to(Path('G:/EEGMovement/Android').resolve()) or digest(apk) != build['output_sha256']:
        raise ValueError('APK identity mismatch')
    sources = build['source_hashes']
    for name, expected in sources.items():
        relative = PurePosixPath(name)
        path = (ROOT / name).resolve()
        if ('..' in relative.parts or relative.is_absolute() or not path.is_relative_to(ROOT / 'unity/EEGMovement')
                or digest(path) != expected):
            raise ValueError('Source identity mismatch: ' + name)
    snapshot = Path('G:/EEGMovement/SourceSnapshots') / ('Android-' + build['build_id'] + '.zip')
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(snapshot, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(sources):
            archive.write(ROOT / name, name)
        archive.writestr('_build.json', json.dumps(build, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(snapshot) as archive:
        if archive.testzip() is not None:
            raise ValueError('Snapshot CRC validation failed')
        for name, expected in sources.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected or digest(ROOT / name) != expected:
                raise ValueError('Source changed while archiving: ' + name)
    build.update(build_success=True, path=str(apk), sha256=build['output_sha256'], headset_verified=False,
                 source_snapshot=dict(path=str(snapshot), sha256=digest(snapshot)))
    (ROOT / 'outputs/android_build_validation.json').write_text(json.dumps(build, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(build_id=build['build_id'], source_files=len(sources), snapshot=str(snapshot),
                          apk_sha256=build['output_sha256'], scope='Build and source identity; physical headset acceptance separate')))


if __name__ == '__main__':
    main()
