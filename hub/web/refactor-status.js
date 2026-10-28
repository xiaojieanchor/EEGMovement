'use strict';
(function(root,factory){
 const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;
 else if(root.document)api.mount(root.document);
})(typeof globalThis!=='undefined'?globalThis:this,function(){
 const finite=x=>typeof x==='number'&&Number.isFinite(x),number=(x,d=0)=>finite(x)?x.toLocaleString('zh-CN',{maximumFractionDigits:d}):'—';
 const percent=x=>finite(x)?(x*100).toFixed(1)+'%':'—',escape=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const time=x=>finite(x)?x<60?Math.round(x)+'秒':Math.floor(x/60)+'分'+Math.round(x%60)+'秒':'—';
 function label(r){
  if(/^jepamotion-mc\d+-/.test(r.id||''))return 'JEPA动静识别 · 覆盖轮换对照';
  if(/^jepamotion-mw\d+-/.test(r.id||''))return 'JEPA动静识别 · 通道曝光纠正';
  if(/^jepamotion-m2-signed-/.test(r.id||''))return 'JEPA动静识别 · 因果慢带支路';
  if(/^jepamotion-/.test(r.id||''))return 'JEPA动静识别 · REST / MOVE';
  if(/^jepareset-/.test(r.id||''))return 'JEPA分类头重置 · 保留私人编码器';
  if(/^jepascopev\d+-/.test(r.id||''))return '单卡适配对照 · '+(r.id.includes('-public-')?'公共初始化＋全量适配':r.id.includes('-tail-')?'末层适配':r.id.includes('-adapter-')?'低秩适配':'冻结JEPA特征＋分类头');
  if(/^jepanewv\d+-.*-rest50-/.test(r.id||''))return 'JEPA动静曝光对照 · 静止50%／运动50%';
  if(/^jepanewv\d+-/.test(r.id||''))return '新录制 JEPA · '+(r.id.includes('-head-')?'冻结特征＋分类头':'全量适配');
  if(/^balancedfixv\d+-/.test(r.id||''))return '梯度修复 · 冻结JEPA特征＋输出头';
  if(/^baseline(?:gpu1)?v\d+-/.test(r.id||''))return '因果诊断基线 · '+(r.id.includes('slow-linear')?'低频极性分类器':r.id.includes('eegnet')?'EEGNet':r.id.includes('eegpt')?'EEGPT':r.id.includes('reve')?'REVE':'CBraMod')+(r.id.includes('-ea-')?' · 空间对齐':'');
  if(r.id?.startsWith('scopev2-'))return r.id.includes('-public-')?'公共初始化 · 全量适配':'适配范围 · '+(r.id.includes('-adapter-')?'低秩适配':r.id.includes('-tail-')?'末层适配':'冻结特征＋分类头');
  if(r.id?.startsWith('balancedv1-'))return '类别均衡 · '+(r.id.includes('-head-')?'冻结JEPA特征＋输出头':'全量JEPA适配');
  if(r.id?.startsWith('freshv1-'))return '当前权重历史重建 · '+(r.id.includes('-warmup-cosine-')?'预热＋较低学习率':'恒定学习率');
  if(r.id?.startsWith('stable'))return '稳定性对照 · '+(r.id.includes('warmup')?'预热＋较低学习率':'恒定学习率');
  if(r.id?.startsWith('mixed'))return (r.id.includes('-main-')?'主容量':'小容量')+' · VR＋电脑混合适配';
  if(r.id?.startsWith('parcapv1-'))return (r.id.includes('-large-')?'大容量':'主容量')+(r.context?.stage==='public_ssl'||r.id.includes('-public-')?' · 公共JEPA':' · 核心GRU个人适配');
  if(r.id?.startsWith('seq'))return r.context?.adaptation==='full'||r.id.includes('-full-')?'完整模型适配':'轻量头部适配';
  if(r.id?.includes('public-common-none'))return '公共底模预训练';
  if(r.id?.includes('frozen-post'))return '冻结编码器＋后置GRU';
  if(r.id?.includes('joint-post-ce'))return '联合监督＋后置GRU';
  if(r.id?.includes('joint-post-jepa'))return '后置GRU＋JEPA辅助目标';
  if(r.id?.includes('joint-core'))return '核心GRU＋JEPA辅助目标';
  return r.id||'等待任务';
 }
 const finalScoring=r=>!r.report&&r.scoring?.state==='scoring'&&['selected','last','selected_slow_zero'].includes(r.scoring?.checkpoint_role);
 function runModel(r,fresh,confirmed=true){
  const p=r.progress||{},report=r.report,ev=r.evaluation||{},completed=!!report;
  const total=finite(p.total_steps)?p.total_steps:report?.actual_updates;
  const step=finite(p.step)?p.step:report?.actual_updates;
  let status=completed?(report?.stopped_early?'已按计划早停，结果已保存':'训练已结束'):({failed:'训练失败，已停止',inactive:'未运行，保留最后进度',waiting:'等待训练',preparing_inputs:'装载模型与固定验证片段，尚未优化',running:confirmed?'训练中':'等待队列确认'})[r.state]||'等待回执';
  if(fresh&&confirmed&&!completed&&r.state==='preparing_inputs'&&r.execution?.state==='preparing_feature_cache')status='正在建立冻结特征缓存；尚未开始本次参数更新';
  if(!fresh&&!completed)status='状态已过期，保留最后进度';
  if(fresh&&confirmed&&!completed&&r.state==='running'&&(step>=total||finalScoring(r)))status='优化已完成，正在评分';
  const committedStep=r.execution?.committed_step??r.execution?.actual_updates??(r.validation_scope==='full_chronological_development'?r.validation_history?.at(-1)?.step:null);
  if(!completed&&r.execution?.state==='stopped')status='已按授权提前停止；保留第 '+number(committedStep)+'步完整状态，原训练报告未完成';
  const bestStep=ev.best_step??report?.best_step??null;
  return {...r,title:label(r),step,total,percent:finite(step)&&finite(total)&&total>0?Math.min(100,Math.max(0,100*step/total)):null,
   status,completed,bestStep,initialRetained:bestStep===0,developmentBA:finite(ev.development?.balanced_accuracy)?ev.development.balanced_accuracy:null,
   trainingBA:finite(ev.training?.balanced_accuracy)?ev.training.balanced_accuracy:null,coverage:finite(ev.development?.coverage)?ev.development.coverage:null};
 }
 function viewModel(s,now=Date.now()/1000){
  const remote={...(s.remote||{})};remote.fresh=remote.fresh===true&&!remote.error;
  const q=remote.queue||{},runs=[...(remote.runs||[])],active=q.active_experiments?.length?q.active_experiments:q.experiment?[q.experiment]:[];
  if(/^(?:motion_gpu1_v[123]|motion_(?:signed|mask|coverage)_gpu1_v1)$/.test(q.owner||'')&&q.motion_run?.id===q.experiment&&q.motion_run?.model_task==='binary_motion'){
   const i=runs.findIndex(r=>r.id===q.motion_run.id),base=i>=0?runs[i]:{},value={...base,...q.motion_run,progress:{...(base.progress||{}),...(q.motion_run.progress||{})}};
   if(q.state==='training'&&!value.state)value.state=finite(value.progress?.step)?'running':'preparing_inputs';
   const binary=q.motion_run.binary_validation;
   if(binary&&finite(binary.step)&&binary.step<=q.motion_run.binary_committed_step){
    value.validation_history=(value.validation_history||[]).map(h=>h.step===binary.step?{...h,...binary}:h);
    if(!value.validation_history.some(h=>h.step===binary.step))value.validation_history.push(binary);
   }
   if(i>=0)runs[i]=value;else runs.push(value);
  }
  const ids=active.length?active:q.completed_conditions?.length?q.completed_conditions:[];
  const selected=ids.length?runs.filter(r=>ids.includes(r.id)):runs.filter(r=>!r.id?.includes('recovery')).slice(-2);
  const current=selected.map(r=>runModel(r,remote.fresh===true,q.state==='training'&&active.includes(r.id)));
  const history=runs.filter(r=>!selected.includes(r));const sizes=current.map(r=>r.architecture?.parameters).filter(finite);
  return {remote,q,current,history,completed:current.filter(r=>r.completed).length,
   isTraining:remote.fresh===true&&q.state==='training'&&current.some(r=>!r.completed&&!finalScoring(r)&&r.state==='running'&&finite(r.step)&&(!finite(r.total)||r.step<r.total)),
   isPreparing:remote.fresh===true&&q.state==='training'&&current.some(r=>!r.completed&&r.state==='preparing_inputs'),
   isScoring:remote.fresh===true&&(q.state==='offline_evaluation'||q.state==='training'&&current.some(r=>finalScoring(r)||!r.completed&&finite(r.total)&&r.step>=r.total)),
   liveQualified:current.length>0&&current.every(r=>r.report?.live_qualified===true),parameters:sizes.length&&sizes.every(x=>x===sizes[0])?sizes[0]:null,
   total:current.length&&current.every(r=>finite(r.total))?current.reduce((n,r)=>n+r.total,0):null,step:current.some(r=>finite(r.step))?current.reduce((n,r)=>n+(r.step||0),0):null,now};
 }
 function chart(values,accuracy=false,metricLabel=accuracy?'开发BA':'训练损失'){
  const pts=(values||[]).filter(v=>finite(v.step)&&finite(accuracy?v.balanced_accuracy:v.loss)).slice(-64).sort((a,b)=>a.step-b.step);
  if(pts.length<2)return '<div class="tw-chart-empty">暂无足够的真实采样点</div>';
  const ys=pts.map(v=>accuracy?v.balanced_accuracy:v.loss),lo=Math.min(...ys),hi=Math.max(...ys),pad=(hi-lo)*.12||.05;
  const min=Math.max(0,lo-pad),max=hi+pad,x0=pts[0].step,x1=pts[pts.length-1].step;
  const x=v=>50+(v.step-x0)/Math.max(1,x1-x0)*390,y=v=>125-((accuracy?v.balanced_accuracy:v.loss)-min)/(max-min)*102;
  const fmt=v=>accuracy?percent(v):v.toFixed(3);
  return '<svg viewBox="0 0 470 154" class="tw-chart" role="img" aria-label="'+metricLabel+'实际采样趋势"><path d="M50 23H440M50 74H440M50 125H440" class="tw-grid"/><text x="0" y="27">'+fmt(max)+'</text><text x="0" y="129">'+fmt(min)+'</text><polyline class="tw-line" points="'+pts.map(v=>x(v).toFixed(2)+','+y(v).toFixed(2)).join(' ')+'"/>'+pts.map(v=>'<circle cx="'+x(v).toFixed(2)+'" cy="'+y(v).toFixed(2)+'" r="3"><title>第'+v.step+'步 · '+fmt(accuracy?v.balanced_accuracy:v.loss)+'</title></circle>').join('')+'<text x="50" y="150">'+number(x0)+'步</text><text x="440" y="150" text-anchor="end">'+number(x1)+'步</text></svg>';
 }
 function lossTrend(r){
  const t=r.loss_trend;if(!t)return '';
  if(r.model_task==='binary_motion')return '<p class="tw-score-caption">未加权BCE：前 '+number(t.first_count)+' 步均值 '+number(t.first_bce,3)+' → 最近 '+number(t.last_count)+' 步均值 '+number(t.last_bce,3)+'。训练拟合与开发识别分别判断。</p>';
  return '<p class="tw-score-caption">未加权CE：前 '+number(t.first_count)+' 步均值 '+number(t.first_ce,3)+' → 最近 '+number(t.last_count)+' 步均值 '+number(t.last_ce,3)+'。仅描述训练拟合，不能据此确认各层收到监督梯度或泛化改善。</p>';
 }
 function metric(label,value,note=''){return '<div class="tw-metric"><span>'+label+'</span><strong>'+value+'</strong>'+(note?'<small>'+note+'</small>':'')+'</div>';}
 function probe(r){const p=r.validation_probe;if(!p)return '';const names=['静止','前进','后退','左转','右转'],missing=(p.missing_primary_classes||[]).map(i=>names[i]).join('、');return '<p class="tw-note">最近开发探针 '+number(p.step)+'步 · BA '+percent(p.balanced_accuracy)+(missing?' · 缺少 '+missing+'，不能作为完整五类成绩':'')+' · 预选合格片段，非独立测试或实时覆盖率</p>';}
 function scoringProgress(r){
  const s=r.scoring;if(!s||r.completed)return '';
  const role={selected:'选中权重',last:'最后权重',current:'当前权重'}[s.checkpoint_role]||'权重',split={train:'训练录制',validation:'开发录制'}[s.split]||'录制';
  return '<p class="tw-scope" role="status">'+(s.state==='failed'?'评分失败：'+escape(s.error):s.state==='complete'?'本次重放已完成，等待汇总':'正在评分')+' · '+role+' · '+split+' '+number(s.recordings_done)+' / '+number(s.recordings_total)+' · 事件 '+number(s.events_done)+' / '+number(s.events_total)+(s.recording?' · '+escape(s.recording):'')+' · 最近回执 '+(finite(s.updated_at)?escape(new Date(s.updated_at*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})):'—')+'</p>';
 }
 function runCard(r,preferences={}){
  const id=escape(r.id),p=r.progress||{},ev=r.evaluation||{},mode=preferences[r.id]||'loss';
  const isPublic=(r.context?.stage||r.report?.stage)==='public_ssl',isBinary=r.model_task==='binary_motion'||/^jepamotion-/.test(r.id||''),sequenceFlow=ev.kind==='historical_sequence_shadow',d05Flow=ev.kind==='historical_d05_shadow',fullFlow=sequenceFlow||d05Flow;
  const gpu=finite(r.context?.physical_gpu_index)?'物理第 '+number(r.context.physical_gpu_index+1)+' 张 GPU':r.context?.world_size===2?'双卡DDP':r.context?.device?.startsWith('cuda:')?'GPU '+escape(r.context.device.split(':')[1]):'设备未上报';
  const best=r.bestStep===null?'等待选模':r.initialRetained?'保留初始权重（第0步）；本轮适配未超过初始候选。':'候选取第 '+number(r.bestStep)+' 步；'+(isPublic?'按固定分域验证目标选模。':isBinary?'按动静两类较低召回及完整开发结果选模。':sequenceFlow?'按完整开发录制选模。':d05Flow?'按固定开发探针选模；上方为所选权重的完整重放。':'按固定开发片段选模，完整重放另报。');
  const subtitle=isBinary?'阶段一：无导航意图 / 有导航意图；冻结JEPA，仅训练全新二分类头':r.context?.adaptation==='head'?'固定编码器，仅适配输出层':r.context?.adaptation==='full'?'联合适配编码器与分类头':r.context?.stage==='public_ssl'?'掩码潜表示预训练':'因果时序模型实验';
  const chronological=r.validation_scope==='full_chronological_development';
  const history=r.validation_history||[],latest=history[history.length-1],selected=history.find(v=>v.step===r.bestStep),validationLabel=isPublic?'验证目标':sequenceFlow||chronological?'开发BA':'开发探针BA';
  const result=isPublic?'<div class="tw-result-grid">'+metric('最近验证目标',finite(latest?.loss)?latest.loss.toFixed(4):'—','按域宏平均')+metric('最佳验证目标',finite(selected?.loss)?selected.loss.toFixed(4):'—','固定分域样本')+metric('选中步数',number(r.bestStep),'私人导航效果另验')+'</div><p class="tw-score-caption">掩码潜表示与正则目标；不是导航准确率。验证目标待评分回执更新。</p>':
   isBinary?'<div class="tw-result-grid">'+metric('训练动静 BA',finite(r.trainingBA)?percent(r.trainingBA):'结束后评分','所选权重 · 含拒识')+(r.completed?metric('所选开发动静 BA',percent(r.developmentBA),'所选权重 · 含拒识'):'')+metric(r.completed?'最后开发动静 BA':'开发动静 BA',percent(latest?.balanced_accuracy),'最近已提交 · 含拒识')+metric('开发覆盖',percent(latest?.coverage),'完整指令时长')+metric(r.completed?'最后QC合格动静 BA':'QC合格动静 BA',percent(latest?.qc_balanced_accuracy),'拒识另报，不能替代全流结果')+metric('静止召回',percent(latest?.qc_class_recall?.[0]),'QC合格输入')+metric('运动召回',percent(latest?.qc_class_recall?.[1]),'QC合格输入')+metric('静止误运动时长',percent(latest?.false_motion_fraction),'占明确指令静止时长')+metric('开发未加权BCE',number(latest?.binary_ce,3),'完整接纳输入 · 按真实时长')+metric('开发静止支持',time(latest?.rest_seconds),'不等于自然空闲验收')+'</div><p class="tw-score-caption">动静二分类，不表示方向正确。两类较低召回优先选模；准备和UNKNOWN不补静止，含拒识与QC合格指标分别呈现。</p>':
   fullFlow?'<div class="tw-result-grid">'+metric('训练 BA',percent(r.trainingBA),(ev.training?.recordings??'—')+'段 / '+(ev.training?.classes??'—')+'类')+metric('开发 BA',percent(r.developmentBA),(ev.development?.recordings??'—')+'段 / '+(ev.development?.classes??'—')+'类')+metric('有效覆盖',percent(r.coverage),'完整开发录制')+'</div><p class="tw-score-caption">所选权重 · 录制宏平均BA · 计入质量拒识</p>':
   '<div class="tw-result-grid">'+metric('训练 BA',finite(r.trainingBA)?percent(r.trainingBA):'结束后评分','完整训练录制重放')+metric(chronological?'最近开发BA':'开发探针BA',percent(r.validation_probe?.balanced_accuracy??latest?.balanced_accuracy),chronological?'已提交优化步的完整因果重放':'固定合格片段')+metric('完整覆盖',chronological?percent(latest?.coverage):'—',chronological?'计入质量拒识':'待连续重放')+'</div><p class="tw-score-caption">'+(chronological?'完整开发录制；所选及最终权重的训练评分待任务结束。':'探针仅含预选合格片段；完整录制分数与覆盖另报。')+'</p>';
  const scoreDetails=isPublic?'<p>本阶段为公开无标签JEPA预训练；导航分数在个人适配与完整重放后另行评分。</p>':isBinary?'<p>所选权重：训练动静BA '+percent(r.trainingBA)+'，开发动静BA '+percent(r.developmentBA)+'，均含拒识。最新已提交开发指标见上方，尚无新佩戴独立验收。</p>':'<p>所选权重训练分数：录制宏平均BA '+percent(r.trainingBA)+'；按时长汇总BA '+percent(ev.training?.pooled_balanced_accuracy)+'。</p>'+(d05Flow?'<p>图表为固定开发探针；完整重放计入拒识，上方另报其BA和覆盖。</p>':'<p>最后更新的开发BA '+percent(ev.latest_development_ba)+'；此值与上方所选权重分数分开呈现。</p>');
  return '<article class="tw-run" aria-label="'+escape(r.title)+'"><div class="tw-run-heading"><div><span class="tw-overline">'+gpu+(finite(r.context?.seed)?' · seed '+r.context.seed:'')+' · '+(finite(r.architecture?.parameters)?(r.architecture.parameters/1e6).toFixed(2)+'M':'容量未上报')+'</span><h3>'+escape(r.title)+'</h3><p>'+subtitle+'</p></div><span class="tw-tag '+(r.state==='failed'?'tw-tag-danger':r.completed?'tw-tag-done':'')+'">'+r.status+'</span></div>'+ 
   '<div class="tw-progress-label"><strong>'+number(r.step)+' <small>/ '+number(r.total)+' 次更新</small></strong><span>'+ (r.percent===null?'待上报':r.percent.toFixed(1)+'%')+'</span></div><progress max="100" value="'+(r.percent??0)+'" aria-label="'+escape(r.title)+'真实权重更新进度"></progress>'+ 
   '<div class="tw-runtime">'+metric(isBinary?(/^jepamotion-mw\d+-/.test(r.id||'')?'通道加权BCE':'动静优化BCE'):'加权loss',finite(p.loss)?p.loss.toFixed(4):'—','实际优化目标')+metric(isBinary?'未加权BCE':'未加权CE',finite(isBinary?p.unweighted_bce:p.unweighted_ce)?(isBinary?p.unweighted_bce:p.unweighted_ce).toFixed(4):'—','仅有真实日志时显示')+metric('梯度范数',finite(p.gradient_norm)?p.gradient_norm.toFixed(2):'—',finite(p.gradient_norm)?p.gradient_norm>1?'已裁剪至1.0':'本步未触发裁剪':'等待日志')+metric('学习率',finite(p.learning_rate)?p.learning_rate.toExponential(2):'—','锁定实验计划')+metric('优化阶段耗时',time(p.elapsed_seconds),'最近一步 '+(finite(p.step_seconds)?p.step_seconds.toFixed(2)+'秒':'—'))+'</div>'+ 
   (!isPublic?'<p class="tw-score-caption">批次切换和事件权重会影响单步loss；需结合未加权CE、梯度及固定开发集判断。旧任务未记录的CE不追溯伪造。</p>':'')+
   lossTrend(r)+scoringProgress(r)+result+'<p class="tw-best">'+best+'</p>'+ 
   '<div class="tw-chart-heading"><div role="tablist" aria-label="'+escape(r.title)+'趋势类型">'+['loss','validation'].map(k=>'<button type="button" role="tab" tabindex="'+(mode===k?'0':'-1')+'" aria-selected="'+(mode===k)+'" data-run="'+id+'" data-metric="'+k+'" data-focus="'+id+'-'+k+'">'+(k==='loss'?'优化损失':validationLabel)+'</button>').join('')+'</div><span>真实采样点</span></div>'+chart(mode==='loss'?r.loss_history:r.validation_history,mode!=='loss'&&!isPublic,mode==='loss'?'训练损失':validationLabel)+probe(r)+
   '<details class="tw-run-details" data-key="run-'+id+'"><summary data-focus="detail-'+id+'">实验标识与记录</summary><code>'+id+'</code><p>更新 '+number(r.step)+' / '+number(r.total)+' · '+(finite(p.step_seconds)?p.step_seconds.toFixed(2)+'秒/步':'逐步耗时未上报')+(r.execution?.restored_step?' · 从 '+r.execution.restored_step+'步恢复':'')+'</p><p>可训练参数 '+number(r.architecture?.trainable_parameters)+' · 训练loss '+(finite(p.loss)?p.loss.toFixed(4):'—')+'</p>'+scoreDetails+'<div class="tw-table-scroll"><table><thead><tr><th>采样更新</th><th>训练loss</th><th>阶段耗时</th></tr></thead><tbody>'+(r.loss_history||[]).slice(-6).map(v=>'<tr><td>'+number(v.step)+'</td><td>'+v.loss.toFixed(4)+'</td><td>'+time(v.elapsed_seconds)+'</td></tr>').join('')+'</tbody></table></div><p class="tw-note">采样记录覆盖实际训练过程；不按时间推算epoch或优化百分比。</p></details></article>';
 }
 function renderDashboard(s,preferences={}){
  const v=viewModel(s),{remote,q}=v;let title=q.state==='preparing_workspace'?'正在准备独立训练目录':q.state==='validating_implementation'?'正在核对执行能力与显存':q.state==='locking_protocol'?'正在校验训练输入与协议':q.state==='failed'?'本轮任务中断':v.isTraining?'正在进行真实权重更新':v.isScoring?'正在评估完整录制':v.isPreparing?'训练准备中，尚未更新权重':v.current.length&&v.completed===v.current.length?'本轮训练已结束':'等待训练任务';
  if(remote.fresh!==true)title='服务器状态待同步';
  const queueNames={sequence_screening_complete:'连续输入适配对照已完成；候选尚未通过新佩戴验收',d05_screening_complete:'D05单种子机制对照与重放完成；尚需复验和新佩戴验收',private_screening_complete:'个人适配对照与历史重放已完成；候选尚未通过新佩戴验收',offline_evaluation:'本组训练已完成，正在连续时序重放与历史机制评估',recovery_validation_complete:'真实EEG断点恢复对照已完成；研究训练仍待继续',training:'训练任务已启动',locking_protocol:'检查实际输入与锁定协议'};
  const history=v.history.map(r=>runModel(r,remote.fresh===true,false));
  const size=v.parameters?(v.parameters/1e6).toFixed(2)+'M':v.current.some(r=>finite(r.architecture?.parameters))?'多种容量':'—';
  queueNames.preparing_workspace='复用现有EEG文件建立隔离目录；此阶段不计权重更新。';queueNames.validating_implementation='使用生成输入验证完整模型的精度、恢复、导出与显存；此阶段不是真实EEG训练。';queueNames.capacity_screening_complete='容量对照与历史重放已完成；候选效果与新佩戴验收另行判定。';
  queueNames.sequence_screening_stopped='本组已按授权提前停止；完整状态与日志已保留，未完成评分另列。';
  const caption=remote.fresh===true?(queueNames[q.state]||'读取实际训练回执'):'状态已过期，保留最后进度；当前运行情况尚未确认。';
  const p=remote.preparation||{};
  let html='<div class="tw-overview"><div class="tw-overview-top"><div><span class="tw-overline">本轮实验 / CAUSAL JEPA</span><h2>'+title+'</h2><p>'+caption+'</p>'+(remote.fresh&&v.current.length&&v.completed===v.current.length&&!v.isTraining?'<p class="tw-queue-note">当前队列空闲，本轮结果已保存。</p>':'')+'</div><span class="tw-publication">'+(v.liveQualified?'验收回执已通过':'候选未发布')+'</span></div><div class="tw-stats">'+metric('本轮任务',number(v.completed)+' / '+v.current.length,'已结束 / 已显示任务')+metric('实际优化更新',number(v.step)+' / '+number(v.total),'以服务器optimizer回执为准')+metric('本轮模型容量',size,'实际构建参数量')+metric('发布资格',v.liveQualified?'已有验收回执':v.completed?'效果待改进':'尚待评估','开发评分与新佩戴验收')+'</div></div>';
  if(finite(q.canonical_files_total))html+='<p class="tw-scope">独立目录数据文件：'+number(q.canonical_files_linked)+' / '+number(q.canonical_files_total)+'；复用已下载数据，此阶段不计入权重更新。</p>';
  if(q.pending_conditions?.length)html+='<p class="tw-scope">后续排队 '+q.pending_conditions.length+' 项 · 已按依赖与显存安排并发。</p>';
  if(q.gpus?.length)html+='<p class="tw-scope">'+q.gpus.map(g=>'GPU '+number(g.index)+' · 负载 '+number(g.utilization_percent)+'% · 剩余 '+(g.free_bytes/1024**3).toFixed(1)+' GiB').join('　｜　')+'</p>';
  if(q.state==='failed')html+='<p class="tw-alert">任务失败：'+escape(q.error||'请展开实验记录查看回执')+'</p>';
  if(remote.auxiliary_training?.state==='failed')html+='<p class="tw-alert" role="alert">辅助训练或评分队列中断：'+escape(remote.auxiliary_training.failures?.[0]?.error||'等待诊断回执')+'。其他任务按各自优化回执显示。</p>';
  for(const task of remote.parallel_training||[])if(task.owner!==remote.auxiliary_training?.owner&&task.state==='failed')html+='<p class="tw-alert" role="alert">并行任务 '+escape(task.owner||'')+' 中断：'+escape(task.error||'请查看独立失败回执')+'</p>';
  for(const task of remote.parallel_training||[])if(task.fresh&&['generated_validation','locking_protocol','waiting_for_gpu_memory','waiting_for_predecessors','waiting_for_scope_validation','copying_verified_inputs'].includes(task.state))html+='<p class="tw-scope">'+escape(task.owner||'并行任务')+' · '+({generated_validation:'完整A100生成入口验证，尚未更新真实EEG权重',locking_protocol:'绑定本组输入、类别支持与协议',waiting_for_gpu_memory:'等待训练名额或显存准入',waiting_for_predecessors:'等待历史重建对照完成，保留健康任务',waiting_for_scope_validation:'等待同源完整A100验证，尚未训练',copying_verified_inputs:'复制已核对的训练与开发输入，尚未训练'})[task.state]+'</p>';
  html+='<div class="tw-section-heading"><h2>'+(v.isTraining?'当前训练任务':'本轮任务与结果')+'</h2><span>训练结束 ≠ 效果验收通过</span></div><div class="tw-runs">'+(v.current.length?v.current.map(r=>runCard(r,preferences)).join(''):'<div class="tw-empty">暂无可确认的训练回执，正在读取服务器。</div>')+'</div>';
  html+='<p class="tw-scope">BA为平衡准确率。训练与开发分别计分，类别数和质量覆盖同时列出。当前使用历史录制，未经新佩戴独立验收；loss下降不代表泛化改善。</p>';
  html+='<details class="tw-disclosure" data-key="history"><summary data-focus="history">最近历史实验 <span>'+history.length+'项 · 展开查看</span></summary><div class="tw-table-scroll"><table><thead><tr><th>实验</th><th>状态</th><th>实际更新</th><th>完整训练BA</th><th>完整开发BA</th><th>开发覆盖</th><th>最后训练loss</th></tr></thead><tbody>'+history.map(r=>'<tr><td><strong>'+escape(r.title)+'</strong><code>'+escape(r.id)+'</code>'+probe(r)+'</td><td>'+r.status+'</td><td>'+number(r.step)+' / '+number(r.total)+'</td><td>'+percent(r.trainingBA)+'</td><td>'+percent(r.developmentBA)+'</td><td>'+percent(r.coverage)+'</td><td>'+(finite(r.progress?.loss)?r.progress.loss.toFixed(4):'—')+'</td></tr>').join('')+'</tbody></table></div><p class="tw-note">完整分数来自所选权重的历史连续重放；— 表示尚无该评分回执，公共SSL不填写导航BA。</p></details>';
  html+='<details class="tw-disclosure" data-key="preparation"><summary data-focus="preparation">数据准备与诊断 <span>源数据、协议、恢复检查</span></summary><div class="tw-preparation"><p>原始分片 '+number(p.files_done)+' / '+number(p.files_total)+' 个来源 · '+number(p.shards)+' 个分片。此进度不计入优化更新。</p><p>服务器'+(remote.fresh?'已同步':'状态待同步')+' · 状态年龄 '+(finite(remote.age_ms)?Math.round(remote.age_ms/1000)+'秒':'未知')+'</p>';
  for(const c of remote.preflight||[])if(c.phase&&c.phase!=='ready'&&finite(c.updated_at)&&Date.now()/1000-c.updated_at<60)html+='<p>'+(c.phase==='hashing_inputs'?'输入完整性 · '+number(c.files_checked)+' / '+number(c.files_total)+' 文件 · 本轮读取 '+number(c.content_hashed)+' · 复用未变文件校验 '+number(c.cache_hits):'协议与划分检查 · '+escape(c.phase)+' · '+number(c.records_checked)+' / '+number(c.records_total)+' 条数据')+'</p>';
  if(remote.recovery_validation){const r=remote.recovery_validation;html+='<p>双 A100 真实断点恢复：'+r.restored_step+' → '+r.final_step+'步 · 状态最大差 '+r.max_absolute_state_delta+' · 稳定性检查，非准确率</p>';}
  for(const a of remote.future_audit?.models||[])html+='<p>'+escape(a.experiment)+' · '+a.validation_cells+'个领域/时距对照中，'+a.cells_learned_better_than_constant+'个超过训练均值基线 · 非导航准确率</p>';
  return html+'</div></details>';
 }
 function mount(doc){
  const host=doc.getElementById('trainingWorkbench');if(!host)return;
  const refreshButton=doc.getElementById('trainingRefresh'),stamp=doc.getElementById('trainingSync');
  if(doc.body.dataset.deviceTransition==='gnautilus'){
   const script=doc.createElement('script');script.src='/gnautilus-ml.js';script.onload=()=>window.GnautilusTraining.mount(host);script.onerror=()=>{host.textContent='训练工作台资源加载失败，请刷新页面'};doc.head.append(script);if(refreshButton){refreshButton.disabled=false;refreshButton.textContent='刷新任务';refreshButton.onclick=()=>location.reload()}if(stamp)stamp.textContent='新设备模型通路';const intro=doc.getElementById('trainingIntro');if(intro)intro.textContent='围绕联合导航想象与眼动辅助，管理数据、训练任务、结果与模型。';const history=doc.getElementById('legacyTrainingHistory');if(history){history.open=false;history.hidden=true;}return;
  }
  let snapshot=null,signature='',busy=false;const preferences={};
  function render(){
   const opened=new Set([...host.querySelectorAll('details[open]')].map(v=>v.dataset.key));const focused=host.contains(doc.activeElement)?doc.activeElement?.dataset.focus:null;
   host.innerHTML=renderDashboard(snapshot,preferences);for(const detail of host.querySelectorAll('details'))if(opened.has(detail.dataset.key))detail.open=true;
   if(focused)[...host.querySelectorAll('[data-focus]')].find(v=>v.dataset.focus===focused)?.focus({preventScroll:true});
   host.setAttribute('aria-busy','false');
  }
  host.addEventListener('click',event=>{const button=event.target.closest?.('[data-metric]');if(!button)return;preferences[button.dataset.run]=button.dataset.metric;render();});
  host.addEventListener('keydown',event=>{const button=event.target.closest?.('[data-metric]');if(!button||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const key=event.key==='Home'?'loss':event.key==='End'?'validation':button.dataset.metric==='loss'?'validation':'loss';preferences[button.dataset.run]=key;const focus=button.dataset.run+'-'+key;render();[...host.querySelectorAll('[data-focus]')].find(v=>v.dataset.focus===focus)?.focus({preventScroll:true});});
  async function refresh(){if(busy)return;busy=true;if(refreshButton)refreshButton.disabled=true;
   try{const r=await fetch('/api/refactor/status',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error('HTTP '+r.status);snapshot=await r.json();
    const remote=viewModel(snapshot).remote,key=JSON.stringify({...remote,age_ms:0,preparation_log_tail:null});if(key!==signature){signature=key;render();}
    if(stamp)stamp.textContent=(remote.fresh?'已同步 · ':'状态待同步 · ')+new Date().toLocaleTimeString('zh-CN',{hour12:false});
   }catch(e){snapshot={...(snapshot||{}),remote:{...(snapshot?.remote||{}),fresh:false,error:'connection_unavailable'}};render();signature='';if(stamp)stamp.textContent='连接失败，保留最后回执';}
   finally{busy=false;if(refreshButton)refreshButton.disabled=false;}
  }
  refreshButton?.addEventListener('click',refresh);
  const legacy=doc.getElementById('legacyTrainingHistory');let loaded=false;
  legacy?.addEventListener('toggle',()=>{if(!legacy.open||loaded)return;loaded=true;for(const name of ['training.js','training-detail.js','vr-training-replay.js']){const script=doc.createElement('script');script.src='/'+name;script.async=false;doc.head.append(script);}});
  refresh();setInterval(()=>{if(!doc.hidden)refresh();},5000);
 }
 return {viewModel,runModel,renderDashboard,chart,mount};
});
