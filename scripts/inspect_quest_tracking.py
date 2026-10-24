"""Read the app's own XR logs and signed receiver state; never controls system UI."""
import argparse
import datetime
import json
import re
import subprocess
import urllib.request
from pathlib import Path
from quest_process_log import collect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', default='340YC10G8W0CQ3')
    parser.add_argument('--adb', default='G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb.exe')
    args = parser.parse_args()
    def adb(*arguments):
        return subprocess.check_output([args.adb, '-s', args.serial, *arguments], text=True,
                                       encoding='utf-8', errors='replace', timeout=15)
    pid = adb('shell', 'pidof', 'org.eegmovement.workspace').strip()
    if not re.fullmatch(r'\d+', pid):
        raise ValueError('Exactly one running app process required')
    build = json.loads(Path('G:/Unity/android-build-status.json').read_text(encoding='utf-8-sig'))
    log = collect(adb, ROOT, pid, build)
    with urllib.request.urlopen('http://127.0.0.1:8765/api/vr/status', timeout=5) as response:
        clients = json.load(response)['vr']['clients']
    peers = [p for p in clients if p.get('telemetry', {}).get('build_id') == build.get('build_id')]
    if not build.get('success') or not peers:
        raise ValueError('Current APK receiver identity has not been observed')
    events = [line for line in log.splitlines() if any(key in line for key in
              ['EEG_XR_FOCUS', 'EEG_XR_MODE', 'EEG_XR_HEIGHT', 'EEG_PANEL ', 'EEG_PANEL_DRAG', 'EEG_SCENE_RECENTER', 'EEG_SETTINGS_BUTTON', 'EEG_STATUS_OVERLAY'])]
    poses = [line for line in log.splitlines() if 'EEG_XR_POSE ' in line]
    lost = False
    cycle = False
    xr_started = False
    desktop_fallback = False
    for line in log.splitlines():
        if 'EEG_XR_MODE visual_xr=True' in line: xr_started = True
        if xr_started and 'EEG_XR_MODE visual_xr=False' in line: desktop_fallback = True
        if 'EEG_XR_FOCUS source=display focused=False' in line: lost = True
        if lost and 'EEG_XR_FOCUS source=display focused=True' in line: cycle = True
    checks = dict(pose_samples_observed=bool(poses), xr_mode_observed=xr_started,
                  no_desktop_fallback=not desktop_fallback,
                  desktop_camera_off=bool(poses) and all('desktop_camera=False' in p for p in poses),
                  height_calibration_observed=any('EEG_XR_HEIGHT calibrated_eye=1.65' in e for e in events))
    report = dict(checked_at=datetime.datetime.now().astimezone().isoformat(), build_id=build['build_id'],
                  pid=int(pid), checks=checks, software_tracking_checks_pass=all(checks.values()),
                  display_focus_cycle_observed=cycle, pose_samples=len(poses),
                  unfocused_pose_samples=sum('focused=False ' in p for p in poses),
                  latest_pose=poses[-1] if poses else None, events=events,
                  user_ergonomic_acceptance='pending',
                  scope='Own-process pose/focus/camera evidence. A display-focus cycle alone cannot prove comfortable world tracking or physical display latency. Headset and PC clocks are not synchronized.')
    (ROOT/'outputs/quest_view_runtime.log').write_text(log, encoding='utf-8')
    (ROOT/'outputs/quest_tracking_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['events','latest_pose']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
