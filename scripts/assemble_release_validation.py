"""Assemble final evidence only from successful, same-build executable checks."""
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hub.preparation import unity_sources
from scripts.package_release import digest


def read(name):
    return json.loads((ROOT / 'outputs' / name).read_text(encoding='utf-8-sig'))


def require(condition, message):
    if not condition:
        raise ValueError(message)

def assemble():
    build = json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig'))
    require(build.get('success') is True and build.get('source_snapshot_unchanged') is True, 'Final Windows build must pass')
    identity = build['build_id']
    reports = {name: read(name + '.json') for name in [
        'unity_udp_validation', 'execution_e2e', 'execution_soak_validation',
        'unity_frame_stall_validation', 'unity_safety_validation', 'unity_acquisition_validation']}
    for name, report in reports.items():
        require(report.get('status') == 'pass' or report.get('success') is True, name + ' did not pass')
        require(report.get('build_id') == identity, name + ' belongs to a different build')
        require(bool(report.get('cases')) and all(c.get('passed') is True for c in report['cases']), name + ' has failed cases')
        require(datetime.datetime.fromisoformat(report['checked_at'].replace('Z', '+00:00')) >= datetime.datetime.fromisoformat(build['finished_at'].replace('Z', '+00:00')), name + ' predates build')
    def case(report, label):
        matches = [c for c in reports[report]['cases'] if c.get('label') == label]
        require(len(matches) == 1 and matches[0]['passed'] is True, label)
        return {'passed': True, 'evidence': report + '.json', 'check': label}
    cases = []
    mapping = {
        'stationary': ('execution_e2e', '真实EEG/GPU链路上的LIVE停止回执'),
        'forward': ('execution_e2e', 'TEST前进实际应用而非只发送'),
        'backward': ('execution_e2e', 'TEST后退实际应用'),
        'left': ('execution_e2e', 'TESTLEFT实际转向回执'),
        'right': ('execution_e2e', 'TESTRIGHT实际转向回执'),
        'test_expiry': ('execution_e2e', '测试租约到期自动回到LIVE停止'),
        'stop': ('execution_e2e', '操作停止回执'),
        'bad_signature': ('unity_udp_validation', '坏签名不改变有效方向'),
        'replay_ordering': ('unity_udp_validation', '重复和乱序不反转'),
        'network_loss': ('unity_udp_validation', '断包看门狗180ms租约制动'),
        'reconnect_stopped': ('execution_e2e', '采集恢复后LIVE仍保持停止'),
        'collision': ('unity_udp_validation', 'Unity碰撞后真实位移停止且目标仍前进'),
    }
    for key, (report, label) in mapping.items():
        cases.append({'id': key, **case(report, label)})
    for item in reports['unity_frame_stall_validation']['cases']:
        cases.append({'id': item['id'], 'passed': item['passed'], 'evidence': 'unity_frame_stall_validation.json'})
    cases.append({'id': 'emergency_reset', 'passed': True, 'evidence': 'unity_safety_validation.json',
                  'scope': 'Actual scene controller methods, XR bindings and applied-pose stop; explicit CLI self-check.'})
    cases.append({'id':'guided_acquisition','passed':True,'evidence':'unity_acquisition_validation.json','checks':len(reports['unity_acquisition_validation']['cases'])})
    sources = {p.relative_to(ROOT).as_posix(): digest(p) for p in unity_sources(ROOT)}
    require(sources == build['source_hashes'], 'Unity inputs changed since the final build')
    proof = {'checked_at': datetime.datetime.now().astimezone().isoformat(), 'status': 'pass',
             'build_id': identity, 'source_hashes': sources, 'cases': cases,
             'evidence_hashes': {name + '.json': digest(ROOT / 'outputs' / (name + '.json')) for name in reports},
             'metrics': {name: report.get('metrics') for name, report in reports.items() if report.get('metrics')},
             'quest_verified': False, 'wearable_artifact_robustness_verified': False,
             'intention_classifier_validated': False,
             'scope': 'Final Windows Unity Player; real Socket/A100 diagnostics plus explicit synthetic TEST, fault injection and CLI controller checks.'}
    (ROOT / 'outputs/unity_runtime_validation.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': proof['status'], 'build_id': identity, 'required_cases': len(cases)}, ensure_ascii=False))


if __name__ == '__main__':
    assemble()
