"""Evidence gate for the bounded v1.0.1 pairing repair; never reads the SDK."""
from pathlib import Path
import datetime,hashlib,json,re,urllib.request,zipfile
ROOT=Path(__file__).resolve().parents[1];DOC=ROOT/'docs/gnautilus/integration_20261022';VERIFY=DOC/'verification';FIX=DOC/'connection_fix_v101'

def read(path):return json.loads(Path(path).read_text('utf-8-sig'))
def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def main():
    build=read(VERIFY/'acquisition-build.json');packages=read(VERIFY/'packages.json');extracted=read(VERIFY/'portable-extracted.json')
    assert build['success'] and build['frozen_qt_offscreen_render'] and build['frozen_isolated_worker_error_path']
    assert all(sha(ROOT/name)==digest for name,digest in build['source_sha256'].items())
    assert sha(build['exe'])==build['exe_sha256']
    assert extracted['zip_sha256']==packages['portable']['sha256'] and extracted['frozen_local_hub_authentication']
    assert extracted['all_extracted_file_hashes_match'] and extracted['clean_path_without_python']
    for key in ['portable','source']:
        info=packages[key];assert sha(info['path'])==info['sha256']
        with zipfile.ZipFile(info['path']) as z:assert z.testzip() is None
    paired=read('C:/Users/19410/Desktop/gNautilus-pairing-192.168.123.173.json')
    with zipfile.ZipFile(packages['portable']['path']) as z:
        info=read(Path(build['exe']).parent/'build-info.json');assert info['version']=='1.0.1'
        for entry in z.infolist():
            if Path(entry.filename).suffix.lower() in ['.json','.py','.md','.txt']:
                assert paired['secret'].encode() not in z.read(entry),'Real pairing credential must not be bundled'
    original=Path('C:/Users/19410/Desktop/gNautilus-events.json');assert sha(original)==sha(FIX/'reported-events.json')
    ui=read(FIX/'ui-connection-check.json');local=read(FIX/'local-hub-check.json');frozen=read(FIX/'frozen-hub-check.json')
    assert all(ui[key] for key in ['missing_pairing_rejected_before_tcp','authenticated_hub_probe','selected_path_persisted','credentials_absent_from_logs'])
    assert local['local_wlan_tcp_tls_identity_status'] and frozen['version']=='1.0.1' and 'hub' in frozen
    tests=(FIX/'python-tests.log').read_text('utf-8-sig');count=int(re.search(r'Ran (\d+) tests',tests)[1]);assert tests.rstrip().endswith('OK') and count==216
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8765/api/status',timeout=3) as response:hub=json.load(response)
    assert hub['project']=='EEGMovement' and not hub['recording']['active']
    result=dict(schema='gnautilus.connection_fix.v1',version='1.0.1',verified_at=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
        original_log_sha256=sha(original),root_cause='empty/unselected pairing path became current directory before TCP; hub initially not listening',
        tests_passed=count,independent_review_tests=19,build=build,packages=packages,portable_extracted=extracted,
        local_hub=dict(endpoint=local['endpoint'],running=True,auth_and_status_passed=True,recording_started=False),
        pairing_file='C:/Users/19410/Desktop/gNautilus-pairing-192.168.123.173.json',pairing_secret_bundled=False,
        remote_pc_verified=False,real_eeg_accessed=False,firewall_changed=False,vr_changed=False,training_started=False,
        material_audit='Reuses prior unchanged-material audit; this patch touched client/UI/package/docs only.')
    (FIX/'fix-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    prior=read(FIX/'baseline/docs/gnautilus/integration_20261022/verification/delivery.json')
    prior.update(verified_at=result['verified_at'],acquisition=build,packages=packages,latest_patch=dict(version='1.0.1',report=str(FIX/'fix-report.json'),baseline=str(FIX/'baseline'),remote_pc_verified=False))
    prior['tests'].update(python_passed=count,python_log=str(FIX/'python-tests.log'),portable_extracted=extracted,qt_ui=read(VERIFY/'ui/review.json'))
    prior['materials']['audit_reused_for_patch']=True
    (VERIFY/'delivery.json').write_text(json.dumps(prior,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(version='1.0.1',tests_passed=count,portable=packages['portable']['path'],portable_sha256=packages['portable']['sha256'],local_hub=local['endpoint'],remote_pc_verified=False),ensure_ascii=False))

if __name__=='__main__':main()
