"""Freeze and sign distributables. Private pairing/CA/release keys never enter packages."""
import argparse,base64,hashlib,json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
APP_VERSION='1.1.13';APP_RELEASE_SEQ=11013;QUEST_VERSION='2.1.4';QUEST_CODE=21005
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from eeg_relay.update import file_hash,verify_manifest

def sign_artifact(path,platform,version,seq,**extra):
    value=dict(schema='eegmovement.update.v1',platform=platform,version=version,release_seq=seq,protocol=1,path='releases/'+path.name,size=path.stat().st_size,sha256=file_hash(path),**extra)
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode();key=serialization.load_pem_private_key((ROOT/'hub/runtime/relay_private/release-signing.key').read_bytes(),password=None)
    signed=json.dumps(dict(payload=base64.b64encode(raw).decode(),signature=base64.b64encode(key.sign(raw,padding.PKCS1v15(),hashes.SHA256())).decode()),indent=2).encode()
    config=json.loads((ROOT/'gnautilus_acquisition/relay_public.json').read_text('utf-8'));verify_manifest(signed,config['release_public_key'],platform)
    return signed,value

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--quest-only',action='store_true');ap.add_argument('--windows-only',action='store_true');args=ap.parse_args()
    out=ROOT/'outputs/relay_20261023/release';out.mkdir(parents=True,exist_ok=True);records={}
    if not args.quest_only:
        subprocess.run([sys.executable,'scripts/build_gnautilus_acquisition.py'],cwd=ROOT,check=True)
        folder=ROOT/'outputs/gnautilus_release/EEGMovement-gNautilus';archive=out/f'EEGMovement-Windows-x64-{APP_VERSION}.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for p in sorted(folder.rglob('*')):
                if p.is_file():z.write(p,p.relative_to(folder.parent).as_posix())
        signed,value=sign_artifact(archive,'windows-x64',APP_VERSION,APP_RELEASE_SEQ);(out/'windows-x64.json').write_bytes(signed);records['windows']=value
        work=ROOT/'outputs/relay_20261023/installer';work.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'gnautilus_acquisition/relay_public.json',work/'relay_public.json');shutil.copyfile(archive,work/'bootstrap.zip');(work/'bootstrap.manifest.json').write_bytes(signed)
        win=Path(os.environ.get('WINDIR','C:/Windows'));py=Path(sys.executable).parent;env=dict(os.environ,PATH=os.pathsep.join(map(str,[py,py/'Scripts',win/'System32',win])))
        def freeze(name,data):
            command=[sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onefile','--windowed','--name',name,'--paths',str(ROOT),'--distpath',str(out),'--workpath',str(work/'build'),'--specpath',str(work),'--exclude-module','numpy','--exclude-module','PySide6']
            for src,dest in data:command+=['--add-data',str(src)+';'+dest]
            command+=['scripts/gnautilus_launcher.py']
            with (work/(name+'.log')).open('w',encoding='utf-8') as log:subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        launcher='EEGMovement-Launcher-'+APP_VERSION
        freeze(launcher,[(work/'relay_public.json','.')]);shutil.copyfile(out/(launcher+'.exe'),work/'EEGMovement-Launcher.exe')
        freeze('EEGMovement-Setup-'+APP_VERSION,[(work/'relay_public.json','.'),(work/'bootstrap.zip','.'),(work/'bootstrap.manifest.json','.'),(work/'EEGMovement-Launcher.exe','.')])
    if not args.windows_only:
        import re
        source=Path('G:/EEGMovement/Builds/20261024-gaze/EEGMovement-2.1.4.apk');dest=out/f'EEGMovement-Quest-{QUEST_VERSION}.apk'
        aapt=next(Path('G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/build-tools').glob('*/aapt.exe'))
        badging=subprocess.check_output([str(aapt),'dump','badging',str(source)],text=True,encoding='utf-8',errors='replace')
        actual=re.search(r"versionCode='(\d+)' versionName='([^']+)'",badging)
        if not actual or actual.groups()!=(str(QUEST_CODE),QUEST_VERSION):raise ValueError('APK actual version differs from update manifest')
        sdk=Path('G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer')
        signer=next((sdk/'SDK/build-tools').glob('*/lib/apksigner.jar'))
        signed_cert=subprocess.check_output([str(sdk/'OpenJDK/bin/java.exe'),'-jar',str(signer),'verify','--print-certs',str(source)],text=True,encoding='utf-8',errors='replace')
        if '656c3b5b180f1cac817a881ed65642c04ac3f6b39fba2351f632094791e549ce' not in signed_cert.lower():raise ValueError('APK signing identity differs from installed update contract')
        shutil.copy2(source,dest)
        signed,value=sign_artifact(dest,'quest-arm64',QUEST_VERSION,QUEST_CODE,version_code=QUEST_CODE,package_name='org.eegmovement.workspace',certificate_sha256='656c3b5b180f1cac817a881ed65642c04ac3f6b39fba2351f632094791e549ce')
        (out/'quest-arm64.json').write_bytes(signed);records['quest']=value
    report=ROOT/'docs/gnautilus/relay_20261023_followup/release.json';old=json.loads(report.read_text('utf-8')) if report.exists() else {};old.update(records)
    old['artifacts']={p.name:dict(bytes=p.stat().st_size,sha256=file_hash(p)) for p in out.iterdir() if p.is_file()};report.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(old,ensure_ascii=False))

if __name__=='__main__':main()
