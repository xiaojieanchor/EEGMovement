"""Run the owned Player's explicit validation entry points, without desktop input."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.package_release import digest


def run(mode):
    build=json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig'))
    if build.get('success') is not True:raise ValueError('A successful current build is required')
    binary=Path(build['output'])
    for name,expected in build['artifact_hashes'].items():
        if digest(binary.parent/name)!=expected:raise ValueError('Player artifact changed: '+name)
    pairing=ROOT/'hub/runtime/vr_pairing.json'
    if not pairing.is_file():raise ValueError('Hub pairing file unavailable')
    output=ROOT/'outputs'/('unity_safety_validation.json' if mode=='safety' else 'unity_acquisition_player.json')
    output.write_text(json.dumps({'success':False,'state':'validation_started','build_id':build['build_id']}),encoding='utf-8')
    command=[str(binary),'--eeg-config',str(pairing),'--eeg-port','19772',
             '--eeg-validate-'+mode,str(output),'-screen-fullscreen','0',
             '-logFile','G:/Unity/Logs/EEGMovement-'+mode+'.log']
    if mode=='safety':command.append('--eeg-allow-test')
    process=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        code=process.wait(timeout=35 if mode=='safety' else 110)
    except subprocess.TimeoutExpired:
        process.terminate();process.wait(timeout=8)
        raise RuntimeError('Explicit validation timed out; only the owned test process was terminated')
    result=json.loads(output.read_text(encoding='utf-8-sig'))
    print(json.dumps({'exit_code':code,**result},ensure_ascii=False,indent=2))
    if code or result.get('success') is not True or result.get('build_id')!=build['build_id']:
        raise RuntimeError('Unity '+mode+' validation did not pass')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['safety','acquisition']);args=parser.parse_args();run(args.mode)
