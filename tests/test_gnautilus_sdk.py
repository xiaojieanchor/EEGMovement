"""Participant permissions and worker supervision, using no vendor DLL or EEG."""
import multiprocessing
import time
import unittest
from pathlib import Path

import numpy as np

from gnautilus_acquisition.sdk import CffiDriver, ParticipantReader, SDKError, make_cdef
from gnautilus_acquisition.worker import SDKWorker, discover_devices


def supplied_headers():
    """Vendor headers stay outside Git; installed SDK tests are optional on CI."""
    import os
    from gnautilus_acquisition.sdk import _locate_sdk
    roots = [os.environ.get('EEGMOVEMENT_SDK_DIR'),
             str(Path(__file__).resolve().parents[1] / 'programfile_gtec/gtec/gtec/gNEEDaccess Client API/C'),
             str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'gtec/gNEEDaccess Client API')]
    for root in filter(None, roots):
        try:return _locate_sdk(root)[0]
        except (SDKError, OSError):pass
    raise unittest.SkipTest('Vendor ABI check needs installed SDK or EEGMOVEMENT_SDK_DIR; proprietary headers are not distributed')


def configuration():
    return {
        'SamplingRate': 512, 'NumberOfScans': 15, 'InputSignal': 0,
        'Slave': False, 'NoiseReduction': True, 'CAR': False,
        'AccelerationData': True, 'Counter': True,
        'LinkQualityInformation': True, 'BatteryLevel': True,
        'DigitalIOs': True, 'ValidationIndicator': True, 'NetworkChannel': 11,
        'Channels': [dict(Enabled=i in (0, 2), Sensitivity=187500.,
                          UsedForNoiseReduction=True, UsedForCar=False,
                          BandpassFilterIndex=4, NotchFilterIndex=1,
                          BipolarChannel=-1) for i in range(64)],
    }


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.groups = [{'in_use': True, 'devices': [
            {'serial': 'NA-123', 'device_type': 3}]}]
        self.config = configuration()
        self.width = 10
        self.creator = False
        self.error = None
        self.changed = None
        self.buffer = np.arange(30, dtype=np.float32).reshape(3, 10)

    def discover(self):
        self.calls.append('discover')
        return self.groups

    def connect(self, names):
        self.calls.append(('connect', names))
        if self.error:
            raise SDKError(self.error, 'fake refusal')
        return 7, self.creator

    def get_configuration(self, handle):
        self.calls.append('get_configuration')
        return self.config

    def get_available_channels(self, handle, serial):
        return [True] * 32 + [False] * 32

    def get_supported_sampling_rates(self, handle, serial):
        return [250, 512, 4000]

    def get_scaling(self, handle, serial):
        return {'Factor': [1.] * 64, 'Offset': [0.] * 64}

    def get_channel_names(self, handle, serial):
        return ['Fp1', 'Fp2', 'C3'] + [''] * 61

    def get_device_information(self, handle, serial):
        return 'Synthetic fake SDK test; no hardware'

    def get_filter_info(self, handle, serial):
        return {'bandpass': [{'TypeId': 1, 'Order': 4, 'SamplingRate': 512.,
                              'LowerCutoffFrequency': float(i), 'UpperCutoffFrequency': 45.}
                             for i in range(5)],
                'notch': [{'TypeId': 1, 'Order': 4, 'SamplingRate': 512.,
                           'LowerCutoffFrequency': 48., 'UpperCutoffFrequency': 52.}
                          for _ in range(2)]}

    def get_data_info(self, handle):
        self.calls.append('get_data_info')
        return self.width

    def watch(self, handle):
        self.calls.append('watch')

    def state(self):
        return self.changed

    def start_streaming(self, handle):
        self.calls.append('start_streaming')

    def get_data(self, handle, capacity, width):
        self.calls.append('get_data')
        return self.buffer

    def stop_streaming(self, handle):
        self.calls.append('stop_streaming')

    def disconnect(self, handle):
        self.calls.append('disconnect')

    def close(self):
        self.calls.append('close')


class SDKTests(unittest.TestCase):
    def reader(self, driver=None, **kw):
        driver = driver or FakeDriver()
        return ParticipantReader('unused', 'NA-123', driver=driver, **kw), driver

    def test_participant_configures_no_device_and_preserves_enabled_column_order(self):
        reader, driver = self.reader()
        desc = reader.open()
        self.assertEqual(desc['sampling_rate'], 512)
        self.assertEqual([c['kind'] for c in desc['channels']],
                         ['EEG', 'EEG', 'ACC', 'ACC', 'ACC', 'COUNTER',
                          'LINK', 'BATTERY', 'DIGITAL', 'VALIDATION'])
        self.assertEqual([c['physical_index'] for c in desc['channels'][:2]], [0, 2])
        self.assertEqual(desc['channels'][0]['name'], 'CH01')
        self.assertFalse(desc['layout_verified'])
        self.assertEqual(desc['configuration']['device_channel_names'][0], 'Fp1')
        data = reader.read()
        self.assertEqual(data.shape, (3, 10))
        np.testing.assert_array_equal(data, driver.buffer)
        driver.buffer[:] = -1
        self.assertEqual(data[0, 0], 0.)
        reader.close()
        reader.close()
        self.assertEqual(driver.calls.count('disconnect'), 1)
        self.assertEqual(driver.calls.count('stop_streaming'), 1)
        self.assertFalse(any('acquisition' in str(c) or 'set_' in str(c)
                             for c in driver.calls))

    def test_no_existing_session_is_not_created(self):
        reader, driver = self.reader()
        driver.groups[0]['in_use'] = False
        with self.assertRaisesRegex(SDKError, '共享'):
            reader.open()
        self.assertFalse(any(isinstance(c, tuple) and c[0] == 'connect' for c in driver.calls))

    def test_creator_race_is_released_without_streaming(self):
        reader, driver = self.reader()
        driver.creator = True
        with self.assertRaises(SDKError) as ctx:
            reader.open()
        self.assertEqual(ctx.exception.code, 'unexpected_creator')
        self.assertIn('disconnect', driver.calls)
        self.assertNotIn('start_streaming', driver.calls)

    def test_exclusive_session_refusal_is_not_retried_as_creator(self):
        reader, driver = self.reader()
        driver.error = 'access_denied'
        with self.assertRaises(SDKError):
            reader.open()
        self.assertEqual(sum(isinstance(c, tuple) and c[0] == 'connect' for c in driver.calls), 1)

    def test_width_mismatch_and_non_electrode_input_fail_closed(self):
        for mutate in [lambda d: setattr(d, 'width', 11),
                       lambda d: d.config.update(InputSignal=5)]:
            with self.subTest(mutate=mutate):
                reader, driver = self.reader()
                mutate(driver)
                with self.assertRaises(SDKError):
                    reader.open()
                self.assertNotIn('start_streaming', driver.calls)
                self.assertIn('disconnect', driver.calls)

    def test_partial_and_empty_reads_never_add_padding(self):
        reader, driver = self.reader()
        reader.open()
        driver.buffer = np.zeros((0, 10), dtype=np.float32)
        self.assertEqual(reader.read().shape, (0, 10))
        driver.buffer = np.full((1, 10), np.nan, dtype=np.float32)
        self.assertTrue(np.isnan(reader.read()).all())
        reader.close()

    def test_callback_invalidation_stops_stream_instead_of_silent_reconfiguration(self):
        reader, driver = self.reader()
        reader.open()
        driver.changed = 'configuration_changed'
        with self.assertRaises(SDKError) as ctx:
            reader.read()
        self.assertEqual(ctx.exception.code, 'configuration_changed')
        self.assertNotIn('get_data', driver.calls)
        reader.close()

    def test_layout_requires_explicit_verification_and_physical_identity(self):
        reader, _ = self.reader(layout={'channels': {'0': 'Fp1', '2': 'C3'},
                                        'verified': True, 'reference': 'REF', 'ground': 'GND',
                                        'wearing_id': 'wear-1'})
        desc = reader.open()
        self.assertTrue(desc['layout_verified'])
        self.assertEqual([c['name'] for c in desc['channels'][:2]], ['Fp1', 'C3'])
        self.assertEqual(desc['wearing_id'], 'wear-1')
        reader.close()

    def test_competing_single_devices_require_selection_and_multidevice_is_explicit(self):
        reader, driver = self.reader()
        driver.groups[0]['devices'].append({'serial': 'NA-456', 'device_type': 3})
        with self.assertRaises(SDKError) as ctx:
            reader.open()
        self.assertEqual(ctx.exception.code, 'unsupported_device_group')

    def test_cdef_tracks_supplied_abi_without_exposing_device_writes(self):
        import cffi
        base = supplied_headers()
        text = make_cdef(base)
        self.assertNotIn('GDS_SetConfiguration(', text)
        self.assertNotIn('GDS_StartAcquisition(', text)
        self.assertNotIn('GDS_StopAcquisition(', text)
        self.assertNotIn('GDS_BecomeCreator(', text)
        ffi = cffi.FFI()
        ffi.cdef(text)
        self.assertEqual(ffi.sizeof('GDS_GNAUTILUS_CHANNEL_CONFIGURATION'), 40)
        # 13 four-byte fields, four alignment bytes, then 64*40 bytes.
        self.assertEqual(ffi.offsetof('GDS_GNAUTILUS_CONFIGURATION', 'Channels'), 56)
        self.assertEqual(ffi.sizeof('GDS_GNAUTILUS_CONFIGURATION'), 2616)
        self.assertIn('void GDS_Initialize()', text)

    def test_callback_that_arrives_during_read_vetoes_the_returned_block(self):
        reader, driver = self.reader()
        reader.open()
        def changed_read(*args):
            driver.changed = 'server_disconnected'
            return driver.buffer
        driver.get_data = changed_read
        with self.assertRaises(SDKError) as ctx:
            reader.read()
        self.assertEqual(ctx.exception.code, 'server_disconnected')
        reader.close()

    def test_configuration_hash_binds_filters_and_scaling_without_modification(self):
        reader, driver = self.reader()
        original = configuration()
        desc = reader.open()
        self.assertEqual(driver.config, original)
        self.assertEqual(desc['configuration']['Channels'][0]['BandpassFilterIndex'], 4)
        self.assertEqual(desc['configuration']['scaling']['Factor'][0], 1.)
        self.assertEqual(set(desc['configuration']['filters']['bandpass']), {'4'})
        self.assertEqual(desc['configuration']['filters']['catalog_counts']['bandpass'], 5)
        first = desc['configuration_sha256']
        reader.close()
        other, second = self.reader()
        second.config['Channels'][0]['NotchFilterIndex'] = -1
        self.assertNotEqual(other.open()['configuration_sha256'], first)
        other.close()

    def test_running_acquisition_scaling_refusal_is_explicit_unknown_metadata(self):
        reader, driver = self.reader()
        def refused(*args):
            raise SDKError('gds_4294967295', 'Data acquisition is running.', native_code=0xFFFFFFFF)
        driver.get_scaling = refused
        desc = reader.open()
        self.assertIsNone(desc['configuration']['scaling'])
        self.assertEqual(desc['configuration']['scaling_query']['status'], 'unavailable_during_acquisition')
        self.assertFalse(desc['configuration']['scaling_query']['client_rescaling_applied'])
        np.testing.assert_array_equal(reader.read(), driver.buffer)
        reader.close()
        self.assertEqual(driver.calls.count('stop_streaming'), 1)
        self.assertFalse(any('acquisition' in str(c) for c in driver.calls))

    def test_unrelated_scaling_failures_still_stop_before_streaming(self):
        for code, message in [(22, 'Timeout'), (0xFFFFFFFF, 'Other failure'), (13, 'Data acquisition is running.')]:
            reader, driver = self.reader()
            def refused(*args):
                raise SDKError('test_refusal', message, native_code=code)
            driver.get_scaling = refused
            with self.assertRaises(SDKError):reader.open()
            self.assertNotIn('start_streaming', driver.calls)
            self.assertIn('disconnect', driver.calls)

    def test_configuration_change_during_scaling_refusal_still_vetoes_stream(self):
        reader, driver = self.reader()
        def refused(*args):
            driver.changed = 'configuration_changed'
            raise SDKError('gds_4294967295', 'Data acquisition is running.', native_code=0xFFFFFFFF)
        driver.get_scaling = refused
        with self.assertRaises(SDKError) as caught:reader.open()
        self.assertEqual(caught.exception.code, 'configuration_changed')
        self.assertNotIn('start_streaming', driver.calls)

    def test_active_filter_index_must_exist_in_reported_catalog(self):
        reader, driver = self.reader()
        driver.config['Channels'][0]['BandpassFilterIndex'] = 999
        with self.assertRaises(SDKError) as ctx:
            reader.open()
        self.assertEqual(ctx.exception.code, 'filter_index_mismatch')


class NativeMarshallingTests(unittest.TestCase):
    """Real CFFI layouts and buffer ownership with a Python fake native surface."""
    def setUp(self):
        import cffi
        base = supplied_headers()
        self.ffi = cffi.FFI()
        self.ffi.cdef(make_cdef(base))
        self.driver = CffiDriver.__new__(CffiDriver)
        self.driver.ffi = self.ffi
        self.driver._buffer = self.driver._buffer_shape = None

    def result(self, code=0):
        return self.ffi.new('GDS_RESULT *', {'ErrorCode': code, 'ErrorMessage': b'fake native'})[0]

    def test_connect_uses_nonexclusive_loopback_and_ephemeral_client_port(self):
        outer = self
        class Native:
            def GDS_Connect(self, destination, local, names, count, exclusive, handle, creator):
                outer.assertEqual(outer.ffi.string(destination.IpAddress), b'127.0.0.1')
                outer.assertEqual(destination.Port, 50223)
                outer.assertEqual(outer.ffi.string(local.IpAddress), b'127.0.0.1')
                outer.assertEqual(local.Port, 0)
                outer.assertEqual(exclusive, 0)
                outer.assertEqual(count, 1)
                outer.assertEqual(outer.ffi.string(names[0]), b'NA-123')
                handle[0], creator[0] = 55, 0
                return outer.result()
        self.driver.dll = Native()
        self.assertEqual(self.driver.connect(['NA-123']), (55, False))

    def test_native_partial_read_uses_actual_scan_count_and_copies_memory(self):
        outer = self
        class Native:
            def GDS_GetData(self, handle, count, destination, capacity):
                outer.assertEqual(count[0], 0)
                outer.assertEqual(capacity, 30)
                count[0] = 2
                for i in range(20):
                    destination[i] = i
                return outer.result()
        self.driver.dll = Native()
        samples = self.driver.get_data(7, 3, 10)
        self.assertEqual(samples.shape, (2, 10))
        self.driver._buffer[0] = -1
        self.assertEqual(samples[0, 0], 0.)

    def test_native_overflow_is_visible_instead_of_padding_or_continuing(self):
        outer = self
        class Native:
            def GDS_GetData(self, *args):
                return outer.result(17)
        self.driver.dll = Native()
        with self.assertRaises(SDKError) as ctx:
            self.driver.get_data(7, 3, 10)
        self.assertEqual(ctx.exception.code, 'transmission_buffer_overflow')
        self.assertEqual(ctx.exception.native_code, 17)


class WorkerReader:
    def __init__(self, **settings):
        self.settings = settings

    def open(self):
        if self.settings.get('fail'):
            raise SDKError('test_failure', 'fake SDK refusal')
        return {'test': True}

    def discover(self):
        if self.settings.get('block'):
            time.sleep(30)
        return [{'in_use': True, 'devices': [{'serial': 'TEST-ONLY', 'device_type': 3}]}]

    def read(self):
        if self.settings.get('block'):
            time.sleep(30)
        if self.settings.get('delay'):time.sleep(self.settings['delay'])
        return np.zeros((1, 2), dtype=np.float32)

    def close(self):
        pass


class WorkerTests(unittest.TestCase):
    def test_read_observation_includes_native_call_and_copy_duration(self):
        worker=SDKWorker({'delay':.05},reader_factory=WorkerReader).start()
        try:
            self.assertEqual(worker.receive(5)['kind'],'descriptor');block=worker.receive(5)
            self.assertGreaterEqual(block['read_completed_monotonic_ns']-block['read_monotonic_ns'],40_000_000)
            self.assertGreaterEqual(block['read_completed_utc_ns'],block['utc_ns'])
        finally:worker.stop()
    def test_writer_failure_during_shutdown_still_closes_child(self):
        worker=SDKWorker({},reader_factory=WorkerReader).start()
        self.assertEqual(worker.receive(5)['kind'],'descriptor');time.sleep(.03)
        def failed(message):raise OSError('disk full during shutdown')
        with self.assertRaises(OSError):worker.stop(on_message=failed)
        self.assertFalse(worker.alive)
        self.assertTrue(worker.last_stop_messages)

    def test_discovery_isolated_and_never_opens_or_streams(self):
        groups = discover_devices({'fail': True}, reader_factory=WorkerReader)
        self.assertEqual(groups[0]['devices'][0]['serial'], 'TEST-ONLY')

    def test_discovery_timeout_is_bounded(self):
        began = time.monotonic()
        with self.assertRaises(SDKError) as ctx:
            discover_devices({'block': True}, timeout=.1, reader_factory=WorkerReader)
        self.assertEqual(ctx.exception.code, 'discovery_timeout')
        self.assertLess(time.monotonic() - began, 2)

    def test_worker_emits_descriptor_copied_blocks_and_bounded_close(self):
        worker = SDKWorker({}, reader_factory=WorkerReader)
        worker.start()
        try:
            desc = worker.receive(5)
            self.assertEqual(desc['kind'], 'descriptor')
            block = worker.receive(5)
            self.assertEqual(block['kind'], 'block')
            self.assertEqual(block['samples'].shape, (1, 2))
            self.assertGreater(block['read_monotonic_ns'], 0)
        finally:
            worker.stop()
        self.assertFalse(worker.alive)

    def test_worker_error_is_reported_and_process_exits(self):
        worker = SDKWorker({'fail': True}, reader_factory=WorkerReader)
        worker.start()
        message = worker.receive(5)
        self.assertEqual(message['kind'], 'error')
        self.assertEqual(message['code'], 'test_failure')
        worker.stop()
        self.assertFalse(worker.alive)

    def test_shutdown_returns_queued_blocks_to_writer_instead_of_silent_loss(self):
        worker = SDKWorker({}, reader_factory=WorkerReader)
        worker.start()
        self.assertEqual(worker.receive(5)['kind'], 'descriptor')
        time.sleep(.03)
        pending = worker.stop()
        self.assertTrue(any(m['kind'] == 'block' for m in pending))
        self.assertEqual(worker.last_stop_messages, pending)

    def test_stuck_sdk_is_terminated_by_supervisor_without_waiting_for_dll(self):
        worker = SDKWorker({'block': True}, reader_factory=WorkerReader)
        worker.start()
        self.assertEqual(worker.receive(5)['kind'], 'descriptor')
        self.assertIsNone(worker.receive(.05))
        began = time.monotonic()
        worker.stop(timeout=.1)
        self.assertLess(time.monotonic() - began, 2)
        self.assertFalse(worker.alive)


if __name__ == '__main__':
    unittest.main()
