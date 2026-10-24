"""Discovery, service-query and stream refusal boundaries; synthetic SDK only."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from gnautilus_acquisition.diagnostics import describe_devices, diagnose_sdk, probe_participant
from gnautilus_acquisition.sdk import ParticipantReader, SDKError, _locate_sdk, query_service_status
from tests.test_gnautilus_sdk import FakeDriver


def group(serial='TEST-ONLY', in_use=False):
    return dict(in_use=in_use, devices=[dict(serial=serial, device_type=3)])


class DiscoveryTests(unittest.TestCase):
    def test_idle_device_remains_visible_but_is_not_a_shared_session(self):
        report = describe_devices([group()])
        self.assertEqual(report['devices'][0]['serial'], 'TEST-ONLY')
        self.assertEqual(report['code'], 'shared_session_absent')
        self.assertFalse(report['ready_for_participant'])
        self.assertFalse(report['stream_verified'])
        self.assertIn('非独占', report['message'])

    def test_missing_device_and_disconnected_selected_serial_are_distinct(self):
        self.assertEqual(describe_devices([])['code'], 'device_not_found')
        report = describe_devices([group('NEW', True)], 'OLD')
        self.assertEqual(report['code'], 'selected_device_not_found')
        self.assertEqual(report['devices'][0]['serial'], 'NEW')
        self.assertFalse(report['ready_for_participant'])

    def test_session_existence_never_claims_samples_or_shared_permissions(self):
        report = describe_devices([group(in_use=True)])
        self.assertTrue(report['ready_for_participant'])
        self.assertFalse(report['stream_verified'])
        self.assertIn('尚未验证', report['message'])

    def test_multiple_sessions_require_selection_but_idle_neighbors_do_not(self):
        groups = [group('A', True), group('B', True)]
        self.assertEqual(describe_devices(groups)['code'], 'device_selection_required')
        self.assertEqual(describe_devices(groups, 'B')['selected_serial'], 'B')
        groups[1]['in_use'] = False
        self.assertEqual(describe_devices(groups)['selected_serial'], 'A')
        self.assertFalse(describe_devices(groups, 'B')['ready_for_participant'])

    def test_multiple_device_group_is_not_opened_as_one_device(self):
        g = group(in_use=True)
        g['devices'].append(dict(serial='SECOND', device_type=3))
        self.assertEqual(describe_devices([g])['code'], 'unsupported_device_group')

    def test_reader_refuses_missing_or_idle_device_without_connect(self):
        for groups, serial, code in [([], None, 'device_not_found'),
                                    ([group()], None, 'shared_session_absent'),
                                    ([group(in_use=True)], 'OLD', 'selected_device_not_found')]:
            driver = FakeDriver()
            driver.groups = groups
            reader = ParticipantReader('unused', serial, driver=driver)
            with self.assertRaises(SDKError) as caught:
                reader.open()
            self.assertEqual(caught.exception.code, code)
            self.assertFalse(any(isinstance(c, tuple) and c[0] == 'connect' for c in driver.calls))

    def test_sdk_path_picker_accepts_root_c_x64_and_quotes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'C/x64').mkdir(parents=True)
            for name in ['GDSClientAPI.h', 'GDSClientAPI_gNautilus.h']:
                (root/'C'/name).write_text('test fixture')
            (root/'C/x64/GDSClientAPI.dll').write_bytes(b'not a vendor library')
            for chosen in [str(root), str(root/'C'), str(root/'C/x64'), '"'+str(root)+'"']:
                self.assertEqual(_locate_sdk(chosen)[0], root/'C')
            with self.assertRaises(SDKError) as caught:
                _locate_sdk(root/'missing')
            self.assertEqual(caught.exception.code, 'sdk_directory_missing')


class ServiceTests(unittest.TestCase):
    def api(self, *, state=4, service=True, query=True):
        from types import SimpleNamespace
        from unittest.mock import Mock
        def status(handle, pointer):
            pointer._obj.state = state
            return query
        return SimpleNamespace(OpenSCManagerW=Mock(return_value=11),
                               OpenServiceW=Mock(return_value=22 if service else 0),
                               QueryServiceStatus=Mock(side_effect=status),
                               CloseServiceHandle=Mock(return_value=True))

    def test_service_query_does_not_need_pywin32_and_closes_both_handles(self):
        api = self.api()
        with patch('gnautilus_acquisition.sdk.os.name', 'nt'), patch('ctypes.WinDLL', return_value=api, create=True):
            self.assertEqual(query_service_status()['status'], 'running')
        self.assertEqual([c.args[0] for c in api.CloseServiceHandle.call_args_list], [22, 11])
        api.OpenServiceW.assert_called_once_with(11, 'GDS', 4)

    def test_missing_service_is_distinct_from_query_permission_failure(self):
        for error, code in [(1060, 'service_not_installed'), (5, 'service_query_failed')]:
            api = self.api(service=False)
            with patch('gnautilus_acquisition.sdk.os.name', 'nt'), patch('ctypes.WinDLL', return_value=api, create=True), patch('ctypes.get_last_error', return_value=error, create=True):
                with self.assertRaises(SDKError) as caught:
                    query_service_status()
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.native_code, error)
            api.CloseServiceHandle.assert_called_once_with(11)

    def test_failed_status_query_closes_handles_and_does_not_claim_stopped(self):
        api = self.api(query=False)
        with patch('gnautilus_acquisition.sdk.os.name', 'nt'), patch('ctypes.WinDLL', return_value=api, create=True), patch('ctypes.get_last_error', return_value=5, create=True):
            with self.assertRaises(SDKError) as caught:
                query_service_status()
        self.assertEqual(caught.exception.code, 'service_query_failed')
        self.assertEqual(api.CloseServiceHandle.call_count, 2)


class ProbeWorker:
    def __init__(self, settings):
        self.settings = settings
        self.alive = True
        self.forced_termination = False
        self.messages = list(settings['messages'])
        self.stopped = False
    def start(self):return self
    def receive(self, timeout):
        if self.messages:return self.messages.pop(0)
        time.sleep(min(.01, timeout))
    def stop(self):
        self.stopped = True
        self.forced_termination = self.settings.get('forced', False)
        return self.settings.get('pending', [])


class ProbeTests(unittest.TestCase):
    def descriptor(self):
        return dict(kind='descriptor', descriptor=dict(sampling_rate=512, channels=[dict(kind='EEG')]))
    def probe(self, messages, **settings):
        self.worker = ProbeWorker(dict(messages=messages, **settings))
        return probe_participant({}, seconds=.1, startup_timeout=.1, worker_factory=lambda _: self.worker)

    def test_discovery_defaults_to_no_streaming_and_preserves_running_service(self):
        with patch('gnautilus_acquisition.sdk._locate_sdk', return_value=(Path('headers'), Path('dll'))), patch('gnautilus_acquisition.sdk.query_service_status', return_value=dict(state=4, status='running')), patch('gnautilus_acquisition.worker.discover_devices', return_value=[group()]), patch('gnautilus_acquisition.diagnostics.probe_participant') as probe:
            result = diagnose_sdk('unused', probe_stream=True)
        self.assertEqual(result['code'], 'shared_session_absent')
        self.assertFalse(result['ok'])
        self.assertFalse(result['hardware_verified'])
        probe.assert_not_called()

    def test_descriptor_alone_never_counts_as_a_verified_stream(self):
        result = self.probe([self.descriptor()])
        self.assertFalse(result['stream_verified'])
        self.assertEqual(result['samples'], 0)
        self.assertTrue(self.worker.stopped)

    def test_samples_are_counted_without_exposing_raw_eeg(self):
        result = self.probe([self.descriptor(), dict(kind='block', samples=np.zeros((4, 1)))])
        self.assertTrue(result['stream_verified'])
        self.assertEqual(result['samples'], 4)
        self.assertEqual(result['eeg_channels'], 1)
        self.assertNotIn('descriptor', result)
        self.assertTrue(self.worker.stopped)

    def test_exclusive_or_stopped_session_preserves_native_refusal(self):
        for code, native in [('access_denied', 5), ('acquisition_not_running', 13)]:
            result = self.probe([dict(kind='error', code=code, message='synthetic refusal', native_code=native)])
            self.assertFalse(result['stream_verified'])
            self.assertEqual(result['code'], code)
            self.assertEqual(result['native_code'], native)
            self.assertTrue(self.worker.stopped)

    def test_session_change_after_samples_vetoes_success(self):
        result = self.probe([self.descriptor(), dict(kind='block', samples=np.zeros((4, 1))),
                             dict(kind='error', code='configuration_changed', message='changed')])
        self.assertFalse(result['stream_verified'])
        self.assertEqual(result['samples'], 4)
        self.assertEqual(result['code'], 'configuration_changed')

    def test_forced_shutdown_or_close_failure_vetoes_stream_success(self):
        for settings in [dict(forced=True), dict(pending=[dict(kind='state', state='close_errors', errors=['failed'])])]:
            result = self.probe([self.descriptor(), dict(kind='block', samples=np.zeros((4, 1)))], **settings)
            self.assertFalse(result['stream_verified'])

    def test_stopped_service_is_not_used_to_discover_devices(self):
        with patch('gnautilus_acquisition.sdk._locate_sdk', return_value=(Path('headers'), Path('dll'))), patch('gnautilus_acquisition.sdk.query_service_status', return_value=dict(state=1, status='stopped')), patch('gnautilus_acquisition.worker.discover_devices') as discover:
            result = diagnose_sdk('unused')
        self.assertEqual(result['code'], 'service_not_running')
        discover.assert_not_called()


class CliTests(unittest.TestCase):
    def test_missing_sdk_emits_report_and_nonzero_exit_without_hardware(self):
        import json
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp)/'diagnosis.json'
            completed = subprocess.run([sys.executable, '-m', 'gnautilus_acquisition', '--diagnose',
                                        '--sdk-dir', str(Path(temp)/'no-sdk'), '--report-path', str(report)],
                                       cwd=root, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 2)
            result = json.loads(report.read_text('utf-8'))
            self.assertEqual(result['code'], 'sdk_directory_missing')
            self.assertFalse(result['hardware_verified'])
            self.assertFalse(result['stream_verified'])

    def test_probe_requires_diagnose_and_bounded_duration(self):
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        for flags in [['--probe-stream'], ['--diagnose', '--probe-seconds', '100'],
                      ['--diagnose', '--probe-seconds', 'nan']]:
            completed = subprocess.run([sys.executable, '-m', 'gnautilus_acquisition', *flags],
                                       cwd=root, capture_output=True, timeout=10)
            self.assertEqual(completed.returncode, 2)
            self.assertIn(b'usage:', completed.stderr)


if __name__ == '__main__':unittest.main()
