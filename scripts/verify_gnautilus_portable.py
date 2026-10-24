"""Re-extract the delivered ZIP and run it with no Python/toolchain on PATH."""
from pathlib import Path
import argparse,json,os,subprocess,sys,tempfile,zipfile
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VERIFY=ROOT/'docs/gnautilus/integration_20261022/verification'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pairing');args=parser.parse_args()
    from gnautilus_acquisition.audit import file_hash
    from tests.test_gnautilus_audit import AuditTests
    record=json.loads((VERIFY/'packages.json').read_text('utf-8'));archive=Path(record['portable']['path'])
    base=ROOT/'outputs/gnautilus_portable_verification';base.mkdir(exist_ok=True)
    env=dict(os.environ);windows=Path(env.get('WINDIR','C:/Windows'));env['PATH']=os.pathsep.join(map(str,[windows/'System32',windows]))
    for name in ['PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QML2_IMPORT_PATH']:env.pop(name,None)
    with tempfile.TemporaryDirectory(prefix='采集工作台 clean ',dir=base) as temporary:
        temporary=Path(temporary).resolve()
        if not temporary.is_relative_to(base.resolve()):raise ValueError('Temporary path escaped verification root')
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                if not (temporary/item.filename).resolve().is_relative_to(temporary):raise ValueError('Unsafe ZIP member')
            z.extractall(temporary)
        folder=temporary/'EEGMovement-gNautilus';manifest=json.loads((folder/'FILES_SHA256.json').read_text('utf-8'))
        if any(file_hash(folder/name)!=row['sha256'] for name,row in manifest.items()):raise ValueError('Extracted file hash mismatch')
        exe=folder/'EEGMovement-gNautilus.exe'
        commands=[['--smoke-test'],['--ui-smoke-test'],['--diagnose','--sdk-dir',str(temporary/'未安装SDK')]]
        tests=[]
        for index,flags in enumerate(commands):
            path=VERIFY/f'extracted-{index}.json'
            completed=subprocess.run([str(exe),*flags,'--report-path',str(path)],cwd=temporary,env=env,timeout=30)
            assert completed.returncode == (2 if '--diagnose' in flags else 0)
            tests.append(json.loads(path.read_text('utf-8')))
        left,right=temporary/'本地原始流',temporary/'中枢副本';fixture=AuditTests();fixture.write(left);fixture.write(right,(2,1))
        path=VERIFY/'extracted-audit.json'
        subprocess.run([str(exe),'--audit',str(left),'--compare',str(right),'--report-path',str(path)],cwd=temporary,env=env,check=True,timeout=30)
        audit=json.loads(path.read_text('utf-8'));assert audit['comparison_match'] is True
        paired=None
        if args.pairing:
            path=VERIFY/'extracted-local-hub.json'
            completed=subprocess.run([str(exe),'--diagnose','--pairing',str(Path(args.pairing).resolve()),'--report-path',str(path)],cwd=temporary,env=env,timeout=30)
            assert completed.returncode in (0,2)  # SDK may be absent; pairing is checked separately below.
            paired=json.loads(path.read_text('utf-8'));assert 'hub' in paired and 'hub_error' not in paired
    result=dict(zip_sha256=file_hash(archive),all_extracted_file_hashes_match=True,clean_path_without_python=True,unicode_and_space_path=True,
        frozen_import=True,frozen_qt_idle_render=tests[1]['idle_rendered'],frozen_diagnosis_rejected_missing_sdk=tests[2].get('code')=='sdk_directory_missing',frozen_two_end_logical_audit=audit['comparison_match'],
        native_device_verified=False,actual_absence_of_python_on_target_verified=False,frozen_local_hub_authentication=bool(paired) if args.pairing else None)
    (VERIFY/'portable-extracted.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result))

if __name__=='__main__':main()
