"""Explicit retirement of the former device/model stack; VR transport stays usable."""
import json
from pathlib import Path

MESSAGE = '旧模型及训练体系已归档；g.Nautilus数据接入程序已提供，实机状态见采集页；新模型尚未配置。VR连接、停止和急停仍可使用。'


def transition_state(root):
    path = Path(root) / 'configs/device_transition.json'
    if not path.exists():
        return dict(legacy_archived=False)
    state = json.loads(path.read_text(encoding='utf-8'))
    if type(state.get('legacy_archived')) is not bool:
        raise ValueError('Invalid device transition phase')
    return state


def retired_action(path, data, archived, new_device=False):
    if not archived:
        return False
    path = path.replace('/v1/', '/api/', 1)
    if new_device and path in {'/api/acquisition/start','/api/record/start','/api/models/activate','/api/models/refresh','/api/intent/start','/api/intent/control'}:
        return False
    if path == '/api/intent/control':
        return bool(data.get('enabled'))
    if path == '/api/source':
        return data.get('mode') != 'paused'
    if path == '/api/intent/test' and data.get('action') == 'release':
        return False
    return path in {
        '/api/models/activate', '/api/models/refresh', '/api/intent/start',
        '/api/intent/replay', '/api/intent/test', '/api/calibration/start',
        '/api/acquisition/start', '/api/record/start',
    }


def background_targets(names, archived):
    return [n for n in names if not archived or n in {'acquire', 'analyze', 'infer'}]


def annotate_html(body, archived):
    if not archived:
        return body
    start = body.find(b'<body')
    end = body.find(b'>', start) if start >= 0 else -1
    if end < 0:
        return body
    # The workbench owns its notice/status containers. A new body child changes
    # its grid and sidebar layout, so retirement is carried as page metadata.
    tag = body[start:end]
    if b'data-device-transition=' not in tag:
        body = body[:end] + b' data-device-transition="gnautilus"' + body[end:]
    return body
