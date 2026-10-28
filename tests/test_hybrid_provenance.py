import copy
import pytest
from gnautilus_ml.common import atomic, file_hash
from gnautilus_ml.navigation_contract import CHANNELS


def dataset(tmp_path):
    folder=tmp_path/'personal';path=folder/'.metadata/canonical/stream1/descriptor.original.json'
    descriptor=dict(device_serial='synthetic-device',sampling_rate=250,reference='dedicated_REF_unknown',ground='dedicated_GND_unknown',configuration=dict(InputSignal='EEG',CAR=False,NoiseReduction=False,Channels=[dict(BipolarChannel=-1,Enabled=True,Sensitivity=187500,BandpassFilterIndex=1,NotchFilterIndex=0) for _ in range(32)],filters=dict(bandpass_hz=[.5,100],notch_hz=50)))
    atomic(path,descriptor);rel=path.relative_to(folder).as_posix()
    return dict(folder=str(folder),channels=CHANNELS,contract_review=dict(attestation='User confirmed all 32 positions and swap16/19; dedicated REF/GND outside 32; physical REF/GND sites remain unknown',original_descriptors_unchanged=True),episodes=[dict(contract_id='native_first',stream_id='stream1')],source_receipt=dict(files={rel:file_hash(path)})),path,descriptor


def test_missing_legacy_metadata_is_rebuilt_from_hashed_descriptor_without_relabeling_columns(tmp_path):
    from gnautilus_ml.hybrid_provenance import native_export_metadata
    data,path,descriptor=dataset(tmp_path);before=path.read_bytes();result=native_export_metadata(data)
    assert result['input_contract']['channels']==CHANNELS
    assert result['input_contract']['channels'][15]=='Cz' and result['input_contract']['channels'][18]=='C5'
    assert result['montage_attestation']['source']=='explicit_user_confirmation'
    assert result['input_contract']['unknown_reference_site'] is True
    assert result['input_contract']['signal_identity']['reference']=='dedicated_REF_unknown'
    assert path.read_bytes()==before


def test_changed_original_descriptor_and_unconfirmed_montage_cannot_be_exported(tmp_path):
    from gnautilus_ml.hybrid_provenance import native_export_metadata
    data,path,descriptor=dataset(tmp_path);missing=copy.deepcopy(data);missing['contract_review']['attestation']='unknown'
    with pytest.raises(ValueError,match='confirmed'):native_export_metadata(missing)
    descriptor['configuration']['CAR']=True;atomic(path,descriptor)
    with pytest.raises(ValueError,match='hash'):native_export_metadata(data)


def test_different_native_contracts_cannot_silently_be_merged(tmp_path):
    from gnautilus_ml.hybrid_provenance import native_export_metadata
    data,path,descriptor=dataset(tmp_path);other=path.parent.parent/'stream2/descriptor.original.json';descriptor['configuration']['CAR']=True;atomic(other,descriptor);data['episodes'].append(dict(contract_id='native_second',stream_id='stream2'));data['source_receipt']['files'][other.relative_to(data['folder']).as_posix()]=file_hash(other)
    with pytest.raises(ValueError,match='differ'):native_export_metadata(data)
