"""Synthetic boundary regressions; no device or real data is used."""
from unittest.mock import patch
import numpy as np
import pytest


def test_pending_jobs_are_not_hidden_by_200_completed_rows(tmp_path):
    from gnautilus_ml.registry import Registry
    r=Registry(tmp_path/'state.sqlite');first=r.create_job({'x':0},'first')
    for i in range(205):
        row=r.create_job({'x':i+1},str(i));r.transition(row['id'],'cancelled')
    assert r.pending_jobs()[0]['id']==first['id']


def test_live_artifact_requires_exact_navigation_class_order(tmp_path):
    from tests.test_gnautilus_ml_v1 import trials
    from gnautilus_ml.algorithms import Classifier
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    from gnautilus_ml.common import read_json,atomic,digest
    x,y,g,c,fs=trials()
    with patch('gnautilus_ml.algorithms.authorize_training'):
        m=Classifier('bandpower_lda',c,fs).fit(x,y,g)
    p=save_artifact(tmp_path,m,{'runtime_accepted':True,'class_labels':['RIGHT','LEFT','BACKWARD','FORWARD','REST']})
    with pytest.raises(ValueError,match='class'):
        load_artifact(p)


def test_checkpoint_interrupted_receipt_preserves_previous_commit(tmp_path):
    from gnautilus_ml.network import fit_network
    from tests.test_gnautilus_ml_v1 import trials
    from gnautilus_ml.common import read_json
    torch=pytest.importorskip('torch')
    torch.set_num_threads(2);x,y,_,_,_=trials();x=x[:40];y=y[:40];cp=tmp_path/'latest.pt'
    fit_network(x,y,np.unique(y),17,1,'cpu',None,cp)
    before=read_json(cp.with_suffix('.json'))
    with patch('gnautilus_ml.common.atomic',side_effect=OSError('injected before pointer commit')):
        with pytest.raises(OSError):fit_network(x,y,np.unique(y),17,2,'cpu',None,cp)
    assert read_json(cp.with_suffix('.json'))==before
    arrays,_=fit_network(x,y,np.unique(y),17,2,'cpu',None,cp)
    expected,_=fit_network(x,y,np.unique(y),17,2,'cpu',None)
    for k in expected:np.testing.assert_allclose(arrays[k],expected[k],atol=1e-7)


def test_replay_requires_idle_compute(tmp_path):
    from gnautilus_ml.service import ModelService
    s=ModelService(tmp_path,{},tmp_path)
    s.runner.resource.acquire()
    try:
        with pytest.raises(ValueError,match='resource'):
            s.post('/models/replay',{'dataset_id':'a'*64})
    finally:s.runner.resource.release()


def test_cancelled_stale_queue_snapshot_does_not_kill_scheduler(tmp_path):
    from gnautilus_ml.service import ModelService
    s=ModelService(tmp_path,{},tmp_path);j=s.registry.create_job({'x':1},'a')
    s.runner.inference_enabled=True;s.runner.cancel(j['id']);s.runner.run(j['id'])
    assert s.registry.job(j['id'])['state']=='cancelled'


def test_bridge_status_exposes_age_for_quest_contract(tmp_path):
    import time
    from pathlib import Path
    from gnautilus_ml.ssh import ComputeBridge
    from gnautilus_ml.common import atomic
    atomic(tmp_path/'configs/gnautilus_compute.json',{'host':'fixture','port':5522})
    b=ComputeBridge(tmp_path);b.status.update(worker_connected=True,last_health_time=time.monotonic())
    assert 0<=b.snapshot()['health_age_ms']<100


def _intent_fixture():
    import time
    from unittest.mock import Mock
    from hub.ml_runtime import MLIntent
    hub=Mock();hub.epoch=4;hub.stop_event.wait.side_effect=[False,True]
    d={'sampling_rate':128,'channels':[{'name':'C3','kind':'EEG'},{'name':'Validation','kind':'VALIDATION'}]}
    history=np.ones((192,2),dtype='float32')
    payload=dict(descriptor=d,sample_index_end=192,stream_id='stream',source_epoch=4,samples=history,input_origin_monotonic=time.monotonic())
    hub.gnautilus_runtime.model_input.return_value=payload
    manager=Mock();manager.cached={'models':[dict(model_id='m',window_s=1.5,algorithm='bandpower_lda')]}
    intent=MLIntent(hub,manager);intent.model_id='m';intent.inference_enabled=True
    return intent,hub,manager,payload


def test_invalid_older_sample_blocks_whole_history_even_when_latest_block_valid():
    intent,hub,manager,payload=_intent_fixture();hub.quality={'device_valid':True};payload['samples'][0,1]=0
    intent.loop();assert intent.latest is None and 'device-invalid' in intent.error
    manager.bridge.call.assert_not_called()


def test_inflight_prediction_cannot_publish_after_stop():
    intent,hub,manager,payload=_intent_fixture()
    def returning(*a,**kw):
        intent.invalidate('source_lost')
        return dict(source_epoch=4,sample_index_end=192,model_id='m',probabilities=[.01,.95,.01,.01,.02])
    manager.bridge.call.side_effect=returning
    intent.loop();assert not intent.inference_enabled and intent.latest is None


def test_failed_model_switch_keeps_previous_model_and_stops(tmp_path):
    from gnautilus_ml.service import ModelService
    s=ModelService(tmp_path,{},tmp_path);previous=object();s.model=previous;s.runner.inference_enabled=True
    with pytest.raises(ValueError):s.post('/models/activate',{'id':'a'*64})
    assert s.model is previous and not s.runner.inference_enabled


def test_causal_filter_prefix_does_not_change_with_future():
    from gnautilus_ml.algorithms import filtered
    x=np.random.default_rng(17).normal(size=(2,3,256))
    a=filtered(x[:,:,:128],128,(8,12));b=filtered(x,128,(8,12))
    np.testing.assert_array_equal(a,b[:,:,:128])


def test_ssh_socket_retains_response_file_after_http10_header_close():
    import http.client
    from gnautilus_ml.ssh import TunnelSocket
    class Channel:
        def __init__(self):self.parts=[b'HTTP/1.0 200 OK\r\nContent-Length: 11\r\n\r\n',b'{"ok":true}'];self.closed=False
        def recv(self,n):
            if self.closed or not self.parts:return b''
            p=self.parts.pop(0);self.parts.insert(0,p[n:]) if len(p)>n else None;return p[:n]
        def close(self):self.closed=True
        def sendall(self,data):pass
        def gettimeout(self):return 10
        def settimeout(self,value):pass
    channel=Channel();connection=http.client.HTTPConnection('fixture');connection.sock=TunnelSocket(channel)
    connection.request('GET','/health');response=connection.getresponse()
    assert connection.sock is None and not channel.closed
    assert response.read()==b'{"ok":true}' and channel.closed
