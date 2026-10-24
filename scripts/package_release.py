"""Package a hash-bound, validated local release without private runtime data."""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {'runtime', '__pycache__', '.git', 'Library', 'Temp', 'Logs', 'UserSettings',
                  'obj', 'bin', 'sessions', '.pytest_cache'}
PRIVATE_SUFFIXES = {'.pyc', '.key', '.pem', '.pfx', '.p12', '.safetensors', '.pth', '.pt', '.ckpt', '.jks', '.keystore'}
PRIVATE_STEMS = {'password', 'passwords', 'credential', 'credentials', 'secret', 'secrets',
                 'account', 'accounts', 'token', 'tokens', 'vr_pairing', 'pairing',
                 'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa'}
VALIDATION_ROOT = PurePosixPath('unity/EEGMovement/Assets/Validation~')
SOURCE_DIRECTORIES = ['hub', 'scripts', 'tests', 'training', 'configs', 'protocols', 'docs',
                      'unity/EEGMovement/Assets', 'unity/EEGMovement/Packages',
                      'unity/EEGMovement/ProjectSettings', 'receiver']
EVIDENCE_FILES = ['release_validation.json', 'training_readiness.json', 'training_preflight.json',
                  'training_data_audit.json', 'training_environment_freeze.txt',
                  'unity_installation_validation_20261015.json', 'unity_runtime_validation.json',
                  'unity_scene_validation.json', 'execution_validation.json', 'execution_release_manifest.json', 'execution_e2e.json', 'execution_soak_validation.json', 'receiver_udp_validation.json', 'signal_noise_validation.json', 'pilot_schedule.json', 'unity_udp_validation.json', 'unity_safety_validation.json', 'unity_frame_stall_validation.json', 'unity_acquisition_validation.json', 'android_build_validation.json', 'preparation_final_snapshot.json', 'api_responsiveness_validation.json', '代码审阅与修复_20261015.md', 'VR改版与最终验收_20261015.md']


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def safe_archive_name(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Package member is outside the named workspace')
    name = path.relative_to(root).as_posix()
    if not name or name.startswith('/') or '..' in Path(name).parts:
        raise ValueError('Unsafe archive member name')
    return name


def unity_runtime_source(name):
    normalized=name.casefold()
    return (normalized=='unity/eegmovement/assets/scripts/runtime'
            or normalized.startswith('unity/eegmovement/assets/scripts/runtime/')
            or normalized.startswith('unity/eegmovement/packages/'))


def private_member(name, allow_runtime_source=False):
    """Name-based exclusions apply to every entry point, case-insensitively."""
    path = PurePosixPath(name)
    parts = {part.casefold() for part in path.parts}
    excluded = {part.casefold() for part in EXCLUDED_PARTS}
    if allow_runtime_source:excluded.discard('runtime')
    return bool(parts & excluded or path.suffix.lower() in PRIVATE_SUFFIXES
                or path.name.lower().startswith('.env') or path.stem.lower() in PRIVATE_STEMS)


def source_member_allowed(name):
    if private_member(name, allow_runtime_source=unity_runtime_source(name)):
        return False
    path = PurePosixPath(name)
    if path.is_relative_to(VALIDATION_ROOT):
        relative = path.relative_to(VALIDATION_ROOT)
        # Downloaded XR packages and generated checks/results are scratch data.
        return len(relative.parts) == 1 and (path.suffix.lower() in {'.cs', '.csproj'}
                                             or path.name == 'README.md')
    return True


def collect_source_files(root):
    root = Path(root).resolve()
    selected = set()
    def include(path):
        if path.is_symlink():
            return
        if path.is_file() and source_member_allowed(safe_archive_name(root, path)):
            selected.add(path)
    for directory in SOURCE_DIRECTORIES:
        base = root / directory
        if not base.is_dir():
            continue
        for current, directories, files in os.walk(base, followlinks=False):
            current = Path(current)
            directories[:] = [name for name in directories
                              if not (current / name).is_symlink()
                              and not private_member(safe_archive_name(root, current / name),
                                  allow_runtime_source=unity_runtime_source(safe_archive_name(root,current/name)))
                              and current.relative_to(root).as_posix() != str(VALIDATION_ROOT)]
            for name in files:
                include(current / name)
    for name in ['README.md', 'requirements-hub.txt', '.gitignore', 'unity/README.md',
                 '启动Unity体验.cmd', '启动中枢.cmd', '停止中枢.cmd', '训练准备检查.cmd', '启动控制联调.cmd', '停止控制接收器.cmd']:
        include(root / name)
    for directory in ['research', 'models']:
        base = root / directory
        if base.is_dir():
            for path in base.glob('*'):
                if path.suffix in {'.md', '.json', '.csv', '.tsv', '.txt'}:
                    include(path)
    for name in EVIDENCE_FILES:
        include(root / 'outputs' / name)
    for path in (root / 'outputs').glob('vr_ui_final*.png'):
        include(path)
    return sorted(selected, key=lambda path: safe_archive_name(root, path))


def validate_hash_manifest(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(label + ' has no file hashes')
    seen = set()
    for name, expected in value.items():
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or PurePosixPath(name).as_posix() != name or name.casefold() in seen):
            raise ValueError(label + ' has an unsafe or duplicate member name')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError(label + ' has an invalid SHA256 hash')
        seen.add(name.casefold())
    return value


def windows_file_hashes(directory):
    """Snapshot every player file, including Data/Managed and streaming assets."""
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('Windows player directory does not exist')
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('Windows player tree must not contain linked members')
        if path.is_file():
            name = safe_archive_name(directory, path)
            if private_member(name):
                raise ValueError('Windows player contains an excluded private/cache member: ' + name)
            files[name] = digest(path)
    return validate_hash_manifest(files, 'Windows player tree')


def validate_artifact(path, build_id, evidence):
    if evidence.get('status') != 'pass' or not build_id or evidence.get('build_id') != build_id:
        raise ValueError('Final software validation is incomplete or belongs to a different build')
    validate_hash_manifest(evidence.get('source_hashes'), 'Final validation source_hashes')
    expected = validate_hash_manifest(evidence.get('windows_files'), 'Final validation windows_files')
    path = Path(path).resolve()
    actual = windows_file_hashes(path.parent)
    if actual != expected:
        raise ValueError('Windows player file tree changed after final validation')
    if actual.get(path.name) != evidence.get('windows_sha256'):
        raise ValueError('Windows executable changed after final validation')
    return actual[path.name]


def verify_archive(destination, expected, extras=()):
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | set(extras):
            raise ValueError('Archive members differ from the validated file tree')
        if archive.testzip() is not None:
            raise ValueError('Archive failed its CRC check')
        for name, wanted in expected.items():
            actual = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    actual.update(block)
            if actual.hexdigest() != wanted:
                raise ValueError('Archive content differs from validated file: ' + name)


def write_source_archive(root, destination):
    files = collect_source_files(root)
    hashes = {safe_archive_name(root, path): digest(path) for path in files}
    manifest = {'created_at': datetime.datetime.now().astimezone().isoformat(), 'files': hashes,
                'exclusions': 'private runtime, credentials, raw EEG sessions, weights, large datasets, caches'}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, safe_archive_name(root, path))
        archive.writestr('_source_manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    verify_archive(destination, hashes, extras=('_source_manifest.json',))
    return {'path': str(destination), 'files': len(files), 'sha256': digest(destination)}


def package(root, destination, windows_directory, build_record, evidence):
    root, destination, windows_directory = Path(root).resolve(), Path(destination).resolve(), Path(windows_directory).resolve()
    windows_binary = windows_directory / 'EEGMovement.exe'
    validate_artifact(windows_binary, build_record.get('build_id'), evidence)
    selected={safe_archive_name(root,path) for path in collect_source_files(root)}
    for name,expected in (build_record.get('source_hashes') or {}).items():
        path=(root/name).resolve()
        safe_archive_name(root,path)
        if name not in selected or digest(path)!=expected:
            raise ValueError('Build identity input missing or changed in source archive: '+name)
    def recheck_sources():
        for name, expected in evidence['source_hashes'].items():
            path = (root / name).resolve()
            safe_archive_name(root, path)
            if not path.is_file() or digest(path) != expected:
                raise ValueError('Validated source changed: ' + name)
    recheck_sources()
    if destination.is_relative_to(windows_directory):
        raise ValueError('Release destination must be outside the Windows player tree')
    destination.mkdir(parents=True, exist_ok=True)
    # Validation failures must leave any previous delivery and manifest intact.
    with tempfile.TemporaryDirectory(prefix='.eeg-release-', dir=destination) as temporary:
        staging = Path(temporary)
        source = write_source_archive(root, staging / 'EEGMovement-Source.zip')
        source['path'] = str(destination / 'EEGMovement-Source.zip')
        player = staging / 'EEGMovement-Windows.zip'
        with zipfile.ZipFile(player, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in evidence['windows_files']:
                archive.write(windows_directory / name, name)
        verify_archive(player, evidence['windows_files'])
        result = {'created_at': datetime.datetime.now().astimezone().isoformat(), 'build_id': build_record['build_id'],
                  'validation': 'outputs/release_validation.json', 'formal_training_ready': False,
                  'windows_files': evidence['windows_files'], 'source_hashes': evidence['source_hashes'],
                  'artifacts': [source, {'path': str(destination / player.name), 'sha256': digest(player)}]}
        android = evidence.get('android') or {}
        if android.get('build_success') and android.get('path'):
            apk = Path(android['path'])
            if digest(apk) != android.get('sha256'):
                raise ValueError('Android artifact changed after build validation')
            target = staging / 'EEGMovement-Android.apk'
            shutil.copy2(apk, target)
            if digest(target) != android.get('sha256'):
                raise ValueError('Android artifact changed during packaging')
            result['artifacts'].append({'path': str(destination / target.name), 'sha256': digest(target), 'build_id':android.get('build_id'), 'source_hashes':android.get('source_hashes',{}), 'headset_verified': False})
        validate_artifact(windows_binary, build_record.get('build_id'), evidence)
        recheck_sources()
        manifest = staging / 'SHA256-Manifest.json'
        manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        # Publish the manifest last, after all complete archives have passed.
        for artifact in result['artifacts']:
            target = Path(artifact['path'])
            (staging / target.name).replace(target)
        manifest.replace(destination / manifest.name)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', required=True)
    parser.add_argument('--windows', default='G:/EEGMovement/Windows')
    args = parser.parse_args()
    evidence = json.loads((ROOT / 'outputs/release_validation.json').read_text(encoding='utf-8-sig'))
    build = json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig'))
    result = package(ROOT, args.destination, args.windows, build, evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
