"""A recovery descriptor must not replace the selected live runtime contract."""
import collections
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from eeg_relay.protocol import pack
from gnautilus_acquisition.contract import block_frame
from hub.gnautilus_runtime import GnautilusRuntime
from tests.test_gnautilus_transport import descriptor


def test_historical_connection_descriptor_does_not_reset_live_buffers(tmp_path):
    hub = SimpleNamespace(lock=threading.RLock(),record_lock=threading.RLock(),raw=collections.deque(),
        filtered=collections.deque(),notched=collections.deque(),epoch=0,sample_index=0,last_packet=None,
        source_mode='paused',connected=False,recorder=None,recording_name=None,recording_samples=0,
        event=Mock(),vr=SimpleNamespace(stop_test=Mock()),intent_control=SimpleNamespace(configure=Mock()))
    runtime=GnautilusRuntime(hub,tmp_path,port=0,lan_ws_port=-1)
    live=descriptor('gtec_live');old=dict(live,stream_id='e'*32)
    h,b=block_frame(live,1,0,np.ones((8,32),np.float32))
    runtime.accept(live,dict(h,ingest_transport='lan_ws'),np.frombuffer(b,dtype='<f4').reshape(-1,32))
    epoch=hub.epoch
    class Connection:
        def __iter__(self):
            return iter([pack(dict(kind='descriptor',agent_id='c'*32,descriptor=old))])
        def send(self,frame):pass
    try:
        runtime.lan_ws._handle_ws(Connection())
        assert runtime.descriptor['stream_id']==live['stream_id']
        assert hub.epoch==epoch and len(hub.raw)==8
    finally:runtime.close()
