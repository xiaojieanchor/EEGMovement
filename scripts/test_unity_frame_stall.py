"""Bounded fault injection into our own Unity process, without desktop interaction."""
import argparse
import ctypes
import datetime
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_execution_e2e import get, post
from scripts.execution_target import ExecutionTarget



def run(pid, expected):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    handle = kernel.OpenProcess(0x0800 | 0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    suspended = False
    ntdll = ctypes.WinDLL('ntdll')
    for name in ['NtSuspendProcess', 'NtResumeProcess']:
        getattr(ntdll, name).argtypes = [ctypes.c_void_p]
        getattr(ntdll, name).restype = ctypes.c_long
    cases = []
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if Path(buffer.value).resolve() != Path(expected).resolve():
            raise RuntimeError('PID is not the explicitly named Unity binary')
        candidates = [c for c in get()['vr']['clients'] if c['online'] and c['role'] == 'unity']
        if len(candidates) != 1 or not candidates[0]['allow_test']:
            raise RuntimeError('Exactly one opted-in Unity receiver required')
        build_id = json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig'))['build_id']
        target = ExecutionTarget(get(), 'unity', build_id)
        receiver_id = target.receiver_id
        def current():
            return target.select(get())
        post('/api/vr/test', {'action': 'FORWARD', 'receiver_id': receiver_id, 'duration_s': 3})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and abs(current()['telemetry']['actual_speed']) < .05:
            time.sleep(.03)
        before = current()
        assert abs(before['telemetry']['actual_speed']) > .05, 'Moving precondition not met'
        assert ntdll.NtSuspendProcess(handle) == 0
        suspended = True
        time.sleep(.55)
        stale = get()['vr']
        expired = next(c for c in stale['clients'] if c['receiver_id'] == receiver_id)
        cases.append({'id': 'telemetry_expiry', 'passed': not expired['online'] and
                      stale['execution']['source'] != 'unity',
                      'age_ms': expired['telemetry_age_ms']})
        post('/api/vr/stop')
        assert ntdll.NtResumeProcess(handle) == 0
        suspended = False
        deadline = time.monotonic() + 3
        rows = []
        while time.monotonic() < deadline:
            state = get()
            matches = [c for c in state['vr']['clients'] if c.get('receiver_id') == receiver_id]
            if len(matches) != 1 or matches[0].get('role') != 'unity' or matches[0].get('telemetry', {}).get('build_id') != build_id:
                raise RuntimeError('Receiver identity or build changed during recovery')
            current_peer = matches[0]
            if current_peer['online'] and current_peer['telemetry']['rendered_frames'] > before['telemetry']['rendered_frames']:
                current_peer = target.select(state)
                rows.append(current_peer['telemetry'])
                if len(rows) >= 12:
                    break
            time.sleep(.03)
        # Heartbeats sample applied frames; they do not expose every resumed fixed tick.
        # Also bound total pose drift and require the monotonic-clock watchdog to fire.
        drift = max((math.hypot(r['x'] - before['telemetry']['x'], r['z'] - before['telemetry']['z']) for r in rows), default=None)
        yaw_drift = max((abs((r['yaw'] - before['telemetry']['yaw'] + 180) % 360 - 180) for r in rows), default=None)
        cases.append({'id': 'frame_stall', 'passed': bool(rows) and
                      all(abs(r['actual_speed']) < .005 and abs(r['actual_yaw_rate']) < .005 for r in rows)
                      and drift < .08 and yaw_drift < .5
                      and rows[-1]['watchdog_stops'] > before['telemetry']['watchdog_stops'],
                      'rendered_frames_advanced': bool(rows) and rows[-1]['rendered_frames'] > before['telemetry']['rendered_frames'],
                      'pose_drift_m': drift, 'yaw_drift_degrees': yaw_drift,
                      'watchdog_brake_ms': rows[-1]['watchdog_brake_ms'] if rows else None,
                      'note': 'No frame renders while suspended. Sampled resumed frames must stop, watchdog must fire, and pose drift is bounded; this does not observe each fixed tick.'})
        return {'status': 'pass' if all(c['passed'] for c in cases) else 'failed',
                'checked_at': datetime.datetime.now().astimezone().isoformat(),
                'build_id': build_id, 'cases': cases}
    finally:
        if suspended:
            ntdll.NtResumeProcess(handle)
        kernel.CloseHandle(handle)
        post('/api/vr/stop')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.pid, args.binary)
    (ROOT / 'outputs/unity_frame_stall_validation.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] != 'pass':
        raise SystemExit(1)
