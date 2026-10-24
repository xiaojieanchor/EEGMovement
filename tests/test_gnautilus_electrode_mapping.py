"""User port numbers are one-based; SDK physical_index remains zero-based."""
import json
from pathlib import Path
import numpy as np
from gnautilus_acquisition.sdk import ParticipantReader
from tests.test_gnautilus_sdk import FakeDriver

PATH=Path(__file__).resolve().parents[1]/'configs/gnautilus_layout32_hybrid_candidate.example.json'


def test_current_port16_and_port19_swap_is_applied_without_reordering_raw_columns():
    layout=json.loads(PATH.read_text(encoding='utf-8'))
    assert set(layout['channels'])==set(map(str,range(32)))
    assert layout['channels']['15']=='Cz' and layout['channels']['18']=='C5'
    # Confirmation below is only this synthetic fixture, not real cap verification.
    layout.update(verified=True,reference='FIXTURE_REF',ground='FIXTURE_GND',wearing_id='SYNTHETIC')
    driver=FakeDriver()
    for i,c in enumerate(driver.config['Channels']):c['Enabled']=i in (15,18)
    reader=ParticipantReader('unused','NA-123',layout,driver=driver)
    try:
        d=reader.open();eeg=[c for c in d['channels'] if c['kind']=='EEG']
        assert [(c['id'],c['physical_index'],c['name']) for c in eeg]==[('EEG16',15,'Cz'),('EEG19',18,'C5')]
        np.testing.assert_array_equal(reader.read(),driver.buffer)
        assert not any('acquisition' in str(c) or 'set_' in str(c) for c in driver.calls)
    finally:reader.close()


def test_mapping_delta_does_not_claim_all_32_positions_or_reference_verified():
    layout=json.loads(PATH.read_text(encoding='utf-8'))
    assert layout['verified'] is False and layout['reference'] is None and layout['ground'] is None
