"""Verify current-device feature integration through own-process logs and local APIs."""
import datetime
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from quest_process_log import collect

ROOT=Path(__file__).resolve().parents[1]
ADB='G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb.exe'
SERIAL='340YC10G8W0CQ3'


def main():
    def adb(*args):return subprocess.check_output([ADB,'-s',SERIAL,*args],text=True,encoding='utf-8',errors='replace',timeout=20)
    def read(path):
        with urllib.request.urlopen('http://127.0.0.1:8765'+path,timeout=10) as response:return response.read()
    build=json.loads(Path('G:/Unity/android-build-status.json').read_text(encoding='utf-8-sig'))
    pid=adb('shell','pidof','org.eegmovement.workspace').strip()
    if not pid.isdecimal():raise ValueError('Running own app process required')
    log=collect(adb,ROOT,pid,build)
    page=read('/vr-preview').decode();script=read('/quest.js')
    state=json.loads(read('/api/status'));clients=state['vr']['clients']
    origin_events=[l for l in log.splitlines() if 'EEG_SCENE_RECENTER' in l]
    origin_applied=[l for l in origin_events if 'applied' in l]
    origin_ok=all('authority_unchanged=True' in l for l in origin_applied) and not any('rejected' in l for l in origin_events)
    checks=dict(current_build_online=any(p['online'] and p['telemetry'].get('build_id')==build['build_id'] for p in clients),
                native_humanoid_initialized=any('EEG_AVATAR_READY' in l and 'humanoid=True' in l and 'root_motion=false' in l for l in log.splitlines()),
                native_animation_idle_observed='EEG_AVATAR_MOTION idle' in log,
                signed_headset_status_received='EEG_STATUS_LINK signed=true' in log,
                preview_page_available='questFullscreen' in page and 'questImage' in page,
                served_preview_code_current=hashlib.sha256(script).digest()==hashlib.sha256((ROOT/'hub/web/quest.js').read_bytes()).digest(),
                fresh_application_mirror=state['quest']['mirror_fresh'],
                no_native_reference_exception=not any(s in log for s in ['NullReferenceException','MissingReferenceException','TypeLoadException','DllNotFoundException']),
                reported_origin_updates_complete=origin_ok and (not origin_events or bool(origin_applied) and 'applied' in origin_events[-1]))
    report=dict(status='pass' if all(checks.values()) else 'needs_attention',checked_at=datetime.datetime.now().astimezone().isoformat(),
                build_id=build['build_id'],apk_sha256=build['output_sha256'],pid=int(pid),checks=checks,
                process_identity=json.loads((ROOT/'outputs/quest_process_log_cache.json').read_text(encoding='utf-8'))['identity'],
                observed_settings_summon='EEG_PANEL summoned' in log,
                observed_monitor_open='EEG_STATUS_OVERLAY visible=True' in log,
                observed_settings_A_show='EEG_SETTINGS_BUTTON key=A visible=True' in log,
                observed_settings_A_hide='EEG_SETTINGS_BUTTON key=A visible=False' in log,
                observed_monitor_hold_open=any('EEG_STATUS_OVERLAY visible=True' in l and 'interaction=hold_B' in l for l in log.splitlines()),
                observed_monitor_hold_close=any('EEG_STATUS_OVERLAY visible=False' in l and 'interaction=hold_B' in l for l in log.splitlines()),
                observed_system_recenter=any('EEG_SCENE_RECENTER applied' in l and 'authority_unchanged=True' in l for l in log.splitlines()),
                user_ergonomic_acceptance='pending',
                scope='Current Quest process, native humanoid initialization/idle, authenticated live status and PC preview integration. Does not prove button feel, on-device moving gait acceptance, EEG intention accuracy or formal training readiness.')
    (ROOT/'outputs/quest_feature_runtime.log').write_text(log,encoding='utf-8')
    (ROOT/'outputs/quest_feature_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    if report['status']!='pass':raise SystemExit(1)


if __name__=='__main__':main()
