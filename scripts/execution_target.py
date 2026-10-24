"""Pin acceptance observations to one fresh process and its build identity."""
import math
import time


def projected_displacement(before, after):
    """Signed translation along the avatar's initial forward axis, not world Z."""
    yaw=math.radians(before['yaw'])
    return (after['x']-before['x'])*math.sin(yaw)+(after['z']-before['z'])*math.cos(yaw)


def wait_for_target(read_state, role, build_id=None, timeout=8):
    deadline=time.monotonic()+timeout
    while True:
        state=read_state()
        if any(c.get('online') and c.get('role')==role for c in state['vr']['clients']):
            return ExecutionTarget(state,role,build_id)
        if time.monotonic()>=deadline:
            raise ValueError('Requested receiver did not become online before the startup deadline')
        time.sleep(.05)


class ExecutionTarget:
    def __init__(self, state, role, build_id=None):
        candidates = [c for c in state['vr']['clients'] if c.get('role') == role and c.get('online')]
        if len(candidates) != 1:
            raise ValueError('Exactly one online receiver of the requested role is required')
        self.receiver_id = candidates[0]['receiver_id']
        self.role, self.build_id = role, build_id
        if role == 'unity' and not build_id:
            raise ValueError('Unity validation requires an explicit final build identity')
        self.select(state)

    def select(self, state):
        candidates = [c for c in state['vr']['clients'] if c.get('receiver_id') == self.receiver_id]
        if len(candidates) != 1:
            raise ValueError('Selected receiver disappeared or restarted; validation cannot mix processes')
        peer = candidates[0]
        age = peer.get('telemetry_age_ms')
        if (not peer.get('online') or peer.get('role') != self.role or type(age) not in [int, float]
                or not math.isfinite(age) or not 0 <= age < 300):
            raise ValueError('Selected receiver is stale or changed role')
        if self.build_id and peer.get('telemetry', {}).get('build_id') != self.build_id:
            raise ValueError('Selected receiver build differs from the validated build')
        return peer
