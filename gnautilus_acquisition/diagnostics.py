"""Device discovery is not a successful shared session or verified EEG stream."""
from __future__ import annotations

import time


def describe_devices(groups, serial=None):
    """Classify all visible devices without opening or creating a session.

    InUse means a DAQ session exists. It does not establish that its creator
    permits participants, that acquisition is running, or that EEG is valid.
    """
    devices = [dict(device, in_use=bool(group['in_use']))
               for group in groups for device in group['devices']
               if device['device_type'] == 3]
    matching = [group for group in groups if any(
        device['device_type'] == 3 and (not serial or device['serial'] == serial)
        for device in group['devices'])]
    active = [group for group in matching if group['in_use']]
    result = dict(devices=devices, ready_for_participant=False,
                  stream_verified=False, selected_group=None, selected_serial=None)
    if not devices:
        code, message = 'device_not_found', 'GDS可访问，但未发现g.Nautilus；请检查基站USB、原厂驱动、设备供电及配对。'
    elif serial and not matching:
        code, message = 'selected_device_not_found', f'GDS已发现设备，但未找到所选序列号 {serial}；请核对或清空设备序列号后重新发现。'
    elif not active:
        code, message = 'shared_session_absent', '设备已发现，但原厂共享会话尚未建立；请在g.Recorder、g.NEEDaccess Demo或原厂采集程序中打开本机设备，以非独占模式建立会话并开始采集，再重新检测。仅接上USB或打开MATLAB不等于建立GDS会话。'
    elif len(active) != 1:
        code, message = 'device_selection_required', '发现多个原厂会话，请填写明确的g.Nautilus序列号后重新检测。'
    elif len(active[0]['devices']) != 1 or active[0]['devices'][0]['device_type'] != 3:
        code, message = 'unsupported_device_group', '原厂会话包含多设备；当前只支持单台g.Nautilus，不改变原厂设备组。'
    else:
        code, message = 'shared_session_detected', '原厂会话已存在；尚未验证非独占权限、采集运行状态及实际数据。开始接收或执行流检测后才能确认。'
        result.update(ready_for_participant=True, selected_group=active[0],
                      selected_serial=active[0]['devices'][0]['serial'])
    result.update(code=code, message=message)
    return result


def probe_participant(settings, *, seconds=2., startup_timeout=8., worker_factory=None):
    """Bounded opt-in stream check in a child process; never stores sample values."""
    from .worker import SDKWorker
    if not .1 <= seconds <= 10 or not .1 <= startup_timeout <= 30:
        raise ValueError('流检测时长须为0.1–10秒，启动超时须为0.1–30秒')
    worker = (worker_factory or SDKWorker)(settings)
    result = dict(stream_verified=False, samples=0, blocks=0, code='stream_timeout',
                  message='等待原厂共享会话或样本超时；未确认实际数据流。')
    descriptor = None
    began = time.monotonic()
    deadline = began + startup_timeout
    try:
        worker.start()
        while time.monotonic() < deadline:
            message = worker.receive(min(.1, max(0., deadline - time.monotonic())))
            if message is None:
                if not worker.alive:
                    result.update(code='worker_exited', message='SDK进程已退出，未确认实际数据流。')
                    break
                continue
            if message['kind'] == 'descriptor':
                descriptor = message['descriptor']
                result.update(sampling_rate=descriptor['sampling_rate'],
                              columns=len(descriptor['channels']),
                              eeg_channels=sum(c['kind'] == 'EEG' for c in descriptor['channels']),
                              sampling_rate_source='vendor_configuration',
                              scaling_status=descriptor.get('configuration', {}).get('scaling_query', {}).get('status', 'unknown'))
                deadline = time.monotonic() + seconds
            elif message['kind'] == 'block':
                result['samples'] += len(message['samples'])
                result['blocks'] += 1
            elif message['kind'] == 'error':
                result.update(code=message['code'], message=message['message'],
                              native_code=message.get('native_code'))
                break
            elif message['kind'] == 'state' and message['state'] == 'closed':
                break
        else:
            if descriptor is not None and result['samples']:
                result.update(stream_verified=True, code='stream_received',
                              message='已从原厂Participant收到样本；电极接触质量、通道位置及识别效果仍需另行验收。')
    except Exception as error:
        result.update(code=getattr(error, 'code', type(error).__name__), message=str(error),
                      native_code=getattr(error, 'native_code', None))
    finally:
        try:
            pending = worker.stop()
            for message in pending:
                if message.get('kind') == 'error':
                    result.update(stream_verified=False, code=message['code'], message=message['message'],
                                  native_code=message.get('native_code'))
            close_errors = [m for m in pending if m.get('kind') == 'state' and m.get('state') == 'close_errors']
            if close_errors:
                result.update(stream_verified=False, code='close_errors', message=str(close_errors))
            if worker.forced_termination:
                result.update(stream_verified=False, code='forced_termination',
                              message='SDK进程未正常释放，已强制终止；请核查原厂会话。')
        except Exception as error:
            result.update(stream_verified=False, code='close_failed', message=str(error))
    result['elapsed_ms'] = round((time.monotonic() - began) * 1000)
    return result


def diagnose_sdk(sdk_dir=None, serial=None, *, probe_stream=False, seconds=2.):
    """Discover without streaming by default; a stream check is explicit."""
    from .sdk import _locate_sdk, query_service_status
    from .worker import discover_devices
    result = dict(hardware_verified=False, stream_verified=False,
                  ready_for_participant=False, device_groups=[], ok=False)
    try:
        headers, dll = _locate_sdk(sdk_dir)
        result.update(sdk_headers=str(headers), sdk_dll=str(dll))
        result['service'] = query_service_status()
        if result['service']['state'] != 4:
            result.update(code='service_not_running', message='GDS服务未运行；请通过原厂程序检查并启动服务。')
            return result
        settings = dict(sdk_dir=str(headers), serial=serial or None)
        groups = discover_devices(settings)
        result.update(device_groups=groups, **describe_devices(groups, serial))
        if probe_stream and result['ready_for_participant']:
            result['stream'] = probe_participant(settings, seconds=seconds)
            result.update(stream_verified=result['stream']['stream_verified'],
                          code=result['stream']['code'], message=result['stream']['message'])
        result['ok'] = result['stream_verified'] if probe_stream else result['ready_for_participant']
    except Exception as error:
        result.update(code=getattr(error, 'code', type(error).__name__), message=str(error),
                      native_code=getattr(error, 'native_code', None))
    return result
