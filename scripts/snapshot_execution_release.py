"""Write a bounded, secret-free release snapshot for the local execution lab."""
import datetime
import hashlib
import json
import math
import urllib.request
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hub.preparation import PreparationMonitor,unity_sources


def sha(path):
    with path.open('rb') as stream:
        value=hashlib.sha256()
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
        return value.hexdigest()


def require(condition, reason):
    if not condition:raise ValueError(reason)


def collect_snapshot(state,root=ROOT,unity_root=Path('G:/Unity')):
    """Validate first; callers cannot publish partial or failed acceptance."""
    vr=state.get('vr') or {};server=state.get('server') or {};health=server.get('last_health') or {}
    source=state.get('source') or {}
    require(source.get('mode')=='live' and source.get('fresh') is True,'Fresh LIVE source required')
    require(server.get('ssh_connected') is True and server.get('worker_connected') is True
            and state.get('result_fresh') is True and health.get('ok') is True,'Fresh server/GPU return required')
    require(vr.get('test_active') is False and (state.get('recording') or {}).get('active') is False,
            'Stop TEST and recording before release snapshot')
    require((state.get('control') or {}).get('enabled') is False and vr.get('model_control_enabled') is False,
            'LIVE model output must remain locked')
    peers=vr.get('clients') or []
    online=[p for p in peers if p.get('online') is True and type(p.get('telemetry_age_ms')) in [int,float]
            and 0<=p['telemetry_age_ms']<300]
    require(any(p.get('role')=='unity' for p in online),'Fresh Unity receiver required; C# harness is insufficient')
    for peer in online:
        telemetry=peer.get('telemetry') or {}
        require(telemetry.get('state')=='STOP' and telemetry.get('mode')=='LIVE','Every online receiver must be stopped in LIVE')
        for name in ['actual_speed','actual_yaw_rate']:
            value=telemetry.get(name)
            require(type(value) in [int,float] and math.isfinite(value) and abs(value)<.001,
                    'Missing or nonzero actual motion: '+name)
    preparation=PreparationMonitor(root,unity_root,cache_seconds=0).snapshot(state)
    require(preparation['artifacts']['unity']['build_verified'],'Current full Unity source/artifact verification required')
    require(preparation['artifacts']['unity_acceptance']['verified'],'Current complete Unity fault acceptance required')
    require(any(s['id']=='unity_runtime' and s['status']=='ok' for s in preparation['stages']),
            'Fresh rendered Unity runtime must match current build')
    require(preparation['training']['software_ready'],'Current training software evidence required')
    validation=json.loads((root/'outputs/execution_validation.json').read_text(encoding='utf-8-sig'))
    require(validation.get('status')=='pass','Core execution validation must pass')
    hashes={}
    for name,expected in state['build_hashes'].items():
        path=(root/name).resolve()
        require(path.is_relative_to(root/'hub') and not path.is_relative_to(root/'hub/runtime') and path.suffix=='.py',
                'Private or external source in running hub manifest')
        hashes[name]=sha(path)
    require(hashes==state['build_hashes'],'Running hub source differs from current files')
    require({'hub/app.py','hub/vr.py','hub/preparation.py'}<=set(name.replace('\\','/') for name in hashes),
            'Running hub source manifest is incomplete')
    for name,expected in validation['source_hashes'].items():
        path=(root/name).resolve()
        require(path.is_relative_to(root.resolve()) and not path.is_relative_to(root/'hub/runtime'),
                'Private or external source in validation')
        require(sha(path)==expected,'Validated receiver source/binary changed: '+name)
    for pattern in ['hub/web/*','receiver/*.cs','scripts/*execution*.py','configs/execution_link.json',
                    'protocols/execution-v1.md','unity/README.md']:
        for path in root.glob(pattern):
            if path.is_file():hashes[str(path.relative_to(root))]=sha(path)
    hashes.update(validation['source_hashes'])
    for path in unity_sources(root):hashes[path.relative_to(root).as_posix()]=sha(path)
    hashes={name.replace('\\','/'):value for name,value in hashes.items()}
    clients=[{key:peer.get(key) for key in ['role','online','telemetry_age_ms','applied_seq','sent_seq','telemetry']} for peer in peers]
    result=dict(checked_at=datetime.datetime.now().astimezone().isoformat(),version=state['version'],
        validated_cases=validation['passed'],running_hub_source_matches=True,validated_core_and_binary_match=True,
        source={key:source[key] for key in ['mode','connected','fresh','sfreq_measured','loss_pct']},
        quality=state['quality']['valid'],server={key:health.get(key) for key in ['pid','torch','device','weights_loaded','control_enabled','gpus']},
        recording_active=False,test_active=False,clients=clients,unity_engine_compiled=True,
        unity_build=preparation['artifacts']['unity'],unity_runtime_verified=True,
        preparation=dict(software_ready=True,formal_training_ready=preparation['formal_training_ready'],live_control_enabled=False),
        quest_verified=False,wearable_artifact_robustness_verified=False,intention_classifier_validated=False,
        files=hashes,evidence_files={name:sha(root/'outputs'/name) for name in [
            'execution_validation.json','unity_runtime_validation.json','unity_acquisition_validation.json','receiver_udp_validation.json','execution_e2e.json',
            'signal_noise_validation.json','execution_soak_validation.json','pilot_schedule.json']})
    return result


def main():
    with urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=5) as response:
        state = json.load(response)
    result=collect_snapshot(state)
    path=ROOT/'outputs/execution_release_manifest.json'
    temporary=path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)
    print(json.dumps({key:result[key] for key in ['checked_at','version','validated_cases','unity_engine_compiled','unity_runtime_verified','quest_verified']},ensure_ascii=False))


if __name__ == '__main__':
    main()
