"""Synthetic contract tests; these tests do not measure real EEG accuracy."""
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
from gnautilus_ml.common import atomic,digest,file_hash
from gnautilus_ml.governance import record_group,check_protocol
from gnautilus_ml.public_study import electrical_window,load_prepared


POLICY=dict(max_abs_uv=1000,max_ptp_uv=500,max_centered_rms_uv=100,min_ptp_uv=.1,
            max_line_fraction=.5,line_frequencies_hz=[50,60])


def test_gross_quality_keeps_physical_failures_distinct():
    fs=250;t=np.arange(375)/fs;good=np.stack([10*np.sin(2*np.pi*10*t),8*np.cos(2*np.pi*12*t)])
    assert electrical_window(good,fs,POLICY)['eligible']
    flat=good.copy();flat[0]=0
    assert 'flat' in electrical_window(flat,fs,POLICY)['reasons']
    noise=good.copy();noise[0]=80*np.sin(2*np.pi*50*t)
    assert 'line' in electrical_window(noise,fs,POLICY)['reasons']
    bad=good.copy();bad[0,5]=np.nan
    assert electrical_window(bad,fs,POLICY)['reasons']==['nonfinite']


def test_native_public_groups_do_not_invent_dates_or_relax_personal_wearing():
    row=dict(reader='halt',subject='A',date=None,wearing_id=None,prepared_windows={'schema':'stub'},
             public_metadata=dict(admission='approved',license_receipt='review',source_version='v1',
                                  grouping=dict(level='native_date',native_id='160223'),source_session_verified=True))
    assert record_group(row)==('halt','A','native_date','160223')
    row['public_metadata']['grouping']={}
    with pytest.raises(ValueError,match='Verified public'):record_group(row)
    with pytest.raises(ValueError,match='Unknown subject'):record_group(dict(reader='gnautilus',subject='A'))


def test_prepared_windows_recheck_actual_original_and_label_source(tmp_path):
    source=tmp_path/'raw';source.write_bytes(b'original');labels=tmp_path/'labels';labels.write_bytes(b'labels')
    cache=tmp_path/'windows.npz';x=np.ones((4,2,50),np.float32);y=np.array(['LEFT','RIGHT','LEFT','RIGHT']);np.savez(cache,x=x,y=y)
    identities=[dict(source=file_hash(source),trial=i,start=i*50,end=(i+1)*50,native_label=label) for i,label in enumerate(y)]
    report=tmp_path/'qc.json';atomic(report,dict(admitted_for_pilot=True,source_sha256=file_hash(source),channels=['C3','C4'],window_s=.5,sampling_rate=100,
        ancillary_files=[dict(path=str(labels),sha256=file_hash(labels))],source_samples=200,retained_windows=identities))
    record=dict(path=str(source),source_sha256=file_hash(source),channels=['C3','C4'],sampling_rate=100,
        prepared_windows=dict(schema='gnautilus.public_prepared.v1',path=str(cache),sha256=file_hash(cache),report_path=str(report),report_sha256=file_hash(report)))
    xx,yy,ids=load_prepared(record,.5,.1);assert len(ids)==4;np.testing.assert_array_equal(xx,x)
    labels.write_bytes(b'changed')
    with pytest.raises(ValueError,match='label source changed'):load_prepared(record,.5,.1)
    labels.write_bytes(b'labels');source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='raw public source changed'):load_prepared(record,.5,.1)


def test_development_evaluation_and_best_export_survive_resume(tmp_path):
    torch=pytest.importorskip('torch')
    from gnautilus_ml.algorithms import Classifier
    torch.set_num_threads(2);rng=np.random.default_rng(4);x=rng.normal(size=(16,3,50)).astype(np.float32)
    y=np.tile([0,1],8);groups=np.repeat(['a','b'],8);dev=x+4;events=[]
    with patch('gnautilus_ml.algorithms.authorize_training'),patch('gnautilus_ml.algorithms.compute_device',return_value='cpu'):
        first=Classifier('cnn_gru',['C3','Cz','C4'],100,.5,max_epochs=1);first.checkpoint_path=tmp_path/'cp.pt';first.fit(x,y,groups,progress=events.append,validation=(dev,y,groups))
        resumed=Classifier('cnn_gru',['C3','Cz','C4'],100,.5,max_epochs=2);resumed.checkpoint_path=tmp_path/'cp.pt';resumed.fit(x,y,groups,progress=events.append,validation=(dev,y,groups))
        full=Classifier('cnn_gru',['C3','Cz','C4'],100,.5,max_epochs=2).fit(x,y,groups,validation=(dev,y,groups))
    assert [e['epoch'] for e in events]==[1,2]
    assert all('development_balanced_accuracy' in e and e['gradient_norm_min']>0 for e in events)
    np.testing.assert_allclose(full.arrays['input_mean'],x.mean((0,2),keepdims=True))
    np.testing.assert_allclose(resumed.predict(dev)['probabilities'],full.predict(dev)['probabilities'],atol=1e-7)
    assert resumed.details['selected_epoch']==full.details['selected_epoch']
