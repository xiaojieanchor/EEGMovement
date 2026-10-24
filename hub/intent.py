"""Independent causal model monitor: current signal or bounded personal replay.

No model score is substituted for the existing VR execution acknowledgement.
"""
import json,threading,time,secrets,collections,http.client
from pathlib import Path
import numpy as np
from .remote import ChannelHTTP
from .training_input import TrainingClipLibrary,HeldTrainingInput


def effective_mask(quality):
    mask=quality.get('training_mask')
    if not isinstance(mask,list) or len(mask)!=7 or any(type(v) is not bool for v in mask):return [False]*7
    mask=list(mask);channels=quality.get('channels',[])
    if quality.get('policy')=='neuradock_notch50_rechecked_v2':return mask
    if len(channels)==7 and all(mask[i] for i in [0,1,3,4,5,6]):
        ch=channels[2]
        if ch.get('native_code')==1 and (ch.get('reasons')==['50Hz干扰'] or (ch.get('valid') is True and ch.get('reasons')==[])):mask[2]=True
    return mask


def make_live_payload(raw,available_end,epoch,quality_events,last_sent,stream_id):
    from training.quality import channel_sample_mask,online_channel_mask
    events=[dict(q,kind='channel_quality',training_mask=effective_mask(q)) for q in quality_events if q.get('kind')!='quality_resolution' and q.get('source_epoch')==epoch and q.get('sample_index_end',available_end+1)<=available_end]
    if not events:return None
    online=all(type(q.get('available_sample_index_end')) is int for q in events)
    end=available_end if online else max(q['sample_index_end'] for q in events)
    continuity=online and events[-1].get('continuity_policy')=='native_status_continuity_200ms_v1'
    pending=events[-1].get('continuity_pending_start') if continuity else None
    if pending is not None:end=min(end,pending)
    start=last_sent[1] if last_sent and last_sent[0]==epoch else max(0,end-1000)
    oldest=available_end-len(raw)
    start=max(start,oldest)
    if start>=end or end-start<25:return None
    # All samples since the last successful request; never drop a tail because a
    # polling period happened to contain 30 or 35 samples instead of exactly25.
    samples=[list(row) for row in raw[start-oldest:end-oldest]]
    if continuity:
        from training.quality_continuity import resolved_channel_mask
        resolutions=[q for q in quality_events if q.get('kind')=='quality_resolution']
        mask=resolved_channel_mask(events+resolutions,start,end,epoch,available_end)
    else:mask=(online_channel_mask if online else channel_sample_mask)(events,start,end,epoch)
    return dict(stream_id=stream_id,head='trial',raw=samples,quality=mask.tolist(),source_epoch=epoch,sample_index_end=end,
        qc_semantics='delayed_status_confirmed_200ms_v1' if continuity else 'online_known_at_sample' if online else 'legacy_retrospective',qc_available_sample_index_end=available_end,probability_smoothing='hub_only')


class IntentMonitor:
    def __init__(self,hub,root):
        self.hub=hub;self.root=Path(root);self.lock=threading.RLock();self.rpc_lock=threading.Lock();self.client=None;self.connection=None;self.token=None
        self.mode='live';self.latest=None;self.latest_at=None;self.health=None;self.error=None;self.running_replay=False;self.cancel=threading.Event();self.replay_report=None
        self.stream_id='live-'+secrets.token_hex(6);self.last_live=None;self.attempt_after=0
        self.health_checked_at=0
        self.quality_events=collections.deque(maxlen=64)
        self.inference_enabled=False;self.starting=False;self.generation=0
        self.diagnostic_only=False;self.service_port=18862
        self.clip_library=TrainingClipLibrary(root,hub);self.test_input=HeldTrainingInput(self.clip_library.get);self.test_revision=0

    def _test_status(self):
        state=self.test_input.snapshot()
        if state['revision']!=self.test_revision:
            self.test_revision=state['revision'];self.generation+=1;self.latest=None;self.latest_at=None;self.last_live=None
            self.mode='TRAINING_TEST' if state['active'] else 'live';self.error=None
            self.attempt_after=0
            if hasattr(self.hub,'intent_control'):self.hub.intent_control.update(None) if hasattr(self.hub.intent_control,'update') else None
        return state

    def cancel_test(self):
        with self.lock:self.test_input.cancel();self._test_status()

    def test_command(self,data):
        if data.get('action')=='press':
            if getattr(self.hub,'recorder',None) is not None or hasattr(self.hub,'acquisition') and self.hub.acquisition.snapshot().get('active'):raise ValueError('请先结束个人采集，再运行训练数据测试')
        with self.hub.lock,self.lock:
            if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
            if data.get('action')=='press':
                if not self.inference_enabled or self.running_replay:raise ValueError('请先启动实时推理')
                manifest=self.clip_library.catalog();health=self.health or {}
                if manifest['model_id']!=health.get('model_id') or manifest['artifact_sha256']!=health.get('artifact_sha256'):raise ValueError('测试片段与当前模型不匹配')
            self.test_input.command(data);value=self._test_status()
        if data.get('action')=='press' and hasattr(self.hub,'vr'):self.hub.vr.stop_test()
        return value

    def start(self,purpose='normal'):
        if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
        if purpose not in ['normal','diagnostic']:raise ValueError('Unknown inference purpose')
        calibration=getattr(self.hub,'calibration',None)
        if getattr(self.hub,'source_mode','live')!='live':raise ValueError('请先切换到真实设备输入')
        with self.hub.lock,self.lock:
            if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
            if self.running_replay:raise ValueError('请先停止个人记录回放')
            already_enabled=self.inference_enabled and self.diagnostic_only==(purpose=='diagnostic')
            if not already_enabled:
                self.generation+=1;generation=self.generation;self.starting=True;self.error=None;self.latest=None
        # snapshot takes hub.lock before self.lock. Idempotent starts must obey
        # that same order, including when a VR readiness tick is in progress.
        if already_enabled:return self.snapshot()
        if hasattr(self.hub,'intent_control'):self.hub.intent_control.configure({'enabled':False})
        if hasattr(self.hub,'vr'):self.hub.vr.stop_test()
        try:
            health=self.request('GET','/health')
            if not health.get('weights_loaded') or health.get('runtime_profile') not in ['vr_fit80','causal_jepa_v2']:raise ValueError('JEPA候选尚未加载，请检查模型服务')
            with self.hub.lock,self.lock:
                if self.generation==generation:
                    if calibration:calibration.observe_model(health)
                    self.health=health;self.health_checked_at=time.monotonic();self.inference_enabled=True;self.starting=False;self.cancel.clear();self.diagnostic_only=purpose=='diagnostic'
                    self.latest=None;self.latest_at=None;self.last_live=None;self.stream_id='live-'+secrets.token_hex(6);self.attempt_after=0
            return self.snapshot()
        except Exception as error:
            with self.lock:
                if self.generation==generation:self.starting=False;self.inference_enabled=False;self.error=str(error)[:160]
            raise ValueError(str(error)) from error

    def stop(self):
        with self.lock:
            self.test_input.cancel();self._test_status()
            self.inference_enabled=False;self.starting=False;self.generation+=1;self.cancel.set();self.latest=None;self.latest_at=None;self.diagnostic_only=False
            self.last_live=None;self.stream_id='live-'+secrets.token_hex(6)
        if hasattr(self.hub,'intent_control'):self.hub.intent_control.configure({'enabled':False})
        if hasattr(self.hub,'vr'):self.hub.vr.stop_test()
        return self.snapshot()

    def record_quality(self,quality):
        with self.lock:self.quality_events.append(dict(quality,kind=quality.get('kind','channel_quality')))

    def request(self,method,path,payload=None):
        with self.rpc_lock:
            client=self.hub.remote.client
            if client is None or client.get_transport() is None or not client.get_transport().is_active():raise ConnectionError('请先连接计算服务器')
            if client is not self.client:
                if self.connection:self.connection.close()
                self.client=client;self.connection=None
                with client.open_sftp() as ftp:
                    with ftp.open('/home/sysu/EEGMovement/run/intent.token') as f:self.token=f.read().decode().strip()
            for attempt in range(2):
                if self.connection is None:self.connection=ChannelHTTP(client.get_transport(),self.service_port)
                try:
                    self.connection.request(method,path,body=json.dumps(payload,allow_nan=False).encode() if payload is not None else None,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
                    response=self.connection.getresponse();body=response.read()
                    if response.status!=200:raise RuntimeError('Model service HTTP '+str(response.status))
                    return json.loads(body)
                except Exception as error:
                    self.connection.close();self.connection=None
                    # GET is side-effect free; never replay a possibly committed
                    # streaming POST after an ambiguous broken connection.
                    if method=='GET' and attempt==0 and isinstance(error,(OSError,http.client.HTTPException)):continue
                    raise

    def set_service_port(self,port):
        if port not in [18862,18863,18864,18865]:raise ValueError('Unexpected model service port')
        with self.rpc_lock:
            if self.connection:self.connection.close()
            self.connection=None;self.service_port=port
        with self.lock:
            self.generation+=1;self.latest=None;self.latest_at=None;self.health=None;self.last_live=None;self.error=None
            self.stream_id='live-'+secrets.token_hex(6)

    def snapshot(self):
        now=time.monotonic()
        source=None
        source_samples=0
        quality_error=None
        if hasattr(self.hub,'lock'):
            with self.hub.lock:
                source=(self.hub.epoch,self.hub.source_mode,self.hub.last_packet,bool(self.hub.remote.status.get('ssh_connected')))
                source_samples=len(getattr(self.hub,'raw',[]))
                quality_error=getattr(self.hub,'quality',{}).get('native_error')
        with self.lock:
            test=self._test_status()
            calibration=getattr(self.hub,'calibration',None)
            calibration_state=calibration.snapshot() if calibration else None
            age=(now-self.latest_at)*1000 if self.latest_at else None
            fresh=bool(age is not None and age<500 and self.latest and self.latest.get('eligible'))
            fresh=fresh and (self.inference_enabled or self.mode=='PERSONAL_REPLAY' and not self.cancel.is_set())
            if self.latest and self.latest.get('source')=='LIVE' and source is not None:
                epoch,mode,packet,connected=source
                fresh=fresh and bool(mode=='live' and packet is not None and now-packet<.25 and connected and epoch==self.latest.get('source_epoch'))
                # A recent server return does not make its old input recent.
                fresh=fresh and now-self.latest.get('input_origin_monotonic',0)<.5
            if self.latest and self.latest.get('source')=='TRAINING_TEST':fresh=fresh and (source is None or source[3]) and test['active'] and self.latest.get('test_revision')==test['revision'] and self.latest.get('source_epoch')==test['source_epoch'] and now-self.latest.get('input_origin_monotonic',0)<.5
            current=bool(self.latest and (test['active'] and self.latest.get('source')=='TRAINING_TEST' and self.latest.get('test_revision')==test['revision'] or not test['active'] and (source is None or self.latest.get('source_epoch')==source[0])) and age is not None and age<750)
            progress=(self.latest or {}) if current else {}
            count=int(progress.get('warmup_samples',0));required=int(progress.get('warmup_required_samples',1000))
            message='';inference_state='warming'
            if self.starting:inference_state='starting';message='正在连接并检查模型服务'
            elif not self.inference_enabled:inference_state='stopped';message='推理已停止；点击开始实时推理'
            elif self.diagnostic_only and not test['active']:inference_state='diagnostic_ready';message='诊断已就绪；按住训练数据方向盘发送，不接收实时EEG'
            elif source and not source[3]:inference_state='waiting_server';message='计算服务器未连接；请先恢复服务器连接'
            elif not test['active'] and source and (source[1]!='live' or source[2] is None or now-source[2]>=.25):
                inference_state='waiting_source';count=0;message='等待采集新样本：检查设备蓝牙和官方Socket数据源；中枢会自动重连'
            elif self.error:inference_state='error';message=self.error
            elif fresh:inference_state='running';message='实时推理中'
            elif current and progress.get('reason') in ['filter_warming','model_warming']:
                inference_state=progress['reason']
                message=(f'滤波预热 {count}/{required} 样本（{count/250:.2f}/{required/250:.2f}秒）' if inference_state=='filter_warming' else '模型预热：正在重建因果卷积、GRU和注意力状态')
            elif current and progress.get('reason')=='warming':
                message=f'连续信号预热 {count} / {required} 样本（{count/250:.2f} / {required/250:.2f} 秒）；真实缺包会重新积累'
            elif current and progress.get('reason') not in [None,'eligible','duplicate']:
                inference_state='rejected';message={'insufficient_usable_channels':f"质量门控：过去4秒连续通过 {len(progress.get('usable_channels',[]))} 路，至少需要4路；等待质量恢复",'channel_quality_excluded':'质量门控：输入含排除通道','late_return':'模型返回超过500ms，已丢弃；检查服务器及网络延迟'}.get(progress['reason'],progress['reason'])
            elif test['active']:message='训练数据测试：正在加载历史上下文并等待真实模型返回'
            elif source:
                inference_state='waiting_input';message=f'等待连续输入与质量检测（当前连续段 {source_samples} 样本）；断点后自动恢复'
            if not test['active'] and self.inference_enabled and inference_state in ['warming','waiting_input','rejected'] and quality_error:message+='；'+str(quality_error)[:140]
            # The headset score block has two short lines for the reason. Keep
            # the full diagnostic on PC and send a readable compact counterpart.
            short={'starting':'正在检查模型服务','stopped':'推理已停止','waiting_server':'等待计算服务器连接','waiting_source':'等待设备样本 · 检查蓝牙/Socket','waiting_input':'等待连续输入及质量检测','running':'实时推理中','error':'推理服务异常，请检查电脑中枢'}.get(inference_state)
            if inference_state=='warming':short=f'预热 {count}/{required} 样本（{count/250:.1f}/{required/250:.1f}秒）'
            elif inference_state=='filter_warming':short=f'滤波预热 {count}/{required} 样本'
            elif inference_state=='model_warming':short='模型预热 · 正在重建历史状态'
            elif inference_state=='rejected':short=f"质量门控：有效 {len(progress.get('usable_channels',[]))}/4 路" if progress.get('reason')=='insufficient_usable_channels' else '返回未通过门控，请检查电脑中枢'
            readiness=dict(message=message,short_message=short,warmup_samples=count,warmup_required_samples=required,continuous_source_samples=source_samples,source_epoch=source[0] if source else None)
            input_age=(now-self.latest.get('input_origin_monotonic',now))*1000 if self.latest else None
            value=dict(mode=self.mode,state='replaying' if self.running_replay else 'monitoring',fresh=fresh,age_ms=age,input_age_ms=input_age,
                       inference_enabled=self.inference_enabled,inference_state=inference_state,generation=self.generation,
                       diagnostic_only=self.diagnostic_only,calibration=calibration_state,
                       readiness=readiness,
                       training_test=test,
                       health=self.health if source is None or source[3] else None,error=self.error,latest=self.latest if fresh else None,rejection=(self.latest or {}).get('reason'),replay_report=self.replay_report,
                       calibrated=False,model_check_required=False,vr_control_enabled=bool(getattr(self.hub,'intent_control',None) and self.hub.intent_control.snapshot()['enabled']))
            return value

    def publish(self,result,origin,mode,truth=None,generation=None):
        result=dict(result,source=mode,truth=truth,roundtrip_ms=(time.monotonic()-origin)*1000,input_origin_monotonic=origin)
        if result['roundtrip_ms']>=500:result.update(eligible=False,reason='late_return',probabilities=None,active_probability=None,direction_probabilities=None,prediction=None)
        with self.lock:
            test=self._test_status()
            if mode in ['LIVE','TRAINING_TEST'] and (not self.inference_enabled or generation is not None and generation!=self.generation or mode=='LIVE' and test['active'] or mode=='TRAINING_TEST' and (not test['active'] or result.get('test_revision')!=test['revision'])):return dict(result,eligible=False,reason='inference_stopped')
            self.latest=result;self.latest_at=time.monotonic();self.error=None
        # Persist real model decisions alongside raw EEG only during an explicit
        # recording. Scores never create a user-intention label.
        if mode=='LIVE' and hasattr(self.hub,'record_lock'):
            with self.hub.record_lock:
                if self.hub.recorder:self.hub.recorder.write(json.dumps(dict(kind='model_prediction',**result),allow_nan=False)+'\n')
        return result

    def loop(self):
        while not self.hub.stop_event.wait(.03):
            calibration=getattr(self.hub,'calibration',None)
            if calibration:calibration.tick()
            if self.running_replay or time.monotonic()<self.attempt_after:continue
            try:
                if not self.hub.remote.status.get('ssh_connected'):continue
                if not self.health or time.monotonic()-self.health_checked_at>=5:
                    with self.lock:health_generation=self.generation
                    health=self.request('GET','/health')
                    with self.hub.lock,self.lock:
                        if self.generation!=health_generation or getattr(self.hub,'model_library',None) and self.hub.model_library.switching():continue
                        self.health=health;self.health_checked_at=time.monotonic()
                        if not self.inference_enabled:self.error=None
                        if calibration:calibration.observe_model(health)
                with self.lock:
                    calibrating=bool(calibration and calibration.snapshot()['active'])
                    if not self.inference_enabled and not calibrating:continue
                    test=self._test_status()
                    generation=self.generation;stream_id=self.stream_id;last_live=self.last_live
                    test_payload=self.test_input.batch() if test['active'] else None
                if test['active']:
                    if getattr(self.hub,'recorder',None) is not None or self.hub.acquisition.snapshot().get('active'):self.cancel_test();continue
                    if test_payload is None:continue
                    origin=time.monotonic();result=self.request('POST','/predict',test_payload)
                    if result.get('source_epoch')!=test_payload['source_epoch'] or result.get('sample_index_end')!=test_payload['sample_index_end']:raise ValueError('Test model response identity differs')
                    result.update(test_revision=test['revision'],test_label=test['label'],training_recording=test['recording'])
                    self.publish(result,origin,'TRAINING_TEST',generation=generation);continue
                if self.diagnostic_only and not calibrating:continue
                with self.hub.lock:
                    epoch=self.hub.epoch;end=self.hub.sample_index;last=self.hub.last_packet;mode=self.hub.source_mode;q=self.hub.quality.copy();raw=list(self.hub.raw)
                if mode!='live' or last is None or time.monotonic()-last>.25 or len(raw)<25:continue
                with self.lock:events=list(self.quality_events)
                payload=make_live_payload(raw,end,epoch,events,last_live,stream_id)
                if payload is None:continue
                for i in getattr(self.hub,'disabled_channels',()):
                    for sample,mask in zip(payload['raw'],payload['quality']):sample[i]=0.;mask[i]=False
                origin=last-(end-payload['sample_index_end'])/250
                result=self.request('POST','/predict',payload)
                if result.get('source_epoch')!=epoch or result.get('sample_index_end')!=payload['sample_index_end']:raise ValueError('Model response source identity differs')
                with self.lock:
                    if (not self.inference_enabled and not calibrating) or self.generation!=generation:continue
                    self.last_live=(epoch,payload['sample_index_end'])
                with self.hub.lock:unchanged=self.hub.epoch==epoch and self.hub.source_mode=='live' and self.hub.last_packet is not None and time.monotonic()-self.hub.last_packet<.25
                if unchanged:
                    if calibrating:
                        check_result=result if time.monotonic()-origin<.5 else dict(result,eligible=False,reason='late_return')
                        calibration.consume(check_result,payload)
                    if self.inference_enabled and not self.diagnostic_only:self.publish(result,origin,'LIVE',generation=generation)
            except Exception as error:
                with self.lock:self.error=str(error)[:160];self.latest=None;self.health=None
                self.attempt_after=time.monotonic()+3

    def start_replay(self,session,head='heldout'):
        from .replay import session_path
        if head not in ['trial','heldout']:raise ValueError('Unknown model head')
        path=session_path(self.root/'outputs/sessions',session)
        self.stop()
        with self.hub.lock,self.lock:
            if getattr(self.hub,'model_library',None) and self.hub.model_library.switching():raise ValueError('模型正在切换，请等待加载结果')
            if self.running_replay:raise ValueError('个人回放正在运行')
            self.running_replay=True;self.cancel.clear();self.mode='PERSONAL_REPLAY';self.latest=None;self.replay_report=None
        threading.Thread(target=self.replay,args=(path,head),daemon=True).start()

    def stop_replay(self):
        self.cancel.set()

    def replay(self,path,head):
        from training.intent_stream import personal_windows
        from training.metrics import classification_metrics
        from training.intent_stream import probability_decision
        directory=self.root/'outputs/intent_replay'/(time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3))
        predictions=[];aborted=False
        try:
            directory.mkdir(parents=True,exist_ok=False)
            rows,session=personal_windows(path);truth={r['end']:r for r in rows};indices=session['indices'];epochs=session['epochs']
            records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            packets=[r for r in records if r.get('kind')=='eeg']
            times=np.asarray([p['sample_index_end'] for p in packets]);clock=np.asarray([p['receive_monotonic'] for p in packets])
            start=time.monotonic();base=clock[0];stream='replay-'+secrets.token_hex(6);self.health=self.request('GET','/health')
            with (directory/'predictions.jsonl').open('x',encoding='utf-8',buffering=1) as log:
                for a in range(0,len(indices),25):
                    b=min(a+25,len(indices));end=int(indices[b-1])+1
                    target=clock[min(np.searchsorted(times,end),len(clock)-1)]-base
                    if self.cancel.wait(max(0,target-(time.monotonic()-start))):aborted=True;break
                    if self.hub.stop_event.is_set():aborted=True;break
                    if session['breaks'][a:b].any() or len(np.unique(epochs[a:b]))!=1 or np.any(np.diff(indices[a:b])!=1):continue
                    origin=time.monotonic()
                    payload=dict(stream_id=stream,head=head,raw=session['eeg'][a:b].tolist(),quality=session['ch3_exception_quality'][a:b].tolist(),source_epoch=int(epochs[a]),sample_index_end=end)
                    result=self.request('POST','/predict',payload);r=truth.get(end,{})
                    result=self.publish(result,origin,'PERSONAL_REPLAY',dict(label=r.get('label'),trial=r.get('trial')))
                    result.update(replay_elapsed_s=time.monotonic()-start,schedule_lag_ms=max(0,(time.monotonic()-start-target)*1000),expected_eligible=bool(r.get('valid')))
                    log.write(json.dumps(result,allow_nan=False)+'\n');predictions.append(result)
            good=[r for r in predictions if r['eligible'] and r['truth']['label'] is not None]
            by_trial={}
            for r in good:
                t=r['truth']['trial']
                if t and ':vr_task' in t:by_trial.setdefault(t,[]).append(r)
            yy=[];pp=[];hierarchical=[];trial_details=[]
            for trial,items in by_trial.items():
                y=items[0]['truth']['label'];prob=np.mean([r['probabilities'] for r in items],axis=0);d=probability_decision(prob)
                yy.append(y);pp.append(int(prob.argmax()));hierarchical.append((['REST','FORWARD','BACKWARD','LEFT','RIGHT']).index(d['prediction']))
                trial_details.append(dict(trial=trial,label=y,probabilities=prob.tolist(),joint_prediction=int(prob.argmax()),hierarchical_prediction=hierarchical[-1],windows=len(items)))
            lat=[r['roundtrip_ms'] for r in predictions if r['eligible']];lag=[r['schedule_lag_ms'] for r in predictions]
            report=dict(state='aborted' if aborted else 'complete',session=path.name,head=head,training_overlap='short session only' if head=='heldout' else 'both sessions; seen-data replay',responses=len(predictions),eligible_responses=sum(r['eligible'] for r in predictions),labeled_windows=len(good),trial_metrics=classification_metrics(yy,pp,5) if yy else None,
                        latency_ms=dict(p50=float(np.percentile(lat,50)),p95=float(np.percentile(lat,95)),p99=float(np.percentile(lat,99))) if lat else None,schedule_lag_p95_ms=float(np.percentile(lag,95)) if lag else None,elapsed_s=time.monotonic()-start,directory=str(directory),model_id=(self.health or {}).get('model_id'))
            import hashlib
            report.update(hierarchical_trial_metrics=classification_metrics(yy,hierarchical,5) if yy else None,trial_details=trial_details,expected_eligible_responses=sum(r['expected_eligible'] for r in predictions),eligibility_mismatches=sum(r['eligible']!=r['expected_eligible'] for r in predictions),
                          normalized_probability_failures=sum(r['eligible'] and (len(r['probabilities'])!=5 or not np.isclose(sum(r['probabilities']),1,atol=1e-6)) for r in predictions),
                          session_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),quality_mode='Recorded retrospective sample masks; does not validate online QC detection delay',source_mode='original chronological raw samples at recorded pacing; causal filters; reset on gaps/epochs',signal_to_vr_latency_measured=False,probability_calibration_verified=False,
                          implementation_sha256={p:hashlib.sha256((self.root/p).read_bytes()).hexdigest() for p in ['hub/intent.py','training/intent_stream.py']})
            (directory/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            with self.lock:self.replay_report=report
        except Exception as error:
            with self.lock:self.error=type(error).__name__+': '+str(error)
        finally:
            with self.lock:self.running_replay=False;self.mode='live';self.latest=None;self.last_live=None;self.stream_id='live-'+secrets.token_hex(6)
