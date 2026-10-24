"""Exercise real frozen bootstrap, then a signed bad-version rollback in an isolated root."""
import json,os,subprocess,sys,time,zipfile
from pathlib import Path
from unittest.mock import patch
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.build_public_release import sign_artifact,APP_VERSION,APP_RELEASE_SEQ
from scripts import gnautilus_launcher as launcher

def main():
    target=ROOT/('outputs/relay_20261023/install-check-'+str(time.time_ns()));target.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,EEGMOVEMENT_INSTALL_ROOT=str(target),LOCALAPPDATA=str(target/'profile'),QT_QPA_PLATFORM='offscreen');os.environ['QT_QPA_PLATFORM']='offscreen'
    setup=ROOT/f'outputs/relay_20261023/release/EEGMovement-Setup-{APP_VERSION}.exe'
    process=subprocess.Popen([str(setup),'--install','--no-shortcut'],env=env)
    began=time.monotonic();previous=[];launched=[]
    try:
        result=process.wait(timeout=120)
        if result:raise RuntimeError('Bootstrap exit '+str(result))
        current=json.loads((target/'current.json').read_text());reports=list((target/'updates').glob('health-*.json'))
        receipt=json.loads(reports[0].read_text());child=psutil.Process(receipt['pid'])
        if not receipt.get('ready') or not str(child.exe()).startswith(str(target)):raise ValueError('No matching native application health')
        child.terminate();child.wait(10);previous.append(receipt['pid'])
        bad=target/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:z.writestr('EEGMovement-gNautilus/EEGMovement-gNautilus.exe',b'not a Windows executable')
        version=APP_VERSION.rsplit('.',1)[0]+'.'+str(int(APP_VERSION.rsplit('.',1)[1])+1)
        signature,_=sign_artifact(bad,'windows-x64',version,APP_RELEASE_SEQ+1);manifest=target/'bad.json';manifest.write_bytes(signature)
        cfg=json.loads((ROOT/'gnautilus_acquisition/relay_public.json').read_text())
        original_launch=launcher.launch
        def track(*args,**kwargs):
            p=original_launch(*args,**kwargs);launched.append(p);return p
        failed=False
        with patch.object(launcher,'config',lambda:cfg),patch.object(launcher,'launch',track):
            try:launcher.apply(target,bad,manifest)
            except RuntimeError:failed=True
        restored=json.loads((target/'current.json').read_text())
        if not failed or restored!=current or not launched:raise ValueError('Rollback did not restore previous version')
        time.sleep(2)
        if launched[-1].poll() is not None:raise ValueError('Previous native version failed to relaunch')
        record=dict(scope='isolated native Windows bootstrap and failed update',setup_exit=result,health_ready=True,installed_version=current['version'],signature_verified=True,bad_executable_rejected=True,previous_pointer_restored=True,previous_native_version_relaunched=True,sdk_started=False,desktop_shortcut_created=False,elapsed_s=round(time.monotonic()-began,2))
        (ROOT/'docs/gnautilus/relay_20261023_followup/windows-install-check.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))
    finally:
        if process.poll() is None:process.terminate()
        for p in launched:
            if p.poll() is None:p.terminate();p.wait(10)

if __name__=='__main__':main()
