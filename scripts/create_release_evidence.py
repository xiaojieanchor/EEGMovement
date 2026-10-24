"""Bind a current successful software acceptance to every source/player file."""
import datetime
import json
import sys
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hub.preparation import PreparationMonitor
from scripts.package_release import collect_source_files,digest,windows_file_hashes


def main():
    build=json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig'))
    android=json.loads((ROOT/'outputs/android_build_validation.json').read_text(encoding='utf-8'))
    with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=8) as response:state=json.load(response)
    preparation=PreparationMonitor(ROOT,cache_seconds=0).snapshot(state)
    if not preparation['software_ready']:raise ValueError('Current software readiness is not verified')
    if state['vr']['test_active'] or state['recording']['active']:raise ValueError('Stop TEST/recording before packaging')
    validation=json.loads((ROOT/'outputs/execution_validation.json').read_text(encoding='utf-8'))
    if validation.get('status')!='pass' or validation.get('build_id')!=build['build_id']:raise ValueError('Current execution validation missing')
    for name,expected in validation['source_hashes'].items():
        if digest(ROOT/name)!=expected:raise ValueError('Validated source changed: '+name)
    files=[p for p in collect_source_files(ROOT) if p.relative_to(ROOT).parts[0]!='outputs']
    proof=dict(status='pass',checked_at=datetime.datetime.now().astimezone().isoformat(),build_id=build['build_id'],
        windows_sha256=digest(Path(build['output'])),windows_files=windows_file_hashes(Path(build['output']).parent),
        source_hashes={p.relative_to(ROOT).as_posix():digest(p) for p in files},android=android,software_ready=True,
        formal_training_ready=False,quest_verified=False,
        scope='Final PC Unity full tree, guided EEG task collection and real Socket/GPU execution/fault evidence; no trained intention or headset acceptance.',
        evidence_hashes={name:digest(ROOT/'outputs'/name) for name in ['execution_validation.json','unity_runtime_validation.json',
            'unity_acquisition_validation.json','training_readiness.json','execution_release_manifest.json']})
    (ROOT/'outputs/release_validation.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'outputs/preparation_final_snapshot.json').write_text(json.dumps(preparation,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status='pass',build_id=build['build_id'],source_files=len(files),player_files=len(proof['windows_files']))))


if __name__=='__main__':main()
