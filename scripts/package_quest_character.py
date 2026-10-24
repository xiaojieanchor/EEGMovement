"""Private local Quest delivery; retain current build identity and exclude runtime secrets."""
import datetime
import json
import shutil
import zipfile
from pathlib import Path

from package_release import ROOT, collect_source_files, safe_archive_name, digest, verify_archive


def main():
    build=json.loads((ROOT/'outputs/android_build_validation.json').read_text(encoding='utf-8'))
    if not (build.get('success') and build.get('source_snapshot_unchanged') and build.get('phase')=='succeeded'):
        raise ValueError('Stable successful Android build required')
    for name in ['quest_runtime_validation.json','quest_feature_validation.json']:
        proof=json.loads((ROOT/'outputs'/name).read_text(encoding='utf-8'))
        if proof.get('status')!='pass' or proof.get('build_id')!=build['build_id'] or proof.get('apk_sha256')!=build['output_sha256']:
            raise ValueError('Current native runtime proof required: '+name)
    sources=build['source_hashes']
    for name,expected in sources.items():
        if digest(ROOT/name)!=expected:raise ValueError('Build source changed: '+name)
    destination=Path('G:/EEGMovement/Releases/Quest-Character-Monitor-20261015')/build['build_id']
    destination.mkdir(parents=True,exist_ok=True)
    files=[p for p in collect_source_files(ROOT) if not safe_archive_name(ROOT,p).startswith('outputs/')]
    selected={safe_archive_name(ROOT,p) for p in files}
    if not set(sources).issubset(selected):raise ValueError('Source delivery is missing build inputs')
    hashes={safe_archive_name(ROOT,p):digest(p) for p in files}
    archive_path=destination/'EEGMovement-Source.zip'
    with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in files:archive.write(p,safe_archive_name(ROOT,p))
        archive.writestr('_source_manifest.json',json.dumps(hashes,indent=2))
    verify_archive(archive_path,hashes,extras=('_source_manifest.json',))
    copies={
        'EEGMovement.apk':Path(build['output']),
        'Android-Build-Inputs.zip':Path(build['source_snapshot']['path']),
    }
    evidence=['android_build_validation.json','quest_deployment.json','quest_runtime_validation.json',
              'quest_feature_validation.json','quest_tracking_validation.json','quest_feature_runtime.log',
              'quest_view_runtime.log','avatar_retarget_validation.json','character_asset_manifest.json',
              'avatar-walk-retarget.gif','quest_live_preview.jpg','training_readiness.json',
              'training_server_verification.json','training_gpu_check.json','training_runner_check.json',
              'training_environment_freeze.txt','training_readiness_final_check.txt',
              'quest_user_feedback.json','pretraining_remaining_checklist.json','quest_preparation_snapshot.json',
              'quest_current_chain_status.json',
              'device_wiring_user_report_20261015.json',
              'quest_log_collector_validation.json','training_data_audit.json','training_preflight.json',
              'training_environment.json','training_concurrent_link_observation.json','training_unit_tests.txt',
              '角色监控与训练前检查_20261015.md']
    for name in evidence:copies['Evidence/'+name]=ROOT/'outputs'/name
    expected=dict(hashes=hashes,apk=build['output_sha256'],snapshot=build['source_snapshot']['sha256'])
    for name,path in copies.items():
        if not path.is_file():raise ValueError('Missing delivery file: '+str(path))
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        if digest(path)!=digest(target):raise ValueError('Copy hash mismatch: '+name)
    if digest(destination/'EEGMovement.apk')!=expected['apk'] or digest(destination/'Android-Build-Inputs.zip')!=expected['snapshot']:
        raise ValueError('Frozen artifact hash mismatch')
    for name,wanted in hashes.items():
        if digest(ROOT/name)!=wanted:raise ValueError('Source changed during packaging: '+name)
    manifest=dict(created_at=datetime.datetime.now().astimezone().isoformat(),build_id=build['build_id'],
                  apk_sha256=build['output_sha256'],scope='Private local Quest build and PC hub source. Does not update or validate the older Windows Unity player.',
                  redistribution='Model by 水色の描. Personal local prototype only; no public redistribution.',
                  exclusions='runtime credentials, enrollment, pairing keys, EEG sessions, model weights, caches',
                  files={p.relative_to(destination).as_posix():digest(p) for p in destination.rglob('*') if p.is_file() and p.name!='SHA256-Manifest.json'})
    (destination/'SHA256-Manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(destination=str(destination),build_id=build['build_id'],files=len(manifest['files']),source_files=len(files)),ensure_ascii=False))


if __name__=='__main__':main()
