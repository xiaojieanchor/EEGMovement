"""Explicit model trials with immutable inventory, isolated loader and rollback."""
import copy,hashlib,json,threading,time
from pathlib import Path
from .model_catalog import inventory,group_models
from .remote import ChannelHTTP
from training.gpu_placement import project_gpu_environment

FILES=['server/model_library_service.py','server/model_library_worker.py','server/causal_intent.py','hub/model_catalog.py','training/__init__.py','training/gpu_placement.py',
    'training/causal_jepa.py','training/causal_stream.py','training/preprocessing.py','training/action_schema.py','staging/causal_mechanisms_v1/model.py']

class ModelLibrary:
    def __init__(self,hub,root):
        self.hub=hub;self.root=Path(root);self.lock=threading.RLock();self.refresh_lock=threading.Lock();self.switch_lock=threading.Lock()
        self.rows={};self.remote_at=None;self.error=None;self.selection=dict(state='idle');self.remote_rows=[]
        self.refreshing=False;self.refresh_revision=0;self.refresh_state='idle';self.refresh_completed_at=None;self.worker_receipt=None
        self.cache=self.root/'hub/runtime/model-library-cache.json';self.pointer=self.root/'hub/runtime/selected-model.json'
        self.active=None
        try:
            if self.cache.exists():self.remote_rows=json.loads(self.cache.read_text(encoding='utf-8'))['models']
            if self.pointer.exists():self.active=json.loads(self.pointer.read_text(encoding='utf-8'))
        except (OSError,ValueError,KeyError):pass
        if self.active:self.hub.intent.service_port=self.active.get('port',18862)
        self.rebuild()

    def rebuild(self):
        rows=inventory(self.root,[Path('G:/EEGMovement/Models')])['models']
        merged={r['id']:dict(r,remote=False)for r in rows}
        for r in self.remote_rows:merged[r['id']]=dict(r,remote=True)
        with self.lock:self.rows=merged

    def sources(self):return {name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in FILES}

    def stage(self):
        hashes=self.sources();build=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:20]
        base='/home/sysu/EEGMovement/run/model-library-v1/'+build
        client=self.hub.remote.client
        if client is None or not client.get_transport() or not client.get_transport().is_active():raise ValueError('请先连接计算服务器')
        with client.open_sftp() as ftp:
            def mkdir(path):
                parts=path.split('/');current=''
                for part in parts:
                    if not part:continue
                    current+='/'+part
                    try:ftp.stat(current)
                    except FileNotFoundError:ftp.mkdir(current,mode=0o700)
            for name in FILES:
                target=base+'/'+name;mkdir(target.rsplit('/',1)[0])
                try:ftp.stat(target)
                except FileNotFoundError:ftp.put(str(self.root/name),target);ftp.chmod(target,0o600)
        return base,hashes

    def connected(self):
        client=getattr(self.hub.remote,'client',None)
        if client is not None:return bool(client.get_transport() and client.get_transport().is_active())
        return bool(self.hub.remote.status.get('ssh_connected'))

    def _begin_refresh(self):
        if not self.refresh_lock.acquire(blocking=False):return False
        with self.lock:self.refreshing=True;self.refresh_state='syncing';self.refresh_revision+=1
        return True

    def request_refresh(self):
        if not self.connected():raise ValueError('服务器连接已断开，请先在总览页连接服务器；刷新目录不会建立 SSH 连接')
        if self._begin_refresh():
            with self.lock:
                if self.selection['state']=='failed':self.selection=dict(state='idle')
            threading.Thread(target=self._refresh_locked,daemon=True).start()
        return self.snapshot()

    def refresh(self):
        if not self._begin_refresh():return
        self._refresh_locked()

    def _refresh_locked(self):
        try:
            base,_=self.stage()
            value=self.hub.remote.command("import json,subprocess\np=subprocess.run(['/home/sysu/EEGMovement/.venv-training/bin/python',"+repr(base+'/hub/model_catalog.py')+",'--root','/home/sysu/EEGMovement'],capture_output=True,text=True,timeout=20)\nif p.returncode:raise RuntimeError(p.stderr[-300:])\nprint(p.stdout)",timeout=30)
            if value.get('schema')!='model-library-v1' or not isinstance(value.get('models'),list):raise ValueError('Unexpected model inventory')
            with self.lock:self.remote_rows=value['models']
            self.rebuild();self.cache.parent.mkdir(exist_ok=True);tmp=self.cache.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');tmp.replace(self.cache)
            with self.lock:self.remote_at=time.monotonic();self.error=None;self.refresh_state='completed';self.refresh_completed_at=time.time()
        except Exception as error:
            lines=[line.strip()for line in str(error).splitlines()if line.strip()]
            with self.lock:self.error=(lines[-1] if lines else type(error).__name__)[:220];self.refresh_state='failed'
        finally:
            with self.lock:self.refreshing=False
            self.refresh_lock.release()

    def loop(self):
        while not self.hub.stop_event.is_set():
            if self.hub.remote.status.get('ssh_connected'):self.refresh()
            self.hub.stop_event.wait(45)

    def worker_request(self,port,path,data=None):
        client=self.hub.remote.client
        if client is None or not client.get_transport() or not client.get_transport().is_active():raise ValueError('服务器连接已断开')
        with client.open_sftp() as ftp:
            with ftp.open('/home/sysu/EEGMovement/run/intent.token') as file:token=file.read().decode().strip()
        connection=ChannelHTTP(client.get_transport(),port)
        try:
            connection.connect();connection.sock.settimeout(90)
            connection.request('POST' if data is not None else 'GET',path,body=json.dumps(data).encode() if data is not None else None,
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=connection.getresponse();value=json.loads(response.read())
            if response.status!=200:raise ValueError(value.get('error','模型服务加载失败'))
            return value
        finally:connection.close()

    def ensure_worker(self):
        base,hashes=self.stage()
        command=['/home/sysu/EEGMovement/.venv-training/bin/python',base+'/server/model_library_service.py','--root','/home/sysu/EEGMovement','--base',base,'--hashes',json.dumps(hashes),'--protected',str(getattr(self.hub.intent,'service_port',18862))]
        code="import subprocess,json,hashlib\nfrom pathlib import Path\np=Path("+repr(base+'/server/model_library_service.py')+")\nif hashlib.sha256(p.read_bytes()).hexdigest()!="+repr(hashes['server/model_library_service.py'])+":raise ValueError('Service manager identity mismatch')\nr=subprocess.run("+repr(command)+",capture_output=True,text=True,timeout=30)\nif r.returncode:raise RuntimeError(r.stderr[-500:])\nprint(r.stdout)"
        receipt=self.hub.remote.command(code,timeout=35)
        with self.lock:self.worker_receipt=receipt
        return receipt['port']

    def switching(self):
        with self.lock:return self.selection['state']=='switching'

    def activate(self,data):
        if set(data)!={'id','weights_sha256'}:raise ValueError('请选择模型库中的明确版本')
        if not self.switch_lock.acquire(blocking=False):raise ValueError('已有模型正在切换')
        try:
            with self.lock:row=copy.deepcopy(self.rows.get(data['id']))
            if not row or row['weights_sha256']!=data['weights_sha256']:raise ValueError('模型版本已改变，请刷新后重试')
            if not row['can_activate'] or not row.get('remote'):raise ValueError(row['availability_reason'] if row else '模型尚未同步')
            if self.remote_at is None or time.monotonic()-self.remote_at>90:raise ValueError('远端模型目录已过期，请先刷新目录')
            # Serialize the transition against acquisition and start/control APIs.
            with self.hub.lock:
                if self.hub.recorder is not None or self.hub.acquisition.snapshot()['active'] or self.hub.calibration.snapshot()['active']:raise ValueError('请先结束当前采集或校验，再切换模型')
                with self.lock:self.selection=dict(state='switching',id=row['id'],started_at=time.time(),message='加载并核验模型中')
                self.hub.intent.stop()
            threading.Thread(target=self._switch,args=(row,),daemon=True).start();return self.snapshot()
        except Exception:self.switch_lock.release();raise

    def _switch(self,row):
        previous_port=getattr(self.hub.intent,'service_port',18862);previous_active=self.active
        old_pointer=None;pointer_committed=False
        try:
            if self.pointer.exists():old_pointer=self.pointer.read_bytes()
            if row['adapter']=='fit80':port=18862;health=self.worker_request(port,'/health')
            else:
                port=self.ensure_worker();health=self.worker_request(port,'/activate',dict(id=row['id'],weights_sha256=row['weights_sha256']))
            if not health.get('weights_loaded') or health.get('artifact_sha256')!=row['weights_sha256']:raise ValueError('实际加载权重与所选版本不一致')
            if row['adapter']!='fit80' and health.get('registry_id')!=row['id']:raise ValueError('实际模型版本不一致')
            # Persist only after strict load + numerical readiness. User's motion stays off.
            active=dict(id=row['id'],model_id=health['model_id'],weights_sha256=row['weights_sha256'],port=port,selected_at=time.time())
            tmp=self.pointer.with_suffix('.tmp');tmp.write_text(json.dumps(active),encoding='utf-8');tmp.replace(self.pointer)
            pointer_committed=True
            self.hub.intent.set_service_port(port);self.hub.intent.health=health;self.hub.intent.health_checked_at=time.monotonic()
            self.hub.calibration.observe_model(health);self.active=active
            self.hub.intent.stop()
            with self.lock:self.selection=dict(state='completed',id=row['id'],message='已加载，推理与动作保持停止，请手动启动')
            self.hub.event('实时模型已切换：'+health['model_id'],'good')
        except Exception as error:
            self.hub.intent.set_service_port(previous_port);self.active=previous_active
            self.hub.intent.stop()
            if pointer_committed:
                try:
                    if old_pointer is None:self.pointer.unlink(missing_ok=True)
                    else:
                        rollback=self.pointer.with_suffix('.rollback');rollback.write_bytes(old_pointer);rollback.replace(self.pointer)
                except OSError as restore_error:error=RuntimeError(str(error)+'；持久选择恢复失败：'+str(restore_error))
            with self.lock:self.selection=dict(state='failed',id=row['id'],message='切换失败，保留此前模型：'+str(error)[:220])
            self.hub.event('模型切换失败：'+str(error)[:160],'error')
        finally:self.switch_lock.release()

    def snapshot(self):
        with self.lock:
            health=self.hub.intent.health or {};active_sha=health.get('artifact_sha256')
            versions=[]
            connected=self.connected();fresh=self.remote_at is not None and time.monotonic()-self.remote_at<90 and connected
            for row in self.rows.values():
                value={k:v for k,v in row.items() if k not in ['weights_path','report_path','evaluation_path']}
                value['current']=bool(active_sha and health.get('weights_loaded') and row['weights_sha256']==active_sha)
                value['can_activate']=bool(row['can_activate'] and row.get('remote') and fresh)
                if not row.get('remote'):value['availability_reason']='仅本地归档，等待同步服务器模型目录'
                elif not connected:value['availability_reason']='服务器未连接 · 请在总览页重连'
                elif not fresh:value['availability_reason']='正在同步服务器目录' if self.refreshing else '模型目录已过期，刷新后可切换'
                versions.append(value)
            return dict(schema='model-library-v1',groups=group_models(versions),total_versions=len(versions),
                remote_fresh=bool(fresh),server_connected=connected,refreshing=self.refreshing,refresh_state=self.refresh_state,refresh_revision=self.refresh_revision,
                refreshed_at=self.refresh_completed_at,directory_age_ms=round((time.monotonic()-self.remote_at)*1000)if self.remote_at is not None else None,
                worker_receipt=copy.deepcopy(self.worker_receipt),error=self.error,switch=copy.deepcopy(self.selection),current_model_id=health.get('model_id'),
                current_weights_sha256=active_sha,active=copy.deepcopy(self.active),switching=self.selection['state']=='switching')
