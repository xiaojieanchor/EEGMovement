"""Leased, auditable training-EEG input override; never generates predictions."""
import hashlib,json,re,secrets,time,threading,shutil
from pathlib import Path
import numpy as np

LABELS=['REST','FORWARD','BACKWARD','LEFT','RIGHT']
ALL_LABELS=LABELS+['FORWARD_LEFT','FORWARD_RIGHT','BACKWARD_LEFT','BACKWARD_RIGHT']


class TrainingClipLibrary:
    def __init__(self,root,hub=None):
        self.root=Path(root);self.base=self.root/'outputs/training_test';self.directory=self.base;self.loaded={};self.manifest=None;self.hub=hub
        self.lock=threading.RLock();self.preparing=set();self.errors={};self.next_attempt={}
    def catalog(self):
        with self.lock:return self._catalog()
    def _catalog(self):
        health=(self.hub.intent.health or {}) if self.hub else {};digest=health.get('artifact_sha256')
        if digest:
            if not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('模型权重身份无效')
            directory=self.base/'models'/digest
            if not (directory/'library.json').exists():
                old=self.base/'library.json'
                legacy=json.loads(old.read_text(encoding='utf-8')) if old.exists() else {}
                if legacy.get('artifact_sha256')==digest:directory=self.base
                else:
                    self.prepare(health)
                    raise ValueError(self.errors.get(digest,'正在同步当前模型的已训练片段，请稍候'))
            if directory!=self.directory:self.directory=directory;self.manifest=None;self.loaded={}
        if self.manifest is None:
            p=self.directory/'library.json'
            if not p.exists():raise ValueError('训练测试片段尚未准备')
            self.manifest=json.loads(p.read_text(encoding='utf-8'))
            labels=set(self.manifest.get('clips',{}))
            if self.manifest.get('version') not in [1,2] or not set(LABELS)<=labels or not labels<=set(ALL_LABELS):self.manifest=None;raise ValueError('训练测试片段清单不完整')
        if digest and (self.manifest.get('artifact_sha256')!=digest or self.manifest.get('model_id')!=health.get('model_id')):raise ValueError('测试片段与当前模型不匹配')
        return self.manifest
    def prepare(self,health):
        digest=health['artifact_sha256']
        if self.hub is None or digest in self.preparing or time.monotonic()<self.next_attempt.get(digest,0):return
        self.preparing.add(digest);self.next_attempt[digest]=time.monotonic()+30
        threading.Thread(target=self._prepare,args=(dict(health),),daemon=True).start()
    def _prepare(self,health):
        digest=health['artifact_sha256'];partial=None
        try:
            with self.hub.model_library.lock:
                row=next((dict(r)for r in self.hub.model_library.rows.values()if r.get('weights_sha256')==digest and r.get('remote')),None)
            if not row or row.get('adapter') not in ['causal','placement']:raise ValueError('当前模型尚无可追溯的训练片段导出')
            source=self.root/'hub/training_clip_export.py';source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
            remote='/home/sysu/EEGMovement/run/training-clip-export-'+source_sha+'.py';client=self.hub.remote.client
            if client is None or not client.get_transport().is_active():raise ValueError('请先连接计算服务器以同步训练片段')
            with client.open_sftp()as ftp:
                try:ftp.stat(remote)
                except FileNotFoundError:ftp.put(str(source),remote);ftp.chmod(remote,0o600)
            code="import subprocess,json,hashlib\nfrom pathlib import Path\np=Path("+repr(remote)+")\nif hashlib.sha256(p.read_bytes()).hexdigest()!="+repr(source_sha)+":raise ValueError('Exporter identity changed')\nr=subprocess.run(['/home/sysu/EEGMovement/.venv-training/bin/python',str(p),'--root','/home/sysu/EEGMovement','--row',"+repr(json.dumps(row))+",'--model-id',"+repr(health['model_id'])+"],capture_output=True,text=True,timeout=75)\nif r.returncode:raise RuntimeError(r.stderr[-400:])\nprint(r.stdout)"
            result=self.hub.remote.command(code,timeout=85);manifest=result['manifest']
            if manifest['artifact_sha256']!=digest or manifest['protocol_sha256']!=health['protocol_sha256']:raise ValueError('导出数据与模型不匹配')
            remote_dir='/home/sysu/EEGMovement/run/training-test-v2/'+digest
            if result['directory']!=remote_dir:raise ValueError('Unexpected export directory')
            target=self.base/'models'/digest;target.parent.mkdir(parents=True,exist_ok=True);partial=target.with_name(digest+'.partial-'+secrets.token_hex(4));partial.mkdir()
            with client.open_sftp()as ftp:
                for label,info in manifest['clips'].items():
                    if label not in ALL_LABELS or info['file']!=label+'.npz' or info['role']!='train':raise ValueError('Invalid training clip identity')
                    path=partial/info['file'];ftp.get(remote_dir+'/'+info['file'],str(path))
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('训练片段下载校验失败')
            (partial/'library.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            verifier=TrainingClipLibrary(self.root);verifier.directory=partial
            for label in manifest['clips']:verifier.get(label)
            partial.rename(target);partial=None
            with self.lock:self.errors.pop(digest,None)
        except Exception as error:
            with self.lock:self.errors[digest]='训练片段同步失败：'+str(error)[-300:]
        finally:
            if partial is not None:shutil.rmtree(partial)
            with self.lock:self.preparing.discard(digest)
    def get(self,label):
        with self.lock:return self._get(label)
    def _get(self,label):
        if label not in ALL_LABELS:raise ValueError('未知训练数据方向')
        info=self.catalog()['clips'][label]
        if label not in self.loaded:
            p=(self.directory/info['file']).resolve()
            if not p.is_relative_to(self.directory.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest()!=info['sha256']:raise ValueError('训练测试片段完整性校验失败')
            with np.load(p,allow_pickle=False) as f:raw=f['raw'].copy();quality=f['quality'].copy()
            if raw.ndim!=2 or raw.shape[1]!=7 or not 1025<=len(raw)<=60000 or not np.isfinite(raw).all() or quality.shape!=raw.shape or quality.dtype!=bool:raise ValueError('训练测试片段格式错误')
            if np.count_nonzero(quality[:1000].all(0))<4:raise ValueError('训练上下文未通过历史质量门')
            raw.setflags(write=False);quality.setflags(write=False)
            self.loaded[label]=dict(info,raw=raw,quality=quality,label=label)
        return self.loaded[label]


class HeldTrainingInput:
    """Called under IntentMonitor.lock; sequence tombstones prevent late presses."""
    def __init__(self,load,clock=time.monotonic):
        self.load=load;self.clock=clock;self.sequences={};self.current=None;self.revision=0;self.epoch=0;self.stream='training-test-'+secrets.token_hex(8)
    def cancel(self):
        if self.current is not None:self.current=None;self.revision+=1
    def expire(self):
        if self.current and self.clock()>=self.current['until']:self.cancel()
    def command(self,data):
        self.expire();owner=data.get('client_id');seq=data.get('seq');hold=data.get('hold_id');action=data.get('action')
        if not isinstance(owner,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',owner) or type(seq) is not int or not 1<=seq<=2147483647:raise ValueError('Invalid test client/sequence')
        if action not in ['press','renew','release'] or not isinstance(hold,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}',hold):raise ValueError('Invalid held-test command')
        previous=self.sequences.get(owner,(0,0))
        if seq<=previous[0]:return self.snapshot()
        if len(self.sequences)>=64 and owner not in self.sequences:
            self.sequences={k:v for k,v in self.sequences.items() if self.clock()-v[1]<300}
            if len(self.sequences)>=64:raise ValueError('测试客户端过多，请稍后重试')
        self.sequences[owner]=(seq,self.clock())
        if self.current and self.current['owner']!=owner:raise ValueError('其它页面正在按压测试，请先松开')
        if action=='press':
            label=data.get('label');clip=self.load(label);self.revision+=1;self.epoch+=1
            self.current=dict(owner=owner,hold=hold,label=label,clip=clip,until=self.clock()+.6,cursor=0,loops=0,due=0.)
        elif action=='renew':
            if not self.current or self.current['hold']!=hold:raise ValueError('按压已结束，请重新按下')
            self.current['until']=self.clock()+.6
        elif self.current and self.current['hold']==hold:self.cancel()
        return self.snapshot()
    def snapshot(self):
        self.expire();c=self.current
        return dict(active=c is not None,revision=self.revision,source_epoch=self.epoch,label=c['label'] if c else None,hold_id=c['hold'] if c else None,
                    loops=c['loops'] if c else 0,playhead_samples=c['cursor'] if c else 0,recording=c['clip']['recording'] if c else None,
                    samples=len(c['clip']['raw']) if c else 0,bootstrap_samples=1000,lease_ms=600,input_kind='recorded_training_eeg',not_accuracy_evaluation=True)
    def batch(self):
        self.expire();c=self.current;now=self.clock()
        if not c or now<c['due']:return None
        if c['cursor']>=len(c['clip']['raw']):c['cursor']=0;c['loops']+=1;self.epoch+=1
        a=c['cursor'];b=min(len(c['clip']['raw']),a+(1000 if a==0 else 25));c['cursor']=b;c['due']=now+.1
        return dict(stream_id=self.stream,head='trial',raw=c['clip']['raw'][a:b].tolist(),quality=c['clip']['quality'][a:b].tolist(),source_epoch=self.epoch,sample_index_end=b,probability_smoothing='hub_only')
