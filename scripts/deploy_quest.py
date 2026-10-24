"""Install our APK and USB-provision paired LAN credentials without logging keys."""
import argparse,hashlib,json,os,re,secrets,socket,subprocess,sys,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hub.quest import private_ipv4,enrolled_ipv4

def main():
    p=argparse.ArgumentParser();p.add_argument('--serial');p.add_argument('--hub-host');p.add_argument('--apk')
    p.add_argument('--allow-campus-host',action='store_true',help='Explicitly enroll this campus unicast IPv4 even when it is not RFC1918')
    p.add_argument('--adb',default='G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb.exe');args=p.parse_args()
    def adb(*arguments):
        command=[args.adb]+(['-s',args.serial] if args.serial else [])+list(arguments)
        result=subprocess.run(command,capture_output=True,text=True,timeout=120)
        if result.returncode:raise RuntimeError('ADB operation failed: '+result.stderr[-300:]+result.stdout[-300:])
        return result.stdout
    if not args.serial:
        rows=[line.split()[0] for line in adb('devices').splitlines()[1:] if '\tdevice' in line]
        if len(rows)!=1:raise ValueError('Specify one authorized --serial')
        args.serial=rows[0]
    address=re.search(r'inet (\d+\.\d+\.\d+\.\d+)/',adb('shell','ip','-4','addr','show','wlan0'))
    if not address or not private_ipv4(address[1]):raise ValueError('Quest private Wi-Fi IPv4 required')
    address=address[1]
    if not args.hub_host:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.connect((address,8766));args.hub_host=probe.getsockname()[0]
    if not enrolled_ipv4(args.hub_host) or not private_ipv4(args.hub_host) and not args.allow_campus_host:raise ValueError('Specify actual --hub-host; public campus IPv4 also requires --allow-campus-host')
    latest=ROOT/'docs/gnautilus/integration_20261022/verification/android-build.json'
    build_path=latest if latest.exists() else Path('G:/Unity/android-build-status.json')
    build=json.loads(build_path.read_text(encoding='utf-8-sig'))
    apk=Path(args.apk or build['output'])
    if args.apk and apk.resolve()!=Path(build['output']).resolve():
        build_path=Path('G:/Unity/android-build-status.json');build=json.loads(build_path.read_text(encoding='utf-8-sig'))
    sha=hashlib.sha256(apk.read_bytes()).hexdigest()
    if not build.get('success') or sha!=build.get('output_sha256'):raise ValueError('APK does not match successful build evidence')
    # A changed Windows/Gradle user-home must not silently switch Android's
    # debug key and tempt a destructive uninstall of the existing application.
    from scripts.sign_android_debug import verify,CERT
    if verify(apk)!=CERT:
        if apk.resolve()!=Path(build['output']).resolve():raise ValueError('Custom APK signing certificate differs from the project original')
        subprocess.run([sys.executable,str(ROOT/'scripts/sign_android_debug.py'),'--build-record',str(build_path)],cwd=ROOT,check=True)
        if build_path!=latest:subprocess.run([sys.executable,str(ROOT/'scripts/archive_android_build.py')],cwd=ROOT,check=True)
        build=json.loads(build_path.read_text(encoding='utf-8-sig'));sha=hashlib.sha256(apk.read_bytes()).hexdigest()
    runtime=ROOT/'hub/runtime';device_file=runtime/'quest_devices.json'
    records=json.loads(device_file.read_text())['devices'] if device_file.exists() else []
    record=next((r for r in records if r.get('serial')==args.serial),None)
    if record is None:
        record=dict(device_id=secrets.token_hex(16),secret=secrets.token_hex(32),serial=args.serial);records.append(record)
    record['address']=address
    device_file.write_text(json.dumps(dict(devices=records),indent=2),encoding='utf-8');os.chmod(device_file,0o600)
    enrollment=dict(version=1,device_id=record['device_id'],secret=record['secret'],hub_host=args.hub_host,port=8766,explicit_host=bool(args.allow_campus_host))
    local=runtime/'quest_enrollment.json';local.write_text(json.dumps(enrollment),encoding='utf-8');os.chmod(local,0o600)
    request=urllib.request.Request('http://127.0.0.1:8765/api/quest/reload',data=b'{}',headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
    with urllib.request.urlopen(request,timeout=5) as response:assert json.load(response)['ok']
    output=adb('install','-r',str(apk.resolve()))
    if 'Success' not in output:raise ValueError('APK installation not confirmed')
    package='org.eegmovement.workspace';directory='/sdcard/Android/data/'+package+'/files'
    adb('shell','mkdir','-p',directory);adb('push',str(local),directory+'/eeg_enrollment.json')
    activity=adb('shell','cmd','package','resolve-activity','--brief',package).strip().splitlines()[-1]
    if not activity.startswith(package+'/'):raise ValueError('Own app activity not resolved')
    adb('shell','am','force-stop',package);launch=adb('shell','am','start','-W','-n',activity)
    if 'Error' in launch:raise ValueError('App start not confirmed')
    status='waiting_for_headset_system_confirmation' if 'LaunchCheck' in launch else 'installed_and_started_not_yet_runtime_verified'
    report=dict(status=status,serial=args.serial,device_ip=address,hub_ip=args.hub_host,build_id=build['build_id'],apk_sha256=sha,activity=activity,transport='Wi-Fi LAN; USB only for install/provision',key_values_logged=False)
    (ROOT/'outputs/quest_deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
