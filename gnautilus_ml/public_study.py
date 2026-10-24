"""Source-bound public MI quality assessment and immutable finite-window caches.

No fitting occurs here. Native labels and original files stay unchanged. A public
session identity is not presented as a measured cap wearing/date identity.
"""
import argparse
from collections import Counter, defaultdict
import gc
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import time
import zipfile
import numpy as np
from scipy.io import loadmat
from scipy.signal import butter, sosfilt
from .common import atomic, digest, file_hash, read_json, within

BCI_CHANNELS = ['Fz','FC3','FC1','FCz','FC2','FC4','C5','C3','C1','Cz','C2','C4','C6','CP3','CP1','CPz','CP2','CP4','P1','Pz','P2','POz']
MOTOR = ['FC5','FC3','FC1','FCz','FC2','FC4','FC6','C5','C3','C1','Cz','C2','C4','C6','CP5','CP3','CP1','CPz','CP2','CP4','CP6']
BCI_LABELS = {1:'LEFT_HAND_MI',2:'RIGHT_HAND_MI',3:'BOTH_FEET_MI',4:'TONGUE_MI'}
HALT_LABELS = {1:'LEFT_HAND_MI',2:'RIGHT_HAND_MI',3:'PASSIVE',4:'LEFT_LEG_MI',5:'TONGUE_MI',6:'RIGHT_LEG_MI'}


def electrical_window(values, fs, policy):
    """Fixed gross artifact observations in uV, before any learned transform."""
    values = np.asarray(values, dtype=np.float64)
    if not np.isfinite(values).all():
        return dict(eligible=False, reasons=['nonfinite'], channel_reasons={})
    centered = values - np.mean(values, axis=1, keepdims=True)
    ptp = np.ptp(values, axis=1)
    rms = np.sqrt(np.mean(centered**2, axis=1))
    absolute = np.max(np.abs(values), axis=1)
    frequency = np.fft.rfftfreq(values.shape[1], 1/fs)
    power = np.abs(np.fft.rfft(centered, axis=1))**2
    denominator = power[:,(frequency>=1)&(frequency<=min(100,fs/2))].sum(1) + 1e-20
    line = np.maximum.reduce([power[:,np.abs(frequency-hz)<=2].sum(1)/denominator for hz in policy['line_frequencies_hz']])
    checks = dict(absolute=absolute>policy['max_abs_uv'], ptp=ptp>policy['max_ptp_uv'],
                  flat=ptp<policy['min_ptp_uv'], rms=rms>policy['max_centered_rms_uv'], line=line>policy['max_line_fraction'])
    bad = {str(i):[k for k,v in checks.items() if bool(v[i])] for i in range(len(values)) if any(v[i] for v in checks.values())}
    return dict(eligible=not bad, reasons=sorted({v for reasons in bad.values() for v in reasons}),
                channel_reasons=bad, maximum_ptp_uv=float(ptp.max()), maximum_rms_uv=float(rms.max()), maximum_line_fraction=float(line.max()))


def descriptive(values):
    """All-sample gross observations; no thresholds are fitted on any split."""
    a=np.asarray(values); count=np.zeros(a.shape[0],dtype=np.int64); total=np.zeros(a.shape[0]); square=total.copy()
    low=np.full(a.shape[0],np.inf); high=-low.copy()
    for at in range(0,a.shape[1],100000):
        block=np.asarray(a[:,at:at+100000],dtype=np.float64); good=np.isfinite(block); clean=np.where(good,block,0)
        count+=good.sum(1); total+=clean.sum(1); square+=np.square(clean).sum(1)
        low=np.minimum(low,np.where(good,block,np.inf).min(1)); high=np.maximum(high,np.where(good,block,-np.inf).max(1))
    mean=total/np.maximum(count,1); std=np.sqrt(np.maximum(0,square/np.maximum(count,1)-mean**2))
    return dict(samples=int(a.shape[1]), channels=int(a.shape[0]), nonfinite_by_channel=(a.shape[1]-count).tolist(),
                min_uv=[float(v) if np.isfinite(v) else None for v in low], max_uv=[float(v) if np.isfinite(v) else None for v in high],
                std_uv=std.tolist(), contact_quality='unknown', effective_hardware_sample_rate='not_independently_measured')


def group_identity(record):
    meta=record['public_metadata']; grouping=meta['grouping']
    return '|'.join([record['reader'],str(record['subject']),str(grouping['native_id'])])


def load_prepared(record, window_s, hop_s):
    prepared=record['prepared_windows']; path=Path(prepared['path']); report_path=Path(prepared['report_path'])
    if prepared['schema']!='gnautilus.public_prepared.v1' or file_hash(path)!=prepared['sha256'] or file_hash(report_path)!=prepared['report_sha256']:
        raise ValueError('Prepared public windows/QC receipt changed')
    report=read_json(report_path)
    for source,checksum in report.get('preparation_code_sha256',{}).items():
        if file_hash(source)!=checksum:raise ValueError('QC preparation code receipt no longer resolves to its immutable source')
    if not report['admitted_for_pilot'] or report['source_sha256']!=record['source_sha256'] or report['channels']!=record['channels']:
        raise ValueError('Prepared public admission/source/channel contract differs')
    if report['window_s']!=window_s or report['sampling_rate']!=record['sampling_rate']:
        raise ValueError('Requested window/rate differs from reviewed public derivation')
    for item in report['ancillary_files']:
        if file_hash(item['path'])!=item['sha256']:raise ValueError('Public label source changed')
    if file_hash(record['path'])!=record['source_sha256']:raise ValueError('Actual raw public source changed')
    with np.load(path,allow_pickle=False) as z:x=z['x'].copy();y=z['y'].copy()
    ids=report['retained_windows']
    if len(ids)!=len(x) or x.shape!=(len(y),len(record['channels']),round(window_s*record['sampling_rate'])) or not np.isfinite(x).all():
        raise ValueError('Prepared public dimensions/numeric content invalid')
    for index,(identity,label) in enumerate(zip(ids,y)):
        if identity['source']!=record['source_sha256'] or identity['native_label']!=label or not 0<=identity['start']<identity['end']<=report['source_samples']:
            raise ValueError('Prepared native source/sample/label identity differs')
    return x,y,ids


class Study:
    def __init__(self,root,plan_path):
        self.root=Path(root).resolve();self.public=self.root/'data/public';self.plan=read_json(plan_path);self.plan_hash=digest(self.plan)
        self.code_hash=file_hash(__file__)
        self.output=self.root/'studies'/(self.plan['id']+'-'+self.plan_hash[:8]+'-'+self.code_hash[:8]);self.output.mkdir(parents=True,exist_ok=True)
        self.cache=self.root/'data/public_prepared'/self.plan_hash/self.code_hash[:12];self.cache.mkdir(parents=True,exist_ok=True)
        self.inventory=read_json(self.public/'recording-index.json')['files'];self.rows={};self.records=defaultdict(list)
        self.originals={r['relative_path']:r['sha256'] for r in read_json(self.public/'original-file-index.json')['files']}
        self.started=time.time();self.last_progress=0;atomic(self.output/'study-plan.json',self.plan)
        if (self.output/'quality-progress.json').exists():
            previous=read_json(self.output/'quality-progress.json');self.rows=previous['records'];self.records.update(previous.get('dataset_records',{}))

    def checkpoint(self,force=False):
        if not force and time.monotonic()-self.last_progress<2:return
        atomic(self.output/'quality-progress.json',dict(schema='gnautilus.public_quality.v1',plan_sha256=self.plan_hash,
            assessed_files=len(self.rows),errors={k:v['error'] for k,v in self.rows.items() if 'error' in v},
            elapsed_s=time.time()-self.started,training_started=False,records=self.rows,dataset_records=self.records))
        self.last_progress=time.monotonic()

    def raw(self,row):
        path=within(self.public/row['path'],self.public)
        if file_hash(path)!=row['sha256']:raise ValueError('Original source differs from the independently verified download')
        return path

    def commit(self,row,reader,values,names,fs,trials,role,subject,session,ancillary=None,partition=None):
        config=self.plan['datasets'][reader]; policy=self.plan['quality'];source=self.raw(row)
        if len(names)!=values.shape[0] or len(set(names))!=len(names):raise ValueError('Native data/channel dimensions disagree')
        if reader=='bci_iv_2a':selected=BCI_CHANNELS
        elif reader=='halt':selected=config['channels']
        else:selected=[c for c in MOTOR if c in names]
        if len(selected)<3 or any(c not in names for c in selected):raise ValueError('Declared motor montage is missing')
        indices=[names.index(c) for c in selected];target_fs=config.get('target_sampling_rate',fs)
        if reader=='openbmi' and (fs!=1000 or target_fs!=250):raise ValueError('OpenBMI causal decimation requires its verified 1000Hz source')
        n=round(config['window_s']*fs);offset=round(config['offset_from_cue_s']*fs);windows=[];labels=[];ids=[];checks=[]
        for trial in trials:
            label=trial['label']
            if label not in config['classes']:continue
            start=int(trial['cue'])+offset;end=start+n
            entry=dict(trial=int(trial['trial']),native_label=label,start=start,end=end,provider_rejected=bool(trial.get('provider_rejected',False)))
            if start<0 or end>values.shape[1] or end>trial['end']:
                entry.update(eligible=False,reasons=['task_or_recording_boundary']);checks.append(entry);continue
            sample=np.asarray(values[indices,start:end],dtype=np.float64)
            q=electrical_window(sample,fs,policy);entry.update(q)
            if entry['provider_rejected']:entry['eligible']=False;entry['reasons']=sorted(set(entry['reasons']+['provider_marked_artifact']))
            checks.append(entry)
            if not entry['eligible']:continue
            if target_fs!=fs:
                sample=sosfilt(butter(8,100,fs=fs,output='sos'),sample,axis=1)[:,3::4]
            windows.append(sample.astype(np.float32));labels.append(label)
            ids.append(dict(source=row['sha256'],trial=int(trial['trial']),start=start,end=end,native_label=label,source_sampling_rate=fs))
        key=digest(dict(source=row['sha256'],plan=self.plan_hash,reader=reader,partition=partition))
        counts=Counter(labels);admitted=set(counts)==set(config['classes']) and min(counts.values(),default=0)>=self.plan['minimum_retained_trials_per_class_per_record']
        report=dict(schema='gnautilus.public_record_qc.v1',source_sha256=row['sha256'],source_path=str(source),
            preparation_code_sha256={str(Path(__file__).resolve()):file_hash(__file__)},
            packages={name:importlib.metadata.version(name) for name in ['numpy','scipy','mne']},
            plan_sha256=self.plan_hash,reader=reader,source_samples=int(values.shape[1]),source_sampling_rate=fs,
            sampling_rate=target_fs,channels=selected,native_channels=names,window_s=config['window_s'],
            role=role,subject=subject,native_session=session,native_partition=partition,ancillary_files=ancillary or [],
            gross_observations=descriptive(values),quality_policy=policy,trials=checks,retained_windows=ids,
            source_class_counts=dict(Counter(t['native_label'] for t in checks)),retained_class_counts=dict(Counter(labels)),
            coverage=len(windows)/len(checks) if checks else 0,admitted_for_pilot=admitted,
            interpretation='Native cued MI only; not navigation, not contact impedance or independent personal accuracy')
        report_path=self.cache/(key+'.json');cache_path=self.cache/(key+'.npz')
        if cache_path.exists() and report_path.exists():
            if read_json(report_path)!=report:raise ValueError('Existing immutable quality receipt differs')
            with np.load(cache_path,allow_pickle=False) as old:
                if not np.array_equal(old['x'],np.asarray(windows,dtype=np.float32)) or not np.array_equal(old['y'],np.asarray(labels,dtype='<U24')):raise ValueError('Existing immutable window cache differs')
        else:
            with cache_path.with_suffix('.npz.tmp').open('wb') as output:np.savez(output,x=np.asarray(windows,dtype=np.float32),y=np.asarray(labels,dtype='<U24'))
            cache_path.with_suffix('.npz.tmp').replace(cache_path);atomic(report_path,report)
        self.rows[row['path']]=dict(dataset=row['dataset'],qc_report=str(report_path),coverage=report['coverage'],
            source_class_counts=report['source_class_counts'],retained_class_counts=report['retained_class_counts'],
            gross_observations=report['gross_observations'],admitted=admitted,role=role)
        if admitted:
            grouping=dict(level='subject' if reader=='eegmmidb' else 'native_session',native_id=str(subject) if reader=='eegmmidb' else str(session))
            if reader=='halt':grouping=dict(level='native_date',native_id=row['recording_date_code'])
            record=dict(schema='gnautilus.dataset_record.v1',reader=reader,path=str(source),source_sha256=row['sha256'],
                channels=selected,sampling_rate=target_fs,reference=config.get('reference'),filter_configuration=dict(source=config.get('source_filters'),
                    derivation='none' if fs==target_fs else config['decimation'],plan_sha256=self.plan_hash),subject=str(subject),date=None,wearing_id=None,
                role=role,closed=True,training_ready=True,electrical_policy=policy,
                public_metadata=dict(admission='approved',license_receipt='public download source license + source-bound QC '+str(report_path),
                    source_version=row['dataset']+'/download-20261023',grouping=grouping,study_plan_sha256=self.plan_hash,
                    source_unit='uV',native_partition=partition,source_session_verified=True),
                prepared_windows=dict(schema='gnautilus.public_prepared.v1',path=str(cache_path),sha256=file_hash(cache_path),report_path=str(report_path),report_sha256=file_hash(report_path)))
            self.records[reader].append(record)

    def bci2a(self,row):
        import mne
        source=self.raw(row);raw=mne.io.read_raw_gdf(source,eog=[22,23,24],preload=True,verbose='ERROR')
        try:
            if len(raw.ch_names)!=25 or raw.get_channel_types().count('eog')!=3 or raw.info['sfreq']!=250:raise ValueError('GDF EEG/EOG/rate differs from official contract')
            values=raw.get_data(picks=list(range(22)))*1e6;events=[(round(t*250),int(c)) for t,c in zip(raw.annotations.onset,raw.annotations.description) if c.isdigit()]
            starts=[t for t,c in events if c==768];cues=[(t,c) for t,c in events if c in [769,770,771,772,783]];rejected=[t for t,c in events if c==1023]
            label_path=source.parent.parent/'true_labels'/(source.stem+'.mat');label_hash=file_hash(label_path)
            label_archive=source.parent.parent.parent/'raw/true_labels.zip'
            if file_hash(label_archive)!=self.originals[str(label_archive.relative_to(self.public))]:raise ValueError('Official label archive hash changed')
            with zipfile.ZipFile(label_archive) as original:
                if hashlib.sha256(original.read(label_path.name)).hexdigest()!=label_hash:raise ValueError('Extracted labels differ from their verified official archive')
            labels=np.ravel(loadmat(label_path)['classlabel']).astype(int)
            if len(labels)!=288 or len(starts)!=288 or len(cues)!=288:raise ValueError('Official labels/trial/cue counts do not agree')
            trials=[]
            for i,((cue,code),label,start) in enumerate(zip(cues,labels,starts)):
                if code!=783 and code-768!=label:raise ValueError('Published label order disagrees with native GDF events')
                stop=starts[i+1] if i+1<len(starts) else values.shape[1]
                if not start<=cue<stop:raise ValueError('Cue lies outside its trial')
                trials.append(dict(trial=i,cue=cue,end=cue+4*250,label=BCI_LABELS[int(label)],provider_rejected=any(start<=v<stop for v in rejected)))
            self.commit(row,'bci_iv_2a',values,BCI_CHANNELS,250,trials,'train' if source.stem.endswith('T') else 'development',source.stem[:3],source.stem,
                        ancillary=[dict(path=str(label_path),sha256=label_hash),dict(path=str(label_archive),sha256=file_hash(label_archive))])
        finally:raw.close()

    def halt(self,row,role):
        source=self.raw(row);o=loadmat(source,simplify_cells=True)['o'];all_names=[str(v) for v in o['chnames']]
        if len(all_names)!=22 or all_names[-1] not in ['X3','X5'] or o['binsuV']!=1 or o['sampFreq']!=200:raise ValueError('HaLT source/unit/channel contract differs')
        take=[i for i,c in enumerate(all_names) if c not in ['A1','A2','X3','X5']];names=[all_names[i] for i in take]
        marker=np.ravel(o['marker']);edges=np.r_[0,np.flatnonzero(np.diff(marker))+1,len(marker)]
        trials=[dict(trial=int(a),cue=int(a),end=int(b),label=HALT_LABELS[int(marker[a])]) for a,b in zip(edges[:-1],edges[1:]) if marker[a] in HALT_LABELS]
        self.commit(row,'halt',np.asarray(o['data'][:,take]).T,names,200,trials,role,row['subject'],source.stem)

    def physio(self,row):
        import mne
        source=self.raw(row);raw=mne.io.read_raw_edf(source,preload=True,verbose='ERROR')
        try:
            fs=float(raw.info['sfreq']);values=raw.get_data()*1e6
            if len(raw.ch_names)!=64:raise ValueError('PhysioNet source is not 64 EEG channels')
            aliases={v.lower():v for v in mne.channels.make_standard_montage('standard_1005').ch_names}
            names=[aliases.get(c.strip('.').lower(),c.strip('.')) for c in raw.ch_names]
            if fs!=160 or row['run'] not in [4,8,12]:
                self.rows[row['path']]=dict(dataset='eegmmidb',source_sha256=row['sha256'],source_sampling_rate=fs,
                    scope='descriptive_only_not_in_pilot',reason='different_header_rate' if fs!=160 else 'baseline_or_different_task',gross_observations=descriptive(values),admitted=False)
                return
            trials=[]
            for i,(onset,duration,label) in enumerate(zip(raw.annotations.onset,raw.annotations.duration,raw.annotations.description)):
                if label not in ['T1','T2']:continue
                trials.append(dict(trial=i,cue=round(onset*fs),end=round((onset+duration)*fs),label='LEFT_HAND_MI' if label=='T1' else 'RIGHT_HAND_MI'))
            self.commit(row,'eegmmidb',values,names,fs,trials,'train' if int(row['subject'][1:])<=80 else 'development',row['subject'],None)
        finally:raw.close()

    def openbmi(self,row):
        source=self.raw(row);data=loadmat(source,simplify_cells=True);entry={}
        for partition in ['EEG_MI_train','EEG_MI_test']:
            v=data[partition];values=np.asarray(v['x']).T;fs=int(v['fs']);names=[str(c) for c in v['chan']]
            table={int(c):str(n).lower() for c,n in np.asarray(v['class'])}
            if table!={1:'right',2:'left'} or fs!=1000 or values.shape[0]!=62:raise ValueError('OpenBMI source class/rate/channel table differs')
            onsets=np.ravel(v['t']).astype(int);labels=np.ravel(v['y_dec']).astype(int)
            if len(onsets)!=100 or len(labels)!=100 or np.any(np.diff(onsets)<=0):raise ValueError('OpenBMI event count/order differs')
            # Verify the actual MATLAB index convention against the supplied
            # segmented raw epoch, rather than guessing a one-sample offset.
            segment=np.asarray(v['smt'])[:64,0,:];errors={}
            for base in [0,1]:
                start=onsets[0]-base
                errors[base]=float(np.max(np.abs(np.asarray(v['x'])[start:start+64]-segment)))
            if min(errors.values())>1e-8 or errors[0]==errors[1]:raise ValueError('Cannot establish OpenBMI native onset convention against smt: '+str(errors))
            base=min(errors,key=errors.get)
            entry[partition]=dict(gross_observations=descriptive(values),source_sampling_rate=fs,source_channels=names,index_base=base,index_probe_max_error=errors[base],
                label_counts=dict(Counter(str(i) for i in labels)),native_partition=partition)
            if partition=='EEG_MI_train' and int(row['subject'])<=12:
                trials=[dict(trial=i,cue=int(at)-base,end=int(at)-base+4*fs,label='RIGHT_HAND_MI' if label==1 else 'LEFT_HAND_MI') for i,(at,label) in enumerate(zip(onsets,labels))]
                self.commit(row,'openbmi',values,names,fs,trials,'train' if row['session']==1 else 'development',row['subject'],row['session'],partition=partition)
        self.rows.setdefault(row['path'],dict(dataset='openbmi_mi',source_sha256=row['sha256'],scope='descriptive_only_outside_pilot',admitted=False))['partitions']=entry

    def finish_dataset(self,reader):
        records=self.records[reader]
        if not records:return
        native_dataset={'halt':'kaya2018_halt','bci_iv_2a':'bci_iv_2a','eegmmidb':'eegmmidb','openbmi':'openbmi_mi'}[reader]
        if any('error' in r and r['dataset']==native_dataset for r in self.rows.values()):
            raise ValueError('Source parsing/integrity errors must be resolved before dataset registration: '+reader)
        roles={r['role'] for r in records}
        support={role:Counter() for role in roles}
        for record in records:
            report=read_json(record['prepared_windows']['report_path']);support[record['role']].update(report['retained_class_counts'])
        expected=set(self.plan['datasets'][reader]['classes'])
        if roles!={'train','development'} or any(set(v)!=expected or min(v.values())<3 for v in support.values()):
            raise ValueError('Post-QC train/development native class support is insufficient: '+str(support))
        dataset=dict(schema='gnautilus.dataset.v1',title='公开初步方法验证 / '+reader,records=records,study_plan_sha256=self.plan_hash,
                     scope='public_method_development',training_admission='source_bound_quality_review',quality_summary_path=str(self.output/'quality-progress.json'))
        identity=digest(dataset);atomic(self.root/'datasets'/(identity+'.json'),dataset)
        atomic(self.output/(reader+'-dataset.json'),dict(id=identity,dataset=dataset,support=support))

    def run(self,only=None):
        wanted=only or ['bci_iv_2a','halt','eegmmidb','openbmi']
        dataset_map={'halt':'kaya2018_halt','bci_iv_2a':'bci_iv_2a','eegmmidb':'eegmmidb','openbmi':'openbmi_mi'}
        halt_groups=defaultdict(list)
        for row in self.inventory:
            if row['dataset']=='kaya2018_halt':halt_groups[row['subject']].append(row)
        for reader in wanted:
            rows=[r for r in self.inventory if r['dataset']==dataset_map[reader]]
            for row in rows:
                if row['path'] in self.rows and 'error' not in self.rows[row['path']]:continue
                if reader=='halt' and len({r['recording_date_code'] for r in halt_groups[row['subject']]})<2:
                    self.rows[row['path']]=dict(dataset='kaya2018_halt',admitted=False,scope='single_session_reserved_not_in_pilot');continue
                try:
                    if reader=='bci_iv_2a':self.bci2a(row)
                    elif reader=='halt':
                        last=max(v['recording_date_code'] for v in halt_groups[row['subject']]);role='development' if row['recording_date_code']==last else 'train';self.halt(row,role)
                    elif reader=='eegmmidb':self.physio(row)
                    else:self.openbmi(row)
                except Exception as error:self.rows[row['path']]=dict(dataset=row['dataset'],admitted=False,error=type(error).__name__+': '+str(error))
                self.checkpoint();gc.collect()
            self.finish_dataset(reader);self.checkpoint(force=True)
        atomic(self.output/'quality-completion.json',dict(completed=True,assessed_files=len(self.rows),
            error_count=sum('error' in r for r in self.rows.values()),plan_sha256=self.plan_hash,
            datasets={r:len(v) for r,v in self.records.items()},training_started=False))
        print(json.dumps(dict(study=str(self.output),records={r:len(v) for r,v in self.records.items()},quality_errors=sum('error' in r for r in self.rows.values()))),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--plan',required=True);parser.add_argument('--only',nargs='+')
    args=parser.parse_args();Study(args.root,args.plan).run(args.only)


if __name__=='__main__':main()
