"""Manual ocular ranges over immutable, A100-prepared display waveforms."""
import datetime,hashlib,json,re,threading
from pathlib import Path
from gnautilus_ml.common import atomic,read_json,within

PHASES=('onset','offset')
DIRECTIONS={'UP','DOWN','LEFT','RIGHT'}

class AnnotationConflict(ValueError):pass

class OcularAnnotationStore:
    def __init__(self,root):self.root=Path(root);self.lock=threading.RLock()
    def _source(self):
        p=self.root/'hub/runtime/ocular_annotation_source.json'
        if not p.exists():raise ValueError('眼动打标数据尚未准备，请稍后刷新')
        c=read_json(p);folder=within(self.root/c['waveform_folder'],self.root/'data');labels=within(self.root/c['annotations_folder'],self.root/'data')
        if folder==labels or folder in labels.parents or labels in folder.parents:raise ValueError('标注与原始波形必须分开保存')
        raw=(folder/'manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=c['manifest_sha256']:raise ValueError('波形清单已变化，请重新核对数据身份')
        m=json.loads(raw)
        if m.get('schema')!='gnautilus.manual_ocular_waveforms.v1' or m.get('sampling_rate')!=250 or len(m.get('channels',[]))!=32:raise ValueError('眼动打标波形合同不匹配')
        return m,folder,labels
    def _meta(self,m,tid):
        if not isinstance(tid,str) or re.fullmatch('[a-f0-9]{64}',tid) is None:raise ValueError('无效的试次身份')
        row=next((x for x in m['trials'] if x['trial_id']==tid),None)
        if row is None:raise ValueError('没有找到该试次')
        return row
    def _latest(self,labels,tid):
        folder=labels/tid;versions=sorted(folder.glob('[0-9]'*8+'.json')) if folder.exists() else []
        if not versions:return dict(revision=0,phases={k:dict(status='pending') for k in PHASES})
        return read_json(versions[-1])
    def _wave(self,folder,row):
        p=within(folder/row['waveform_file'],folder);raw=p.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['waveform_sha256']:raise ValueError('该试次波形已变化，停止保存以避免标错来源')
        return json.loads(raw)
    def catalog(self):
        with self.lock:
            m,_,labels=self._source();rows=[dict(q,annotation=self._latest(labels,q['trial_id'])) for q in m['trials']]
        return dict(dataset_id=m['dataset_id'],title=m['title'],sampling_rate=m['sampling_rate'],channels=m['channels'],trials=rows)
    def trial(self,tid):
        with self.lock:
            m,folder,labels=self._source();q=self._meta(m,tid);wave=self._wave(folder,q)
            if wave.get('dataset_id')!=m['dataset_id'] or wave.get('trial_id')!=tid:raise ValueError('波形身份不匹配')
            return dict(metadata=q,waveform=wave,annotation=self._latest(labels,tid),dataset_id=m['dataset_id'],channels=m['channels'],sampling_rate=m['sampling_rate'])
    def save(self,data):
        if not isinstance(data,dict):raise ValueError('无效的标注请求')
        with self.lock:
            m,folder,labels=self._source();q=self._meta(m,data.get('trial_id'));self._wave(folder,q)
            if data.get('dataset_id')!=m['dataset_id'] or data.get('waveform_sha256')!=q['waveform_sha256']:raise ValueError('数据身份已变化，请刷新后重新核对')
            old=self._latest(labels,q['trial_id']);revision=data.get('revision')
            if type(revision) is not int or revision<0:raise ValueError('无效的标注版本')
            if revision!=old['revision']:raise AnnotationConflict('这段标注已在其他页面更新，请重新载入后核对，未覆盖已有标注')
            phases=data.get('phases')
            if not isinstance(phases,dict) or set(phases)!=set(PHASES):raise ValueError('每段只能保存开始和结束两个标注槽位')
            cleaned={}
            for key in PHASES:
                value=phases[key]
                if not isinstance(value,dict) or set(value)-{'status','direction','sample_start','sample_end'}:raise ValueError('无效的眼动标注字段')
                status=value.get('status')
                if status not in ['pending','marked','absent','uncertain']:raise ValueError('无效的人工确认状态')
                entry=dict(status=status)
                if status=='marked':
                    a,b=value.get('sample_start'),value.get('sample_end');w=q['windows'][key]
                    if type(a) is not int or type(b) is not int or not w['start']<=a<b<=w['end']:raise ValueError('框选范围必须位于对应的5秒波形内，且结束晚于开始')
                    direction=value.get('direction')
                    if direction not in DIRECTIONS:raise ValueError('请确认眼动方向为上、下、左或右')
                    expected=q['default_directions'][key]
                    entry.update(sample_start=a,sample_end=b,direction=direction,expected_direction=expected,
                        within_expected_one_second=(b<=q['task_start']+250 if key=='onset' else a>=q['task_end']-250),
                        human_confirmed=True)
                cleaned[key]=entry
            a,b=cleaned['onset'],cleaned['offset']
            if a['status']==b['status']=='marked' and a['sample_start']<b['sample_end'] and a['sample_end']>b['sample_start']:raise ValueError('开始与结束的两次眼动范围不能重叠')
            record=dict(schema='gnautilus.manual_ocular_annotation.v1',dataset_id=m['dataset_id'],trial_id=q['trial_id'],stream_id=q['stream_id'],
                split=q['split'],waveform_sha256=q['waveform_sha256'],task_range=[q['task_start'],q['task_end']],revision=revision+1,
                updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),annotation_source='manual_user',phases=cleaned,
                sample_bounds='absolute source sample indices, start inclusive / end exclusive',sampling_rate=m['sampling_rate'],automatic_training=False)
            atomic(labels/q['trial_id']/(f'{record["revision"]:08d}.json'),record)
            return record
    def export(self):
        c=self.catalog();reviews=[q['annotation'] for q in c['trials'] if q['annotation']['revision']];events=[]
        for q in c['trials']:
            for phase in PHASES:
                p=q['annotation']['phases'][phase]
                if p['status']!='marked':continue
                events.append(dict(dataset_id=c['dataset_id'],trial_id=q['trial_id'],stream_id=q['stream_id'],split=q['split'],phase=phase,
                    **{k:p[k] for k in ['sample_start','sample_end','direction']},revision=q['annotation']['revision'],
                    training_eligible=q['split']=='train',annotation_source='manual_user',sampling_rate=c['sampling_rate']))
        return dict(schema='gnautilus.manual_ocular_export.v1',dataset_id=c['dataset_id'],automatic_training=False,events=events,reviews=reviews,
            semantics='Human ocular event ranges; source roles retained. No-event and uncertain reviews never become REST. New training needs a locked protocol.')
