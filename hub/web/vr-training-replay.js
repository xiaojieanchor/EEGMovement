'use strict';
(function(){
 const names={online_original:'旧在线模型',fewshot_v3:'低样本微调',gaze_lowshot:'眼动局部分支',large_personal:'69M 个人微调'};
 function summaries(report){return Object.entries(report?.results||{}).map(([key,value])=>{
  const s=key==='online_original'?value:value.equal_weight_seed_ensemble;
  return {key,name:names[key]||key,role:'训练内VR回放',accuracy:s?.metrics?.frame?.accuracy,balancedAccuracy:s?.metrics?.frame?.balanced_accuracy,
   stable:s?.stable_metrics?.frame?.accuracy,initial:s?.initial_two_guided_vr?.frame?.accuracy,crossRecordBA:value.fixed_neural_vr_only_development?.metrics?.frame?.balanced_accuracy};
 })}
 function targetedSummaries(snapshot){
  const out=[];
  for(const key of ['targeted_gate_context','targeted_gate_local']){
   const report=snapshot?.[key+'_report'];if(!report?.aggregate_selected)continue;
   const m=report.aggregate_selected,control=report.outer_control||{};let total=0,executed=0;
   for(const r of control.recordings||[]){const n=Math.max(0,(r.eligible_frames||0)-Math.round((r.eligible_rest_seconds||0)*10));total+=n;executed+=n*(r.active_execution_fraction||0)}
   out.push({name:key.endsWith('context')?'新69M动静门':'新轻量动静门',activeBA:m.active_at_half?.balanced_accuracy,fitActiveBA:report.original_training_vr_fit?.active_at_half?.balanced_accuracy,
    gatedBA:m.gated_five_class_at_half?.balanced_accuracy,executedActiveFraction:total?executed/total:null,falseOnsets:control.rest_false_activation_onsets,restSeconds:control.eligible_rest_seconds});
  }
  return out;
 }
 function fit80Summary(snapshot){
  const r=snapshot?.vr_fit80_report,j=snapshot?.vr_fit80,replay=r?.selected?.strict_replay,m=replay?.metrics;if(!m)return null;
  const cm=m.frame?.confusion||[],support=cm.map(row=>row.reduce((a,b)=>a+b,0)),recall=cm.map((row,i)=>support[i]>0?row[i]/support[i]:0);
  const values=[m.frame?.accuracy,m.frame?.balanced_accuracy,m.active_at_half?.balanced_accuracy,m.gated_five_class_at_half?.accuracy,m.gated_five_class_at_half?.balanced_accuracy];
  const met=Boolean(j?.state==='complete'&&r.state==='complete'&&j.directory===r.directory&&r.fit_goal_met&&replay.fit_goal_met&&replay.strict_load&&replay.actual_batch1_replay&&m.eligible_frames===1175&&support.reduce((a,b)=>a+b,0)===1175&&recall.length===5&&recall.every(v=>v>=.75)&&values.every(v=>Number.isFinite(v)&&v>=.8&&v<=1));
  return {met,accuracy:m.frame?.accuracy,balancedAccuracy:m.frame?.balanced_accuracy,activeBA:m.active_at_half?.balanced_accuracy,gatedAccuracy:m.gated_five_class_at_half?.accuracy,gatedBA:m.gated_five_class_at_half?.balanced_accuracy,
   frames:m.eligible_frames,labelled:m.labelled_primary_frames,recall,developmentAccuracy:replay.development?.metrics?.frame?.accuracy,developmentBA:replay.development?.metrics?.frame?.balanced_accuracy,p95:replay.model_compute?.p95_ms,
   seed:r.selected.seed,epoch:r.selected.epoch,parameters:r.selected.parameters,independentTest:false,liveReady:false};
 }
 if(typeof module!=='undefined')module.exports={summaries,targetedSummaries,fit80Summary};
 if(typeof document==='undefined')return;
 const percent=x=>Number.isFinite(x)?(100*x).toFixed(1)+'%':'—';
 function render(report,snapshot){
  const fit=fit80Summary(snapshot),fitBlock=document.getElementById('vrFit80Block');
  if(fitBlock){fitBlock.hidden=!fit;if(fit){
   document.getElementById('vrFit80State').textContent=fit.met?'训练内80%目标已达标 · 保存模型逐帧复核通过':'等待同一轮严格回放确认，暂不判定达标';
   document.getElementById('vrFit80Metrics').textContent='五类准确率 '+percent(fit.accuracy)+' · 五类平衡准确率 '+percent(fit.balancedAccuracy)+' · 动静平衡准确率 '+percent(fit.activeBA)+' · 活动门控五类准确率 '+percent(fit.gatedAccuracy)+' / 平衡准确率 '+percent(fit.gatedBA)+'。';
   document.getElementById('vrFit80Recall').textContent=['静止','前进','后退','左转','右转'].map((s,i)=>s+'召回 '+percent(fit.recall[i])).join(' · ');
   document.getElementById('vrFit80Scope').textContent='原始六段VR，'+fit.frames+' / '+fit.labelled+'个五类标注帧通过原质量门；训练与选模均使用这些记录。约 '+(fit.parameters/1e6).toFixed(2)+'M参数 · seed '+fit.seed+' · 入选epoch '+fit.epoch+' · 单帧GPU计算p95 '+Number(fit.p95).toFixed(2)+'ms（不含证据积累、网络和VR）。';
   document.getElementById('vrFit80Development').textContent='跨记录开发VR：准确率 '+percent(fit.developmentAccuracy)+'，平衡准确率 '+percent(fit.developmentBA)+'。训练内高分不能代表新记录可靠；本候选尚未替换在线模型。';
  }}
  const section=document.getElementById('vrTrainingReplay');if(!section)return;
  const rows=summaries(report),body=document.getElementById('vrReplayRows');body.replaceChildren();
  document.getElementById('vrReplayState').textContent=rows.length?'原始训练VR记录 · 已完成回放':'等待原始VR回放报告';
  for(const row of rows){const tr=document.createElement('tr');for(const text of [row.name,percent(row.accuracy),percent(row.stable),percent(row.initial),percent(row.crossRecordBA)]){const td=document.createElement('td');td.textContent=text;tr.append(td)}body.append(tr)}
  const targetRows=document.getElementById('vrTargetedRows');targetRows.replaceChildren();
  const target=targetedSummaries(snapshot);
  for(const r of target){const tr=document.createElement('tr');for(const text of [r.name,percent(r.fitActiveBA),percent(r.activeBA),percent(r.gatedBA),percent(r.executedActiveFraction),(r.falseOnsets??'—')+' / '+Number(r.restSeconds||0).toFixed(1)+'秒']){const td=document.createElement('td');td.textContent=text;tr.append(td)}targetRows.append(tr)}
  document.getElementById('vrTargetedBlock').hidden=!target.length;
  if(!rows.length)return;
  const latest=report.results.large_personal?.equal_weight_seed_ensemble,m=latest?.metrics;
  document.getElementById('vrReplayScope').textContent=(report.selected_recordings?.length||0)+' 段原始训练VR记录，共 '+Number(report.raw_seconds/60).toFixed(2)+' 分钟；五类有效 '+(m?.eligible_frames||0)+' / '+(m?.labelled_primary_frames||0)+' 帧（100ms更新）。此次评分排除电脑端记录；组合方向另列于原始数据，未混入五分类。';
  if(!latest)return;
  const records=latest.recordings||[],seconds=records.reduce((s,r)=>s+(r.controller?.eligible_rest_seconds||0),0),falseOnsets=records.reduce((s,r)=>s+(r.controller?.rest_false_activation_onsets||0),0);
  const idle=m?.active_at_half?.confusion?.[0]||[];
  document.getElementById('vrReplayFinding').textContent='69M：训练内五分类平衡准确率 '+percent(m?.frame?.balanced_accuracy)+'；真实活动片段方向准确率 '+percent(m?.intention?.direction_on_all_true_active_trials?.accuracy)+'。动概率阈值0.5时，静止误判为动 '+(idle[1]??'—')+' / '+idle.reduce((a,b)=>a+b,0)+' 帧；固定原控制阈值回放，'+seconds.toFixed(1)+'秒有效静止有 '+falseOnsets+' 次误启动。当前仍未通过实时控制可靠性验收。';
 }
 window.EEGVRReplay={render};
})();
