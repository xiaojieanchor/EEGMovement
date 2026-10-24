"""Read-only live check of the restored Hub and the attached real Quest."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eeg_relay.update import fetch
from scripts.build_public_release import QUEST_VERSION, QUEST_CODE


def main():
    evidence = ROOT / 'docs/gnautilus/relay_20261023_followup'
    def read(path):
        with urllib.request.urlopen('http://127.0.0.1:8765' + path, timeout=5) as response:
            return response.read()

    state = json.loads(read('/api/status'))
    assert state['project'] == 'EEGMovement'
    pages = {}
    for path in ('/', '/gnautilus'):
        page = read(path).decode('utf-8')
        pages[path] = 'studio-hero' in page and 'workspace-nav' in page and 'data-device-transition="gnautilus"' in page and '<aside class="device-transition"' not in page
    baseline = json.loads((ROOT / 'outputs/gnautilus_release/workspace-restoration-20261023.json').read_text())
    styles = {name: hashlib.sha256(read('/' + name)).hexdigest() == record['baseline_sha256'] for name, record in baseline['styles'].items()}
    config = json.loads((ROOT / 'gnautilus_acquisition/relay_public.json').read_text())
    raw, path = fetch(config, 'health')
    cloud = json.loads(raw)
    adb = 'G:/Unity/Editors/6000.3.26f1/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb.exe'
    serial = '340YC10G8W0CQ3'
    package = subprocess.check_output([adb, '-s', serial, 'shell', 'dumpsys', 'package', 'org.eegmovement.workspace'], text=True, encoding='utf-8', errors='replace', timeout=15)
    name = re.search(r'versionName=([^\s]+)', package).group(1)
    code = int(re.search(r'versionCode=(\d+)', package).group(1))
    installed_path = subprocess.check_output([adb, '-s', serial, 'shell', 'pm', 'path', 'org.eegmovement.workspace'], text=True, timeout=15).strip().removeprefix('package:')
    installed_digest = subprocess.check_output([adb, '-s', serial, 'shell', 'sha256sum', installed_path], text=True, timeout=15).split()[0]
    expected_digest = hashlib.sha256((ROOT / f'outputs/relay_20261023/release/EEGMovement-Quest-{QUEST_VERSION}.apk').read_bytes()).hexdigest()
    peers = [p for p in state['vr']['clients'] if p['online'] and p.get('role') == 'unity']
    telemetry = peers[0]['telemetry'] if len(peers) == 1 else {}
    checks = dict(original_pages=all(pages.values()), original_css=all(styles.values()),
                  cloud_healthy=cloud.get('status') == 'ok', no_private_proxy=cloud.get('private_proxy') is False,
                  hub_relay_online=state['public_relay']['connected'],
                  single_quest_online=len(peers) == 1,
                  current_apk_installed=(name, code) == (QUEST_VERSION, QUEST_CODE),
                  installed_apk_matches_published=installed_digest == expected_digest,
                  current_apk_reporting=telemetry.get('app_version') == QUEST_VERSION,
                  invalid_wire_zero=telemetry.get('invalid_wire') == 0,
                  motion_stopped=telemetry.get('actual_speed') == 0 and telemetry.get('actual_yaw_rate') == 0,
                  mirror_fresh=state['quest']['mirror_fresh'])
    result = dict(checked_at=datetime.datetime.now().astimezone().isoformat(),
                  status='pass' if all(checks.values()) else 'needs_attention', checks=checks,
                  pages=pages, styles=styles, installed=dict(version=name, code=code),
                  installed_apk_sha256=installed_digest,
                  command_roundtrip_ms=telemetry.get('command_roundtrip_ms'),
                  preview={k:state['quest'][k] for k in ('frame_seq','frame_bytes','frame_age_ms','mirror_fresh')},
                  cloud_network_path=path, real_quest=True, real_eeg=False,
                  new_model_configured=False, training_started=False)
    (evidence / 'final-live-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    if result['status'] != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
