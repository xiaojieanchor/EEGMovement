"""Public readers retain native semantics. Admission/license is a separate receipt."""
from pathlib import Path
import numpy as np
from scipy.io import loadmat

def native_event_label(dataset,event,run=None):
    if dataset=='halt':return {1:'LEFT_HAND_MI',2:'RIGHT_HAND_MI',3:'PASSIVE',4:'LEFT_LEG_MI',5:'TONGUE_MI',6:'RIGHT_LEG_MI'}.get(int(event),'UNKNOWN')
    if dataset=='eegmmidb':
        if run not in [4,6,8,10,12,14]:raise ValueError('Motor execution/baselines are not imagery runs')
        return ({0:'TASK_REST',1:'LEFT_HAND_MI',2:'RIGHT_HAND_MI'} if run in [4,8,12] else {0:'TASK_REST',1:'BOTH_HANDS_MI',2:'BOTH_FEET_MI'}).get(int(event),'UNKNOWN')
    if dataset=='bci_iv_2a':return {769:'LEFT_HAND_MI',770:'RIGHT_HAND_MI',771:'BOTH_FEET_MI',772:'TONGUE_MI'}.get(int(event),'UNKNOWN')
    raise ValueError('Native labels require a supported dataset')

def read_public(dataset,path,metadata):
    if metadata.get('admission')!='approved' or not metadata.get('license_receipt') or not metadata.get('source_version'):raise ValueError('Public data lacks a versioned admission/license receipt')
    p=Path(path)
    if dataset=='halt':
        if not p.name.startswith('HaLT-'):raise ValueError('Kaya CLA/NoMT/FreeForm are different paradigms')
        o=loadmat(p,simplify_cells=True)['o'];x=np.asarray(o['data']);names=metadata['channel_names'];cols=metadata['eeg_column_indices']
        if len(names)!=len(cols) or x.ndim!=2:raise ValueError('Explicit HaLT channel order required from description.txt')
        return dict(samples=x[:,cols].T,channels=names,sampling_rate=float(o['sampFreq']),native_labels=np.array([native_event_label('halt',v) for v in np.ravel(o['marker'])]))
    if dataset in ['bci_iv_2a','eegmmidb']:
        import mne
        raw=(mne.io.read_raw_gdf(p,eog=[22,23,24],preload=True,verbose=False) if dataset=='bci_iv_2a' else mne.io.read_raw_edf(p,preload=True,verbose=False))
        try:
            eeg=raw.copy().pick('eeg');events=[]
            if dataset=='bci_iv_2a' and len(eeg.ch_names)!=22:raise ValueError('Official GDF must have 22 EEG and 3 excluded EOG channels')
            for onset,duration,name in zip(raw.annotations.onset,raw.annotations.duration,raw.annotations.description):
                code=({'T0':0,'T1':1,'T2':2}.get(name) if dataset=='eegmmidb' else int(name) if name.isdigit() else None)
                if code is not None:events.append(dict(sample=round(onset*raw.info['sfreq']),duration=duration,label=native_event_label(dataset,code,metadata.get('run'))))
            return dict(samples=eeg.get_data()*1e6,channels=eeg.ch_names,sampling_rate=raw.info['sfreq'],events=events)
        finally:raw.close()
    if dataset=='bci_iv_1':
        o=loadmat(p,simplify_cells=True);nfo=o['nfo'];mrk=o.get('mrk');names=[str(v) for v in np.ravel(nfo['clab'])]
        if metadata.get('physiological_source') is not True:raise ValueError('Artificial/unknown subjects excluded from physiological training')
        return dict(samples=np.asarray(o['cnt'],float).T*.1,channels=names,sampling_rate=float(nfo['fs']),native_markers=mrk,native_classes=np.asarray(nfo['classes']).tolist(),continuous_no_control=True)
    if dataset=='openbmi':
        o=loadmat(p,simplify_cells=True);key=metadata.get('partition')
        if key not in ['EEG_MI_train','EEG_MI_test']:raise ValueError('Explicit original OpenBMI partition required')
        v=o[key]
        return dict(samples=np.asarray(v['x']).T,channels=[str(s) for s in np.ravel(v['chan'])],sampling_rate=float(v['fs']),native_onsets=np.ravel(v['t']),native_labels=np.ravel(v['y_dec']),native_class_table=v['class'])
    if dataset=='eegeyenet':
        # Official prepared benchmark NPZ: metadata binds exact axes/label columns.
        with np.load(p,allow_pickle=False) as v:
            x=v[metadata['data_key']].copy();target=v[metadata['target_key']].copy()
        axes=metadata['axes']
        if sorted(axes)!=['channel','sample','trial'] or x.ndim!=3:raise ValueError('Explicit EEGEyeNet benchmark axes required')
        x=np.transpose(x,[axes.index(k) for k in ['trial','channel','sample']])
        if x.shape[1]!=len(metadata['channel_names']):raise ValueError('EEGEyeNet montage mismatch')
        return dict(windows=x,channels=metadata['channel_names'],sampling_rate=metadata['sampling_rate'],native_targets=target,target_semantics=metadata['target_semantics'])
    raise ValueError('Public reader unavailable')
