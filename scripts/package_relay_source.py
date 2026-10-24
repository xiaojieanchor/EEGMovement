"""Source delta for review; explicit allowlist excludes runtime, vendor payloads and keys."""
import hashlib,json,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    files=set()
    for directory in ['eeg_relay','gnautilus_acquisition','hub','receiver','deploy/eeg-relay','unity/EEGMovement/Assets/Scripts','unity/EEGMovement/Assets/Plugins/Android']:
        for path in (ROOT/directory).rglob('*'):
            relative=path.relative_to(ROOT);parts=relative.parts
            if path.is_file() and not any(p in parts for p in ('runtime','__pycache__','bin','obj')) and path.suffix.lower() in ('.py','.cs','.java','.js','.html','.css','.txt','.conf','.service','.json','.xml'):files.add(path)
    names=['build_gnautilus_acquisition.py','build_public_release.py','build_relay_vr.ps1','deploy_public_relay.py','deploy_quest_public.ps1','publish_public_release.py','provision_public_relay.py','gnautilus_launcher.py','ssh_physical_pipe.py','verify_public_relay.py','verify_windows_relay_install.py','verify_public_updates.py','verify_gnautilus_qt_ui.py','package_relay_source.py','build_receiver.ps1','sign_android_debug.py']
    names.append('verify_public_followup.py')
    names.append('verify_relay_package_secrets.py')
    names.append('verify_gnautilus_automatic_ui.py')
    names.append('verify_windows_setup_doubleclick.py')
    names.append('verify_idle_status_update.py')
    names.append('verify_relay_details.cjs')
    names.append('verify_native_auto_update.py')
    files.update(ROOT/'scripts'/n for n in names)
    files.update((ROOT/'tests').glob('test_relay_*.py'));files.update((ROOT/'tests').glob('test_gnautilus*.py'))
    files.update([ROOT/'tests/RelayProbe.java',ROOT/'tests/test_gnautilus_web.js',ROOT/'unity/EEGMovement/Assets/Resources/relay_public.json',ROOT/'unity/EEGMovement/Assets/link.xml',ROOT/'configs/device_transition.json'])
    files.update((ROOT/'docs/gnautilus/relay_20261023').glob('*.md'))
    files.update((ROOT/'docs/gnautilus/relay_20261023_followup').glob('*.md'))
    files.update((ROOT/'docs/gnautilus/pairing_ui_fix_20261023').glob('*.md'))
    files.update((ROOT/'docs/gnautilus/setup_upgrade_fix_20261023').glob('*.md'))
    files.update((ROOT/'docs/gnautilus/status_link_fix_20261023').glob('*.md'))
    files.add(ROOT/'tests/test_windows_setup.py')
    files.add(ROOT/'tests/test_status_link.py')
    public_files=[]
    for path in files:
        name=path.relative_to(ROOT).as_posix()
        if any(part in path.relative_to(ROOT).parts for part in ('relay_private','relay_enrollments')) or path.name in ('server.key','cache.key'):raise ValueError('Sensitive path in allowlist')
        public_files.append(path)
    from scripts.build_public_release import APP_VERSION,QUEST_VERSION
    folder=ROOT/'outputs/relay_20261023/release';archive=folder/f'EEGMovement-Relay-Source-{APP_VERSION}-Quest-{QUEST_VERSION}.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(public_files):z.write(path,path.relative_to(ROOT).as_posix())
        z.writestr('SOURCE-README.md','# 公网版增量源码\n\n基于现有EEGMovement工程。包含传输、Hub、采集、Quest源码和部署/验证脚本；不是独立Unity完整资源工程。不含原厂SDK、模型、训练、数据、配对秘密、CA私钥或发布私钥。依赖安装和原厂共享方式见文档。\n')
    record=dict(path=str(archive),files=len(public_files),bytes=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),private_inputs_included=False,source_sha256={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(public_files)})
    (ROOT/'docs/gnautilus/relay_20261023_followup/source-package.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in record.items() if k!='source_sha256'}))

if __name__=='__main__':main()
