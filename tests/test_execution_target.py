import unittest
from scripts import execution_target


class ExecutionTargetTests(unittest.TestCase):
    def test_forward_displacement_uses_avatar_heading_at_all_cardinal_rotations(self):
        for yaw,x,z in [(0,0,1),(90,1,0),(180,0,-1),(270,-1,0)]:
            before={'x':0,'z':0,'yaw':yaw}
            self.assertAlmostEqual(execution_target.projected_displacement(before,{'x':x,'z':z}),1)
            self.assertAlmostEqual(execution_target.projected_displacement(before,{'x':-x,'z':-z}),-1)
    def state(self, *peers):
        return {'vr': {'clients': list(peers)}}

    def peer(self, identity='one', build='current'):
        return {'role': 'unity', 'online': True, 'receiver_id': identity,
                'telemetry_age_ms': 10, 'telemetry': {'build_id': build}}

    def test_missing_or_wrong_build_cannot_be_accepted(self):
        for state in [self.state(), self.state(self.peer(build='old'))]:
            with self.assertRaises(ValueError):
                execution_target.ExecutionTarget(state, 'unity', 'current')

    def test_multiple_unity_receivers_cannot_mix_observations(self):
        with self.assertRaises(ValueError):
            execution_target.ExecutionTarget(self.state(self.peer(), self.peer('two')), 'unity', 'current')

    def test_receiver_restart_cannot_silently_replace_selected_target(self):
        target = execution_target.ExecutionTarget(self.state(self.peer()), 'unity', 'current')
        with self.assertRaises(ValueError):
            target.select(self.state(self.peer('new-instance')))

    def test_build_or_staleness_change_is_rejected(self):
        target = execution_target.ExecutionTarget(self.state(self.peer()), 'unity', 'current')
        self.assertEqual(target.select(self.state(self.peer()))['receiver_id'], 'one')
        for changes in [{'telemetry': {'build_id': 'old'}}, {'telemetry_age_ms': 350}, {'online': False}]:
            peer = self.peer(); peer.update(changes)
            with self.assertRaises(ValueError):
                target.select(self.state(peer))
