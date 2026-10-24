"""Read-only participant for the supplied g.NEEDaccess 1.20.01 C ABI.

Importing this module never loads a vendor library or opens hardware. The DLL is
loaded only inside the SDK worker. Configuration and acquisition-control APIs
are deliberately absent from the binding's allowlist.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
import uuid

import numpy as np


class SDKError(RuntimeError):
    def __init__(self, code, message, *, native_code=None):
        self.code = str(code)
        self.native_code = native_code
        super().__init__(message)


READ_APIS = frozenset({
    'GDS_Initialize', 'GDS_Uninitialize', 'GDS_GetConnectedDevices',
    'GDS_FreeConnectedDevicesList', 'GDS_Connect', 'GDS_Disconnect',
    'GDS_GetConfiguration', 'GDS_FreeConfigurationList', 'GDS_GetDataInfo',
    'GDS_StartStreaming', 'GDS_StopStreaming', 'GDS_GetData',
    'GDS_SetConfigurationChangedCallback', 'GDS_SetDataAcquisitionStoppedCallback',
    'GDS_SetDataAcquisitionErrorCallback', 'GDS_SetSessionCreatorDiedCallback',
    'GDS_SetNewSessionCreatorCallback', 'GDS_SetServerDiedCallback',
    'GDS_SetForcedClientShutdownCallback',
    'GDS_GNAUTILUS_GetAvailableChannels', 'GDS_GNAUTILUS_GetChannelNames',
    'GDS_GNAUTILUS_GetScaling', 'GDS_GNAUTILUS_GetDeviceInformation',
    'GDS_GNAUTILUS_GetSupportedSamplingRates',
    'GDS_GNAUTILUS_GetBandpassFilters', 'GDS_GNAUTILUS_GetNotchFilters',
})


def make_cdef(header_dir):
    """Derive declarations from the user's installed headers, without eval/C code."""
    header_dir = Path(header_dir)
    definitions = {}
    prepared = []
    for name in ['GDSClientAPI.h', 'GDSClientAPI_gNautilus.h']:
        text = (header_dir / name).read_text(encoding='utf-8-sig', errors='replace')
        for key, value in re.findall(r'^\s*#define\s+(\w+)\s+([0-9]+|0x[0-9a-fA-F]+)\s*$', text, re.M):
            definitions[key] = value
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('extern "C"') or stripped == '}':
                continue
            if stripped.startswith('GDSCLIENTAPI_API'):
                match = re.search(r'\b(GDS_\w+)\s*\(', line)
                if not match or match.group(1) not in READ_APIS:
                    continue
                line = line.replace('GDSCLIENTAPI_API', '')
            lines.append(line)
        prepared.append('\n'.join(lines))
    code = '\n'.join(prepared)
    for key, value in definitions.items():
        code = re.sub(r'\b' + re.escape(key) + r'\b', value, code)
    return ('typedef unsigned long long uint64_t;\n'
            'typedef unsigned int uint32_t;\n'
            'typedef unsigned short uint16_t;\n'
            'typedef int int32_t;\n' + code)


def _locate_sdk(sdk_dir=None):
    raw = os.fspath(sdk_dir).strip() if sdk_dir else str(
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'gtec/gNEEDaccess Client API')
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ['"', "'"]:
        raw = raw[1:-1].strip()
    root = Path(os.path.expandvars(raw)).expanduser().resolve()
    if not root.is_dir():
        raise SDKError('sdk_directory_missing', f'原厂Client API目录不存在：{root}')
    candidates = [root, root / 'C', root / 'gNEEDaccess Client API' / 'C']
    # Accept an x64 folder selected in the file picker as well.
    if root.name.lower() == 'x64':
        candidates.insert(0, root.parent)
    header_dir = next((p for p in candidates if (p / 'GDSClientAPI.h').is_file()
                       and (p / 'GDSClientAPI_gNautilus.h').is_file()), None)
    if header_dir is None:
        raise SDKError('sdk_headers_missing', '未找到原厂C API头文件，请选择gNEEDaccess Client API目录；仅有运行时DLL不足以绑定。')
    dll = (header_dir / 'x64' / 'GDSClientAPI.dll').resolve()
    if not dll.is_file():
        raise SDKError('sdk_dll_missing', '未找到原厂x64/GDSClientAPI.dll。')
    return header_dir, dll

def query_service_status():
    """Query the installed Windows service, without pywin32 or server fallback."""
    if os.name != 'nt':
        raise SDKError('platform_unsupported', '原厂接收端要求Windows x64。')
    import ctypes
    from ctypes import wintypes

    class ServiceStatus(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in
                    ['service_type', 'state', 'controls', 'exit_code',
                     'service_exit_code', 'checkpoint', 'wait_hint']]

    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenSCManagerW.restype = wintypes.HANDLE
    api.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenServiceW.restype = wintypes.HANDLE
    api.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(ServiceStatus)]
    api.QueryServiceStatus.restype = wintypes.BOOL
    api.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    api.CloseServiceHandle.restype = wintypes.BOOL
    manager = api.OpenSCManagerW(None, None, 1)  # SC_MANAGER_CONNECT only
    if not manager:
        error = ctypes.get_last_error()
        raise SDKError('service_query_failed', f'无法查询Windows服务管理器（错误{error}），不能断言GDS未运行。', native_code=error)
    try:
        service = api.OpenServiceW(manager, 'GDS', 4)  # SERVICE_QUERY_STATUS only
        if not service:
            error = ctypes.get_last_error()
            code = 'service_not_installed' if error == 1060 else 'service_query_failed'
            message = '未安装原厂GDS服务，请安装g.NEEDaccess。' if error == 1060 else '无法查询GDS服务状态，不能断言服务未运行。'
            raise SDKError(code, f'{message}（Windows错误{error}）', native_code=error)
        try:
            status = ServiceStatus()
            if not api.QueryServiceStatus(service, ctypes.byref(status)):
                error = ctypes.get_last_error()
                raise SDKError('service_query_failed', f'查询GDS服务状态失败（Windows错误{error}）。', native_code=error)
            names = {1: 'stopped', 2: 'start_pending', 3: 'stop_pending', 4: 'running',
                     5: 'continue_pending', 6: 'pause_pending', 7: 'paused'}
            return {'name': 'GDS', 'state': int(status.state),
                    'status': names.get(status.state, 'unknown')}
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)


def _service_running():
    # A missing Python dependency must never masquerade as a stopped service.
    return query_service_status()['state'] == 4

def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CffiDriver:
    """The sole vendor DLL owner; all endpoints are loopback and port 0 is local."""
    def __init__(self, sdk_dir):
        if os.name != 'nt' or struct.calcsize('P') != 8:
            raise SDKError('platform_unsupported', '原厂接收端要求Windows x64。')
        self.header_dir, self.dll_path = _locate_sdk(sdk_dir)
        if not _service_running():
            raise SDKError('service_not_running', 'g.NEEDaccess的GDS服务未运行；请在原厂软件中启动服务和采集。')
        import cffi
        self.ffi = cffi.FFI()
        try:
            self.ffi.cdef(make_cdef(self.header_dir))
        except Exception as exc:
            raise SDKError('sdk_header_incompatible', f'原厂头文件ABI解析失败：{exc}') from exc
        if self.ffi.sizeof('GDS_GNAUTILUS_CHANNEL_CONFIGURATION') != 40 or self.ffi.sizeof('GDS_GNAUTILUS_CONFIGURATION') != 2616:
            raise SDKError('sdk_abi_incompatible', '原厂结构大小与当前支持的1.20.01 x64 ABI不一致。')
        self._dll_directory = os.add_dll_directory(str(self.dll_path.parent))
        try:
            self.dll = self.ffi.dlopen(str(self.dll_path))
        except Exception as exc:
            self._dll_directory.close()
            raise SDKError('sdk_dll_load_failed', f'原厂DLL加载失败，请检查原厂安装和依赖：{exc}') from exc
        # These two functions return void in the supplied headers.
        self.dll.GDS_Initialize()
        self._initialized = True
        self._callbacks = []
        self._fault = None
        self._fault_lock = threading.Lock()
        self._buffer = None
        self._buffer_shape = None
        self.provenance = {
            'api': 'g.NEEDaccess C API participant', 'documented_version': '1.20.01',
            'dll_sha256': _sha(self.dll_path),
            'headers_sha256': {p.name: _sha(p) for p in [self.header_dir / 'GDSClientAPI.h', self.header_dir / 'GDSClientAPI_gNautilus.h']},
            'abi': 'Windows x64 / supplied headers / CFFI ABI',
            'hardware_verified': False,
        }

    def _check(self, result):
        if int(result.ErrorCode) == 0:
            return
        code = int(result.ErrorCode)
        names = {5: 'access_denied', 9: 'device_group_in_use', 13: 'acquisition_not_running',
                 16: 'acquisition_buffer_overflow', 17: 'transmission_buffer_overflow',
                 18: 'acquisition_died', 20: 'endpoint_unavailable', 22: 'request_timeout'}
        explanation = {5: '原厂会话为独占模式或不允许参与；不会接管设备。',
                       9: '设备组与原厂当前会话不一致。',
                       13: '原厂尚未启动设备采集，或已经停止。'}
        detail = self.ffi.string(result.ErrorMessage).decode('utf-8', 'replace')
        raise SDKError(names.get(code, f'gds_{code}'), explanation.get(code, '原厂SDK调用失败。') + ' ' + detail,
                       native_code=code)

    def _endpoints(self):
        return self.ffi.new('GDS_ENDPOINT *', {'IpAddress': b'127.0.0.1', 'Port': 50223})[0], self.ffi.new('GDS_ENDPOINT *', {'IpAddress': b'127.0.0.1', 'Port': 0})[0]

    def _serial(self, name):
        data = name.encode('ascii', 'strict')
        if not data or len(data) >= 32:
            raise SDKError('invalid_serial', '设备序列号长度无效。')
        return self.ffi.new('char (*)[32]', data)

    def discover(self):
        destination, local = self._endpoints()
        groups = self.ffi.new('GDS_DEVICE_CONNECTION_INFO **')
        count = self.ffi.new('size_t *')
        self._check(self.dll.GDS_GetConnectedDevices(destination, local, groups, count))
        try:
            if count[0] > 64:
                raise SDKError('invalid_device_count', '原厂设备列表超出支持范围。')
            result = []
            for index in range(count[0]):
                entry = groups[0][index]
                if entry.ConnectedDevicesLength > 64:
                    raise SDKError('invalid_device_count', '原厂会话设备数量超出支持范围。')
                result.append({'in_use': bool(entry.InUse), 'devices': [
                    {'serial': self.ffi.string(entry.ConnectedDevices[j].Name).decode('ascii', 'strict'),
                     'device_type': int(entry.ConnectedDevices[j].DeviceType)}
                    for j in range(entry.ConnectedDevicesLength)]})
            return result
        finally:
            self._check(self.dll.GDS_FreeConnectedDevicesList(groups, count[0]))

    def connect(self, names):
        destination, local = self._endpoints()
        serials = self.ffi.new('char[][32]', [s.encode('ascii', 'strict') for s in names])
        handle, creator = self.ffi.new('GDS_HANDLE *'), self.ffi.new('BOOL *')
        self._check(self.dll.GDS_Connect(destination, local, serials, len(names), 0, handle, creator))
        return int(handle[0]), bool(creator[0])

    def get_configuration(self, handle):
        configurations = self.ffi.new('GDS_CONFIGURATION_BASE **')
        count = self.ffi.new('size_t *')
        self._check(self.dll.GDS_GetConfiguration(handle, configurations, count))
        try:
            if count[0] != 1 or int(configurations[0][0].DeviceInfo.DeviceType) != 3:
                raise SDKError('unsupported_device_group', '当前版本只接收单台g.Nautilus的共享会话。')
            config = self.ffi.cast('GDS_GNAUTILUS_CONFIGURATION *', configurations[0][0].Configuration)[0]
            result = {}
            for field, info in self.ffi.typeof(config).fields:
                if field == 'Channels':
                    result[field] = [{key: (bool(getattr(ch, key)) if key in ['Enabled', 'UsedForNoiseReduction', 'UsedForCar'] else float(getattr(ch, key)) if key == 'Sensitivity' else int(getattr(ch, key)))
                                      for key, _ in self.ffi.typeof(ch).fields} for ch in config.Channels]
                else:
                    result[field] = int(getattr(config, field))
            return result
        finally:
            self._check(self.dll.GDS_FreeConfigurationList(configurations, count[0]))

    def get_available_channels(self, handle, serial):
        values = self.ffi.new('BOOL (*)[64]')
        self._check(self.dll.GDS_GNAUTILUS_GetAvailableChannels(handle, self._serial(serial), values))
        return [bool(v) for v in values[0]]

    def get_channel_names(self, handle, serial):
        modules, count = self.ffi.new('uint32_t *'), self.ffi.new('size_t *')
        self._check(self.dll.GDS_GNAUTILUS_GetChannelNames(handle, self._serial(serial), modules, self.ffi.NULL, count))
        if count[0] > 64:
            raise SDKError('invalid_channel_count', '原厂电极名称数量无效。')
        names = self.ffi.new('char[][4]', count[0])
        self._check(self.dll.GDS_GNAUTILUS_GetChannelNames(handle, self._serial(serial), modules, names, count))
        return [self.ffi.string(name).decode('ascii', 'replace') for name in names]

    def get_supported_sampling_rates(self, handle, serial):
        count = self.ffi.new('size_t *')
        self._check(self.dll.GDS_GNAUTILUS_GetSupportedSamplingRates(handle, self._serial(serial), self.ffi.NULL, count))
        if not 0 < count[0] <= 64:
            raise SDKError('invalid_sampling_rates', '原厂采样率列表无效。')
        rates = self.ffi.new('uint32_t[]', count[0])
        self._check(self.dll.GDS_GNAUTILUS_GetSupportedSamplingRates(handle, self._serial(serial), rates, count))
        return [int(v) for v in rates]

    def get_scaling(self, handle, serial):
        scaling = self.ffi.new('GDS_GNAUTILUS_SCALING *')
        self._check(self.dll.GDS_GNAUTILUS_GetScaling(handle, self._serial(serial), scaling))
        # Preserve diagnostics for unavailable channels without non-standard JSON.
        return {name: [float(v) if math.isfinite(v) else None for v in getattr(scaling[0], name)] for name in ['Factor', 'Offset']}

    def get_device_information(self, handle, serial):
        text = self.ffi.new('char (*)[1024]')
        self._check(self.dll.GDS_GNAUTILUS_GetDeviceInformation(handle, self._serial(serial), text))
        return self.ffi.string(text[0]).decode('utf-8', 'replace')

    def get_filter_info(self, handle, serial):
        result = {}
        for label, suffix in [('bandpass', 'Bandpass'), ('notch', 'Notch')]:
            function = getattr(self.dll, 'GDS_GNAUTILUS_Get' + suffix + 'Filters')
            count = self.ffi.new('size_t *')
            self._check(function(handle, self._serial(serial), self.ffi.NULL, count))
            if count[0] > 8192:
                raise SDKError('invalid_filter_count', '原厂滤波器列表数量无效。')
            filters = self.ffi.new('GDS_FILTER_INFO[]', count[0])
            self._check(function(handle, self._serial(serial), filters, count))
            result[label] = [{name: int(getattr(f, name)) if name in ['TypeId', 'Order'] else float(getattr(f, name))
                              for name, _ in self.ffi.typeof(f).fields} for f in filters]
        return result

    def get_data_info(self, handle):
        scans, count, size = self.ffi.new('size_t *', 1), self.ffi.new('size_t *'), self.ffi.new('size_t *')
        self._check(self.dll.GDS_GetDataInfo(handle, scans, self.ffi.NULL, count, size))
        if count[0] != 1:
            raise SDKError('unsupported_device_group', '取数布局不是单设备。')
        channels = self.ffi.new('size_t[]', 1)
        scans[0] = 1
        self._check(self.dll.GDS_GetDataInfo(handle, scans, channels, count, size))
        if not 1 <= channels[0] <= 72 or size[0] != channels[0]:
            raise SDKError('data_layout_mismatch', '原厂GetDataInfo数据宽度不一致。')
        return int(channels[0])

    def watch(self, handle):
        reasons = [('GDS_SetConfigurationChangedCallback', 'configuration_changed'),
                   ('GDS_SetDataAcquisitionStoppedCallback', 'acquisition_stopped'),
                   ('GDS_SetSessionCreatorDiedCallback', 'creator_disconnected'),
                   ('GDS_SetNewSessionCreatorCallback', 'creator_changed'),
                   ('GDS_SetServerDiedCallback', 'server_disconnected'),
                   ('GDS_SetForcedClientShutdownCallback', 'client_shutdown')]
        self._fault = None
        for function, reason in reasons:
            def notify(_handle, _user, reason=reason):
                with self._fault_lock:
                    self._fault = self._fault or reason
            callback = self.ffi.callback('GDS_Callback', notify)
            self._callbacks.append(callback)
            self._check(getattr(self.dll, function)(handle, callback, self.ffi.NULL))

        def acquisition_error(_handle, result, _user):
            with self._fault_lock:
                self._fault = self._fault or 'acquisition_error_' + str(int(result.ErrorCode))
        callback = self.ffi.callback('GDS_ResultCallback', acquisition_error)
        self._callbacks.append(callback)
        self._check(self.dll.GDS_SetDataAcquisitionErrorCallback(handle, callback, self.ffi.NULL))

    def state(self):
        with self._fault_lock:
            return self._fault

    def start_streaming(self, handle):
        self._check(self.dll.GDS_StartStreaming(handle))

    def get_data(self, handle, capacity, width):
        if self._buffer_shape != (capacity, width):
            self._buffer = self.ffi.new('float[]', capacity * width)
            self._buffer_shape = (capacity, width)
        scans = self.ffi.new('size_t *', 0)
        self._check(self.dll.GDS_GetData(handle, scans, self._buffer, capacity * width))
        if scans[0] > capacity:
            raise SDKError('invalid_scan_count', 'SDK返回扫描数量超出缓冲容量。')
        return np.frombuffer(self.ffi.buffer(self._buffer, int(scans[0]) * width * 4), dtype='<f4').reshape(int(scans[0]), width).copy()

    def stop_streaming(self, handle):
        self._check(self.dll.GDS_StopStreaming(handle))

    def disconnect(self, handle):
        value = self.ffi.new('GDS_HANDLE *', handle)
        self._check(self.dll.GDS_Disconnect(value))
        self._callbacks.clear()

    def close(self):
        if getattr(self, '_initialized', False):
            self._initialized = False
            self.dll.GDS_Uninitialize()
        if getattr(self, '_dll_directory', None):
            self._dll_directory.close()
            self._dll_directory = None


class ParticipantReader:
    def __init__(self, sdk_dir, serial=None, layout=None, *, driver=None):
        self.sdk_dir, self.serial = sdk_dir, serial
        self.layout = copy.deepcopy(layout or {})
        self.driver = driver
        self.handle = None
        self.streaming = False
        self.descriptor = None
        self._closed = False

    def _driver(self):
        if self._closed:
            raise SDKError('reader_closed', '接收器已经关闭，请创建新的接收会话。')
        if self.driver is None:
            self.driver = CffiDriver(self.sdk_dir)
        return self.driver

    def discover(self):
        return copy.deepcopy(self._driver().discover())

    def open(self):
        if self.handle is not None:
            raise SDKError('reader_already_open', '已有接收会话。')
        driver = self._driver()
        try:
            groups = driver.discover()
            from .diagnostics import describe_devices
            diagnosis = describe_devices(groups, self.serial)
            if not diagnosis['ready_for_participant']:
                raise SDKError(diagnosis['code'], diagnosis['message'])
            names = diagnosis['selected_group']['devices']
            self.serial = names[0]['serial']
            self.handle, creator = driver.connect([self.serial])
            if creator:
                raise SDKError('unexpected_creator', '原厂会话已退出；已释放意外创建的空会话，不启动设备。')
            # Register first: changes during the metadata reads must veto this stream.
            driver.watch(self.handle)
            config = copy.deepcopy(driver.get_configuration(self.handle))
            if int(config['InputSignal']) != 0:
                raise SDKError('non_electrode_input', '原厂输入不是Electrode；测试信号不可作为实时EEG。')
            rates = driver.get_supported_sampling_rates(self.handle, self.serial)
            rate = int(config['SamplingRate'])
            if rate not in rates or rate == 4000 or not 1 <= rate <= 20000:
                raise SDKError('sampling_rate_mismatch', '实际采样率不在设备支持的常规采样率中。')
            available = driver.get_available_channels(self.handle, self.serial)
            if len(config['Channels']) != 64 or len(available) != 64:
                raise SDKError('data_layout_mismatch', '原厂配置或可用通道列表长度无效。')
            columns = []
            labels = self.layout.get('channels', {})
            confirmed = self.layout.get('verified') is True
            for index, channel in enumerate(config['Channels']):
                if channel['Enabled']:
                    if not available[index]:
                        raise SDKError('unavailable_channel_enabled', '配置启用了设备不存在的通道。')
                    label = labels.get(str(index)) if confirmed else None
                    if confirmed and (not isinstance(label, str) or not label.strip()):
                        raise SDKError('layout_incomplete', '已确认布局缺少启用通道的实际位置。')
                    columns.append(dict(id=f'EEG{index + 1:02}', name=label or f'CH{index + 1:02}',
                                        kind='EEG', unit='uV', physical_index=index))
            if not columns:
                raise SDKError('no_eeg_channels', '原厂会话未启用EEG通道。')
            if config['AccelerationData']:
                columns.extend(dict(id='ACC_' + axis, name='ACC_' + axis, kind='ACC', unit='g', physical_index=None) for axis in ['X', 'Y', 'Z'])
            for field, kind, unit in [('Counter', 'COUNTER', 'count'), ('LinkQualityInformation', 'LINK', '%'),
                                      ('BatteryLevel', 'BATTERY', '%'), ('DigitalIOs', 'DIGITAL', 'bits'),
                                      ('ValidationIndicator', 'VALIDATION', 'bool')]:
                if config[field]:
                    columns.append(dict(id=kind, name=field, kind=kind, unit=unit, physical_index=None))
            if driver.get_data_info(self.handle) != len(columns):
                raise SDKError('data_layout_mismatch', '实际数据宽度与启用EEG和附加信号不一致；禁止截断或猜测列号。')
            config['available_channels'] = list(available)
            config['supported_sampling_rates'] = list(rates)
            config['device_channel_names'] = driver.get_channel_names(self.handle, self.serial)
            config['scaling'] = driver.get_scaling(self.handle, self.serial)
            catalogs = driver.get_filter_info(self.handle, self.serial)
            catalog_json = json.dumps(catalogs, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            # Active filter metadata belongs to this stream. Whole capability
            # catalogs can be very large, so retain their identities and counts.
            config['filters'] = {'catalog_sha256': hashlib.sha256(catalog_json.encode('utf-8')).hexdigest(),
                                 'catalog_counts': {name: len(values) for name, values in catalogs.items()}}
            for label, field in [('bandpass', 'BandpassFilterIndex'), ('notch', 'NotchFilterIndex')]:
                selected = {}
                for channel in config['Channels']:
                    if not channel['Enabled']:
                        continue
                    index = channel[field]
                    if index == -1:
                        continue
                    if not isinstance(index, int) or not 0 <= index < len(catalogs[label]):
                        raise SDKError('filter_index_mismatch', '启用通道的原厂滤波索引不在返回的列表中。')
                    selected[str(index)] = catalogs[label][index]
                config['filters'][label] = selected
            config['device_information'] = driver.get_device_information(self.handle, self.serial)
            canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
            self.descriptor = dict(schema='gnautilus.stream.v1', stream_id=uuid.uuid4().hex, epoch=0,
                                   source_kind='gtec_live', device_serial=self.serial, sampling_rate=rate,
                                   channels=columns, configuration=config,
                                   configuration_sha256=hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
                                   layout_verified=confirmed, reference=self.layout.get('reference'),
                                   ground=self.layout.get('ground'), wearing_id=self.layout.get('wearing_id'),
                                   role='unassigned', sdk=copy.deepcopy(getattr(driver, 'provenance', {'api': 'injected_test_driver'})),
                                   quality_semantics='device validity and continuity; electrode contact not measured',
                                   timing_semantics='host read timing; no hardware or VR clock timestamp')
            self.width = len(columns)
            self.capacity = max(1, min(4096, math.ceil(rate / 20)))
            self._assert_state()
            driver.start_streaming(self.handle)
            self.streaming = True
            self._assert_state()
            return copy.deepcopy(self.descriptor)
        except BaseException:
            self.close()
            raise

    def _assert_state(self):
        fault = self.driver.state()
        if fault:
            raise SDKError(fault, '原厂会话状态已改变，当前数据流结束；请核查后重新开始接收。')

    def read(self):
        if self.handle is None or not self.streaming:
            raise SDKError('not_streaming', '尚未建立原厂共享数据流。')
        self._assert_state()
        samples = np.asarray(self.driver.get_data(self.handle, self.capacity, self.width))
        self._assert_state()
        if samples.ndim != 2 or samples.shape[1] != self.width or samples.shape[0] > self.capacity:
            raise SDKError('data_layout_mismatch', 'SDK数据形状与当前描述不符。')
        # NaN/Inf are preserved as invalid raw evidence, not padded or repaired.
        return np.array(samples, dtype='<f4', order='C', copy=True)

    def close(self):
        if self._closed:
            return
        self._closed = True
        failures = []
        if self.driver is not None:
            if self.handle is not None:
                if self.streaming:
                    try:
                        self.driver.stop_streaming(self.handle)
                    except Exception as exc:
                        failures.append(str(exc))
                    self.streaming = False
                try:
                    self.driver.disconnect(self.handle)
                except Exception as exc:
                    failures.append(str(exc))
                self.handle = None
            try:
                self.driver.close()
            except Exception as exc:
                failures.append(str(exc))
        self.close_errors = failures
