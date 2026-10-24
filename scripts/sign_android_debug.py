"""Re-sign the private debug APK with the project's pinned original debug key.

The canonical Android debug password is not an account credential. The private
keystore is never copied into the workspace, reports or delivery archives.
"""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
JAVA=Path('G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/OpenJDK/bin/java.exe')
SIGNER=JAVA.parents[1]/'../SDK/build-tools/36.0.0/lib/apksigner.jar'
KEY=Path('G:/Unity/AppData/Android/debug.keystore')
CERT='656c3b5b180f1cac817a881ed65642c04ac3f6b39fba2351f632094791e549ce'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify(p):
    result=subprocess.run([str(JAVA),'-jar',str(SIGNER),'verify','--print-certs',str(p)],capture_output=True,text=True,check=True)
    return next(line.split(': ',1)[1].strip() for line in result.stdout.splitlines() if 'certificate SHA-256 digest:' in line)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--build-record',default='G:/Unity/android-build-status.json');args=parser.parse_args()
    record=Path(args.build_record);build=json.loads(record.read_text(encoding='utf-8-sig'));apk=Path(build['output'])
    if not build['success'] or digest(apk)!=build['output_sha256']:raise ValueError('Current successful APK identity required')
    before=digest(apk);temporary=apk.with_name('EEGMovement.pinned-signature.apk')
    try:
        subprocess.run([str(JAVA),'-jar',str(SIGNER),'sign','--ks',str(KEY),'--ks-key-alias','androiddebugkey','--ks-pass','pass:android','--key-pass','pass:android','--v4-signing-enabled','false','--out',str(temporary),str(apk)],capture_output=True,check=True)
        if verify(temporary)!=CERT:raise ValueError('Project original signing certificate mismatch')
        os.replace(temporary,apk)
    finally:
        if temporary.exists():temporary.unlink()
    build['output_sha256']=digest(apk)
    for name in build.get('artifact_hashes',{}):
        if name.replace('\\','/').split('/')[-1]=='EEGMovement.apk':build['artifact_hashes'][name]=build['output_sha256']
    build['artifact_postprocess']=dict(kind='private_debug_signing_compatibility',pre_sign_sha256=before,post_sign_sha256=build['output_sha256'],certificate_sha256=CERT,source_changed=False,private_key_exported=False)
    record.write_text(json.dumps(build,indent=2),encoding='utf-8');(apk.parent/'build-status.json').write_text(json.dumps(build,indent=2),encoding='utf-8')
    print(json.dumps(dict(build_id=build['build_id'],apk_sha256=build['output_sha256'],certificate_sha256=CERT,private_key_exported=False)))
if __name__=='__main__':main()
