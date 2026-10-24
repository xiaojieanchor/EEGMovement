"""Optional frozen-model assessment, independent of inference/motion switches."""
import copy
import datetime
import hashlib
import json
import re
import secrets
import threading
import time
from pathlib import Path
import numpy as np
from .session_calibration import LABELS,PROMPTS,SessionCalibration as LegacyCalibration
from .model_check_results import CheckMetrics,write_report


ROOT=Path(__file__).resolve().parents[1]


class SessionCalibration:
    fingerprint=LegacyCalibration.fingerprint
    present=LegacyCalibration.present
    def __init__(self,hub,clock=time.monotonic,root=ROOT):
        self.hub=hub;self.clock=clock;self.root=Path(root);self.lock=threading.RLock()
        self.placement_id=secrets.token_hex(12);self.last_model=None;self.binding=None
        self.active=False;self.state='idle';self.reason='模型性能校验可选；实时推理与动作可独立启动'
        self.calibration_id=None;self.started=None;self.finished_at=None;self.surface=None
        self.mode='directions';self.required_seconds=60.;self.elapsed=0.;self.accepted=0.
        self.cursor=0;self.phase_elapsed=0.;self.schedule=[];self.last_key=None;self.metrics=None
        self.recording=None;self.owned_recorder=None;self.directory=None;self.streams={};self.finalizing=False;self.pending_report=None;self.retry_after=0
        previous=self.history(1);self.last_result=previous[0] if previous else None

    def history(self,limit=12):
        folder=self.root/'outputs/model_checks';out=[]
        for path in sorted(folder.glob('*/*/report.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:limit]:
            try:out.append(self._summary(json.loads(path.read_text(encoding='utf-8'))))
            except (OSError,ValueError):continue
        return out

    @staticmethod
    def _summary(report):
        metrics=report['metrics']
        return {k:report.get(k) for k in ['check_id','mode','state','completed','reason','recording','created_at','observed_seconds','storage_complete','storage_warnings']}|dict(
            model_id=(report.get('model_binding',{}).get('model') or [None])[0],
            metrics={k:metrics.get(k) for k in ['coverage','accuracy_including_rejection','accuracy_on_eligible','balanced_accuracy_supported_classes',
                'full_direction_support','classes','event_correct','event_count','frames','rejected_frames','rest_seconds','rest_false_starts','independent_test']},
            result_url='/api/calibration/result?check_id='+report['check_id'])

    def result(self,check_id):
        if not isinstance(check_id,str) or not re.fullmatch('[0-9a-f]{32}',check_id):raise ValueError('Invalid model check ID')
        paths=list((self.root/'outputs/model_checks').glob('*/'+check_id+'/report.json'))
        if len(paths)!=1:raise ValueError('模型校验结果尚未生成或不存在')
        return json.loads(paths[0].read_text(encoding='utf-8'))

    def _write(self,row):
        if not self.streams:return
        row=dict(kind='model_check',check_id=self.calibration_id,model_binding=self.binding,
            placement_id=self.placement_id,hub_receive_monotonic=self.clock(),sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch)|row
        encoded=json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n'
        with self.hub.record_lock:
            if self.hub.recorder is self.owned_recorder and self.hub.recorder:
                self.hub.recorder.write(encoded);self.hub.recorder.flush()
        self.streams['events.jsonl'].write(encoded);self.streams['events.jsonl'].flush()

    def start(self,surface='desktop',mode='directions',repetitions=1):
        if surface not in ['desktop','vr'] or mode not in ['signal','directions']:raise ValueError('Unsupported model check')
        if type(repetitions) is not int or not 1<=repetitions<=6:raise ValueError('校验轮数须为1至6')
        with self.hub.lock:
            if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
            if self.hub.source_mode!='live' or self.hub.last_packet is None or not 0<=self.clock()-self.hub.last_packet<.25:
                raise ValueError('模型校验需要新鲜官方设备数据')
        if self.hub.acquisition.snapshot().get('active') or self.hub.recorder is not None:raise ValueError('请先结束当前采集')
        health=self.hub.intent.request('GET','/health');classes=len(health.get('labels') or LABELS)
        if not health.get('weights_loaded') or classes not in [5,9]:raise ValueError('模型尚未加载或类别不支持')
        with self.lock:
            if self.active or self.finalizing or self.pending_report:raise ValueError('已有模型校验进行中或正在保存结果')
            self.finalizing=True
        check_id=secrets.token_hex(16)
        directory=self.root/'outputs/model_checks'/datetime.datetime.now().strftime('%Y-%m-%d')/check_id;streams={}
        try:
            directory.mkdir(parents=True,exist_ok=False)
            for name in ['frames.jsonl','events.jsonl']:streams[name]=(directory/name).open('x',encoding='utf-8')
            with self.hub.lock:
                if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
                if self.hub.acquisition.snapshot().get('active') or self.hub.recorder is not None:
                    raise ValueError('其他采集已开始，请先结束当前记录')
                self.hub.intent.health=health;self.observe_model(health)
                self.hub.start_recording();self.owned_recorder=self.hub.recorder
        except Exception:
            for stream in streams.values():stream.close()
            self.finalizing=False;raise
        with self.lock:
            self.calibration_id=check_id;self.mode=mode;self.surface=surface
            self.binding=self.fingerprint();self.started=self.clock();self.finished_at=None
            self.state='checking';self.reason='跟随当前指令；本次只测量，不拟合模型';self.elapsed=self.accepted=self.phase_elapsed=0.
            self.cursor=0;self.last_key=None;self.schedule=[]
            rng=np.random.default_rng(int(self.calibration_id[:8],16))
            for _ in range(repetitions if mode=='directions' else 1):
                labels=['REST']+[str(v) for v in rng.permutation(LABELS[1:])] if mode=='directions' else ['REST']
                self.schedule.extend(dict(stage='check',label=label,seconds=12. if mode=='directions' else 15.,settle_seconds=2.) for label in labels)
            self.required_seconds=sum(p['seconds'] for p in self.schedule)
            self.presentation_token=secrets.token_urlsafe(24);self.display_at=None;self.display_cursor=None;self.display_seq=0;self.logged_cursor=None
            policy=self.hub.intent_control.snapshot();self.metrics=CheckMetrics(classes,policy)
            self.directory=directory;self.streams=streams
            self.recording=getattr(self.hub,'recording_name',None);self.active=True;self.finish_attempted=False;self.storage_warnings=[]
            self.finalizing=False
            policy_file=self.root/'configs/device_channels.json'
            self.channel_policy_sha256=hashlib.sha256(policy_file.read_bytes()).hexdigest() if policy_file.is_file() else None
            self.model_health=copy.deepcopy(health)
            self.hub.intent_control.configure({'enabled':False})
            self._write(dict(event='start',schema='frozen-model-check-v4',schedule=self.schedule,recording=self.recording,
                model_fitted=False,role='unassigned_model_check',previously_inspected=True,independent_test=False,gates_runtime=False,
                channel_policy=getattr(self.hub,'disabled_channels',()),surface=surface,channel_policy_sha256=self.channel_policy_sha256,
                model_health=self.model_health,control_policy=policy))
        return dict(self.snapshot(),presentation_token=self.presentation_token)

    def consume(self,result,payload):
        try:self._consume(result,payload)
        except OSError:
            with self.lock:self.storage_warnings.append('frame_or_event_write_failed')
            self._end('aborted','逐帧或事件保存异常；已保留部分数据，报告标记不完整')

    def _consume(self,result,payload):
        self.tick();finish=False
        if self.active and self.binding!=self.fingerprint():
            self._end('aborted','校验期间模型或配置改变');return
        if self.active:
            actual=(result.get('model_id'),result.get('artifact_sha256'))
            expected=self.binding['model']
            if actual[0]!=expected[0] or actual[1] is not None and actual[1]!=expected[1]:
                with self.lock:self._write(dict(event='response_identity_mismatch',expected_model=expected,response_model=actual,
                    response_sample_index_end=result.get('sample_index_end')))
                self._end('aborted','预测响应的模型身份改变或缺失；已保存部分校验，未混合计分');return
        with self.lock:
            if not self.active:return
            if self.display_cursor!=self.cursor or self.display_at is None or self.clock()-self.display_at>.6:
                self.reason='等待校验画面确认，计时暂停';return
            key=(payload['source_epoch'],payload['sample_index_end'])
            if key==self.last_key:return
            previous=self.last_key;self.last_key=key
            advance=key[1]-previous[1] if previous and previous[0]==key[0] else min(25,len(payload['raw']))
            phase=self.schedule[self.cursor];dt=min(.12,max(0.,advance/250),phase['seconds']-self.phase_elapsed)
            before=self.phase_elapsed;self.phase_elapsed=round(before+dt,12);self.elapsed=round(self.elapsed+dt,12)
            scored=max(0.,self.phase_elapsed-max(before,phase['settle_seconds']))
            p=result.get('raw_probabilities') or result.get('probabilities');a=np.asarray(p,dtype=float)
            probability_ok=a.shape==(self.metrics.classes,) and np.isfinite(a).all() and (a>=0).all() and np.isclose(a.sum(),1)
            mask=np.asarray(payload['quality'],bool).all(0)
            eligible=bool(result.get('eligible') and mask.sum()>=4 and len(result.get('usable_channels',[]))>=4 and probability_ok)
            if eligible:self.accepted=round(self.accepted+dt,12)
            row=dict(check_id=self.calibration_id,cursor=self.cursor,instructed_label=phase['label'],scored=scored>0,scored_seconds=scored,
                observed_seconds=dt,eligible=eligible,reason=result.get('reason'),source_epoch=key[0],sample_index_end=key[1],
                raw_sample_start=key[1]-len(payload['raw']),raw_sample_end=key[1],quality_mask=mask.tolist(),
                usable_channels=result.get('usable_channels',[]),probabilities=a.tolist() if probability_ok else None,
                worker_ms=result.get('worker_ms'),input_age_ms=result.get('input_age_ms'),original_prediction=result.get('prediction'),
                hub_receive_monotonic=self.clock(),original_eligible=result.get('eligible'),qc_semantics=payload.get('qc_semantics'),
                input_advance_ms=result.get('input_advance_ms'),warmup_samples=result.get('warmup_samples'))
            row.update(response_model_id=result.get('model_id'),response_artifact_sha256=result.get('artifact_sha256'))
            self.metrics.add(row)
            encoded=json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n'
            self.streams['frames.jsonl'].write(encoded);self.streams['frames.jsonl'].flush()
            self._write(dict(event='prediction',**row))
            self.reason='准备当前动作，尚未计分' if self.phase_elapsed<phase['settle_seconds'] else '正在校验并保存数据' if eligible else '质量不足或模型拒识；保留该段并计入覆盖率'
            if self.phase_elapsed+1e-9>=phase['seconds']:
                self._write(dict(event='phase_end',cursor=self.cursor,label=phase['label'],observed_seconds=self.phase_elapsed))
                self.cursor+=1;self.phase_elapsed=0.
                finish=self.cursor>=len(self.schedule)
        if finish:self._end('completed','本次模型性能校验完成，结果与数据已保存')

    def _end(self,state,reason):
        with self.lock:
            if not self.active:return self.snapshot()
            self.active=False;self.state=state;self.reason=reason;self.finished_at=self.clock()
            self.finalizing=False
            report=dict(schema='frozen-model-check-result-v4',check_id=self.calibration_id,mode=self.mode,state=state,completed=state=='completed',
                reason=reason,model_binding=self.binding,recording=self.recording,placement_id=self.placement_id,schedule=self.schedule,
                planned_seconds=self.required_seconds,observed_seconds=self.elapsed,wall_seconds=round(self.finished_at-self.started,3),
                metrics=self.metrics.summary(),created_at=datetime.datetime.now().astimezone().isoformat(),model_fitted=False,
                channel_policy_sha256=self.channel_policy_sha256,model_health=self.model_health,
                storage_warnings=list(self.storage_warnings),
                gates_runtime=False,role='unassigned_model_check',previously_inspected=True,independent_test=False)
            raw=self.root/'outputs/sessions'/(self.recording+'.jsonl') if self.recording else None;directory=self.directory
            # Queue the recoverable state before any fallible write/flush/close.
            self.pending_report=(directory,report,raw)
        self._save_pending()
        return self.snapshot()

    def _save_pending(self):
        with self.lock:
            if not self.pending_report or self.finalizing:return
            pending=self.pending_report;self.finalizing=True
        try:
            directory,report,raw=pending
            if not self.finish_attempted:
                self.finish_attempted=True
                try:self._write(dict(event='finish',result=report))
                except OSError:report.setdefault('storage_warnings',[]).append('finish_event_write_failed')
            close_errors=[]
            for name,stream in self.streams.items():
                if stream.closed:continue
                try:stream.close()
                except OSError:
                    report.setdefault('storage_warnings',[]).append(name+'_close_failed')
                    if not stream.closed:close_errors.append(name)
            # stop_recording() enters hub.lock through event(); our lock is not
            # held here, and it must run even if one sidecar close failed.
            with self.hub.lock:
                if self.hub.recorder is self.owned_recorder:
                    try:self.hub.stop_recording()
                    except OSError:close_errors.append('raw_recording_close')
                elif self.hub.recorder is not None or not getattr(self.owned_recorder,'closed',True):
                    report.setdefault('storage_warnings',[]).append('raw_recording_ownership_changed')
            if close_errors or self.hub.recorder is self.owned_recorder:raise OSError('recording close pending')
            self.streams={}
            report['storage_complete']=not bool(report.get('storage_warnings'))
            saved=write_report(*pending)
            with self.lock:
                self.last_result=self._summary(saved);self.pending_report=None;self.state=saved['state'];self.reason=saved['reason']
        except OSError:
            with self.lock:self.state='save_error';self.reason='数据保存遇到异常，已保留待保存状态；正在重试';self.retry_after=self.clock()+1
        finally:
            with self.lock:self.finalizing=False

    def cancel(self,reason='用户结束校验，已保存已采数据'):
        return self._end('cancelled',reason)

    def tick(self):
        if self.pending_report and self.clock()>=self.retry_after:self._save_pending()
        with self.lock:
            if not self.active:return
            lost=self.hub.recorder is not self.owned_recorder
            expired=self.clock()-self.started>max(90,self.required_seconds*2)
            abandoned=self.clock()-(self.display_at or self.started)>3
        if lost:self._end('aborted','原始记录已停止或切换，校验已结束并保存部分结果')
        elif expired:self._end('aborted','校验等待超时，部分数据已保存')
        elif abandoned:self._end('aborted','显示端失联，部分数据已保存')

    def observe_model(self,health):
        identity=(health.get('model_id'),health.get('artifact_sha256'))
        if not identity[0]:return
        if self.last_model is not None and self.last_model!=identity:self.cancel('模型更换，已保存旧模型的部分校验数据')
        self.last_model=identity

    def invalidate(self,reason='已标记新佩戴；模型校验可选',new_placement=False):
        self.cancel(reason)
        if new_placement:self.placement_id=secrets.token_hex(12)
        self.reason=reason
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            phase=self.schedule[self.cursor] if self.active and self.cursor<len(self.schedule) else None
            return copy.deepcopy(dict(schema='model-check-v4',active=self.active,state=self.state,valid=False,gates_runtime=False,
                reason=self.reason,mode=self.mode,placement_id=self.placement_id,calibration_id=self.calibration_id,surface=self.surface,
                accepted_seconds=self.accepted,elapsed_seconds=self.elapsed,required_seconds=self.required_seconds,
                wall_seconds=round((self.finished_at if self.finished_at is not None else self.clock())-self.started,1) if self.started is not None else 0,
                cursor=self.cursor,phase_count=len(self.schedule),phase_elapsed=self.phase_elapsed,
                stage='check',label=phase['label'] if phase else None,prompt=PROMPTS.get(phase['label'],'') if phase else '',
                phase_seconds=phase['seconds'] if phase else 0,settle_seconds=phase['settle_seconds'] if phase else 0,
                last_result=self.last_result,manual_start_required=True,independent_accuracy_estimate=False))
