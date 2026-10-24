"""Build the private portable client and bind it to the exact local source hashes."""
from pathlib import Path
import hashlib,json,subprocess,sys,time,os,tempfile

ROOT=Path(__file__).resolve().parents[1]
VERIFY=ROOT/'docs/gnautilus/integration_20261022/verification'


def sources():
    paths=list((ROOT/'gnautilus_acquisition').glob('*.py'))+list((ROOT/'eeg_relay').glob('*.py'))+[ROOT/'gnautilus_acquisition/relay_public.json',ROOT/'scripts/gnautilus_entry.py',ROOT/'gnautilus_acquisition/requirements-build.txt']
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    before=sources();began=time.time()
    command=[sys.executable,'-X','utf8','-m','PyInstaller','--clean','--noconfirm','--onedir','--windowed','--name','EEGMovement-gNautilus','--paths','.',
        '--distpath','outputs/gnautilus_release','--workpath','outputs/gnautilus_build','--specpath','outputs/gnautilus_build',
        '--hidden-import','win32serviceutil','--hidden-import','win32service','--hidden-import','win32timezone',
        '--hidden-import','cffi','--collect-submodules','cffi','--collect-submodules','websockets','--hidden-import','eeg_relay.endpoint','--hidden-import','gnautilus_acquisition.relay_client',
        '--add-data',str(ROOT/'gnautilus_acquisition/relay_public.json')+';gnautilus_acquisition','--exclude-module','matplotlib','--exclude-module','scipy',
        '--exclude-module','torch','--exclude-module','pandas','scripts/gnautilus_entry.py']
    # PDF/image tools on the host PATH contain incompatible ICU/UCRT DLLs. Freeze
    # against Python/Qt and the OS, never those unrelated desktop toolchains.
    build_env=dict(os.environ);windows=Path(os.environ.get('WINDIR','C:/Windows'));python=Path(sys.executable).parent
    build_env['PATH']=os.pathsep.join(map(str,[python,python/'Scripts',windows/'System32',windows]))
    with (VERIFY/'pyinstaller-final.log').open('w',encoding='utf-8') as log:subprocess.run(command,cwd=ROOT,env=build_env,stdout=log,stderr=subprocess.STDOUT,check=True)
    if before!=sources():raise RuntimeError('Acquisition source changed during freeze')
    folder=ROOT/'outputs/gnautilus_release/EEGMovement-gNautilus';exe=folder/'EEGMovement-gNautilus.exe'
    smoke=VERIFY/'frozen-smoke.json';worker=VERIFY/'frozen-worker.json'
    subprocess.run([str(exe),'--smoke-test','--report-path',str(smoke)],cwd=ROOT,check=True,timeout=20)
    subprocess.run([str(exe),'--diagnose','--sdk-dir','C:/EEGMovement-SDK-NOT-INSTALLED-SMOKE','--report-path',str(worker)],cwd=ROOT,check=True,timeout=20)
    assert json.loads(smoke.read_text('utf-8'))['vendor_loaded'] is False
    assert 'sdk_error' in json.loads(worker.read_text('utf-8'))
    ui=VERIFY/'frozen-ui.json'
    subprocess.run([str(exe),'--ui-smoke-test','--report-path',str(ui)],cwd=ROOT,check=True,timeout=30)
    assert json.loads(ui.read_text('utf-8'))['idle_rendered'] is True
    sys.path.insert(0,str(ROOT))
    from tests.test_gnautilus_transport import descriptor
    from gnautilus_acquisition.storage import StreamJournal
    from gnautilus_acquisition.contract import block_frame
    import numpy as np
    with tempfile.TemporaryDirectory() as temp:
        d=descriptor();journal=StreamJournal(temp,d);h,b=block_frame(d,1,0,np.zeros((8,32),np.float32));journal.append(h,b);journal.close()
        audit=VERIFY/'frozen-audit.json'
        subprocess.run([str(exe),'--audit',temp,'--report-path',str(audit)],cwd=ROOT,check=True,timeout=20)
        assert json.loads(audit.read_text('utf-8'))['complete_closed'] is True
    result=dict(success=True,mode='Windows x64 one-folder GUI',source_sha256=before,source_unchanged=True,python=sys.version,
        exe=str(exe),exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),elapsed_seconds=round(time.time()-began,2),
        frozen_import_smoke=True,frozen_isolated_worker_error_path=True,frozen_qt_offscreen_render=True,frozen_readonly_audit=True,isolated_build_path=True,vendor_executed=False,hardware_verified=False)
    (VERIFY/'acquisition-build.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k not in ['source_sha256','python']}))


if __name__=='__main__':main()
