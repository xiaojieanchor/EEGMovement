import collections,threading,time
from pathlib import Path
from unittest.mock import Mock
import numpy as np
import pytest

def test_new_device_actions_are_available_only_for_new_implementation():
    from hub.device_transition import retired_action
    assert not retired_action('/api/intent/start',{},True,True)
    assert not retired_action('/api/models/activate',{},True,True)
    assert retired_action('/api/intent/start',{},True,False)
    assert retired_action('/api/intent/replay',{},True,True)

def test_empty_new_library_is_not_reported_read_only(tmp_path):
    from hub.ml_runtime import MLManager
    bridge=Mock();bridge.status={'ssh_connected':False};bridge.call.side_effect=ConnectionError('offline')
    hub=Mock();manager=MLManager(hub,tmp_path,bridge);s=manager.snapshot()
    assert s['schema']=='gnautilus.models.v1' and not s['read_only'] and not s['server_connected']
    assert s['groups']==[]

def test_unresolved_relay_event_closes_without_starting_a_label():
    from gnautilus_ml.data import event_intervals
    events=[dict(kind='marker',phase='task',label='FORWARD',sample_index_end=10,source_epoch=0),dict(kind='marker',phase='pause',sample_index_end=50,source_epoch=0)]
    assert event_intervals(events,100)[0]['end']==50


def test_new_workbench_script_is_in_the_actual_http_asset_map():
    import ast
    tree=ast.parse((Path(__file__).resolve().parents[1]/'hub/app.py').read_text('utf-8'))
    routes=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Dict):
            try:routes.append(ast.literal_eval(node))
            except (ValueError,TypeError):pass
    assert any(r.get('/gnautilus-ml.js')=='gnautilus-ml.js' for r in routes)
