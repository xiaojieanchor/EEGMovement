"""Synthetic software checks only; no device, real EEG, remote server or VR motion."""
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest


def trials():
    rng=np.random.default_rng(42);fs=128;channels=['C3','Cz','C4','Fp1','Fp2','F7','F8']
    t=np.arange(192)/fs;xs=[];ys=[];groups=[]
    for group in range(4):
        for label in range(5):
            for repeat in range(4):
                x=rng.normal(0,1,(7,192))
                if label:x[(label-1)%3]+=12*np.sin(2*np.pi*(9+label*3)*t)
                x[3:]+=label*3+np.sin(2*np.pi*.8*t)*label
                xs.append(x);ys.append(label);groups.append('session'+str(group))
    return np.asarray(xs),np.asarray(ys),np.asarray(groups),channels,fs


def test_jobs_and_registration_survive_restart(tmp_path):
    from gnautilus_ml.registry import Registry
    r=Registry(tmp_path/'state.sqlite');job=r.create_job({'algorithm':'bandpower_lda'},'request-1')
    assert r.create_job({'algorithm':'bandpower_lda'},'request-1')['id']==job['id']
    with pytest.raises(ValueError):r.create_job({'algorithm':'gaze_lda'},'request-1')
    with pytest.raises(ValueError):r.transition(job['id'],'completed')
    r.transition(job['id'],'preparing');r.event(job['id'],'working',{'phase':'verify'})
    r=Registry(tmp_path/'state.sqlite');assert r.job(job['id'])['state']=='preparing'
    assert r.events(job['id'],0)[-1]['data']['phase']=='verify'


@pytest.mark.parametrize('algorithm',['bandpower_lda','csp_lda','gaze_lda','hybrid'])
def test_classifiers_export_load_match_and_do_not_mutate(tmp_path,algorithm):
    from gnautilus_ml.algorithms import Classifier
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    x,y,groups,channels,fs=trials();train=groups!='session3'
    with patch('gnautilus_ml.algorithms.authorize_training'):
        m=Classifier(algorithm,channels,fs).fit(x[train],y[train],groups[train])
    before=m.predict(x[~train]);assert before['probabilities'].shape==(20,5)
    path=save_artifact(tmp_path/'models',m,{'dataset_sha256':'a'*64,'protocol_sha256':'b'*64,'job_id':'test','evaluation_stage':'synthetic'})
    loaded,meta=load_artifact(path);after=loaded.predict(x[~train])
    np.testing.assert_allclose(before['probabilities'],after['probabilities'],atol=1e-6)
    assert meta['algorithm']==algorithm and meta['runtime_accepted'] is False
    with pytest.raises(ValueError):load_artifact(path,expected_channels=channels[::-1])
    w=path/'weights.npz';w.write_bytes(w.read_bytes()+b'changed')
    with pytest.raises(ValueError):load_artifact(path)


def test_fit_has_no_production_bypass():
    from gnautilus_ml.algorithms import Classifier
    x,y,g,c,fs=trials()
    with pytest.raises(RuntimeError):Classifier('csp_lda',c,fs).fit(x,y,g)


def test_quality_is_distinct_from_contact_and_preserves_eye_polarity():
    from gnautilus_ml.quality import inspect_signal
    x,y,g,c,fs=trials();policy={'rail_uv':20000,'flat_ptp_uv':.001,'max_rms_uv':5000,'max_line_ratio':.8}
    q=inspect_signal(x[1],c,fs,policy)
    assert q['electrical_valid'] and q['contact_quality_known'] is False
    x[1,0]=0;assert not inspect_signal(x[1],c,fs,policy)['electrical_valid']


def test_unknown_and_release_do_not_become_rest():
    from gnautilus_ml.data import event_intervals
    events=[{'kind':'marker','phase':'task','label':'LEFT','sample_index_end':200,'source_epoch':1},
            {'kind':'marker','phase':'release','label':'LEFT','sample_index_end':600,'source_epoch':1}]
    spans=event_intervals(events,1000,epoch=1)
    assert spans==[{'start':200,'end':600,'label':'LEFT','mode':'unspecified','gaze':None,'limb':None}]


def test_public_native_labels_are_not_navigation_aliases():
    from gnautilus_ml.readers import native_event_label
    assert native_event_label('eegmmidb',2,run=6)=='BOTH_FEET_MI'
    assert native_event_label('eegmmidb',2,run=4)=='RIGHT_HAND_MI'
    assert native_event_label('halt',4)=='LEFT_LEG_MI'
    assert native_event_label('halt',0)=='UNKNOWN'
    with pytest.raises(ValueError):native_event_label('eegmmidb',2,run=3)
