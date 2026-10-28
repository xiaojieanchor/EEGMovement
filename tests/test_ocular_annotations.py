import json,hashlib
from pathlib import Path
import pytest

def install(root,end=4000):
    source=root/'data/personal/fixture/.metadata/manual-ocular-waveforms-v1';source.mkdir(parents=True)
    labels=root/'data/personal/fixture/.metadata/manual-ocular-labels-v1'
    tid='a'*64
    meta=dict(trial_id=tid,split='test',label='RIGHT',stream_id='source-1',task_start=1000,task_end=end,
        windows=dict(onset=dict(start=1000,end=2250),offset=dict(start=end-1250,end=end)),default_directions=dict(onset='RIGHT',offset='LEFT'),waveform_file=tid+'.json')
    wave=dict(trial_id=tid,dataset_id='d'*64,sampling_rate=250,windows={key:dict(**value,values=[[0.]*1250]*32,references=[[0.]*1250]*5) for key,value in meta['windows'].items()})
    f=source/(tid+'.json');f.write_text(json.dumps(wave),encoding='utf-8');meta['waveform_sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    manifest=dict(schema='gnautilus.manual_ocular_waveforms.v1',dataset_id='d'*64,title='个人导航数据集',sampling_rate=250,channels=['Fp1','Fp2','AF3','AF4','F7','F8','F3','F4']+['c'+str(i) for i in range(24)],trials=[meta])
    p=source/'manifest.json';p.write_text(json.dumps(manifest),encoding='utf-8')
    runtime=root/'hub/runtime';runtime.mkdir(parents=True)
    (runtime/'ocular_annotation_source.json').write_text(json.dumps(dict(waveform_folder=str(source.relative_to(root)),annotations_folder=str(labels.relative_to(root)),manifest_sha256=hashlib.sha256(p.read_bytes()).hexdigest())),encoding='utf-8')
    return meta,labels

def payload(meta,revision=0):
    return dict(dataset_id='d'*64,trial_id=meta['trial_id'],waveform_sha256=meta['waveform_sha256'],revision=revision,
        phases=dict(onset=dict(status='marked',direction='RIGHT',sample_start=1050,sample_end=1150),offset=dict(status='marked',direction='LEFT',sample_start=3850,sample_end=3950)))

def test_manual_annotation_store_is_available():
    import importlib.util
    assert importlib.util.find_spec('hub.ocular_annotations') is not None

def test_empty_catalog_does_not_create_labels_or_infer_default_ranges(tmp_path):
    from hub.ocular_annotations import OcularAnnotationStore
    meta,labels=install(tmp_path);s=OcularAnnotationStore(tmp_path);row=s.catalog()['trials'][0]
    assert row['annotation']['revision']==0
    assert row['annotation']['phases']['onset']['status']=='pending'
    assert row['default_directions']==dict(onset='RIGHT',offset='LEFT')
    assert not labels.exists()

def test_save_reopen_preserves_absolute_support_role_and_revision_history(tmp_path):
    from hub.ocular_annotations import OcularAnnotationStore,AnnotationConflict
    meta,labels=install(tmp_path);s=OcularAnnotationStore(tmp_path);one=s.save(payload(meta));assert one['revision']==1
    s=OcularAnnotationStore(tmp_path);assert s.trial(meta['trial_id'])['annotation']==one
    two=s.save(payload(meta,1));assert two['revision']==2
    assert len(list(labels.rglob('*.json')))==2
    with pytest.raises(AnnotationConflict):s.save(payload(meta,1))
    export=s.export();assert len(export['events'])==2
    assert export['events'][0]['split']=='test' and not export['events'][0]['training_eligible']
    assert export['events'][0]['stream_id']=='source-1' and export['events'][0]['sample_start']==1050
    assert export['events'][1]['direction']=='LEFT'

@pytest.mark.parametrize('change',[lambda p:p.update(dataset_id='e'*64),lambda p:p.update(waveform_sha256='0'*64),lambda p:p['phases'].update(hold=dict(status='marked')),lambda p:p['phases']['onset'].update(sample_start=999),lambda p:p['phases']['offset'].update(sample_end=4001),lambda p:p['phases']['onset'].update(sample_start=True),lambda p:p['phases']['onset'].update(sample_end=1050),lambda p:p['phases']['onset'].update(direction='FORWARD')])
def test_changed_identity_extra_event_invalid_support_or_non_eye_class_is_rejected(tmp_path,change):
    from hub.ocular_annotations import OcularAnnotationStore
    meta,_=install(tmp_path);s=OcularAnnotationStore(tmp_path);p=payload(meta);change(p)
    with pytest.raises(ValueError):s.save(p)

def test_uncertain_or_absent_is_not_rest_or_automatic_training(tmp_path):
    from hub.ocular_annotations import OcularAnnotationStore
    meta,_=install(tmp_path);s=OcularAnnotationStore(tmp_path);p=payload(meta);p['phases']=dict(onset=dict(status='absent'),offset=dict(status='uncertain'));s.save(p)
    x=s.export();assert not x['events'];assert x['reviews'][0]['phases']['onset']['status']=='absent';assert x['automatic_training'] is False

def test_original_waveform_modification_is_rejected(tmp_path):
    from hub.ocular_annotations import OcularAnnotationStore
    meta,_=install(tmp_path);s=OcularAnnotationStore(tmp_path);folder=tmp_path/'data/personal/fixture/.metadata/manual-ocular-waveforms-v1'
    (folder/meta['waveform_file']).write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError):s.trial(meta['trial_id'])

def test_manifest_path_cannot_escape_data_directory(tmp_path):
    from hub.ocular_annotations import OcularAnnotationStore
    install(tmp_path);p=tmp_path/'hub/runtime/ocular_annotation_source.json';c=json.loads(p.read_text());c['waveform_folder']='../outside';p.write_text(json.dumps(c))
    with pytest.raises(ValueError):OcularAnnotationStore(tmp_path).catalog()

def test_short_trial_overlapping_display_windows_cannot_produce_overlapping_events(tmp_path):
    from hub.ocular_annotations import OcularAnnotationStore
    meta,_=install(tmp_path,end=2300);s=OcularAnnotationStore(tmp_path);p=payload(meta)
    p['phases']['onset'].update(sample_start=1500,sample_end=1600)
    p['phases']['offset'].update(sample_start=1550,sample_end=1650)
    with pytest.raises(ValueError,match='重叠'):s.save(p)

def test_local_http_boundary_and_roundtrip_do_not_need_compute_or_start_actions(tmp_path,monkeypatch):
    import threading,urllib.request,urllib.error
    from types import SimpleNamespace
    from http.server import ThreadingHTTPServer
    from hub import app
    from hub.ocular_annotations import OcularAnnotationStore
    meta,_=install(tmp_path);store=OcularAnnotationStore(tmp_path)
    monkeypatch.setattr(app,'HUB',SimpleNamespace(new_device_mode=False,ocular_annotations=store),raising=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_address[1])
    try:
        with urllib.request.urlopen(url+'/api/ocular-annotations') as r:assert len(json.load(r)['trials'])==1
        with urllib.request.urlopen(url+'/api/ocular-annotations/trials/'+meta['trial_id']) as r:assert len(json.load(r)['waveform']['windows']['onset']['values'])==32
        def post(headers):return urllib.request.Request(url+'/api/ocular-annotations/save',data=json.dumps(payload(meta)).encode(),headers=headers,method='POST')
        with pytest.raises(urllib.error.HTTPError) as missing:urllib.request.urlopen(post({'Content-Type':'application/json'}))
        assert missing.value.code==403
        with urllib.request.urlopen(post({'Content-Type':'application/json','X-EEGHub':'1'})) as r:assert json.load(r)['revision']==1
        with pytest.raises(urllib.error.HTTPError) as conflict:urllib.request.urlopen(post({'Content-Type':'application/json','X-EEGHub':'1'}))
        assert conflict.value.code==409
        with urllib.request.urlopen(url+'/api/ocular-annotations/export') as r:assert len(json.load(r)['events'])==2
    finally:server.shutdown();server.server_close();thread.join(timeout=3)
