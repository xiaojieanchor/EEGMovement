'use strict';
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.GnautilusTraining=api;})(typeof window!=='undefined'?window:this,function(){
 const algorithms={bandpower_lda:'频带功率 / 收缩LDA',csp_lda:'正则化CSP / 收缩LDA',gaze_lda:'前额低频方向 / LDA',cnn_gru:'因果轻量CNN / 单向GRU',fusion:'眼动与运动想象 / 后融合',cnn_gru_baseline:'因果去基线 / 频带CNN＋GRU',fbcsp_lda:'多频带CSP / 收缩LDA',temporal_lda:'低频时域 / 收缩LDA',tangent_lda:'协方差切空间 / 收缩LDA',pipeline_ensemble:'训练组内选择 / 多层融合',gaze_pipeline:'眼动低频与形态 / 训练组内选择'};
 algorithms.causal_jepa_stream='联合导航 / 有状态JEPA';
 const names={queued:'排队',preparing:'准备输入',waiting_resource:'等待计算资源',running:'训练／拟合',evaluating:'评估',exporting:'导出核对',registering:'登记模型库',completed:'完成并已收录',failed:'失败',cancelled:'已取消',interrupted:'中断待恢复'};
 const activeStates=['queued','preparing','waiting_resource','running','evaluating','exporting','registering'];
 const scopeNames={gaze:'公开眼动辅助研究',personal:'个人 g.Nautilus',motor:'公开运动想象方法基线',unknown:'其他／待核对'};
 const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=x=>typeof x==='number'&&Number.isFinite(x),pct=x=>finite(x)?(100*x).toFixed(1)+'%':'—';
 const date=x=>finite(x)?new Date(x*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未上报';
 const alg=id=>algorithms[id]||id||'算法未上报';
 function scopeOf(d){
  if(d?.schema==='gnautilus.personal_study.v1')return 'personal';
  const rows=d?.records||[];if(!rows.length)return 'unknown';
  if(rows.every(r=>r.reader==='gnautilus'||(!r.reader&&r.experiment_mode)))return 'personal';
  if(rows.every(r=>/malta|eegeyenet|eye_bci/i.test(r.reader||'')||r.experiment_mode==='gaze_only'))return 'gaze';
  if(rows.every(r=>/^(halt|bci_iv_1|bci_iv_2a|eegmmidb|openbmi|cho2017|ofner2017)$/.test(r.reader||'')))return 'motor';
  return 'unknown';
 }
 function datasetSummary(d){
  if(d?.schema==='gnautilus.personal_study.v1'){
   const roles={};for(const r of d.episodes||[]){const role=r.split==='validation'?'development':r.split;roles[role]=(roles[role]||0)+1;}
   const missing=[];if(!roles.train)missing.push('缺训练试次');if(!roles.development)missing.push('缺验证试次');if(!roles.test)missing.push('缺测试试次');
   return {roles,scope:'personal',count:(d.episodes||[]).length,channels:'32路',rate:'250 Hz',reference:'独立硬件REF；实际位置未记录',subjects:['本人'],missing,canSubmit:!missing.length};
  }
  const rows=d?.records||[],roles={},unique=k=>[...new Set(rows.map(r=>r[k]).filter(v=>v!==undefined&&v!==null&&v!==''))];
  rows.forEach(r=>roles[r.role||'unknown']=(roles[r.role||'unknown']||0)+1);
  const channels=[...new Set(rows.filter(r=>Array.isArray(r.channels)).map(r=>r.channels.length))];
  const rates=unique('sampling_rate'),refs=unique('reference'),missing=[];
  if(!roles.train)missing.push('缺训练记录');if(!roles.development)missing.push('缺开发记录');
  if(rows.some(r=>r.training_ready===false))missing.push('存在未就绪记录');if(!rows.length)missing.push('没有记录');
  return {roles,scope:scopeOf(d),count:rows.length,channels:channels.length?channels.map(v=>v+'路').join(' / '):'未知',rate:rates.length?rates.map(v=>v+' Hz').join(' / '):'未知',reference:refs.length?refs.join(' / '):'未知',subjects:unique('subject'),missing,canSubmit:!missing.length};
 }
 function filterJobs(jobs,datasets,filter={}){
  const map=new Map(datasets.map(d=>[d.id,d]));
  return jobs.filter(j=>{const d=map.get(j.spec?.dataset_id),state=filter.state||'all';
   return (!filter.scope||filter.scope==='all'||scopeOf(d)===filter.scope)&&(!filter.dataset||j.spec?.dataset_id===filter.dataset)&&
    (state==='all'||state==='recent'&&j.state!=='cancelled'||state==='active'&&activeStates.includes(j.state)||state==='attention'&&['failed','interrupted'].includes(j.state)||j.state===state)&&
    (!filter.query||[j.id,j.model_id,j.spec?.algorithm,alg(j.spec?.algorithm),d?.title,j.spec?.dataset_id].join(' ').toLowerCase().includes(filter.query.toLowerCase()));
  });
 }
 function curveRows(events){const reset=events.reduce((n,r,i)=>r.message==='resumed'?i:n,-1);const rows=events.slice(reset+1).filter(r=>['training_progress','jepa_progress'].includes(r.message)&&finite(r.data?.epoch));const last=rows.at(-1)?.data;return rows.filter(r=>!last?.variant||(r.data.variant===last.variant&&r.data.seed===last.seed)).map(r=>({...r.data,training_loss:r.data.training_joint_ce??r.data.training_loss,development_loss:r.data.validation_joint_ce??r.data.development_loss,development_balanced_accuracy:r.data.validation_balanced_accuracy??r.data.development_balanced_accuracy,seq:r.seq}));}
 const studyMethods={jepa_transfer:'公开迁移＋JEPA',transfer_no_jepa:'公开迁移／无JEPA对照',random_jepa:'随机初始化JEPA对照',finite_history_baseline:'有限历史监督对照'};
 function comparable(a,b){return !!a.dataset_sha256&&a.dataset_sha256===b.dataset_sha256&&!!a.class_labels?.length&&JSON.stringify(a.class_labels)===JSON.stringify(b.class_labels)&&!!a.channels?.length&&JSON.stringify(a.channels)===JSON.stringify(b.channels)&&finite(a.sampling_rate)&&a.sampling_rate===b.sampling_rate&&finite(a.window_s)&&a.window_s===b.window_s&&JSON.stringify(a.input_contract||null)===JSON.stringify(b.input_contract||null);}
 const field=(title,control)=>`<label class="gw-field"><span>${title}</span>${control}</label>`;
 const option=(v,t)=>`<option value="${e(v)}">${e(t)}</option>`;
 const scopes=()=>option('all','全部来源')+Object.entries(scopeNames).map(([k,v])=>option(k,v)).join('');
 const metric=(t,v,n)=>`<div class="gw-stat"><span>${t}</span><strong>${v}</strong><small>${n||''}</small></div>`;
 function modes(){return option('navigation_mi_gaze','行走／转向想象为主＋眼动辅助')+option('navigation_mi_only','行走／转向想象＋中央注视（对照）')+option('gaze_only','仅眼动（辅助分支对照）')+option('paired','四肢想象映射＋眼动（旧范式对照）')+option('motor_only','四肢想象映射＋中央注视（旧范式对照）')+option('no_control','明确无控制')+option('interference','干扰对照');}
 function template(){return `
  <div class="gw-topline"><span class="gw-route"><i></i> 运动想象为主 · 眼动辅助</span><span id="gmServer" role="status">正在检查计算连接…</span><button type="button" class="gw-link" data-pane="tools">连接与工具 ↗</button></div>
  <nav class="gw-tabs" aria-label="训练工作区">${[['overview','总览'],['data','数据与新建训练'],['jobs','任务与结果'],['models','模型库'],['tools','高级工具']].map(([k,t])=>`<button type="button" id="gwTab-${k}" aria-controls="gwPane-${k}" aria-current="${k==='overview'?'page':'false'}" data-pane="${k}">${t}<span id="gwCount-${k}"></span></button>`).join('')}</nav>
  <div id="gmError" role="alert" class="gw-feedback" hidden></div><div id="gmNotice" role="status" class="gw-feedback gw-success" hidden></div>
  <section id="gwPane-overview" class="gw-pane" aria-labelledby="gwTab-overview">
   <div id="gwStats" class="gw-stats"></div>
   <div class="gw-overview-grid"><article class="gw-card gw-focus"><span class="gw-kicker">CURRENT RESEARCH</span><h2>想象行走与转向，眼动提供辅助</h2><p>以第一人称体会前进、后退、左右转向的运动感觉；不指定手腿映射，可正常操作手柄。利用方向眼动提供辅助提示，结合运动区证据判断开始、保持、回中和停止。</p><div class="gw-tags"><span>运动想象主分支</span><span>停止、回中与拒识</span><span>手柄／头动干扰</span></div><p class="gw-boundary">公开肢体想象用于方法基线，公开独立 EOG 用于辅助分支；两者都不直接证明个人行走／转向意图解码有效。</p><div class="gw-actions"><button type="button" class="button primary" data-pane="data">准备数据与训练 →</button><button type="button" class="button" data-pane="models">查看模型结果</button></div></article>
   <article class="gw-card"><div class="gw-heading"><h2>现在该做什么</h2><span class="gw-kicker">NEXT STEP</span></div><div id="gwNext"></div><hr><div id="gwCurrent"></div></article></div>
   <div class="gw-heading"><h2>最近任务</h2><button class="gw-link" type="button" data-pane="jobs">查看全部任务 →</button></div><div id="gwRecent" class="gw-recent"></div>
   <details class="gw-help"><summary>如何读这些数据</summary><p>训练 BA 反映已用于拟合的数据；开发 BA 用于方法比较和选模。BA 是各类别召回的平均，分组 BA 还按参与者或会话汇总。训练与开发要并列看，公开 EOG 分数不能当作个人 EEG 控制准确率。</p><p>接纳覆盖只描述有标签合格窗，完整连续流的质量覆盖、无控制误启动和头显效果另外验证。训练完成表示已导出并登记，不表示独立验收通过。</p></details>
  </section>
  <section id="gwPane-data" class="gw-pane" aria-labelledby="gwTab-data" hidden>
   <div class="gw-heading"><div><h2>数据目录</h2><p>先选一份数据，再核对它的来源、用途和训练配置。</p></div><button class="button" type="button" id="gmDatasetsRefresh">刷新数据集</button></div>
   <div class="gw-filters">${field('搜索数据','<input id="gwDataSearch" type="search" placeholder="名称、数据集ID、参与者">')}${field('研究来源',`<select id="gwDataScope">${scopes()}</select>`)}</div>
   <div class="gw-data-grid"><article class="gw-card gw-directory"><p id="gwDataCount" class="gw-muted"></p><div id="gwDatasets"></div><div class="gw-pager"><button class="button" id="gwDataPrev">上一页</button><span id="gwDataPage"></span><button class="button" id="gwDataNext">下一页</button></div><details class="gw-help"><summary>按完整目录定位数据（备用）</summary><label class="gw-field gw-select-fallback"><span>当前数据集</span><select id="gmDataset" aria-label="当前数据集"></select></label></details></article>
   <article class="gw-card"><h2 id="gwDatasetTitle">选择数据集</h2><div id="gmDatasetInfo"></div><details class="gw-help"><summary>完整记录与来源身份</summary><div id="gwRecordDetails"></div></details>
   <div class="gw-divider"></div><h3>使用这份数据创建训练</h3><div class="gw-form-grid">${field('训练方法','<select id="gmAlgorithm"></select>')}${field('历史窗口（秒）','<input id="gmWindow" type="number" min="0.5" max="5" step="0.05" value="1.5">')}${field('随机种子','<input id="gmSeed" type="number" min="0" max="2147483647" value="17">')}${field('神经网络轮数上限','<input id="gmEpochs" type="number" min="1" max="200" value="50">')}</div><p id="gwRecipe" class="gw-muted"></p><p id="gwTrainReadiness" class="gw-muted"></p><div class="gw-actions"><button type="button" class="button primary" id="gmTrain" disabled>创建训练任务</button><button type="button" class="button" id="gwDatasetJobs">查看相关任务</button></div><p class="gw-muted">提交后由服务器核查分组、标签、输入合同与协议；完成后自动登记模型，推理和动作分别手动启动。</p></article></div>
   <details class="gw-card gw-disclosure"><summary>导入个人 g.Nautilus 完整记录</summary><p>将采集电脑的完整记录复制至本项目 data/gnautilus，再填写实际身份。未知信息不猜填；训练与开发分组由服务器核查。</p>
   <div class="gw-actions">${field('完整记录','<select id="gmRecord"></select>')}<button class="button" type="button" id="gmRecordsRefresh">刷新本机记录</button></div><div class="gw-form-grid">${field('参与者','<input id="gmSubject" placeholder="实际参与者ID">')}${field('记录日期','<input type="date" id="gmDate">')}${field('佩戴ID','<input id="gmWearing" placeholder="同次佩戴共用ID">')}${field('参考','<input id="gmReference" placeholder="与设备描述一致">')}${field('数据用途','<select id="gmRole"><option value="train">训练</option><option value="development">开发验证</option></select>')}${field('实验模式',`<select id="gmMode">${modes()}</select>`)}</div>
   ${field('电气策略 JSON（依据实际输入，接触阻抗另列）',`<textarea id="gmQuality" rows="3" placeholder='{"rail_uv":实际量程,"flat_ptp_uv":平线阈值,"max_rms_uv":有效幅度上限,"max_line_ratio":工频比例上限}'></textarea>`)}<button type="button" class="button" id="gmImport">核查、上传并登记此记录</button><p id="gmImportStatus" role="status"></p></details>
   <details class="gw-card gw-disclosure"><summary>组合已登记数据集</summary><p>仅组合输入合同与任务语义一致的记录；不把不同公开任务混成导航标签。</p>${field('组合名称','<input id="gmCombineTitle" placeholder="为组合取一个可辨认的名称">')}${field('数据集完整ID，每行一个','<textarea id="gmCombineIds" rows="3"></textarea>')}<button class="button" type="button" id="gmCombine">登记组合数据集</button></details>
  </section>
  <section id="gwPane-jobs" class="gw-pane" aria-labelledby="gwTab-jobs" hidden>
   <div class="gw-heading"><div><h2>任务与结果</h2><p id="gmState" role="status">等待服务器回执…</p></div></div>
   <div class="gw-filters">${field('搜索任务','<input type="search" id="gwJobSearch" placeholder="数据名称、算法、任务或模型ID">')}${field('任务状态',`<select id="gwJobState">${option('recent','近期任务（收起已取消）')+option('active','进行中与排队')+option('attention','失败与中断')+option('all','全部状态')+option('completed','已完成')+option('cancelled','已取消')}</select>`)}${field('研究来源',`<select id="gwJobScope">${scopes()}</select>`)}<button class="gw-link" type="button" id="gwClearJobs">清除筛选</button></div><p id="gwJobDataset" class="gw-muted"></p>
   <div class="gw-task-grid"><article class="gw-card gw-job-list"><div class="tw-table-scroll"><table><caption class="gw-sr-only">训练任务目录，选择查看详情</caption><thead><tr><th>训练任务 / 来源</th><th>阶段</th><th>更新时间</th></tr></thead><tbody id="gmJobs"></tbody></table></div><div class="gw-pager"><button class="button" id="gwJobPrev">上一页</button><span id="gwJobPage"></span><button class="button" id="gwJobNext">下一页</button></div></article>
   <article class="gw-card gw-job-detail"><div class="gw-heading"><h2 id="gmLogTitle">选择任务查看详情</h2><span id="gwJobBadge" class="gw-badge"></span></div><div id="gwJobMeta"></div><ol id="gwPhases" class="gw-phases"></ol><div id="gwJobActions" class="gw-actions"></div><div id="gwJobResult"></div>
   <div class="gw-heading"><h3>真实训练曲线</h3>${field('曲线指标','<select id="gwCurveMetric"><option value="loss">交叉熵 / 损失</option><option value="ba">平衡准确率 BA</option></select>')}</div><p id="gwCurveLegend" class="gw-muted">蓝色：训练　绿色虚线：开发</p><svg id="gmChart" class="training-chart" viewBox="0 0 680 220" aria-label="所选任务当前尝试的真实训练曲线" role="img"></svg><p id="gwCurveNote" class="gw-muted"></p><details class="gw-help" open><summary>最近事件与日志</summary><label class="gw-check"><input id="gwLogFollow" type="checkbox" checked> 跟随最新事件</label><pre id="gmLogs" class="training-log" tabindex="0">选择任务后读取事件</pre></details><details class="gw-help"><summary>原始事件 JSON（最近80条）</summary><pre id="gwRawLogs" class="training-log" tabindex="0"></pre></details></article></div>
  </section>
  <section id="gwPane-models" class="gw-pane" aria-labelledby="gwTab-models" hidden><div id="gwModelHost"></div><article class="gw-card"><div class="gw-heading"><h2>同一数据集的结果比较</h2></div><p class="gw-muted">选择一组输入合同；只比较同一数据身份、类别、通道和历史窗口，多个 seed 分别保留。BA 为已查看开发结果。</p>${field('比较数据与输入合同','<select id="gwCompare"></select>')}<div id="gwComparison" class="tw-table-scroll"></div></article><div class="gw-card"><h3>回放当前已加载模型</h3><p>使用“数据与新建训练”中选定的数据集开发部分进行离线回放。运行前会停止推理与动作。</p><p id="gwReplaySelection" class="gw-muted"></p><button class="button" type="button" id="gmReplay" disabled>停止动作并回放开发数据</button><div id="gwReplayResult" role="status"></div></div></section>
  <section id="gwPane-tools" class="gw-pane" aria-labelledby="gwTab-tools" hidden>
   <article class="gw-card"><h2>私有计算连接</h2><p>A100二号机 · 172.25.56.83:5522 · 仅本机中枢通过 SSH 访问。</p><div class="gw-actions">${field('SSH密码','<input id="gmPassword" type="password" autocomplete="off">')}<button type="button" class="button" id="gmConnect">连接服务器</button></div><p>凭据仅用于现有连接流程，刷新任务不会重启服务器。</p></article>
   <article class="gw-card"><h2>下一次 VR 采集范式</h2><p>只设置下一次任务提示；开始采集仍需在 VR 端手动操作。</p><div class="gw-actions">${field('下一次VR采集模式',`<select id="gmCaptureMode">${modes()}</select>`)}<button class="button" type="button" id="gmCaptureApply">设置下一次范式</button></div></article>
   <details class="gw-card gw-disclosure"><summary>导入外部数值分类器</summary><p>项目内 gnautilus.model.v1 目录（manifest.json＋weights.npz）。校验并登记后在模型库手动加载。</p>${field('项目内模型目录','<input id="gmModelPath" placeholder="完整本机路径">')}<button class="button" type="button" id="gmModelImport">校验并上传模型</button></details>
   <details class="gw-card gw-disclosure"><summary>MATLAB 显式数值参数转换</summary><p>只接受支持的数值方程与通道、滤波、归一化合同。WV、.p、对象和回调不能直接加载；首次转换登记为离线分类器。</p>${field('项目内 MAT 完整路径','<input id="gmMatPath">')}${field('gnautilus.numeric.v1 元数据 JSON','<textarea id="gmMatMetadata" rows="4"></textarea>')}<button class="button" type="button" id="gmMatImport">核查、转换并登记</button></details>
  </section>`;}
 function mount(host){
  if(host.dataset.mounted)return;host.dataset.mounted='true';host.classList.add('gw-workbench');host.setAttribute('aria-busy','false');
  const doc=host.ownerDocument;
  let datasets=[],jobs=[],modelSnapshot=null,jobId=null,after=0,events=[],fresh=false,lastFresh=0,serverConnected=false,polling=false,logLoading=false,selection=0,jobPage=0,dataPage=0,pane='overview',lastJobMarkup='',dataLoaded=false;
  const pending=new Set();host.innerHTML=template();
  const $=id=>doc.getElementById(id),set=(id,text)=>{$(id).textContent=text;};
  const library=$('modelLibrary');if(library)$('gwModelHost').append(library);
  function notify(message,failed=false){const el=$(failed?'gmError':'gmNotice');el.textContent=message;el.hidden=!message;}
  async function request(url,data){const post=data!==undefined,r=await fetch(url,{method:post?'POST':'GET',headers:post?{'X-EEGHub':'1','Content-Type':'application/json'}:{},body:post?JSON.stringify(data):undefined,cache:'no-store',signal:AbortSignal.timeout(post?120000:6000)});const s=await r.json();if(!r.ok)throw Error(s.error||'HTTP '+r.status);return s;}
  async function action(key,fn){if(pending.has(key))return;pending.add(key);notify('',true);notify('');updateReadiness();try{await fn();}catch(ex){notify(ex.message,true);}finally{pending.delete(key);updateReadiness();}}
  function showPane(value){pane=['overview','data','jobs','models','tools'].includes(value)?value:'overview';host.querySelectorAll('.gw-pane').forEach(el=>el.hidden=el.id!=='gwPane-'+pane);host.querySelectorAll('.gw-tabs button').forEach(el=>el.setAttribute('aria-current',el.dataset.pane===pane?'page':'false'));if(pane==='models')renderComparison();}
  host.addEventListener('click',ev=>{const button=ev.target.closest('button[data-pane]');if(button)showPane(button.dataset.pane);});
  function currentDataset(){return datasets.find(d=>d.id===$('gmDataset').value);}
  function datasetTitle(id){return datasets.find(d=>d.id===id)?.title||(modelSnapshot?.groups||[]).flatMap(g=>g.versions).find(m=>m.dataset_sha256===id)?.dataset_title||'来源待同步';}
  function live(){return fresh&&serverConnected&&Date.now()-lastFresh<8000;}
  function updateReadiness(){
   const d=currentDataset(),summary=datasetSummary(d),ready=live();$('gmTrain').disabled=!ready||!summary.canSubmit||!$('gmAlgorithm').value||pending.has('train');
   set('gwTrainReadiness',!ready?'等待新鲜的服务器连接后才能提交。':!d?'先选择一份数据集。':summary.missing.length?summary.missing.join('；')+'，先补齐数据。':'训练／开发角色齐备，可提交服务器做完整治理核查。');
   const neural=/^cnn_gru|^causal_jepa_stream$/.test($('gmAlgorithm').value);$('gmEpochs').disabled=!neural;
   set('gwRecipe',d?.schema==='gnautilus.personal_study.v1'?'联合导航JEPA · 固定三种子 · 连续状态；轮次显示实际个人适配进度。':neural?'神经网络按已提交轮次显示进度；轮数上限不是保证完成轮数。':'经典分类器按真实拟合阶段显示，不使用 epoch 百分比。');
   $('gmReplay').disabled=!ready||!modelSnapshot?.current_model_id||!d||pending.has('replay');
   set('gwReplaySelection','当前数据：'+(d?.title||'未选择')+'；模型：'+(modelSnapshot?.current_model_id?.slice(0,12)||'未加载'));
   host.querySelectorAll('[data-job-action]').forEach(b=>b.disabled=!ready||pending.has('job-action'));
   for(const id of ['gmImport','gmCombine','gmModelImport','gmMatImport'])$(id).disabled=!ready||pending.has(id);
   $('gmConnect').disabled=pending.has('connect');$('gmCaptureApply').disabled=pending.has('capture');
  }
  function renderOverview(){
   const active=jobs.filter(j=>activeStates.includes(j.state)),attention=jobs.filter(j=>['failed','interrupted'].includes(j.state)),totalModels=modelSnapshot?.total_versions;
   replaceIfChanged($('gwStats'),metric('已登记数据集',dataLoaded?datasets.length:'—','原始记录与用途可追溯')+metric('进行中 / 排队',jobs.length?active.length:live()?0:'—',live()?'来自服务器最新回执':'最后回执，待同步')+metric('需要处理',jobs.length?attention.length:live()?0:'—','失败与中断任务')+metric('模型版本',finite(totalModels)?totalModels:'—','已导出并登记'));
   set('gwCount-jobs',active.length?' '+active.length:'');set('gwCount-models',finite(totalModels)?' '+totalModels:'');set('gwCount-data',dataLoaded?' '+datasets.length:'');
   replaceIfChanged($('gwNext'),!live()?'<h3>先恢复计算连接</h3><p>查看已有回执；连接恢复后再提交或修改任务。</p><button class="button" data-pane="tools">打开连接设置 →</button>':active.length?`<h3>${active.length} 个任务正在处理</h3><p>查看输入准备、训练、评估和模型登记的实际阶段。</p><button class="button" id="gwActiveJobs">跟踪进行中任务 →</button>`:attention.length?`<h3>${attention.length} 个任务需要检查</h3><p>先看失败原因与最近事件，再决定是否恢复。</p><button class="button" id="gwAttentionJobs">查看待处理任务 →</button>`:'<h3>数据已同步，可以准备下一轮</h3><p>选择目标数据，核对训练／开发分组，再创建任务。</p><button class="button" data-pane="data">选择数据与方法 →</button>');
   const globalJobs=state=>{jobDataset='';$('gwJobSearch').value='';$('gwJobScope').value='all';$('gwJobState').value=state;jobPage=0;renderJobs();showPane('jobs');};
   $('gwActiveJobs')&&($('gwActiveJobs').onclick=()=>globalJobs('active'));
   $('gwAttentionJobs')&&($('gwAttentionJobs').onclick=()=>globalJobs('attention'));
   const current=(modelSnapshot?.groups||[]).flatMap(g=>g.versions).find(v=>v.current);
   replaceIfChanged($('gwCurrent'),'<span class="gw-muted">当前已加载模型</span><h3>'+e(current?alg(current.algorithm||current.experiment):modelSnapshot?.current_model_id||'尚未加载模型')+'</h3><p>'+e(current?.dataset_title||'在模型库查看输入兼容性，再手动选择。')+'</p><a href="/lab" class="gw-link">进入实时推理 →</a>');
   const recent=[...jobs].sort((a,b)=>(activeStates.includes(b.state)?1:0)-(activeStates.includes(a.state)?1:0)||(b.updated||0)-(a.updated||0)).filter(j=>j.state!=='cancelled').slice(0,3);
   replaceIfChanged($('gwRecent'),recent.length?recent.map(j=>{const d=datasets.find(v=>v.id===j.spec?.dataset_id);return `<button type="button" class="gw-recent-item" data-select-job="${e(j.id)}"><span class="gw-badge">${e(names[j.state]||j.state)}</span><strong>${e(alg(j.spec?.algorithm))}</strong><span>${e(d?.title||datasetTitle(j.spec?.dataset_id))}</span><small>${date(j.updated)} · ${e(j.id.slice(0,10))}</small></button>`;}).join(''):'<p class="gw-empty">暂无任务回执。数据准备好后可创建第一项训练。</p>');
  }
  function filteredDatasets(){const q=$('gwDataSearch').value.trim().toLowerCase(),scope=$('gwDataScope').value;return datasets.filter(d=>(scope==='all'||scopeOf(d)===scope)&&(!q||[d.title,d.id,...datasetSummary(d).subjects].join(' ').toLowerCase().includes(q))).sort((a,b)=>{const rank={personal:0,motor:1,gaze:2,unknown:3};return rank[scopeOf(a)]-rank[scopeOf(b)]||String(a.title).localeCompare(String(b.title),'zh-CN')||a.id.localeCompare(b.id);});}
  function renderDatasets(){
   const rows=filteredDatasets(),pages=Math.max(1,Math.ceil(rows.length/8));dataPage=Math.min(dataPage,pages-1);
   set('gwDataCount',dataLoaded?`${rows.length} / ${datasets.length} 份数据 · 个人记录与运动想象方法优先排列`:'数据目录尚未同步 · 连接后自动读取');
   $('gwDatasets').innerHTML=rows.slice(dataPage*8,dataPage*8+8).map(d=>{const s=datasetSummary(d);return `<button type="button" class="gw-data-item" data-dataset="${e(d.id)}" aria-pressed="${d.id===$('gmDataset').value}"><span class="gw-muted">${e(scopeNames[s.scope])} · ${s.count} 段</span><strong>${e(d.title||'未命名数据集')}</strong><small>训练 ${s.roles.train||0} / ${d.schema==='gnautilus.personal_study.v1'?'验证 '+(s.roles.development||0)+' / 测试 '+(s.roles.test||0):'开发 '+(s.roles.development||0)} · ${s.channels} · ${s.rate}</small><code>${e(d.id.slice(0,12))}</code></button>`;}).join('')||(dataLoaded?'<p class="gw-empty">没有匹配的数据。可修改搜索、切换来源，或导入完整记录。</p>':'<p class="gw-empty">计算连接恢复后读取数据目录；已有任务和模型可查看最后回执。</p>');
   set('gwDataPage',`${rows.length?dataPage+1:0} / ${rows.length?pages:0}`);$('gwDataPrev').disabled=!dataPage;$('gwDataNext').disabled=dataPage>=pages-1;
  }
  function datasetInfo(){
   const d=currentDataset(),s=datasetSummary(d);set('gwDatasetTitle',d?.title||'选择数据集');
   const study=d?.schema==='gnautilus.personal_study.v1';
   $('gmWindow').closest('label').querySelector('span').textContent=study?'真实输入预热（秒）':'历史窗口（秒）';
   for(const o of $('gmAlgorithm').options||[])o.disabled=study?o.value!=='causal_jepa_stream':o.value==='causal_jepa_stream';
   if(study){$('gmAlgorithm').value='causal_jepa_stream';$('gmSeed').value='17';$('gmSeed').disabled=true;$('gmEpochs').max='100';$('gmWindow').value='1.5';$('gmWindow').readOnly=true;
    $('gmDatasetInfo').innerHTML=`<p>完整试次 ${s.count} 个：训练 ${s.roles.train||0}／验证 ${s.roles.development||0}／测试 ${s.roles.test||0}</p><p>32路 · 250 Hz · 联合导航想象与眼动辅助</p><p>1.5秒为真实输入预热，历史不以1.5秒截止；8秒为训练反传展开长度，连续状态不在此处清空。</p><p>稳定窗口比例约68.18%／15.98%／15.84%；头尾与保持段随完整试次保存。</p><p class="gw-boundary">同次佩戴内部研究；训练REST含官方来源差异。本轮仅用训练和验证开展修复小试，测试暂不打开，不代表跨佩戴效果。</p>`;
    $('gwRecordDetails').innerHTML='<p>16=Cz，19=C5；原始列不重排。公开模型用于初始化与原任务教师；未标注段保持UNKNOWN。</p>';
    updateReadiness();set('gwRecipe','32路联合编码＋公开权重迁移；本轮比较JEPA与无JEPA对照，种子17。预热1.5秒，训练展开最多8秒，持续保存状态。');renderDatasets();return;
   }
   $('gmSeed').disabled=false;$('gmEpochs').max='200';if($('gmAlgorithm').value==='causal_jepa_stream')$('gmAlgorithm').value='csp_lda';
   if(d?.prepared_window_s){$('gmWindow').value=d.prepared_window_s;$('gmWindow').readOnly=true;}else $('gmWindow').readOnly=false;
   const roles=`共${s.count}段，训练 ${s.roles.train||0} 段／开发 ${s.roles.development||0} 段`+(s.roles.test?`／测试角色 ${s.roles.test} 段`:'');
   $('gmDatasetInfo').innerHTML=`<p>${roles}</p><div class="gw-mini-stats">${metric('实际输入',e(s.channels),e(s.rate))}${metric('研究用途',e(scopeNames[s.scope]),d?.prepared_window_s?'已审核 '+d.prepared_window_s+' 秒窗':'按记录标签生成窗口')}</div><p><b>参考：</b>${e(s.reference)}</p><p class="gw-boundary">${s.scope==='gaze'?'公开眼动数据保留原生任务语义；独立EOG不直接代表32路头皮EEG控制效果。':s.scope==='motor'?'原生肢体想象用于方法基线；不能改名为行走／转向想象标签，后续需个人导航想象记录。':s.scope==='personal'?'个人训练与开发需要完整佩戴／会话分组；独立验收另行保留。':'来源未核对的项目保持未知。'}</p>`;
   const input=d?.records?.[0],boundModels=(modelSnapshot?.groups||[]).flatMap(g=>g.versions).filter(m=>m.dataset_sha256===d?.id),labels=[...new Set(boundModels.flatMap(m=>m.class_labels||[]))];
   if(input)$('gmDatasetInfo').innerHTML+='<p class="gw-muted">幅值单位：'+e(input.electrical_policy?.amplitude_unit||input.filter_configuration?.unit||input.public_metadata?.source_unit||'未知')+' · 接触质量：'+e(input.electrical_policy?.contact_quality||'未测量')+'</p>';
   if(labels.length)$('gmDatasetInfo').innerHTML+='<p class="gw-muted">已登记模型的原生类别：'+e(labels.join(' / '))+'</p>';else $('gmDatasetInfo').innerHTML+='<p class="gw-muted">原生标签与分组由服务器核查；UNKNOWN 和未标注段不补成 REST。</p>';
   const preview=(d?.records||[]).slice(0,8);
   $('gwRecordDetails').innerHTML=preview.map(r=>`<p>${e(r.reader||'读取方式未知')} · ${e(r.role)} · 参与者 ${e(r.subject||'未知')} · ${e(r.date||r.public_metadata?.grouping?.native_id||'日期／分组未知')} · ${e(r.wearing_id||'佩戴未知')} · ${e(r.experiment_mode||'原生公开任务')}<br>${e(r.channels?.join(' / ')||'通道未知')} · ${e(r.sampling_rate??'未知')} Hz</p>`).join('')+((d?.records?.length||0)>8?`<p>其余${d.records.length-8}段保留在数据集清单中</p>`:'')+(d?`<p class="gw-identity">数据集完整ID：${e(d.id)}</p>`:'');
   updateReadiness();renderDatasets();
  }
  async function loadDatasets(){const s=await request('/api/ml/datasets');datasets=Array.isArray(s.datasets)?s.datasets:[];dataLoaded=true;const previous=$('gmDataset').value,sorted=[...datasets].sort((a,b)=>(scopeOf(a)==='gaze'?0:1)-(scopeOf(b)==='gaze'?0:1));$('gmDataset').innerHTML=sorted.map(d=>option(d.id,d.title||d.id)).join('');if(datasets.some(d=>d.id===previous))$('gmDataset').value=previous;else{const latest=jobs.find(j=>datasets.some(d=>d.id===j.spec?.dataset_id&&scopeOf(d)==='personal'));if(latest)$('gmDataset').value=latest.spec.dataset_id;}datasetInfo();renderJobs();renderOverview();renderComparison();}
  async function records(){const s=await request('/api/ml/local-records');$('gmRecord').innerHTML=(s.records||[]).map(r=>`<option value="${e(r.path)}" ${r.closed?'':'disabled'}>${e(r.date||'日期未知')} / ${e(r.path.split(/[\\/]/).pop())} · ${r.channels}路${r.closed?'':' · 未关闭'}</option>`).join('')||option('','暂无本机完整记录');$('gmRecord').onchange=()=>{const r=(s.records||[]).find(v=>v.path===$('gmRecord').value);if(r){$('gmDate').value=r.date||'';$('gmReference').value=r.reference||'';$('gmWearing').value=r.wearing_id||'';}};$('gmRecord').onchange();}
  let jobDataset='';
  function renderJobs(){
   const rows=filterJobs(jobs,datasets,{state:$('gwJobState').value,scope:$('gwJobScope').value,query:$('gwJobSearch').value.trim(),dataset:jobDataset}),pages=Math.max(1,Math.ceil(rows.length/10));jobPage=Math.min(jobPage,pages-1);
   host.querySelector('.gw-job-detail').hidden=!rows.length;
   if(rows.length&&!rows.some(j=>j.id===jobId)){jobId=rows[0].id;selection++;after=0;events=[];set('gmLogs','读取所选任务事件…');$('gmChart').innerHTML='';void jobLogs();}
   const markup=rows.slice(jobPage*10,jobPage*10+10).map(j=>{const d=datasets.find(v=>v.id===j.spec?.dataset_id);return `<tr ${j.id===jobId?'class="gw-selected"':''}><td><button type="button" class="gw-job-select" data-select-job="${e(j.id)}" aria-pressed="${j.id===jobId}">${e(alg(j.spec?.algorithm))}</button><span>${e(d?.title||datasetTitle(j.spec?.dataset_id))}</span><code title="${e(j.id)}">${e(j.id.slice(0,12))}${j.model_id?' → '+e(j.model_id.slice(0,12)):''}</code></td><td><span class="gw-badge" data-state="${e(j.state)}">${e(names[j.state]||j.state)}</span></td><td>${date(j.updated)}</td></tr>`;}).join('')||'<tr><td colspan="3"><p class="gw-empty">没有匹配任务。可清除筛选，或在数据页创建训练。</p></td></tr>';
   if(lastJobMarkup!==markup){$('gmJobs').innerHTML=markup;lastJobMarkup=markup;}
   set('gwJobPage',`${rows.length} 项 · ${rows.length?jobPage+1:0} / ${rows.length?pages:0}`);$('gwJobPrev').disabled=!jobPage;$('gwJobNext').disabled=jobPage>=pages-1;
   set('gwJobDataset',jobDataset?'已关联数据：'+(datasets.find(d=>d.id===jobDataset)?.title||jobDataset)+' · 使用“清除筛选”查看其他数据':'每页最多 10 项，取消任务默认收起；选择任务查看曲线和模型。');renderJobDetail();
  }
  function modelForJob(j){return (modelSnapshot?.groups||[]).flatMap(g=>g.versions).find(v=>v.id===j?.model_id);}
  function scoreResult(model){const t=model?.metrics?.training||model?.training||{},d=model?.metrics?.development||model?.development||{};return `<div class="gw-mini-stats">${metric('训练 BA',pct(t.balanced_accuracy??t.ba),'拟合结果')}${metric('开发 BA',pct(d.balanced_accuracy??d.ba),'已查看开发结果')}${metric('接纳覆盖',pct(d.coverage??d.selective?.coverage),['gnautilus.causal_jepa_stream.v1','gnautilus.causal_jepa_stream.v2'].includes(model?.runtime_schema)?'含预热／拒识的有标签更新':'合格有标签窗口')}</div>`;}
  function renderJobDetail(){
   const j=jobs.find(v=>v.id===jobId);if(!j)return;
   const d=datasets.find(v=>v.id===j.spec?.dataset_id),model=modelForJob(j),latest=curveRows(events).at(-1);
   set('gmLogTitle',alg(j.spec?.algorithm));set('gwJobBadge',names[j.state]||j.state);$('gwJobBadge').dataset.state=j.state;
   const meta=`<p><b>${e(d?.title||datasetTitle(j.spec?.dataset_id))}</b></p><p class="gw-muted">第 ${e(j.attempt??'未知')} 次尝试 · 更新 ${date(j.updated)}${latest?' · '+e(studyMethods[latest.variant]||'最近提交')+' · 第 '+e(latest.epoch)+(latest.total_epochs?' / '+e(latest.total_epochs):'')+' 轮':''}</p><details class="gw-help"><summary>任务与协议身份</summary><p class="gw-identity">任务 ${e(j.id)}<br>数据 ${e(j.spec?.dataset_id||'未知')}<br>协议 ${e(j.spec?.protocol_id||'未知')}<br>模型 ${e(j.model_id||'尚未登记')}</p></details>`;
   replaceIfChanged($('gwJobMeta'),meta);
   const phases=['queued','preparing','running','evaluating','exporting','registering','completed'],at=phases.indexOf(j.state==='waiting_resource'?'queued':j.state);
   replaceIfChanged($('gwPhases'),phases.map((v,i)=>`<li class="${i===at?'is-current':i<at?'is-done':''}">${e(names[v])}</li>`).join(''));
   const buttons=`<button type="button" class="button" data-job-data="${e(j.spec?.dataset_id||'')}">查看来源数据</button>`+(j.model_id?`<button type="button" class="button" data-job-model="${e(j.model_id)}">在模型库查看产物 →</button>`:'')+(activeStates.includes(j.state)?`<button type="button" class="button gw-danger" data-job-action="cancel">取消任务</button>`:['failed','interrupted','cancelled'].includes(j.state)?'<button type="button" class="button" data-job-action="resume">恢复任务（新尝试）</button>':'');
   replaceIfChanged($('gwJobActions'),buttons);
   const fail=[...events.slice(events.reduce((n,r,i)=>r.message==='resumed'?i:n,-1)+1)].reverse().find(v=>v.message==='task_failed');
   let result=model?'<h3>已登记模型结果</h3>'+scoreResult(model)+`<p class="gw-muted">${e(model.evaluation_stage==='same_wearing_internal_research'?'同次佩戴内部研究，控制效果待验收':model.evaluation_stage==='public_method_validation'?'公开方法开发验证':'个人开发验证')} · ${e(model.availability_reason||'输入兼容性待核对')}</p>`:j.state==='completed'?'<p class="gw-boundary">任务已收录，模型目录正在同步；可到模型库刷新查看。</p>':j.state==='failed'&&fail?'<p class="gw-failure">'+e(fail.data?.error||'任务失败，详见日志')+'</p>':'<p class="gw-muted">训练／拟合 → 评估 → 导出核对 → 模型库登记。登记成功后才显示完成。</p>';
   if(model){const dev=model.metrics?.development||model.development||{},recall=Object.entries(dev.recall||{});if(recall.length)result+='<details class="gw-help" open><summary>各类别的开发召回</summary><div class="gw-recall">'+recall.map(([k,v])=>'<div><span>'+e(k)+'</span><strong>'+pct(v)+'</strong><i style="--recall:'+ (finite(v)?Math.max(0,Math.min(100,v*100)):0)+'%"></i></div>').join('')+'</div><p class="gw-muted">原生类别保持原名；目标角度正负、眼球位移和回中不自动换成导航方向。</p></details>';}
   replaceIfChanged($('gwJobResult'),result);
   updateReadiness();
  }
  function replaceIfChanged(el,html){if(el._markup!==html){el.innerHTML=html;el._markup=html;}}
  const messageNames={input_verified:'输入核查通过',training_progress:'训练进度',evaluation:'评估结果',export_verified:'导出一致性核查通过',task_failed:'任务失败',resumed:'已恢复为新尝试',created:'已创建',cancelled:'已取消',...names};
  function eventSummary(row){
   const d=row.data||{},num=x=>finite(x)?x.toFixed(4):'—';
   if(row.message==='jepa_progress'){
    const prefix=(studyMethods[d.variant]?studyMethods[d.variant]+' · ':'')+'种子 '+(d.seed??'—')+' · ';
    if(d.phase==='ssl_phase_reused')return prefix+'已核验并复用 '+(d.completed_steps??'—')+' 次JEPA预训练更新';
    if(d.phase==='ssl')return prefix+'预训练 '+(d.step??'—')+' / '+(d.total_steps??'—')+' · 目标损失 '+num(d.loss);
    if(d.phase==='native_teacher_cache')return prefix+'准备公开原任务教师 · '+(d.windows??'—')+' 个来源窗口';
    if(d.phase==='personal_adaptation')return prefix+'完整试次适配第 '+(d.epoch??'—')+' 轮 · '+(finite(d.training_joint_ce)?'联合CE 训练 '+num(d.training_joint_ce)+'／开发 '+num(d.validation_joint_ce)+' · 训练BA '+pct(d.training_balanced_accuracy):'总损失 '+num(d.loss))+' · 开发BA '+pct(d.validation_balanced_accuracy);
    if(d.phase==='candidate_complete')return prefix+'候选完成 · 开发BA '+pct(d.validation?.balanced_accuracy)+' · 接纳覆盖 '+pct(d.validation?.coverage);
   }
   if(row.message==='training_progress')return '第 '+(d.epoch??'—')+' / '+(d.total_epochs??'—')+' 轮 · 训练损失 '+num(d.training_loss??d.loss)+' · 开发损失 '+num(d.development_loss)+' · 训练BA '+pct(d.training_balanced_accuracy)+' · 开发BA '+pct(d.development_balanced_accuracy);
   if(row.message==='input_verified')return '训练 '+(d.training_windows??'—')+' 窗 · 开发 '+(d.development_windows??'—')+' 窗 · '+(d.groups??'—')+' 个训练分组';
   if(row.message==='evaluation')return '训练BA '+pct(d.training?.balanced_accuracy)+' · 开发BA '+pct(d.development?.balanced_accuracy)+' · 独立测试另报';
   if(row.message==='export_verified')return '核查 '+(d.windows??'—')+' 窗 · 导出标签 '+(d.labels_identical===true?'一致':'待核对')+' · 模型 '+(d.model_id?.slice(0,12)||'未上报');
   if(d.error||d.reason)return String(d.error||d.reason);
   if(d.phase)return names[d.phase]||d.phase;
   return JSON.stringify(d);
  }
  function renderEvents(){
   const log=$('gmLogs'),bottom=log.scrollTop;log.textContent=events.slice(-160).map(r=>date(r.utc)+' · '+(messageNames[r.message]||r.message)+' · '+eventSummary(r)).join('\n')||'此任务尚无已提交事件';if($('gwLogFollow').checked)log.scrollTop=log.scrollHeight;else log.scrollTop=bottom;
   $('gwRawLogs').textContent=events.slice(-80).map(r=>JSON.stringify(r)).join('\n');
   const points=curveRows(events),ba=$('gwCurveMetric').value==='ba',keys=ba?['training_balanced_accuracy','development_balanced_accuracy']:['training_loss','development_loss'];
   if(!ba&&!points.some(p=>finite(p.training_loss)))keys[0]='loss';
   const valid=points.filter(p=>keys.some(k=>finite(p[k]))),values=valid.flatMap(p=>keys.map(k=>p[k]).filter(finite));
   if(!valid.length){$('gmChart').innerHTML='<text x="24" y="105" fill="currentColor">此阶段无逐轮损失或 BA；经典分类器按实际拟合阶段显示</text>';set('gwCurveNote','仅绘制当前尝试已提交的真实采样点，不推算进度。');return;}
   const max=ba?1:Math.max(...values,.001)*1.1,minEpoch=Math.min(...valid.map(p=>p.epoch)),maxEpoch=Math.max(...valid.map(p=>p.epoch));
   const x=p=>52+(p.epoch-minEpoch)/Math.max(1,maxEpoch-minEpoch)*600,y=v=>182-v/max*150;
   const line=(key,color,dash)=>{const rows=valid.filter(p=>finite(p[key]));return `<polyline fill="none" stroke="${color}" stroke-width="2.5" stroke-dasharray="${dash}" points="${rows.map(p=>x(p)+','+y(p[key])).join(' ')}"/>`+rows.map(p=>`<circle cx="${x(p)}" cy="${y(p[key])}" r="2.5" fill="${color}"><title>第 ${p.epoch} 轮 · ${ba?pct(p[key]):p[key].toFixed(4)}</title></circle>`).join('');};
   $('gmChart').innerHTML=`<path d="M52 32H652M52 107H652M52 182H652" stroke="#e0e8f1"/><text x="4" y="36">${ba?'100%':max.toFixed(2)}</text><text x="20" y="186">0</text><text x="52" y="210">第 ${minEpoch} 轮</text><text x="652" y="210" text-anchor="end">第 ${maxEpoch} 轮</text>`+line(keys[0],'#3768be','')+line(keys[1],'#177b68','6 4');
   set('gwCurveNote',`${valid.length} 个已提交轮次 · 当前尝试 · 横轴为真实 epoch。${ba?'BA 为训练／开发评估，独立验收另报。':points.some(p=>finite(p.training_joint_ce))?'实线为训练联合CE，虚线为开发联合CE；辅助损失另见日志，不等于控制准确率。':'损失反映拟合目标，不等于控制准确率。'}`);
  }
  async function selectJob(id,navigate=true){
   if(!filterJobs(jobs,datasets,{state:$('gwJobState').value,scope:$('gwJobScope').value,query:$('gwJobSearch').value.trim(),dataset:jobDataset}).some(j=>j.id===id)){jobDataset='';$('gwJobSearch').value='';$('gwJobScope').value='all';$('gwJobState').value='all';}
   jobId=id;selection++;after=0;events=[];set('gmLogs','读取所选任务事件…');$('gmChart').innerHTML='';renderJobs();if(navigate)showPane('jobs');await jobLogs();
  }
  async function jobLogs(){
   if(!jobId||logLoading)return;if(!live()){if(!events.length)set('gmLogs','计算连接未就绪，恢复连接后再读取所选任务事件。');return;}const id=jobId,token=selection;logLoading=true;
   try{let more=true,pages=0;const known=new Set(events.map(r=>r.seq));
    while(more&&pages++<10){const before=after,s=await request('/api/training/jobs/'+encodeURIComponent(id)+'/events?after='+after);if(id!==jobId||token!==selection)return;
     for(const row of s.events||[]){if(!known.has(row.seq)){events.push(row);known.add(row.seq);}after=Math.max(after,row.seq);}more=(s.events||[]).length===500&&after>before;
    }
    events=events.slice(-5000);renderEvents();renderJobDetail();if(more){$('gmChart').innerHTML='<text x="24" y="105" fill="currentColor">正在同步后续事件，当前尝试的曲线稍后显示</text>';set('gwCurveNote','事件较多，正在按真实序号继续同步；暂不将旧尝试标为当前曲线。');}
   }catch(ex){if(id===jobId&&token===selection)set('gmLogs','任务事件读取失败：'+ex.message+'\n'+$('gmLogs').textContent);}finally{logLoading=false;if(id!==jobId||token!==selection)await jobLogs();}
  }
  function renderComparison(){
   const versions=(modelSnapshot?.groups||[]).flatMap(g=>g.versions),groups=[];
   for(const m of versions){let group=groups.find(g=>comparable(g[0],m));if(!group){group=[];groups.push(group);}group.push(m);}
   const selected=$('gwCompare').value,options=groups.map(g=>option(g[0].id,`${g[0].dataset_title||'来源未命名'} · ${g[0].channels?.length??'?'}路 · ${g[0].window_s??'?'}秒 · ${g.length}版本 · ${g[0].dataset_sha256?.slice(0,8)||'身份未知'}`)).join('');
   replaceIfChanged($('gwCompare'),options);if(groups.some(g=>g[0].id===selected))$('gwCompare').value=selected;
   const rows=groups.find(g=>g[0].id===$('gwCompare').value)||[];
   $('gwComparison').innerHTML=rows.length?'<table><thead><tr><th>算法 / 版本</th><th>训练 BA</th><th>开发 BA</th><th>接纳覆盖</th><th>原生类别与召回</th></tr></thead><tbody>'+rows.map(m=>{const d=m.metrics?.development||{};return `<tr><td>${e(alg(m.algorithm||m.experiment))}<code>${e(m.id.slice(0,12))} · seed ${e(m.seed??'未知')}</code></td><td>${pct(m.training?.ba)}</td><td>${pct(m.development?.ba)}</td><td>${pct(d.selective?.coverage)}</td><td>${e(Object.entries(d.recall||{}).map(([k,v])=>k+' '+pct(v)).join(' / ')||'未上报')}</td></tr>`;}).join('')+'</tbody></table>':'<p class="gw-empty">模型登记后可在此比较同一数据与输入合同下的版本。</p>';
  }
  async function poll(){if(polling)return;polling=true;try{
   const s=await request('/api/training/jobs');jobs=Array.isArray(s.jobs)?s.jobs:[];fresh=s.fresh===true&&!s.error;serverConnected=!!s.server?.worker_connected;if(fresh&&serverConnected)lastFresh=Date.now();
   set('gmServer',live()?'计算连接正常 · 回执已同步':s.server?.connecting?'计算服务器连接中…':s.error||s.server?.last_error||'计算连接未就绪');
   const stamp=s.updated_at?date(s.updated_at):'本次读取';set('gmState',(live()?'服务器回执已同步 · ':'显示最后回执，操作暂不可用 · ')+stamp);if($('trainingSync'))set('trainingSync',live()?'回执已同步':'连接待恢复');
   if(!jobId&&jobs.length){const candidate=jobs.find(j=>activeStates.includes(j.state))||jobs.find(j=>j.state!=='cancelled')||jobs[0];jobId=candidate.id;selection++;}
   if(!live()&&!events.length)set('gmLogs','计算连接未就绪，恢复连接后再读取所选任务事件。');renderOverview();renderJobs();if(jobId&&live()&&(pane==='jobs'||!events.length))await jobLogs();if(live()&&!dataLoaded)await loadDatasets();
  }catch(ex){fresh=false;set('gmState','中枢连接中断：'+ex.message);set('gmServer','连接中断 · 现有回执仅供查看');if($('trainingSync'))set('trainingSync','连接中断');renderOverview();updateReadiness();}finally{polling=false;}}
  host.addEventListener('click',ev=>{const b=ev.target.closest('button');if(!b)return;
   if(b.dataset.dataset){$('gmDataset').value=b.dataset.dataset;datasetInfo();}
   if(b.dataset.selectJob)action('select-'+b.dataset.selectJob,()=>selectJob(b.dataset.selectJob));
   if(b.dataset.jobData){if(datasets.some(d=>d.id===b.dataset.jobData)){$('gmDataset').value=b.dataset.jobData;datasetInfo();showPane('data');}else notify('来源数据尚未同步，请刷新数据目录。',true);}
   if(b.dataset.jobModel){showPane('models');doc.dispatchEvent(new CustomEvent('gnautilus:focus-model',{detail:{id:b.dataset.jobModel}}));}
   if(b.dataset.jobAction)action('job-action',async()=>{if(!live())throw Error('服务器状态已过期，先恢复连接');await request('/api/training/jobs/'+encodeURIComponent(jobId)+'/'+b.dataset.jobAction,{});notify('任务操作已提交，等待服务器回执。');await poll();});
  });
  $('gmDataset').onchange=datasetInfo;$('gmAlgorithm').onchange=updateReadiness;
  for(const id of ['gwDataSearch','gwDataScope'])$(id).addEventListener(id.endsWith('Search')?'input':'change',()=>{dataPage=0;renderDatasets();});
  for(const id of ['gwJobSearch','gwJobScope','gwJobState'])$(id).addEventListener(id.endsWith('Search')?'input':'change',()=>{jobPage=0;renderJobs();});
  for(const [id,fn] of Object.entries({gwDataPrev:()=>{dataPage--;renderDatasets();},gwDataNext:()=>{dataPage++;renderDatasets();},gwJobPrev:()=>{jobPage--;renderJobs();},gwJobNext:()=>{jobPage++;renderJobs();},gwClearJobs:()=>{jobDataset='';$('gwJobSearch').value='';$('gwJobScope').value='all';$('gwJobState').value='all';jobPage=0;renderJobs();},gwDatasetJobs:()=>{jobDataset=$('gmDataset').value;$('gwJobState').value='all';$('gwJobScope').value='all';$('gwJobSearch').value='';jobPage=0;renderJobs();showPane('jobs');}}))$(id).onclick=fn;
  $('gwCurveMetric').onchange=renderEvents;$('gwCompare').onchange=renderComparison;
  $('gmDatasetsRefresh').onclick=()=>action('datasets',loadDatasets);$('gmRecordsRefresh').onclick=()=>action('records',records);
  $('gmConnect').onclick=()=>action('connect',async()=>{const password=$('gmPassword').value;$('gmPassword').value='';await request('/api/server/connect',{password});dataLoaded=false;notify('已提交计算连接请求，正在等待认证与服务回执。');await poll();});
  $('gmCaptureApply').onclick=()=>action('capture',async()=>{await request('/api/ml/collection-profile',{experiment_mode:$('gmCaptureMode').value});notify('已设置下一次采集范式；当前不会启动录制。');});
  $('gmImport').onclick=()=>action('gmImport',async()=>{if(!$('gmRecord').value)throw Error('没有可导入的完整记录');const attrs={subject:$('gmSubject').value.trim(),date:$('gmDate').value,wearing_id:$('gmWearing').value.trim(),role:$('gmRole').value,experiment_mode:$('gmMode').value,reference:$('gmReference').value.trim(),electrical_policy:JSON.parse($('gmQuality').value)};set('gmImportStatus','核对并上传中…');try{const s=await request('/api/ml/datasets/import',{records:[{path:$('gmRecord').value,attributes:attrs}]});set('gmImportStatus','服务器已登记：'+s.dataset_id);$('gmCombineIds').value+=($('gmCombineIds').value?'\n':'')+s.dataset_id;await loadDatasets();}catch(ex){set('gmImportStatus','导入失败：'+ex.message);throw ex;}});
  $('gmCombine').onclick=()=>action('gmCombine',async()=>{const ids=$('gmCombineIds').value.split(/\s+/).filter(Boolean);if(ids.length<2)throw Error('至少选择两份已登记数据集');const s=await request('/api/ml/datasets/combine',{dataset_ids:ids,title:$('gmCombineTitle').value.trim()||'组合数据集'});await loadDatasets();$('gmDataset').value=s.dataset_id;datasetInfo();notify('组合数据已登记，请核对用途和输入合同。');});
  $('gmTrain').onclick=()=>action('train',async()=>{if(!live())throw Error('连接未就绪');if(!datasetSummary(currentDataset()).canSubmit)throw Error('训练与开发记录未齐备');const window_s=Number($('gmWindow').value),max_epochs=Number($('gmEpochs').value),seed=Number($('gmSeed').value);if(!finite(window_s)||window_s<.5||window_s>5||!Number.isInteger(max_epochs)||max_epochs<1||max_epochs>200||!Number.isInteger(seed)||seed<0||seed>2147483647)throw Error('请核对窗口、轮数上限和随机种子的有效范围');const j=await request('/api/training/jobs',{dataset_id:$('gmDataset').value,algorithm:$('gmAlgorithm').value,...($('gmAlgorithm').value==='causal_jepa_stream'?{architecture:'shared_full32_joint_v2',ssl_steps:300}:{}),window_s,max_epochs,seed,request_key:crypto.randomUUID()});jobDataset='';$('gwJobState').value='all';$('gwJobScope').value='all';$('gwJobSearch').value='';jobs=[j,...jobs.filter(v=>v.id!==j.id)];await selectJob(j.id);notify('训练任务已创建；完成导出与登记后出现在模型库。');await poll();});
  $('gmReplay').onclick=()=>action('replay',async()=>{const s=await request('/api/ml/replay',{dataset_id:$('gmDataset').value,role:'development'});$('gwReplayResult').innerHTML=scoreResult({metrics:{development:s.metrics}})+'<p class="gw-muted">离线回放完成，推理与动作已停止。</p><details><summary>完整指标回执</summary><pre class="gw-json">'+e(JSON.stringify(s.metrics,null,2))+'</pre></details>';});
  $('gmModelImport').onclick=()=>action('gmModelImport',async()=>{await request('/api/models/import',{local_path:$('gmModelPath').value.trim()});notify('模型已登记，请刷新模型库。');});
  $('gmMatImport').onclick=()=>action('gmMatImport',async()=>{await request('/api/ml/import-matlab',{local_path:$('gmMatPath').value.trim(),metadata:JSON.parse($('gmMatMetadata').value)});notify('数值分类器已转换并登记，实时合同需另行核对。');});
  doc.addEventListener('gnautilus:open-tools',()=>showPane('tools'));
  doc.addEventListener('gnautilus:models',ev=>{modelSnapshot=ev.detail;renderOverview();renderComparison();renderJobDetail();updateReadiness();});
  doc.addEventListener('gnautilus:open-data',ev=>{const target=datasets.find(d=>d.id===ev.detail?.dataset_id);if(target){$('gmDataset').value=target.id;datasetInfo();showPane('data');}else notify('模型来源数据尚未匹配到已登记目录，可按数据集ID搜索核对。',true);});
  const refresh=$('trainingRefresh');if(refresh){refresh.onclick=()=>action('refresh',async()=>{await poll();if(serverConnected)await loadDatasets();});refresh.textContent='刷新工作台 ↻';}
  action('initial',async()=>{const s=await request('/api/ml/capabilities');$('gmAlgorithm').innerHTML=(s.algorithms||[]).map(a=>option(a.id,a.title)).join('');if((s.algorithms||[]).some(a=>a.id==='csp_lda'))$('gmAlgorithm').value='csp_lda';await poll();});
  action('records',records);action('profile',async()=>{const p=await request('/api/ml/collection-profile');$('gmCaptureMode').value=p.experiment_mode;});
  action('models-initial',async()=>{modelSnapshot=await request('/api/models');renderOverview();renderComparison();renderJobDetail();updateReadiness();});
  renderOverview();datasetInfo();updateReadiness();setInterval(poll,2500);setInterval(()=>{if(!live())updateReadiness();},1000);
 }
 return {mount,scopeOf,datasetSummary,filterJobs,curveRows,comparable};
});
