import time
from unittest.mock import patch

import numpy as np
import pytest

from hub.stream_observations import ReceptionObservations
from gnautilus_acquisition.contract import block_frame
from tests.test_gnautilus_mode_recovery import capture_fixture
from tests.test_gnautilus_transport import descriptor


def test_measured_rate_is_counted_not_copied_from_config_and_survives_delay():
    metric=ReceptionObservations()
    assert metric.snapshot(10)['measured_hz'] is None
    for n in range(101):metric.accept('stream',n*10,10,n+1,10+n*.05+(.002 if n%2 else 0))
    row=metric.snapshot(15.1)
    assert row['measured_hz']==pytest.approx(200,abs=1)
    assert row['loss_pct']==0 and row['received_samples']==1010
    assert metric.snapshot(16)['measured_hz']==row['measured_hz']
    assert metric.snapshot(20)['state']=='retained'
    assert metric.snapshot(20)['measured_hz']==row['measured_hz']


def test_loss_uses_sample_ranges_and_ignores_unseen_initial_prefix_and_duplicates():
    metric=ReceptionObservations();metric.accept('stream',1000000,100,1,10)
    assert metric.snapshot(10)['loss_pct'] is None
    metric.accept('stream',1000200,100,3,11)
    assert metric.snapshot(11)['missing_samples']==100
    assert metric.snapshot(11)['loss_pct']==pytest.approx(100/3,abs=.001)
    metric.accept('stream',1000200,100,3,12)
    assert metric.snapshot(12)['received_samples']==200
    metric.accept('stream',1000300,100,4,13)
    assert metric.snapshot(13)['loss_pct']==25
    metric.accept('other-stream',0,100,1,14)
    assert metric.snapshot(14)['missing_samples']==0 and metric.snapshot(14)['loss_pct'] is None


def test_runtime_reports_actual_rate_quality_and_loss_without_contact_claim(tmp_path):
    hub,runtime=capture_fixture(tmp_path);d=descriptor('gtec_live',fs=250)
    try:
        for seq in range(1,102):
            values=np.tile((15*np.sin(2*np.pi*10*np.arange((seq-1)*10,seq*10)/250))[:,None],(1,32)).astype('float32')
            values[:,0]=0
            h,b=block_frame(d,seq,(seq-1)*10,values)
            with patch('hub.gnautilus_runtime.time.monotonic',return_value=10+seq*.04):runtime.accept(d,h,values)
        with patch('hub.gnautilus_runtime.time.monotonic',return_value=14.05):
            runtime.observe_electrical();snapshot=runtime.snapshot()
        assert snapshot['source']['sfreq_measured']==pytest.approx(250,abs=.5)
        assert snapshot['source']['loss_pct']==0
        q=snapshot['gnautilus']['observations']['electrical']
        assert q['good_channels']==31 and q['total_channels']==32
        assert q['channels'][0]['reasons']==['flatline']
        assert q['channels'][1]['column_index']==1
        assert not q['contact_quality_known'] and not snapshot['quality']['contact_quality_known']
        with patch('hub.gnautilus_runtime.time.monotonic',return_value=18):held=runtime.snapshot()
        assert held['gnautilus']['observations']['electrical']['state']=='retained'
        assert held['gnautilus']['observations']['electrical']['good_channels']==31
        assert held['source']['sfreq_measured']==snapshot['source']['sfreq_measured']
        h,b=block_frame(d,200,1990,values,mode='backfill');before=runtime.reception.received
        runtime.accept(d,h,values)
        assert runtime.reception.received==before
    finally:runtime.close()
