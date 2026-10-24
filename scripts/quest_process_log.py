"""Bounded log history tied to Android boot and process start, not PID alone."""
import datetime
import json
import os
import tempfile
from pathlib import Path


def collect(adb, root, pid, build):
    stat=adb('shell','cat','/proc/'+pid+'/stat').rsplit(') ',1)[1].split()
    start=int(stat[19])  # field 22; the command name can contain spaces.
    ticks=int(adb('shell','getconf','CLK_TCK').strip())
    uptime=float(adb('shell','cat','/proc/uptime').split()[0])
    boot=adb('shell','cat','/proc/sys/kernel/random/boot_id').strip()
    identity=dict(pid=int(pid),start_ticks=start,boot=boot,build_id=build['build_id'])
    current=adb('logcat','-d','--pid='+pid,'-s','Unity')
    cache=Path(root)/'outputs/quest_process_log_cache.json'
    history=[]
    if cache.is_file() and cache.stat().st_size<8*1024*1024:
        try:previous=json.loads(cache.read_text(encoding='utf-8'))
        except (ValueError,OSError):previous={}
        if previous.get('identity')==identity:history=previous.get('lines',[])
    # Recover an earlier verified capture only when the current process was already
    # alive at that capture; a restarted app with a reused PID cannot inherit it.
    proof_dir=Path('G:/EEGMovement/Releases/Quest-Character-Monitor-20261015')/build['build_id']/'Evidence'
    proof_path=proof_dir/'quest_feature_validation.json'
    log_path=proof_dir/'quest_feature_runtime.log'
    if not history and proof_path.is_file() and log_path.is_file() and log_path.stat().st_size<8*1024*1024:
        proof=json.loads(proof_path.read_text(encoding='utf-8'))
        age=datetime.datetime.now().astimezone().timestamp()-datetime.datetime.fromisoformat(proof['checked_at']).timestamp()
        if (proof.get('pid')==int(pid) and proof.get('build_id')==build['build_id']
                and proof.get('apk_sha256')==build['output_sha256'] and 0<=age<900
                and uptime-start/ticks>age+1):
            history=log_path.read_text(encoding='utf-8').splitlines()
    lines=list(dict.fromkeys(history+current.splitlines()))
    if len(lines)>20000:lines=lines[:3000]+lines[-17000:]
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=cache.parent,suffix='.tmp',delete=False) as stream:
        json.dump(dict(identity=identity,lines=lines),stream,ensure_ascii=False)
        temporary=stream.name
    os.replace(temporary,cache)
    return '\n'.join(lines)+'\n'
