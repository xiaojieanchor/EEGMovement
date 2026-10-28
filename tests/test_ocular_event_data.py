import copy, unittest
from gnautilus_ml.navigation_contract import CHANNELS
from gnautilus_ml.ocular_event_data import compile_annotations

def fixture():
    rows=[]
    for i,role in enumerate(['train','validation','test']):
        tid=str(i+1)*64
        rows.append(dict(trial_id=tid,split=role,stream_id='s',task_start=10000*i+1000,task_end=10000*i+4000,
            waveform_sha256='a'*64,label='FORWARD',annotation=dict(revision=1,phases={})))
    for q in rows:
        q['annotation'].update(dataset_id='b'*64,trial_id=q['trial_id'],split=q['split'],stream_id=q['stream_id'],
            waveform_sha256=q['waveform_sha256'],task_range=[q['task_start'],q['task_end']],sampling_rate=250)
        q['annotation']['phases']=dict(onset=dict(status='marked',direction='UP',sample_start=q['task_start']+10,sample_end=q['task_start']+180,human_confirmed=True),
            offset=dict(status='marked',direction='DOWN',sample_start=q['task_end']-70,sample_end=q['task_end'],human_confirmed=True))
    c=dict(dataset_id='b'*64,sampling_rate=250,channels=list(CHANNELS),trials=rows)
    e=dict(schema='gnautilus.manual_ocular_export.v1',dataset_id=c['dataset_id'],reviews=[q['annotation'] for q in rows])
    return c,e

class TestOcularEventData(unittest.TestCase):
    def test_same_direction_pools_keep_roles_and_partial_tail(self):
        c,e=fixture();d=compile_annotations(c,e)
        self.assertEqual(len(d['segments']),6)
        for s in d['segments']:
            self.assertEqual(s['split'],c['trials'][int(s['trial_id'][0])-1]['split'])
            self.assertEqual(s['possibly_right_censored'],s['phase']=='offset')
        self.assertEqual(d['channels'][15],'Cz');self.assertEqual(d['channels'][18],'C5')
        self.assertEqual(d['class_labels'],['NO_EYE','UP','DOWN','LEFT','RIGHT'])

    def test_only_absent_onset_is_baseline_never_navigation_rest(self):
        c,e=fixture();e['reviews'][0]['phases']['offset']={'status':'absent'}
        e['reviews'][1]['phases']['onset']={'status':'absent'}
        d=compile_annotations(c,e);r=[s for s in d['segments'] if s['class_label']=='NO_EYE']
        self.assertEqual(len(r),1);self.assertEqual(r[0]['split'],'validation')
        self.assertEqual((r[0]['start'],r[0]['end']),(11000,11500));self.assertFalse(r[0]['navigation_rest'])
        self.assertFalse(any(s['class_label']=='NO_EYE' and s['split']=='train' for s in d['segments']))

    def test_unknown_never_expands_to_hold_labels(self):
        c,e=fixture();e['reviews'][0]['phases']={'onset':{'status':'uncertain'},'offset':{'status':'pending'}}
        d=compile_annotations(c,e);self.assertFalse(any(s['split']=='train' for s in d['segments']))

    def test_reject_montage_review_identity_or_extra_event(self):
        c,e=fixture()
        for kind in ['channels','role','range','duplicate','third']:
            cc,ee=copy.deepcopy(c),copy.deepcopy(e)
            if kind=='channels':cc['channels'][15],cc['channels'][18]=cc['channels'][18],cc['channels'][15]
            if kind=='role':ee['reviews'][0]['split']='test'
            if kind=='range':ee['reviews'][0]['phases']['onset']['sample_end']=5000
            if kind=='duplicate':ee['reviews'].append(copy.deepcopy(ee['reviews'][0]))
            if kind=='third':ee['reviews'][0]['phases']['middle']={'status':'absent'}
            with self.subTest(kind=kind),self.assertRaises(ValueError):compile_annotations(cc,ee)

    def test_no_eye_cannot_overlap_other_manual_event(self):
        c,e=fixture();a=e['reviews'][0];a['phases']['onset']={'status':'absent'}
        a['phases']['offset'].update(sample_start=1400,sample_end=1600)
        # The offset itself is outside its last-five-second review support.
        with self.assertRaises(ValueError):compile_annotations(c,e)

if __name__=='__main__':unittest.main()
