'use strict';
(function(){
 const names={vr_fit80:'训练内80%目标 · JEPA非线性头',targeted_gate_context:'针对性VR · 69M动静与方向',targeted_gate_local:'针对性VR · 轻量眼动对照',large_jepa:'68.98M JEPA · 公开续训',large_personal:'69M · 个人微调',lowshot_reset:'低样本 · 修复后对照',fewshot_v3:'低样本 · 首次对照',gaze_lowshot:'眼动波形 · 局部支路',causal_gaze:'联合控制 · 因果历史',public_gaze:'实测眼动 · 公开训练',gaze_transfer:'公开眼动 → 个人动作'};
 const phases={starting_vr_fit80:'启动训练内拟合',preparing_vr_fit80:'准备原始VR因果特征',training_vr_fit80:'训练归一化非线性分类头',strict_batch1_vr_replay:'重载模型 · 逐帧回放核验',vr_fit80_finished:'训练内拟合与逐帧核验已完成',targeted_activity_direction_fit:'独立动静门与条件方向训练',targeted_vr_fit_complete:'针对性VR训练完成',preparing_targeted_vr:'准备完整VR记录',optimizer_updates:'训练批次更新',training_full:'多库公开训练',training_warm:'公开热身',epoch_complete_full:'本轮训练与验证已完成',epoch_complete_warm:'本轮热身与验证已完成',full_public_ssl_complete:'公开训练已完成',training_lowshot_encoder:'个人编码器微调',training_causal_gaze_memory:'因果联合控制训练',training_real_posterior_gaze:'实测眼动辅助训练',waiting_for_full_public_and_personal_comparisons:'等待公开训练完成',waiting_for_completed_candidates:'等待候选验收'};
 const num=(x,d=0)=>Number.isFinite(x)?x.toLocaleString('zh-CN',{maximumFractionDigits:d}):'—',loss=x=>Number.isFinite(x)?x.toFixed(5):'—';
 function duration(s){return Number.isFinite(s)?s<60?Math.ceil(s)+'秒':Math.floor(s/60)+'分'+Math.ceil(s%60)+'秒':'—'}
 function viewModel(snapshot,key,now=Date.now()/1000){
  const t=snapshot.telemetry||{},j=t.jobs?.[key]||{},p=j.progress||{},percent=Number.isFinite(p.percent)&&p.percent>=0&&p.percent<=100?p.percent:null;
  const completed=j.state==='complete';
  return {job:j,progress:p,percent,completed,fresh:now-(t.synced_at||0)>=0&&now-(t.synced_at||0)<25,receiptFresh:completed||now-(j.updated_at||0)>=0&&now-(j.updated_at||0)<65,
   epoch:p.epoch??j.epoch,maxEpochs:p.max_epochs??j.max_epochs,batchText:Number.isFinite(p.batch)&&Number.isFinite(p.batches)?num(p.batch)+' / '+num(p.batches)+' 批次':'该任务尚未逐批上报',
   age:Math.max(0,Math.floor(now-(j.updated_at||0)))};
 }
 function eventText(e){
  const parts=[Number.isFinite(e.updated_at)?new Date(e.updated_at*1000).toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'',phases[e.phase]||e.phase||e.state||'训练回执'];
  if(e.epoch)parts.push('epoch '+e.epoch+(e.max_epochs?'/'+e.max_epochs:''));
  if(e.fold!==undefined)parts.push(e.fold==='all'?'全部记录拟合':'外层会话 '+(Number(e.fold)+1));
  if(e.candidate)parts.push(e.candidate);if(e.seed)parts.push('seed '+e.seed);
  if(Number.isFinite(e.steps))parts.push('累计 '+num(e.steps)+' steps');if(Number.isFinite(e.examples_current_epoch))parts.push('本轮 '+num(e.examples_current_epoch)+' 窗口');
  if(Number.isFinite(e.latest_loss))parts.push('loss '+loss(e.latest_loss));if(Number.isFinite(e.train?.loss))parts.push('train loss '+loss(e.train.loss));
  if(Number.isFinite(e.validation?.loss))parts.push('val loss '+loss(e.validation.loss));if(Number.isFinite(e.validation_score))parts.push('验证选模分数 '+loss(e.validation_score));
  if(Number.isFinite(e.training_fit_score))parts.push('训练内最低目标指标 '+(100*e.training_fit_score).toFixed(1)+'%');if(e.error)parts.push(e.error);return parts.filter(Boolean).join(' · ');
 }
 if(typeof module!=='undefined')Object.assign(module.exports,{viewModel,eventText});
 if(typeof document==='undefined')return;
 const el=id=>document.getElementById(id);let snapshot={},selected='vr_fit80';
 const select=el('trainingJob');for(const [key,title] of Object.entries(names)){const o=document.createElement('option');o.value=key;o.textContent=title;select.append(o)}
 select.value=selected;select.addEventListener('change',()=>{selected=select.value;render()});
 function metric(label,value){const box=document.createElement('div'),dt=document.createElement('span'),dd=document.createElement('strong');dt.textContent=label;dd.textContent=value;box.append(dt,dd);return box}
 function chart(rows){
  const svg=el('trainingLossChart');svg.replaceChildren();const ns='http://www.w3.org/2000/svg',values=rows.flatMap(r=>[r.train_loss,r.validation_loss]).filter(Number.isFinite);
  if(!values.length){el('trainingChartNote').textContent='等待首个已完成 epoch 的损失记录';return}
  const min=Math.min(...values),max=Math.max(...values),padding=Math.max((max-min)*.12,.002),lo=Math.max(0,min-padding),hi=max+padding;
  const x=i=>60+i*820/Math.max(1,rows.length-1),y=v=>170-(v-lo)*140/(hi-lo);
  function node(tag,attrs,text){const e=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;svg.append(e);return e}
  for(let i=0;i<3;i++){const v=lo+(hi-lo)*i/2;node('line',{x1:60,y1:y(v),x2:880,y2:y(v),stroke:'#e5eaf2'});node('text',{x:4,y:y(v)+4,fill:'#6c7688','font-size':11},v.toFixed(3))}
  for(const [key,color] of [['train_loss','#3478f6'],['validation_loss','#10a18a']]){
   let points=[];function flush(){if(points.length)node('polyline',{points:points.join(' '),fill:'none',stroke:color,'stroke-width':2.5});points=[]}
   rows.forEach((r,i)=>{if(Number.isFinite(r[key])){points.push(x(i)+','+y(r[key]));node('circle',{cx:x(i),cy:y(r[key]),r:2.5,fill:color})}else flush()});flush();
  }
  node('text',{x:60,y:196,fill:'#6c7688','font-size':11},'Epoch '+rows[0].epoch);node('text',{x:880,y:196,fill:'#6c7688','font-size':11,'text-anchor':'end'},'Epoch '+rows.at(-1).epoch);
  el('trainingChartNote').textContent='蓝色：训练平均 loss · 绿色：验证 loss（有上报时）；仅绘制已完成轮次';
 }
 function render(){
  window.EEGVRReplay?.render(snapshot.vr_training_replay_report,snapshot);
  const m=viewModel(snapshot,selected),j=m.job,p=m.progress,st={running:'训练中',waiting:'等待依赖',complete:'已完成',failed:'失败'};
  el('trainingDetailState').textContent=!m.fresh?'日志同步已过期'+(m.completed?' · 最后确认：已完成':''):(st[j.state]||'等待读取')+' · '+(phases[j.phase]||j.phase||'');
  const completedAt=Number.isFinite(j.updated_at)?new Date(j.updated_at*1000).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}):'—';
  el('trainingDetailStamp').textContent=(m.completed?'任务完成于 '+completedAt+'（北京时间） · 常驻同步每5秒检查':'每5秒同步 · 大模型每30秒上报 · 最近训练回执 '+(m.age<1e6?m.age+'秒前':'—'))+(j.run?' · run '+j.run:'');
  el('trainingEpoch').textContent=m.epoch?'Epoch '+m.epoch+(m.maxEpochs?' / '+m.maxEpochs:'')+(m.completed?' · 已结束':''):m.completed?'拟合与回放已结束':j.phase==='strict_batch1_vr_replay'?'逐帧回放检查':'等待任务开始';
  el('trainingBatch').textContent=m.completed&&m.percent===null?'任务已完成':m.batchText+(m.percent!==null?' · '+m.percent.toFixed(1)+'%':'');
  const progress=el('trainingEpochProgress');progress.hidden=m.percent===null;progress.value=m.percent??0;
  progress.classList.toggle('stale',!m.fresh||!m.receiptFresh);
  const history=(j.history||[]).filter(r=>!p.stage||r.phase===p.stage),last=history.at(-1)||{};
  el('trainingMetrics').replaceChildren(metric('本轮已处理 / 总窗口',Number.isFinite(p.examples)?num(p.examples)+' / '+num(p.total_examples):'—'),metric('最近批次 loss',loss(p.latest_loss??(m.completed?last.train_loss:null))),metric('最近完整轮 train / val loss',loss(last.train_loss)+' / '+loss(last.validation_loss)),metric('近期处理速度',m.completed?'任务已结束':m.fresh&&m.receiptFresh&&Number.isFinite(p.examples_per_second)?num(p.examples_per_second,1)+' 窗口/秒':'等待新回执'),metric('本轮剩余估计',m.completed?'已结束':m.fresh&&m.receiptFresh?duration(p.eta_seconds):'—'),metric('累计优化更新 / 当前配置',Number.isFinite(p.steps)?num(p.steps)+' steps':[j.candidate,j.seed?'seed '+j.seed:'',j.fold!==undefined&&j.fold!==null?j.fold==='all'?'全部记录':'外层 '+(Number(j.fold)+1)+'/4':''].filter(Boolean).join(' · ')||'—'));
  const completion=el('trainingCompletion'),done=['large_jepa','large_personal','retraining_validation'].every(k=>snapshot[k]?.state==='complete')&&!['vr_fit80','targeted_gate_context','targeted_gate_local'].some(k=>snapshot[k]?.state==='running');completion.hidden=!done;
  if(done){const epochs=snapshot.large_jepa.completed_full_epochs??snapshot.telemetry?.jobs?.large_jepa?.progress?.completed_epochs,report=snapshot.large_personal_report||{},personal=[...new Set((report.final_candidates||[]).map(c=>c.epoch))].join('/'),ba=report.aggregate_selected?.frame?.balanced_accuracy;
   completion.textContent='上一轮训练与离线检查已完成：公开续训 '+(epochs??'—')+' 轮，个人最终拟合 '+(personal||'—')+' 轮。'+(Number.isFinite(ba)?'会话隔离开发平衡准确率 '+(100*ba).toFixed(1)+'%。':'')+(report.meets_promotion_gate?'候选尚未部署。':'未达到控制精度与可靠性门槛，当前在线模型未替换。');}
  const gpuRows=snapshot.telemetry?.gpus||[],gib=x=>Number.isFinite(x)?num(x/1024,1):'—';
  const gpu=gpuRows.map(g=>'GPU '+g.index+' · '+num(g.utilization)+'% · '+gib(g.memory_used_mib)+' / '+gib(g.memory_total_mib)+' GiB · '+num(g.power_watts)+' W');
  el('trainingGpu').textContent=(!m.fresh||gpuRows.some(g=>Date.now()/1000-(g.observed_at||0)>30)?'GPU 状态已过期 · ':'')+(gpu.join('    |    ')||'GPU 遥测暂不可用');
  chart(history);const lines=(j.events||[]).map(eventText).join('\n')||'该任务尚无结构化训练日志。';
  const log=el('trainingLog');if(log.textContent!==lines){log.textContent=lines;if(el('trainingLogFollow').checked)log.scrollTop=log.scrollHeight}
  el('trainingRawLog').textContent=j.raw_log||'暂无原始日志';
 }
 async function poll(){try{const r=await fetch('/api/training/status',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('HTTP '+r.status);snapshot=await r.json();render()}catch(e){render();el('trainingDetailState').textContent='日志连接失败 · '+e.message}finally{setTimeout(poll,2000)}}poll();
})();
