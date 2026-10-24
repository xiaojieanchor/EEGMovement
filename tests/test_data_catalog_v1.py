import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_data_catalog_v1 import build,record_index,write


class CatalogTests(unittest.TestCase):
    def test_model_check_reports_and_original_recording_are_linked_without_assigning_training(self):
        raw=self.raw('20261018-160000-abcd')
        with raw.open('a',encoding='utf-8') as stream:stream.write(json.dumps(dict(kind='model_check',surface='desktop',placement_id='observed-wear',event='start'))+'\n')
        directory=self.root/'outputs/model_checks/2026-10-18'/('a'*32);directory.mkdir(parents=True)
        report=dict(check_id='a'*32,recording=raw.stem,mode='directions',state='completed',role='unassigned_model_check',
            model_binding=dict(model=['actual-model','a'*64]),raw_recording=dict(sha256=hashlib.sha256(raw.read_bytes()).hexdigest()))
        (directory/'report.json').write_text(json.dumps(report),encoding='utf-8')
        catalog=build(self.root,'',self.root/'missing-public')
        self.assertEqual(catalog['model_checks'][0]['recording_id'],raw.stem)
        self.assertEqual(catalog['model_checks'][0]['model_id'],'actual-model')
        self.assertEqual(catalog['recordings'][0]['training_role'],'unassigned')
        self.assertTrue(catalog['recordings'][0]['known_inspected'])
        self.assertIn('model_check',catalog['recordings'][0]['conditions'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for folder in ['outputs/sessions','configs','data/personal']:(self.root/folder).mkdir(parents=True)
        (self.root/'configs/device_channels.json').write_text(json.dumps(dict(disabled_channels_1based=[1,6],active_channels_1based=[2,3,4,5,7])))

    def raw(self,name):
        p=self.root/'outputs/sessions'/(name+'.jsonl')
        rows=[dict(kind='session',sfreq=250,channels=['Ch'+str(i+1) for i in range(7)],disabled_channels_1based=[1,6]),
            dict(kind='self_paced_session',donning_id='measured-id',input_surface='desktop'),
            dict(kind='eeg',samples_uv=[[1,2,3,4,5,6,7]]*25,source_epoch=1)]
        p.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
        return p

    def test_active_and_unknown_liveness_do_not_read_or_hash_raw_payload(self):
        p=self.raw('20261018-120000-abcd')
        with patch('pathlib.Path.open',side_effect=AssertionError('must not read active file')):
            self.assertEqual(record_index(p,p.stem,{})['status'],'active_or_unconfirmed_skip')
            self.assertIsNone(record_index(p,None,{})['sha256'])

    def test_sort_original_hash_unknown_roles_and_unchanged_raw(self):
        later=self.raw('20261018-130000-abcd');earlier=self.raw('20261017-120000-abcd')
        before={p:p.read_bytes() for p in [earlier,later]}
        catalog=build(self.root,later.stem,self.root/'missing-public')
        self.assertEqual([x['recording_id'] for x in catalog['recordings']],[earlier.stem,later.stem])
        row=catalog['recordings'][0]
        self.assertEqual(row['sha256'],hashlib.sha256(before[earlier]).hexdigest())
        self.assertEqual(row['raw_seconds'],.1);self.assertEqual(row['training_role'],'unassigned')
        self.assertEqual(row['recorded_disabled_channels_1based'],[1,6])
        self.assertEqual(row['donning_ids'],['measured-id']);self.assertFalse(row['independent_test'])
        write(self.root,catalog)
        self.assertTrue((self.root/'data/catalog/catalog.json').exists())
        for p,data in before.items():self.assertEqual(p.read_bytes(),data)

    def test_hash_mismatch_does_not_inherit_old_role_and_malformed_is_quarantined(self):
        p=self.raw('20261018-120000-abcd')
        row=record_index(p,'',{p.stem:dict(role='train',source_sha256='wrong')})
        self.assertEqual(row['training_role'],'unassigned');self.assertIn('canonical_source_hash_mismatch_no_role_assigned',row['warnings'])
        with p.open('a',encoding='utf-8') as f:f.write('{truncated')
        row=record_index(p,'',{})
        self.assertEqual(row['status'],'invalid_requires_review');self.assertEqual(row['invalid_rows'],1)

    def test_changing_closed_file_is_not_sealed_and_duplicate_sources_remain_linked(self):
        p=self.raw('20261018-120000-abcd');stamp=(p.stat().st_size,p.stat().st_mtime_ns)
        with patch('scripts.build_data_catalog_v1.fingerprint',side_effect=[stamp,(stamp[0]+1,stamp[1])]):
            row=record_index(p,'',{})
        self.assertEqual(row['status'],'changed_during_inventory_skip');self.assertIsNone(row['sha256'])
        twin=self.root/'outputs/sessions/20261018-130000-efgh.jsonl';twin.write_bytes(p.read_bytes())
        catalog=build(self.root,'',self.root/'missing-public')
        self.assertEqual(catalog['duplicate_raw_groups'][0]['recordings'],[p.stem,twin.stem])


if __name__=='__main__':unittest.main()
