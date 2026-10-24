"""Explicit VR cue collection. Cue labels never certify intention or electrode quality."""
import copy
import json
import math
import secrets
import random
import threading
import time
import datetime
import hashlib
from pathlib import Path
from training.action_schema import ACTION_LABELS,LABEL_AXES


LABELS = {'REST','FORWARD','BACKWARD','LEFT','RIGHT'}
PHASES = {'prepare','cue','task','rest','pause','resume','heartbeat'}

def prospective_schedule(rng,target_seconds=780):
    """Variable instructed intervals; idle ~35%, breaks remain UNKNOWN."""
    totals={label:0. for label in ACTION_LABELS};trials=[];elapsed=0
    while elapsed<target_seconds:
        labelled=sum(totals.values())
        idle=totals['REST']/max(labelled,1)
        if (not trials or trials[-1]['label']!='REST') and (labelled==0 or idle<.35):label='REST'
        else:
            minimum=min(totals[label] for label in ACTION_LABELS[1:]);choices=[label for label in ACTION_LABELS[1:] if totals[label]==minimum]
            label=rng.choice(choices)
        seconds=rng.choice([3,5,8,12]);totals[label]+=seconds
        trials.append(dict(trial=len(trials),label=label,block=len(trials)//12,prepare_ms=500,cue_ms=500,task_ms=seconds*1000,rest_ms=500))
        elapsed+=seconds+1.5
    return trials


def readable_schedule(rng,target_seconds=780):
    """v5 uses persistent cues and longer actions; old schedules stay unchanged."""
    totals={label:0. for label in ACTION_LABELS};trials=[];elapsed=0
    while elapsed<target_seconds:
        labelled=sum(totals.values());idle=totals['REST']/max(labelled,1)
        if (not trials or trials[-1]['label']!='REST') and (labelled==0 or idle<.35):label='REST'
        else:
            minimum=min(totals[label] for label in ACTION_LABELS[1:])
            label=rng.choice([label for label in ACTION_LABELS[1:] if totals[label]==minimum])
        seconds=rng.choice([8,10,12]);totals[label]+=seconds
        trials.append(dict(trial=len(trials),label=label,block=len(trials)//12,
            prepare_ms=2000,cue_ms=1000,task_ms=seconds*1000,rest_ms=2000))
        elapsed+=seconds+5
    return trials

def control_policy_metadata(mode=None):
    name='navigation_mi_gaze_policy.json' if mode in ['navigation_mi_gaze','navigation_mi_only'] else 'joint_control_policy.json'
    raw=(Path(__file__).resolve().parents[1]/'configs'/name).read_bytes()
    policy=json.loads(raw)
    return dict(control_intent=policy['control_intent'],control_policy_schema=policy['schema'],
                control_policy_sha256=hashlib.sha256(raw).hexdigest(),eye_tracking_measured=False)


class GuidedAcquisition:
    def __init__(self,hub,clock=time.monotonic):
        self.hub=hub;self.clock=clock;self.lock=threading.RLock();self.state={'active':False,'formal_training_ready':False}
        self.last_payload=None;self.last_ack=None;self.last_seen=0;self.started=0
        self.phase_cursor=0
        self.desktop_token=None

    def snapshot(self):
        with self.lock:return copy.deepcopy(self.state)

    def sync(self):
        with self.hub.lock:
            return {'hub_monotonic_ms':self.clock()*1000,'sample_index_end':self.hub.sample_index,
                    'source_epoch':self.hub.epoch,'trigger_type':'software_frame_end'}

    def _mask(self,fresh):
        n=len(self.hub.quality.get("channels",[])) if getattr(self.hub,"new_device_mode",False) else 7
        return self.hub.quality.get("training_mask",[False]*n) if fresh else [False]*n

    def _write(self,row):
        relay=getattr(self.hub,'public_relay',None)
        runtime=getattr(self.hub,"gnautilus_runtime",None)
        if relay and row.get('kind')=='marker' and not row.get('local_boundary') and getattr(runtime,'active_transport',None)!='lan_ws':
            row['sample_association']=relay.associate_event(row)
        elif row.get('kind')=='marker':row.setdefault('timing_semantics','Hub receipt sample boundary; not a hardware trigger timestamp')
        if runtime and runtime.descriptor:
            row.setdefault("stream_id",runtime.descriptor["stream_id"])
            row.setdefault("source_kind",runtime.descriptor["source_kind"])
        with self.hub.record_lock:
            if not self.hub.recorder:raise ValueError('Guided recording is not open')
            self.hub.recorder.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n');self.hub.recorder.flush()

    def start(self,data):
        if getattr(self.hub,'new_device_mode',False) and not self.hub.gnautilus_runtime.fresh():raise ValueError('请先在原厂启动共享采集，并确认独立采集端正在送入新鲜g.Nautilus数据')
        if data.get('paradigm')=='self_paced_motor_imagery':return self.start_manual(data)
        build=data.get('build_id');blocks=data.get('blocks',2)
        presentation=data.get('presentation_protocol','legacy_fixation_v1')
        if presentation not in ['legacy_fixation_v1','white_centered_instruction_v2','white_centered_instruction_v3','white_blank_variable_axes_v4','white_continuous_countdown_axes_v5','gnautilus_hybrid_neutral_v1']:
            raise ValueError('Unsupported presentation protocol')
        if not isinstance(build,str) or not 1<=len(build)<=120 or data.get('paradigm')!='guided_motor_imagery':
            raise ValueError('Supported collection is explicitly labelled guided_motor_imagery with build identity')
        if type(blocks) is not int or not 1<=blocks<=20:raise ValueError('Blocks must be 1..20')
        intent_policy=control_policy_metadata()
        with self.hub.lock,self.lock:
            if hasattr(self.hub,'calibration') and self.hub.calibration.snapshot()['active']:raise ValueError('请先结束快速校准')
            if self.state.get('active'):raise ValueError('A guided session is already active')
            if self.hub.source_mode!='live' or self.hub.last_packet is None or not 0<=self.clock()-self.hub.last_packet<.25:
                raise ValueError('Fresh official live Socket required; replay cannot start a personal session')
            with self.hub.record_lock:
                if self.hub.recorder:raise ValueError('Finish the existing recording before starting guided collection')
            if hasattr(self.hub,'intent_control'):self.hub.intent_control.configure({'enabled':False})
            self.hub.start_recording()
            self.last_seen=self.started=self.clock();self.last_payload=self.last_ack=None
            self.phase_cursor=0
            self.state=dict(active=True,session_id=secrets.token_hex(16),recording=self.hub.recording_name,
                paradigm='guided_motor_imagery',build_id=build,blocks=blocks,events_saved=0,last_seq=0,
                phase='starting',label=None,formal_training_ready=False,intention_verified=False,
                wearing_status='unverified',montage_verified=False,trigger_type='software_frame_end',presentation_protocol=presentation)
            self.state.update(intent_policy)
            self.state['donning_id']=getattr(getattr(self.hub,'calibration',None),'placement_id',None)
            self.state['collection_day']=datetime.datetime.now().astimezone().date().isoformat()
            self.state['input_surface']='vr'
            rng=random.Random(int(self.state['session_id'][:16],16));trials=[]
            # Preserve historical schedules; v3 extends usable imagery and distinguishes idle from breaks.
            prepare,cue,task,rests=(2000,2000,6000,[3000,3500,4000]) if presentation=='white_centered_instruction_v3' else (1000,1000,3000,[1500,2000,2500])
            for block in range(blocks):
                labels=sorted(LABELS);rng.shuffle(labels)
                trials.extend(dict(trial=block*5+i,label=label,block=block,prepare_ms=prepare,cue_ms=cue,task_ms=task,rest_ms=rng.choice(rests)) for i,label in enumerate(labels))
            if presentation in ['white_blank_variable_axes_v4','white_continuous_countdown_axes_v5']:
                seconds=data.get('target_seconds',780)
                if type(seconds) is not int or not 60<=seconds<=900:raise ValueError('Collection duration must be 60..900 seconds')
                trials=(readable_schedule if presentation=='white_continuous_countdown_axes_v5' else prospective_schedule)(rng,seconds)
            self.state['labels']=ACTION_LABELS if presentation in ['white_blank_variable_axes_v4','white_continuous_countdown_axes_v5'] else sorted(LABELS)
            if presentation=='gnautilus_hybrid_neutral_v1':
                if not getattr(self.hub,'new_device_mode',False):raise ValueError('Hybrid limb protocol requires the new-device path')
                from gnautilus_ml.tasks import task_spec
                mode=self.hub.ml.api_get('/api/ml/collection-profile')['experiment_mode'];self.state['experiment_mode']=mode
                self.state.update(control_policy_metadata(mode))
                self.state['task_binding_version']=task_spec('REST',mode)['task_binding_version'];self.state['labels']=sorted(LABELS);trials=[]
                for block in range(blocks):
                    labels=sorted(LABELS);rng.shuffle(labels)
                    for label in labels:
                        trials.append(dict(trial=len(trials),block=block,prepare_ms=2000,cue_ms=2000,task_ms=8000,rest_ms=rng.choice([3000,4000]),
                            **task_spec(label,mode),neutral_task_display=True))
            self.state['planned_seconds']=sum((t['prepare_ms']+t['cue_ms']+t['task_ms']+t['rest_ms'])/1000 for t in trials)
            if presentation=='white_continuous_countdown_axes_v5':
                self.state.update(instruction_visibility='continuous_during_task',countdown_displayed=True,prepare_seconds=2,task_seconds_min=8)
            self.state['trials']=trials
            self._write(dict(kind='guided_session',**self.state,hub_monotonic=self.clock(),
                label_semantics='instructed task only; not verified intention',display_timing_verified=False))
            return self.snapshot()

    def event(self,data):
        if getattr(self.hub,'new_device_mode',False):
            rt=self.hub.gnautilus_runtime
            stalled=getattr(rt,'paused',False) or self.hub.last_packet is None or (self.clock()-self.hub.last_packet>=1.5 and self.clock()-getattr(rt,'last_accept_monotonic',0)>=1.5)
            if stalled:
                self.pause_input('source_changed')
        if self.state.get('paradigm')=='self_paced_motor_imagery':return self.manual_event(data)
        with self.hub.lock,self.lock:
            if not self.state.get('active') or data.get('session_id')!=self.state['session_id']:
                raise ValueError('Guided session is absent or changed')
            allowed={'session_id','seq','trial','label','phase','sender_monotonic_ms','sender_frame','clock_offset_ms','sync_rtt_ms','head_pose','xr_active'}
            if set(data)-allowed:raise ValueError('Unsupported marker fields')
            seq=data.get('seq');trial=data.get('trial');label=data.get('label');phase=data.get('phase')
            if type(seq) is not int or not 1<=seq<=50000 or type(trial) is not int or not 0<=trial<10000:
                raise ValueError('Invalid event sequence or trial')
            if trial>=len(self.state['trials']) or label!=self.state['trials'][trial]['label']:raise ValueError('Label does not match scheduled instruction')
            if label not in self.state.get('labels',LABELS) or phase not in PHASES:raise ValueError('Invalid instructed label or phase')
            for field in ['sender_monotonic_ms','clock_offset_ms','sync_rtt_ms']:
                value=data.get(field)
                if type(value) not in [int,float] or not math.isfinite(value) or abs(value)>1e15:
                    raise ValueError('Invalid timing field: '+field)
            if not 0<=data['sync_rtt_ms']<=5000 or type(data.get('sender_frame')) is not int or data['sender_frame']<0:
                raise ValueError('Invalid frame or sync bound')
            payload=json.dumps(data,sort_keys=True,allow_nan=False)
            if seq==self.state['last_seq']:
                if payload!=self.last_payload:raise ValueError('Duplicate sequence changed its contents')
                self.last_seen=self.clock();return copy.deepcopy(self.last_ack)
            if seq!=self.state['last_seq']+1:raise ValueError('Events must arrive in sequence; retry the same event')
            normal_phases=['prepare','cue','task','rest']
            if phase in normal_phases and (trial!=self.phase_cursor//4 or phase!=normal_phases[self.phase_cursor%4]):
                raise ValueError('Trial phases must follow the issued schedule')
            if phase not in normal_phases and trial!=max(0,(self.phase_cursor-1)//4):
                raise ValueError('Pause/heartbeat must refer to the current trial')
            pose=data.get('head_pose')
            if pose is not None and (not isinstance(pose,list) or len(pose)!=7 or
                any(type(v) not in [int,float] or not math.isfinite(v) or abs(v)>1e6 for v in pose)):
                raise ValueError('Head pose must contain seven finite position/quaternion values')
            now=self.clock();fresh=self.hub.last_packet is not None and 0<=now-self.hub.last_packet<.25
            row=dict(kind='marker',code='vr_'+phase,**data,hub_receive_monotonic=now,
                sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch,
                source_mode=self.hub.source_mode,source_fresh=fresh,
                quality_valid=self.hub.quality.get('valid') is True,QC_state=self.hub.quality.get('state'),
                presentation_protocol=self.state['presentation_protocol'],
                channel_training_mask=self._mask(fresh),
                channel_quality_policy=self.hub.quality.get('policy','neuradock_native_green_v1'),
                intention_verified=False,formal_training_ready=False,trigger_type='software_frame_end',
                estimated_event_hub_ms=data['sender_monotonic_ms']+data['clock_offset_ms'],
                sync_uncertainty_ms=data['sync_rtt_ms']/2,display_timing_verified=False)
            issued=self.state['trials'][trial]
            for key in ['experiment_mode','gaze_instruction','limb_instruction','motor_instruction','primary_source','auxiliary_source','task_binding_version']:
                if key in issued:row[key]=issued[key]
            # These are observations, not exclusion decisions; invalid intervals remain marked invalid.
            self._write(row);self.last_seen=now;self.last_payload=payload
            if phase in normal_phases:self.phase_cursor+=1
            self.state.update(last_seq=seq,events_saved=seq,phase=phase,label=label,trial=trial,
                              source_fresh=fresh,quality_valid=row['quality_valid'])
            self.last_ack=dict(ok=True,session_id=self.state['session_id'],ack_seq=seq,
                               sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch)
            return copy.deepcopy(self.last_ack)

    def stop(self,session_id=None,reason='operator_stop'):
        if reason not in ['completed','operator_stop','page_exit','emergency','sender_timeout','source_changed','transport_error','shutdown']:
            reason='operator_stop'
        with self.hub.lock,self.lock:
            if not self.state.get('active'):return self.snapshot()
            if session_id is not None and session_id!=self.state.get('session_id'):raise ValueError('Wrong session')
            manual=self.state.get('paradigm')=='self_paced_motor_imagery'
            if reason=='completed' and not manual and self.phase_cursor!=len(self.state['trials'])*4:
                raise ValueError('Incomplete schedule cannot be labelled completed; abort to retain partial data')
            self._write(dict(kind='marker',code='self_paced_end' if manual else 'guided_end',session_id=self.state['session_id'],reason=reason,
                hub_receive_monotonic=self.clock(),sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch,
                completed=reason=='completed',formal_training_ready=False))
            self.hub.stop_recording();self.desktop_token=None;self.state.update(active=False,phase='completed' if reason=='completed' else 'aborted',reason=reason)
            if getattr(self.hub,'new_device_mode',False):self.state['label']=None
            return self.snapshot()

    def stop_client(self,data):
        with self.hub.lock,self.lock:
            if self.state.get('active') and self.state.get('input_surface')=='desktop':
                if not secrets.compare_digest(str(data.get('client_token','')),self.desktop_token or ''):raise ValueError('Desktop capture belongs to another page')
            return self.stop(data.get('session_id'),data.get('reason','operator_stop'))

    def pause_input(self,reason):
        with self.hub.lock,self.lock:
            if not self.state.get('active') or self.state.get('input_paused'):return
            self._write(dict(kind='marker',code='capture_input_pause',phase='pause',label=None,reason=reason,
                             local_boundary=True,
                             session_id=self.state['session_id'],source_epoch=self.hub.epoch,
                             sample_index_end=self.hub.sample_index,source_fresh=False,hub_receive_monotonic=self.clock()))
            self.state.update(input_paused=True,phase='waiting_input',label=None,recovery_reason=reason,resume_pending=True)

    def resume_input(self):
        with self.hub.lock,self.lock:
            if not self.state.get('active') or not self.state.get('input_paused'):return
            self._write(dict(kind='marker',code='capture_input_resume',phase='unlabelled',label=None,
                             local_boundary=True,
                             session_id=self.state['session_id'],source_epoch=self.hub.epoch,
                             sample_index_end=self.hub.sample_index,source_fresh=True,hub_receive_monotonic=self.clock()))
            self.state.update(input_paused=False,phase='unlabelled',label=None,recovery_reason=None,resume_pending=True)

    def expire(self):
        with self.hub.lock,self.lock:
            manual=self.state.get('paradigm')=='self_paced_motor_imagery'
            client_surface=self.state.get('input_surface')=='acquisition_client'
            hold_timeout=2.5 if client_surface else .75
            idle_timeout=5.0 if client_surface else (2 if manual else 10)
            if getattr(self.hub,'new_device_mode',False) and self.state.get('active'):
                if self.clock()-self.started>(7200 if manual else 1800):self.stop(reason='operator_stop')
                elif self.clock()-self.last_seen>(hold_timeout if self.state.get('label') is not None else idle_timeout):self.pause_input('sender_timeout')
                return
            if getattr(self.hub,'new_device_mode',False) and manual and self.state.get('active') and self.state.get('label') is not None and self.clock()-self.last_seen>hold_timeout:
                self.stop(reason='sender_timeout');return
            if self.state.get('active') and (self.clock()-self.last_seen>idle_timeout or self.clock()-self.started>(7200 if manual else 1800)):
                self.stop(reason='sender_timeout')

    def start_manual(self,data):
        build=data.get('build_id')
        surface=data.get('input_surface','vr')
        if surface not in ['vr','desktop','acquisition_client']:raise ValueError('Unsupported collection surface')
        protocol=data.get('presentation_protocol')
        if not isinstance(build,str) or not 1<=len(build)<=120 or protocol not in ['self_paced_hold_v1','self_paced_axes_v2']:raise ValueError('Manual protocol and build identity required')
        intent_policy=control_policy_metadata()
        with self.hub.lock,self.lock:
            if hasattr(self.hub,'calibration') and self.hub.calibration.snapshot()['active']:raise ValueError('请先结束快速校准')
            if self.state.get('active'):raise ValueError('A collection session is already active')
            mode=None
            if getattr(self.hub,'new_device_mode',False):
                mode=self.hub.ml.api_get('/api/ml/collection-profile')['experiment_mode'] if hasattr(self.hub,'ml') else 'navigation_mi_gaze'
                # v2 clients already consume arbitrary issued instruction text and allowed labels.
                if mode in ['navigation_mi_gaze','navigation_mi_only','gaze_only','no_control','interference'] and data.get('task_guidance_version')!='gaze_navigation_v2':
                    raise ValueError('当前客户端不支持版本化任务提示，请刷新电脑采集页面或更新Quest后开始；尚未建立记录')
            max_age=1.5 if surface=='acquisition_client' else .25
            if self.hub.source_mode!='live' or self.hub.last_packet is None or not 0<=self.clock()-self.hub.last_packet<max_age:raise ValueError('Fresh official live Socket required')
            with self.hub.record_lock:
                if self.hub.recorder:raise ValueError('Finish current recording first')
            if hasattr(self.hub,'intent_control'):self.hub.intent_control.configure({'enabled':False})
            if surface=='acquisition_client' and getattr(self.hub,'new_device_mode',False) and hasattr(self.hub,'gnautilus_runtime'):
                self.hub.gnautilus_runtime.start_recording(allow_recent=True)
            else:
                self.hub.start_recording()
            self.last_seen=self.started=self.clock();self.last_payload=self.last_ack=None
            requested_id=data.get('session_id')
            session_id=requested_id if surface=='acquisition_client' and isinstance(requested_id,str) and len(requested_id)==32 and all(c in '0123456789abcdef' for c in requested_id) else secrets.token_hex(16)
            self.state=dict(active=True,session_id=session_id,recording=self.hub.recording_name,paradigm='self_paced_motor_imagery',
                            presentation_protocol=protocol,collection_day=datetime.datetime.now().astimezone().date().isoformat(),labels=ACTION_LABELS if protocol=='self_paced_axes_v2' else ACTION_LABELS[:5],build_id=build,phase='unlabelled',label=None,events_saved=0,last_seq=0,
                            formal_training_ready=False,intention_verified=False,hold_lease_ms=750,input_surface=surface)
            self.state.update(intent_policy)
            if getattr(self.hub,'new_device_mode',False):
                from gnautilus_ml.tasks import task_spec
                self.state.update(control_policy_metadata(mode))
                labels=['REST'] if mode in ['no_control','interference'] else ACTION_LABELS if protocol=='self_paced_axes_v2' and data.get('allow_combos') else ACTION_LABELS[:5]
                cardinal=[l for l in labels if l in ('REST','FORWARD','BACKWARD','LEFT','RIGHT')]
                self.state.update(experiment_mode=mode,task_binding_version=task_spec('REST',mode)['task_binding_version'],labels=labels,
                                  task_instructions={label:task_spec(label,mode) for label in cardinal},task_instructions_list=[task_spec(label,mode) for label in cardinal])
            self.state['donning_id']=getattr(getattr(self.hub,'calibration',None),'placement_id',None)
            self.desktop_token=(data.get('client_token') or secrets.token_urlsafe(24)) if surface in ('desktop','acquisition_client') else None
            self._write(dict(kind='self_paced_session',**self.state,hub_monotonic=self.clock(),
                             label_semantics='User-held button instruction, not verified intention; release is UNKNOWN, never REST',
                             motor_artifact_confound=True,display_timing_verified=False))
            result=self.snapshot()
            if self.desktop_token:result['client_token']=self.desktop_token
            return result

    def manual_event(self,data):
        # Unity JsonUtility encodes a null string as an empty string.
        data=dict(data)
        client_token=data.pop('client_token',None)
        client_sample_index_end=data.pop('client_sample_index_end',None)
        hold_duration_ms=data.pop('hold_duration_ms',None)
        if data.get('label')=='':data['label']=None
        with self.hub.lock,self.lock:
            if not self.state.get('active') or data.get('session_id')!=self.state['session_id']:raise ValueError('Manual session absent or changed')
            surface=self.state.get('input_surface','vr')
            desktop=surface in ('desktop','acquisition_client')
            if desktop and not secrets.compare_digest(str(client_token or ''),self.desktop_token or ''):raise ValueError('Desktop capture belongs to another page')
            allowed={'session_id','seq','label','phase','sender_monotonic_ms','sender_frame','clock_offset_ms','sync_rtt_ms','head_pose','controller_pose','xr_active','pointer_source','page_active'}
            if set(data)-allowed:raise ValueError('Unsupported manual fields')
            seq=data.get('seq');phase=data.get('phase');label=data.get('label')
            if type(seq) is not int or not 1<=seq<=500000 or phase not in ['hold_start','hold_end','heartbeat']:raise ValueError('Invalid manual event sequence/phase')
            if label is not None and label not in self.state.get('labels',LABELS):raise ValueError('Unknown held label')
            for field in ['sender_monotonic_ms','clock_offset_ms','sync_rtt_ms']:
                v=data.get(field)
                if type(v) not in [int,float] or not math.isfinite(v) or abs(v)>1e15:raise ValueError('Invalid manual timing')
            if not 0<=data['sync_rtt_ms']<=5000 or type(data.get('sender_frame')) is not int or data['sender_frame']<0 or type(data.get('xr_active')) is not bool:raise ValueError('Invalid manual frame/XR timing')
            if data.get('pointer_source') not in ['xr_ray','desktop_pointer','desktop_keyboard','validation_fixture']:raise ValueError('Manual pointer source required')
            if desktop:
                if data.get('pointer_source') not in ['desktop_pointer','desktop_keyboard'] or type(data.get('page_active')) is not bool or data.get('xr_active') is not False:raise ValueError('Actual desktop focus/input provenance required')
                if data.get('page_active') is not True and data.get('label') is not None and data.get('phase')!='hold_end':raise ValueError('Inactive page cannot hold an instruction')
            for field,lengths in [('head_pose',[7]),('controller_pose',[14])]:
                v=data.get(field)
                if v is not None and (not isinstance(v,list) or len(v) not in lengths or any(type(x) not in [int,float] or not math.isfinite(x) or abs(x)>1e6 for x in v)):raise ValueError('Invalid pose')
            payload=json.dumps(data,sort_keys=True,allow_nan=False)
            if seq==self.state['last_seq']:
                if payload!=self.last_payload:raise ValueError('Duplicate manual event changed')
                self.last_seen=self.clock();return copy.deepcopy(self.last_ack)
            if surface=='acquisition_client':
                if seq<self.state['last_seq']+1:return copy.deepcopy(self.last_ack or dict(ok=True,session_id=self.state['session_id'],ack_seq=self.state['last_seq'],sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch))
            elif seq!=self.state['last_seq']+1:raise ValueError('Manual events must arrive in sequence')
            if self.state.get('input_paused') and self.hub.last_packet is not None and 0<=self.clock()-self.hub.last_packet<.25:
                self.resume_input()
            if self.state.get('input_paused'):
                self._write(dict(kind='marker',code='manual_input_wait',phase='waiting_input',label=None,
                    requested_label=label,session_id=self.state['session_id'],source_epoch=self.hub.epoch,
                    sample_index_end=self.hub.sample_index,source_fresh=False,hub_receive_monotonic=self.clock()))
                self.last_seen=self.clock();self.last_payload=payload
                self.state.update(last_seq=seq,events_saved=self.state.get('events_saved',0)+1)
                self.last_ack=dict(ok=True,waiting_input=True,session_id=self.state['session_id'],ack_seq=seq,
                                   sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch)
                return copy.deepcopy(self.last_ack)
            if self.state.get('resume_pending'):
                if phase=='heartbeat' and label is not None:
                    data=dict(data,phase='hold_start');phase='hold_start'
                if phase=='hold_end' and self.state.get('label') is None:
                    data=dict(data,phase='heartbeat',label=None);phase='heartbeat';label=None
                self.state['resume_pending']=False
            current=self.state.get('label')
            if surface!='acquisition_client':
                if phase=='hold_start' and (label is None or current is not None):raise ValueError('Release current label before another start')
                if phase=='hold_end' and (current is None or label!=current):raise ValueError('Held label end mismatch')
                if phase=='heartbeat' and label!=current:raise ValueError('Heartbeat must match held label or UNKNOWN')
            else:
                if phase=='hold_start' and label is None:raise ValueError('Release current label before another start')
            now=self.clock();fresh=self.hub.last_packet is not None and 0<=now-self.hub.last_packet<.25
            source_instructions={}
            if getattr(self.hub,'new_device_mode',False):
                from gnautilus_ml.data import MAPPING,NAVIGATION_MODES
                mode=self.state['experiment_mode'];eye,limb=MAPPING.get(label,('UNKNOWN','UNKNOWN'))
                if mode=='motor_only':eye='CENTER' if label else 'UNKNOWN'
                if mode in ['gaze_only','no_control','interference']:limb='NO_CONTROL' if label else 'UNKNOWN'
                if mode=='no_control':eye='CENTER' if label else 'UNKNOWN'
                if mode in ['no_control','interference'] and label not in [None,'REST']:raise ValueError('Use explicit REST holds for no-control/interference; navigation labels are not valid here')
                source_instructions=dict(experiment_mode=mode,gaze_instruction=eye,limb_instruction=limb,navigation_instruction=label if phase=='hold_start' else None)
                if mode in NAVIGATION_MODES:
                    from gnautilus_ml.tasks import task_spec
                    if label in MAPPING and phase!='hold_end':
                        spec=task_spec(label,mode)
                    elif label and phase!='hold_end':
                        combos={'FORWARD_LEFT':('UP_LEFT','IMAGINE_WALK_FORWARD_LEFT'),'FORWARD_RIGHT':('UP_RIGHT','IMAGINE_WALK_FORWARD_RIGHT'),
                                'BACKWARD_LEFT':('DOWN_LEFT','IMAGINE_WALK_BACKWARD_LEFT'),'BACKWARD_RIGHT':('DOWN_RIGHT','IMAGINE_WALK_BACKWARD_RIGHT')}
                        ceye,cmotor=combos.get(label,('UNKNOWN','UNKNOWN'))
                        spec=dict(gaze_instruction=ceye if mode=='navigation_mi_gaze' else 'CENTER',limb_instruction='NOT_PRESCRIBED',motor_instruction=cmotor,
                                  primary_source='motor_imagery',auxiliary_source='gaze' if mode=='navigation_mi_gaze' else 'none',task_binding_version='navigation_mi_gaze_v3')
                    else:
                        spec={}
                    source_instructions.update({key:spec.get(key,'UNKNOWN') for key in ['gaze_instruction','limb_instruction','motor_instruction','primary_source','auxiliary_source','task_binding_version']})
            extra_meta={}
            if type(client_sample_index_end) is int and client_sample_index_end>=0:extra_meta['client_sample_index_end']=client_sample_index_end
            if type(hold_duration_ms) in (int,float) and math.isfinite(hold_duration_ms) and hold_duration_ms>=0:extra_meta['hold_duration_ms']=hold_duration_ms
            self._write(dict(kind='marker',code='manual_'+phase,**data,**source_instructions,**extra_meta,hub_receive_monotonic=now,sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch,
                             source_fresh=fresh,source_mode=self.hub.source_mode,channel_training_mask=self._mask(fresh),
                             estimated_event_hub_ms=data['sender_monotonic_ms']+data['clock_offset_ms'],sync_uncertainty_ms=data['sync_rtt_ms']/2,
                             intention_verified=False,motor_artifact_confound=True,input_surface=surface,label_semantics='held instruction; no label on release',label_axes=LABEL_AXES[ACTION_LABELS.index(label)] if label else None,hold_lease_ms=750))
            self.last_seen=now;self.last_payload=payload
            self.state.update(last_seq=seq,events_saved=seq,label=None if phase=='hold_end' else label,phase='unlabelled' if phase=='hold_end' or label is None else 'held')
            self.last_ack=dict(ok=True,session_id=self.state['session_id'],ack_seq=seq,sample_index_end=self.hub.sample_index,source_epoch=self.hub.epoch)
            return copy.deepcopy(self.last_ack)
