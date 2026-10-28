"""Pinned deployment metadata, with test-only artifact bytes and no real fitting."""
import copy
from unittest.mock import patch
import pytest
from gnautilus_ml.common import atomic,digest,file_hash,read_json
from gnautilus_ml.experimental_live import promote,authorized_experimental,SOURCE_ID,WEIGHTS_SHA256


def test_only_exact_authorized_source_can_be_promoted(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'weights.npz').write_bytes(b'explicit synthetic test bytes')
    meta=dict(schema='gnautilus.model.v1',runtime_schema='gnautilus.hybrid_navigation.v3',runtime_accepted=False,independent_test=False,
              weights_sha256=file_hash(source/'weights.npz'),evaluation_stage='full_personal_adaptation_exploratory',metrics={'development':{'score':.2}})
    meta['model_id']=digest(meta);atomic(source/'manifest.json',meta)
    with pytest.raises(ValueError):promote(source,tmp_path/'models')
    # Only this synthetic test changes the whitelist. Production has no override.
    with patch('gnautilus_ml.experimental_live.SOURCE_ID',meta['model_id']),patch('gnautilus_ml.experimental_live.WEIGHTS_SHA256',meta['weights_sha256']):
        target=promote(source,tmp_path/'models');d=read_json(target/'manifest.json')
        assert authorized_experimental(d) and d['runtime_accepted'] is True and d['independent_test'] is False
        assert d['evaluation_stage']==meta['evaluation_stage'] and d['metrics']==meta['metrics']
        assert file_hash(target/'weights.npz')==meta['weights_sha256'] and read_json(source/'manifest.json')==meta
        assert target==promote(source,tmp_path/'models')


@pytest.mark.parametrize('field,value',[('weights_sha256','changed'),('runtime_schema','gnautilus.hybrid_navigation.v2'),('independent_test',True)])
def test_deployment_stage_does_not_qualify_changed_weights_or_other_models(field,value):
    meta=dict(runtime_schema='gnautilus.hybrid_navigation.v3',weights_sha256=WEIGHTS_SHA256,independent_test=False,
        deployment=dict(stage='experimental_live_v1',source_model_id=SOURCE_ID,authorization='explicit_user_request_20261026'))
    assert authorized_experimental(meta);meta[field]=value;assert not authorized_experimental(meta)
