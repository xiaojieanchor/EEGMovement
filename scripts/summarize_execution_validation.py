"""Run final local checks and aggregate same-build Unity and transport evidence."""
import datetime,hashlib,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def run(command,output):
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    text=result.stdout+result.stderr
    (ROOT/'outputs'/output).write_text(text,encoding='utf-8')
    if result.returncode:raise RuntimeError('Check failed: '+output)
    return text

def main():
    cases=[];breakdown={}
    text=run([sys.executable,'-m','unittest','discover','-s','tests','-v'],'python_execution_tests.txt')
    match=re.search(r'Ran (\d+) tests',text);assert match,'Missing Python test count'
    breakdown['python']=int(match.group(1))
    core=json.loads(run([str(ROOT/'receiver/bin/CoreTests.exe')],'csharp_core_tests.json'));breakdown['csharp_core']=core['passed']
    for name,exe in [('workspace','WorkspaceChecks'),('build_identity','IdentityChecks')]:
        text=run(['G:/EEGMovement/Validation/'+exe+'.exe'],name+'_checks.txt');match=re.search(r'PASS: (\d+)',text);assert match
        breakdown[name]=int(match.group(1))
    frontend=run(['node','--test',*[str(p) for p in (ROOT/'tests/frontend').glob('*.test.cjs')]],'frontend_execution_tests.txt')
    match=re.search(r'# pass (\d+)',frontend);assert match and '# fail 0' in frontend
    breakdown['frontend']=int(match.group(1))
    cases.extend(dict(label=key+' executable checks ('+str(count)+')',passed=True) for key,count in breakdown.items())
    for name,key in [('receiver_udp_validation','shared_core_udp'),('unity_udp_validation','unity_udp'),('execution_e2e','real_chain'),('signal_noise_validation','synthetic_qc'),('execution_soak_validation','stress_soak'),('unity_safety_validation','unity_controller'),('unity_frame_stall_validation','unity_stall'),('unity_acquisition_validation','unity_acquisition')]:
        report=json.loads((ROOT/'outputs'/(name+'.json')).read_text(encoding='utf-8-sig'))
        assert report.get('status')=='pass' or report.get('success') is True,name
        assert report['cases'] and all(c['passed'] is True for c in report['cases']),name
        breakdown[key]=len(report['cases']);cases.extend(dict(label=c.get('label',c.get('name',c.get('id'))),passed=c['passed']) for c in report['cases'])
    acceptance=json.loads((ROOT/'outputs/unity_runtime_validation.json').read_text(encoding='utf-8'));assert acceptance['status']=='pass'
    paths=[*sorted((ROOT/'unity/EEGMovement/Assets/Scripts/Core').glob('*.cs')),ROOT/'receiver/bin/EEGReceiver.exe',*list((ROOT/'scripts').glob('*.py')),*[p for p in (ROOT/'hub/web').glob('*') if p.is_file()]]
    result=dict(status='pass',checked_at=datetime.datetime.now().astimezone().isoformat(),passed=sum(breakdown.values()),breakdown=breakdown,cases=cases,build_id=acceptance['build_id'],source_hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},unity_engine_compiled=True,quest_verified=False,wearable_artifact_robustness_verified=False,intention_classifier_validated=False,limitations=['Motion uses explicit synthetic TEST commands; LIVE intention control stays disabled.','No Quest headset/display/controller or standalone LAN acceptance.','Synthetic QC and transport faults do not prove physiological artifact rejection.','ACK timing excludes evidence window, Bluetooth upstream and display scanout.'])
    (ROOT/'outputs/execution_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ['cases','source_hashes']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
