import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch

import pytest

from hub.instance import ExclusiveHubServer, HubAlreadyRunning, HubInstance


def test_second_process_cannot_own_runtime_and_exit_releases_lock(tmp_path):
    path=tmp_path/'hub.lock'
    code='from hub.instance import HubInstance\nwith HubInstance('+repr(str(path))+'):\n print("owned",flush=True)\n'
    with HubInstance(path):
        child=subprocess.run([sys.executable,'-X','utf8','-c',code],capture_output=True,text=True,encoding='utf8',timeout=10)
        assert child.returncode != 0
        assert 'HubAlreadyRunning' in child.stderr
        assert 'owned' not in child.stdout
    child=subprocess.run([sys.executable,'-X','utf8','-c',code],capture_output=True,text=True,encoding='utf8',timeout=10)
    assert child.returncode == 0,child.stderr
    assert child.stdout.strip() == 'owned'


def test_exception_releases_runtime_lock(tmp_path):
    path=tmp_path/'hub.lock'
    with pytest.raises(ValueError),HubInstance(path):
        raise ValueError('startup failed')
    with HubInstance(path):
        with pytest.raises(HubAlreadyRunning):
            with HubInstance(path):
                pytest.fail('duplicate owner')


def test_web_listener_is_exclusive():
    with ExclusiveHubServer(('127.0.0.1',0),BaseHTTPRequestHandler) as first:
        with pytest.raises(OSError):
            with ExclusiveHubServer(first.server_address,BaseHTTPRequestHandler):
                pytest.fail('shared web port')


def test_duplicate_main_never_constructs_hub_or_starts_ingest(tmp_path):
    from hub import app
    with HubInstance(tmp_path/'hub.instance.lock'),patch.object(app,'RUNTIME',tmp_path),patch.object(app,'Hub') as factory,patch.object(sys,'argv',['hub.app']):
        app.main()
        factory.assert_not_called()


def test_occupied_web_port_never_constructs_hub(tmp_path):
    from hub import app
    import json
    root=tmp_path/'project';(root/'hub').mkdir(parents=True)
    with ExclusiveHubServer(('127.0.0.1',0),BaseHTTPRequestHandler) as server:
        (root/'hub/config.json').write_text(json.dumps(dict(web_host='127.0.0.1',web_port=server.server_port)))
        with patch.object(app,'ROOT',root),patch.object(app,'RUNTIME',tmp_path),patch.object(app,'Hub') as factory,patch.object(sys,'argv',['hub.app']),pytest.raises(OSError):
            app.main()
        factory.assert_not_called()
