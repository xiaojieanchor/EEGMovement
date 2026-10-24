"""Read-only, bounded SSH observer for v2 preparation/training (no credentials)."""
import json,threading,time
from pathlib import Path

PROBE=r'''
import json,math,time,subprocess,hashlib
from pathlib import Path
r=Path('/home/sysu/EEGMovement');out={}
queue=r/'outputs/causal_queue_status.json'
if queue.exists():out['queue']=json.loads(queue.read_text())
out['parallel_training']=[]
for queue_file in ['d05_confirmation_parallel_v1.json','balanced_sequence_status.json','scope_sequence_status.json','baseline_sequence_status.json','corrected_head_status.json']:
 secondary=r/'outputs'/queue_file
 if not secondary.exists() or secondary.stat().st_size>65536:continue
 try:
  extra=json.loads(secondary.read_text());fresh=0<=time.time()-extra.get('updated_at',0)<30
  if queue_file=='d05_confirmation_parallel_v1.json':out['auxiliary_training']=dict(extra,fresh=fresh)
  out['parallel_training'].append({k:extra.get(k)for k in ['owner','state','stage','active_experiments','completed_conditions','pending_conditions','error','updated_at']})
  out['parallel_training'][-1]['fresh']=fresh
  if fresh:
   q=out.setdefault('queue',{});q['primary_phase']=q.get('state')
   q['pending_conditions']=list(dict.fromkeys(q.get('pending_conditions',[])+extra.get('pending_conditions',[])))
   q['completed_conditions']=list(dict.fromkeys(q.get('completed_conditions',[])+extra.get('completed_conditions',[])))
   if extra.get('state')=='training' and extra.get('active_experiments'):
    q['active_experiments']=list(dict.fromkeys(q.get('active_experiments',[])+extra['active_experiments']))
    if q.get('experiment') not in q['active_experiments']:q['experiment']=q['active_experiments'][0]
    if q.get('state')!='failed':q['state']='training'
 except (OSError,ValueError,TypeError):pass
try:
 result=subprocess.run(['nvidia-smi','--query-gpu=index,memory.free,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
 if result.returncode==0:
  gpus=[]
  for line in result.stdout.strip().splitlines()[:4]:
   index,free,utilization=[int(v.strip())for v in line.split(',')]
   gpus.append(dict(index=index,free_bytes=free*1024**2,utilization_percent=utilization))
  out.setdefault('queue',{})['gpus']=gpus
except (OSError,ValueError,subprocess.SubprocessError):pass
out['preflight']=[]
preflight_roots=[r/'outputs/training_governance/preflight']
workspace=out.get('queue',{}).get('workspace')
if isinstance(workspace,str):
 target=(r/workspace).resolve()
 if target.is_relative_to((r/'experiments').resolve()):preflight_roots.append(target/'outputs/training_governance/preflight')
preflight_files=[p for folder in preflight_roots for p in folder.glob('*.json') if p.stat().st_size<=65536]
for p in sorted(preflight_files,key=lambda p:p.stat().st_mtime)[-4:]:
 try:out['preflight'].append(json.loads(p.read_text()))
 except (OSError,ValueError):pass
log=r/'run/causal_prepare.log'
if log.exists():
 with log.open('rb') as f:
  f.seek(max(0,log.stat().st_size-4096));tail=f.read().decode(errors='replace')
 out['preparation_log_tail']=tail[-2000:]
p=r/'outputs/causal_public_preparation.json'
if p.exists():
 v=json.loads(p.read_text());out['preparation']={k:v.get(k) for k in ['state','files_done','files_total','shards','elapsed_seconds']};out['preparation']['failure_count']=len(v.get('failures',[]));out['preparation']['failures']=v.get('failures',[])[:3]
p=r/'outputs/refactor_v2/public-future-summary-v2.json'
if p.exists() and p.stat().st_size<=65536:
 try:
  v=json.loads(p.read_text())
  if v.get('schema')=='public-future-diagnostic-summary-v2' and v.get('task_accuracy_measured') is False and v.get('independent_test') is False and v.get('live_qualified') is False:
   out['future_audit']=dict(state=v.get('state'),task_accuracy_measured=False,independent_test=False,models=[{k:m.get(k) for k in ['experiment','checkpoint_step','checkpoint_kind','training_target_mean_feature_std','validation_cells','cells_learned_better_than_constant','report_sha256']} for m in v.get('models',[])[:2]])
 except (OSError,ValueError,TypeError):pass
recovery=r/'outputs/refactor_v2/real-recovery-v8/report.json'
if recovery.exists() and recovery.stat().st_size<65536:
 try:
  v=json.loads(recovery.read_text())
  if v.get('schema')=='governed-real-eeg-recovery-v8' and v.get('state')=='passed' and v.get('restored_step')==2 and v.get('final_step')==6 and v.get('task_efficacy_validated') is False and v.get('live_qualified') is False:
   out['recovery_validation']={k:v.get(k) for k in ['state','restored_step','final_step','world_size','bf16','max_absolute_state_delta','task_efficacy_validated','live_qualified']}
 except (OSError,ValueError,TypeError):pass
out['runs']=[]
focus_queue=out.get('queue',{})
focused_ids=set(focus_queue.get('active_experiments',[]))
if not focused_ids:focused_ids=set(focus_queue.get('completed_conditions',[]))
if focus_queue.get('experiment'):focused_ids.add(focus_queue['experiment'])
directories=[p for version in ['causal_v2','causal_v6','d05_v1','sequence_v1'] for p in (r/'outputs/training'/version).glob('*') if p.is_dir()]
for d in sorted(directories,key=lambda p:p.stat().st_mtime)[-12:]:
 if not d.is_dir():continue
 row={'id':d.name,'state':'inactive','runner_version':d.parent.name};p=d/'progress-rank0.jsonl';updated=d.stat().st_mtime
 if d.parent.name in ['causal_v6','d05_v1','sequence_v1']:
  attempts=sorted(d.glob('progress-rank0-attempt-*.jsonl'),key=lambda p:p.stat().st_mtime)
  if attempts:p=attempts[-1]
  status=d/'execution-status.json'
  if status.exists():
   try:row['execution']={k:v for k,v in json.loads(status.read_text()).items() if k in ['state','attempt_id','restored_step','actual_updates']}
   except (OSError,ValueError,TypeError):pass
 scoring=d/'scoring-status.json'
 if scoring.exists() and scoring.stat().st_size<=8192:
  try:
   value=json.loads(scoring.read_text())
   if value.get('schema')=='causal-scoring-progress-v1':
    row['scoring']={k:value[k] for k in ['state','checkpoint_role','split','recording','recordings_done','recordings_total','recording_events_done','recording_events_total','events_done','events_total','updated_at','elapsed_seconds','error'] if k in value}
  except(OSError,ValueError,TypeError):pass
 architecture=d/'architecture.json'
 if architecture.exists():
  try:row['architecture']={k:v for k,v in json.loads(architecture.read_text()).items() if k in ['placement','parameters','trainable_parameters','core_gru','post_gru']}
  except (OSError,ValueError,TypeError):pass
 if p.exists():
  updated=p.stat().st_mtime
  with p.open('rb') as f:
   f.seek(max(0,p.stat().st_size-8192));lines=f.read().decode(errors='replace').splitlines()
  for line in reversed(lines):
   try:
    progress=json.loads(line)
    if 'inputs' in progress:progress['actual_input_count']=len(progress.pop('inputs'))
    # Full batch masks/gains, event lists and source metadata stay in immutable
    # logs. They are not needed to render this bounded polling response.
    fields=['step','total_steps','progress','loss','unweighted_ce','learning_rate','gradient_clipped','gradient_norm','domain','step_seconds','elapsed_seconds',
     'protocol_sha256','attempt_id','loss_components','actual_input_count','supervised_hops','pseudo_hops']
    row['progress']={k:progress[k]for k in fields if k in progress};break
   except ValueError:pass
  # Uniform byte positions bound reads while covering the full real run.
  # These are actual optimizer receipts, never interpolated loss values.
  points={};size=p.stat().st_size
  count=24 if d.name in focused_ids else 6
  with p.open('rb') as f:
   for number in range(count):
    position=int(size*number/count);f.seek(position)
    if position:f.readline(16385)
    line=f.readline(16385)
    if len(line)>16384:continue
    try:
     sample=json.loads(line);step=sample.get('step');loss=sample.get('loss')
     if type(step) is int and type(loss) in [int,float] and math.isfinite(loss):points[step]={k:sample.get(k)for k in ['step','loss','unweighted_ce','learning_rate','gradient_norm','elapsed_seconds']}
    except (ValueError,UnicodeError):pass
  latest=row.get('progress',{})
  if type(latest.get('step')) is int and type(latest.get('loss')) in [int,float] and math.isfinite(latest['loss']):points[latest['step']]={k:latest.get(k) for k in ['step','loss','unweighted_ce','learning_rate','gradient_norm','elapsed_seconds']}
  row['loss_history']=[points[k] for k in sorted(points)]
  # Bounded explicit head/tail windows; never smooth sparse sampled points.
  windows=[]
  with p.open('rb')as f:
   head=f.read(1048576).decode(errors='replace').splitlines()
   f.seek(max(0,size-1048576));tail=f.read().decode(errors='replace').splitlines()
  for lines in [head,tail]:
   values=[]
   for line in lines:
    try:
     value=json.loads(line);ce=value.get('unweighted_ce')
     if type(value.get('step'))is int and type(ce)in [int,float]and math.isfinite(ce):values.append(value)
    except(ValueError,UnicodeError):pass
   windows.append(values)
  first=windows[0][:50];last=windows[1][-50:]
  if first and last:row['loss_trend']=dict(first_count=len(first),last_count=len(last),first_step=first[0]['step'],last_step=last[-1]['step'],first_ce=sum(x['unweighted_ce']for x in first)/len(first),last_ce=sum(x['unweighted_ce']for x in last)/len(last),scope='current_attempt_unweighted_ce')
 p=d/'report.json'
 if p.exists():
  updated=max(updated,p.stat().st_mtime);row['state']='complete'
  v=json.loads(p.read_text());row['report']={k:v.get(k) for k in ['stage','actual_updates','maximum_updates','stopped_early','actual_microbatches','best_step','live_qualified','deployed','restored_step','attempt_id']}
  row['report']['history']=[dict(step=h.get('step'),score=h.get('score'),metrics={k:h.get('metrics',{}).get(k) for k in ['loss','accuracy','balanced_accuracy','macro_f1','session_balanced_accuracy']}) for h in v.get('history',[])[-2:]]
  history_limit=64 if d.name in focused_ids else 2
  row['validation_history']=[dict(step=h.get('step'),score=h.get('score'),balanced_accuracy=h.get('metrics',{}).get('session_balanced_accuracy',h.get('metrics',{}).get('balanced_accuracy')),loss=h.get('metrics',{}).get('loss')) for h in v.get('history',[])[-history_limit:]]
  if v.get('stage')in ['supervised_sequence','supervised_stable_sequence','supervised_balanced_sequence','supervised_scope_sequence','supervised_causal_baselines'] and isinstance(v.get('results'),dict):
   def compact_scores(value):
    pooled=value.get('pooled_duration',{});support=pooled.get('class_seconds',[])
    return dict(balanced_accuracy=value.get('session_balanced_accuracy'),pooled_balanced_accuracy=pooled.get('balanced_accuracy'),accuracy=pooled.get('accuracy'),macro_f1=pooled.get('macro_f1'),
     coverage=pooled.get('coverage'),known_seconds=pooled.get('known_seconds'),recordings=len(value.get('per_recording',[])),classes=sum(x>0 for x in support),class_seconds=support,class_recall=pooled.get('class_recall',[]))
   history=row['validation_history'];row['evaluation']=dict(kind='historical_sequence_shadow',best_step=v.get('best_step'),
    training=compact_scores(v['results'].get('train',{})),development=compact_scores(v['results'].get('validation',{})),
    latest_development_ba=history[-1].get('balanced_accuracy') if history else None,independent_test=False,calibrated_track=False)
  if d.parent.name=='d05_v1' and v.get('stage')=='supervised':
   actual_root=d.resolve().parents[3]
   if actual_root==r.resolve() or actual_root.is_relative_to((r/'experiments').resolve()):
    folder=actual_root/'outputs/refactor_v2/d05_eval_v1'/d.name
    for path in sorted(folder.glob('*/report.json'),key=lambda p:p.stat().st_mtime,reverse=True):
     if path.stat().st_size>4*1024*1024:continue
     try:
      shadow=json.loads(path.read_text())
      if shadow.get('schema')!='frozen-d05-private-evaluation-v1' or shadow.get('protocol_sha256')!=v.get('protocol_sha256') or shadow.get('weights_sha256')!=v.get('weights_sha256') or shadow.get('independent_test') is not False or shadow.get('live_qualified') is not False:continue
      scores={}
      for role in ['train','validation']:
       timelines=[t for t in shadow['results'][role]['timelines']if t['intervention']=='normal']
       if not timelines:raise ValueError('No complete normal shadow role')
       matrices=[t['raw_duration_scores']['confusion_seconds_with_reject_column']for t in timelines]
       cm=[[sum(a[i][j]for a in matrices)for j in range(10)]for i in range(9)]
       support=[sum(row)for row in cm];total=sum(support);tp=[cm[i][i]for i in range(9)];pred=[sum(row[i]for row in cm)for i in range(9)]
       present=[i for i,s in enumerate(support)if s>0];recall=[tp[i]/support[i]if support[i]else 0 for i in range(9)]
       scores[role]=dict(balanced_accuracy=sum(t['raw_duration_scores']['balanced_accuracy']for t in timelines)/len(timelines),
        pooled_balanced_accuracy=sum(recall[i]for i in present)/len(present),accuracy=sum(tp)/total,
        macro_f1=sum(2*tp[i]/(support[i]+pred[i])if support[i]+pred[i]else 0 for i in present)/len(present),
        coverage=sum(sum(row[:9])for row in cm)/total,known_seconds=total,recordings=len(timelines),classes=len(present),class_seconds=support,class_recall=recall)
      row['evaluation']=dict(kind='historical_d05_shadow',best_step=v.get('best_step'),training=scores['train'],development=scores['validation'],
       report_relative=str(path.relative_to(actual_root)),independent_test=False,calibrated_track=False)
      break
     except (OSError,ValueError,KeyError,TypeError,ZeroDivisionError):continue
 # Saved validation probabilities are emitted at real validation boundaries.
 # This is conditional crop evidence, never full-flow coverage or a test set.
 try:
  spec=json.loads((d/'protocol.json').read_text())
  row['context']={k:spec.get('runner',{}).get(k) for k in ['stage','device','adaptation','profile','world_size','seed']}
  placement=d/'launch-placement.json';locked=d.resolve().parents[3]/'configs'/(d.name+'.protocol.json')
  if placement.exists()and placement.stat().st_size<=4096 and locked.exists():
   location=json.loads(placement.read_text())
   if location.get('protocol_sha256')==hashlib.sha256(locked.read_bytes()).hexdigest()and location.get('physical_gpu_uuid')=='GPU-68bca3c5-cd6e-066a-b543-7466ac87ad62'and location.get('physical_gpu_index')==1 and location.get('logical_device')==spec.get('runner',{}).get('device'):
    row['context'].update(physical_gpu_index=1,physical_gpu_uuid=location['physical_gpu_uuid'])
  committed=d/'readonly-validation-summary.json'
  if not row.get('report') and committed.exists() and committed.stat().st_size<=65536:
   summary=json.loads(committed.read_text());actual_root=d.resolve().parents[3];source=actual_root/'configs'/(d.name+'.protocol.json')
   if summary.get('schema')=='committed-validation-observer-v1' and summary.get('training_performed')is False and summary.get('selected_weights_changed')is False and summary.get('independent_test')is False and source.exists()and json.loads(source.read_text())==spec and hashlib.sha256(source.read_bytes()).hexdigest()==summary.get('protocol_sha256'):
    row['validation_scope']='full_chronological_development'
    row['validation_history']=[{k:h.get(k)for k in ['step','balanced_accuracy','pooled_balanced_accuracy','coverage','macro_f1']}for h in summary.get('history',[])[-64:]if h.get('step',-1)<=summary.get('committed_step',-1)]
  if spec.get('runner',{}).get('stage')=='supervised':
   files=sorted(d.glob('predictions-*.json'),key=lambda p:(int(p.stem.split('-')[1]),p.stat().st_mtime))
   if files and files[-1].stat().st_size<=2*1024*1024:
    samples=json.loads(files[-1].read_text());support=[0]*9;correct=[0]*9
    for sample in samples:
     labels=sample.get('labels',[]);mask=sample.get('scoring_mask',[True]*len(labels))
     if len(mask)!=len(labels) or any(type(v) is not bool for v in mask):raise ValueError('Invalid locked scoring support')
     for y,prob,accepted in zip(labels,sample.get('probabilities',[]),mask):
      if accepted and isinstance(y,int) and 0<=y<9 and len(prob)==9:
       support[y]+=1;correct[y]+=int(max(range(9),key=lambda i:prob[i])==y)
    present=[i for i,n in enumerate(support) if n]
    if present:row['validation_probe']=dict(step=int(files[-1].stem.split('-')[1]),class_support=support,
     balanced_accuracy=sum(correct[i]/support[i] for i in present)/len(present),
     accuracy=sum(correct)/sum(support),missing_primary_classes=[i for i in range(5) if not support[i]],
     role='viewed_development_probe',conditional_on_quality_selected_crops=True,independent_test=False)
 except (OSError,ValueError,TypeError,KeyError):pass
 row['updated_at']=updated;out['runs'].append(row)
q=out.get('queue',{});active=q.get('experiment');active_set=set(q.get('active_experiments',[]));failed_set={v.get('experiment') for v in q.get('failures',[]) if isinstance(v,dict)}
# Old failed queue snapshots omitted the experiment. Attach them only to the
# latest unfinished run, rather than pretending its last log is still live.
if q.get('state')=='failed' and not active and not failed_set:
 pending=[row for row in out['runs'] if not row.get('report')]
 active=max(pending,key=lambda row:row['updated_at'])['id'] if pending else None
for row in out['runs']:
 if row['id'] in failed_set and not row.get('report'):
  row['state']='failed';row['error']=q.get('error')
 elif (row['id']==active or row['id'] in active_set) and not row.get('report'):
  row['state']='failed' if q.get('state')=='failed' else ('preparing_inputs' if not row.get('progress') else 'running') if q.get('state')=='training' else 'waiting'
  if row['state']=='failed':row['error']=q.get('error')
body=json.dumps(out,allow_nan=False)
if len(body.encode())>60000:
 # Long-budget completed queues may have64 validation entries each. Keep
 # evenly selected ACTUAL records plus the last point; never interpolate.
 out['status_history_compacted']=True
 for row in out['runs']:
  for field in ['loss_history','validation_history']:
   values=row.get(field,[])
   if len(values)>12:row[field]=[values[round(i*(len(values)-1)/11)]for i in range(12)]
 body=json.dumps(out,allow_nan=False)
print(body)
'''


class RefactorObserver:
    def __init__(self,hub,root):self.hub=hub;self.root=Path(root);self.lock=threading.Lock();self.state={};self.at=None;self.error=None
    def loop(self):
        while not self.hub.stop_event.is_set():
            try:
                client=self.hub.remote.client
                if client and client.get_transport() and client.get_transport().is_active():
                    _,stdout,stderr=client.exec_command("/home/sysu/EEGMovement/.venv-training/bin/python - <<'EEG_STATUS'\n"+PROBE+"\nEEG_STATUS",timeout=8)
                    body=stdout.read(65537)
                    if len(body)>65536:raise ValueError('Bounded training status exceeded')
                    if stdout.channel.recv_exit_status()!=0:raise ValueError('Remote preparation status unavailable')
                    value=json.loads(body)
                    with self.lock:self.state=value;self.at=time.monotonic();self.error=None
            except Exception as e:
                with self.lock:self.error=type(e).__name__
            self.hub.stop_event.wait(5)
    def snapshot(self):
        with self.lock:
            age=(time.monotonic()-self.at)*1000 if self.at is not None else None
            remote=dict(self.state,age_ms=age,fresh=age is not None and age<15000,error=self.error)
        return dict(schema='refactor-readiness-v2',calibration=self.hub.calibration.snapshot(),remote=remote,
            models=[dict(profile='pilot',parameters=5609177),dict(profile='main',parameters=34833881),dict(profile='large',parameters=78201049)],
            prospective_data_required=True,automatic_live_promotion=False,collection=dict(days=5,donnings=8,minutes=120,valid_minutes_target=90),
            gates=dict(primary_five_ba=.70,baseline_delta=.05,coverage=.80,idle_far_per_minute=.2,worker_p95_ms=100,action_proxy_p95_ms=500))
