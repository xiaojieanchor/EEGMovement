'use strict';
(()=>{
 if(!document.getElementById('labMain'))return;
 const C=EEGImmersiveCore,layout=new C.Layout(),buffer=new C.SignalBuffer(),outputCache=new C.ObservationCache(),qualityCache=new C.ObservationCache(),vrCache=new C.ObservationCache(),$=id=>document.getElementById(id);
 const names=['静止','前进','后退','左转','右转'],eyeNames=['无方向事件','向上','向下','向左','向右'],colors=['#9ab0c9','#66dfc1','#7da9ff','#c3a0f5','#edc078'];
 const reasonNames={activity_below_threshold:'活动证据不足',direction_below_threshold:'方向证据不足',direction_margin_low:'方向仍有混淆',confirming:'连续确认中',accepted:'方向证据已接纳',rest_supported:'静止证据已接纳',real_history_prewarm:'模型状态预热中'};
 const qualityNames={flatline:'平线',rail:'饱和',rms:'RMS超限',line_noise:'工频占比过高',ptp:'峰峰值超限',nonfinite:'非有限值',quality_stale:'数据已过期'};
 const errorText=e=>/Failed to fetch|Load failed|NetworkError|network request failed/i.test(e?.message||'')?'中枢连接失败，请检查服务与网络':e?.name==='TimeoutError'||e?.name==='AbortError'?'中枢请求超时，请重试':e?.message||'请求失败';
 const pct=n=>Number.isFinite(n)?(100*n).toFixed(1)+'%':'—',num=(n,d=1)=>Number.isFinite(n)?n.toFixed(d):'—';
 const put=(el,v)=>{v=String(v??'');if(el&&el.textContent!==v)el.textContent=v};const text=(id,v)=>put($(id),v);
 const entry=document.createElement('section');entry.className='immersive-entry';entry.id='immersiveEntry';entry.innerHTML='<div><small>沉浸式推理 / 四屏协同</small><h2>沉浸式推理工作区</h2><p>连接、信号、VR 与模型判断，在同一视野中。</p><div class="ims-entry-reasons" id="imsEntryReasons" role="status">正在核对四端与模型输入…</div></div><button class="button primary-action" id="imsEnter" disabled>进入沉浸式推理 ↗</button>';
 document.querySelector('.chain').before(entry);
 const root=document.createElement('section');root.id='immersiveWorkspace';root.className='ims';root.hidden=true;root.setAttribute('aria-label','沉浸式推理工作区');
 function panel(id,index,title,en,body){return `<section class="ims-panel" data-panel="${id}" aria-label="${title}"><header class="ims-head" tabindex="0" role="button" aria-label="放大${title}"><div><small>${index} / ${en}</small><h2>${title}</h2></div><span class="ims-expand" aria-hidden="true">⤢</span></header><div class="ims-body${id==='vr'?' ims-vr-body':''}">${body}</div></section>`}
 root.innerHTML=`<header class="ims-topbar"><div class="ims-brand"><span class="ims-logomark" aria-hidden="true">∿</span><div><strong>EEGMovement</strong><small>沉浸式推理工作区</small></div><span class="ims-tag" id="imsStage">阶段一 · 实验性实时推理</span></div><div class="ims-top-actions"><span id="imsClock" class="ims-clock"></span><button id="imsTheme" class="ims-quiet" aria-label="切换到亮色主题">☀ 亮色</button><button id="imsGridReset" class="ims-quiet" aria-label="恢复四分格">⊞ 四分格</button><button id="imsFullscreen" class="ims-quiet">⤢ 全屏</button><button id="imsStop" class="ims-stop">Ⅱ 立即停止</button><button id="imsExit">退出 ↗</button></div></header><div id="imsGrid" class="ims-grid">`+
 panel('information','01','链路与推理控制','连接与操作',`<div class="ims-links">${[['Hub','中枢'],['Source','采集端'],['Compute','A100'],['Vr','目标 VR']].map(([id,name])=>`<div class="ims-link" id="imsLink${id}"><span>${name}</span><strong>等待</strong><small>—</small></div>`).join('')}</div><div id="imsAlert" class="ims-alert" role="status"></div><div class="ims-model-line"><label>当前模型</label><code id="imsModelId">—</code></div><div class="ims-actions"><button id="imsInference" class="ims-primary" disabled>开始推理</button><button id="imsMotion" disabled>启用模型动作</button></div><div class="ims-state" id="imsActionFeedback" role="status">推理与模型动作独立手动开启</div><div class="ims-details"><div class="ims-form-row"><label>停止后更换模型<select id="imsModels" aria-label="推理模型"></select></label><button id="imsLoadModel" disabled>加载模型</button></div><div class="ims-params"><span>实际设备采样率<b id="imsRate">—</b></span><span>主机到达速率<b id="imsArrival">—</b></span><span>输入 / 配置身份<b id="imsInputMatch">—</b></span><span>流缺口 / 传输<b id="imsPackets">—</b></span><span>输入 → 回执 p95<b id="imsAck">—</b></span><span>原厂配置<b id="imsDeviceConfig">—</b></span></div><p id="imsReference" class="ims-note"></p><p class="ims-note">实验性模型分数不等同于真实意图概率。有效输出与最终执行回执分别显示。</p></div>`)+
 panel('signal','02','实时 EEG 与质量','32通道信号',`<div class="ims-quality-summary"><strong id="imsGood">— / 32</strong><small id="imsQualityCount">软件电气质量 · 等待评估</small></div><div id="imsChannelGrid" class="ims-channel-grid" aria-label="32通道软件质量"></div><p class="ims-qdetail" id="imsQualityDetail">点击通道查看指标</p><div class="ims-wave-tools"><span id="imsWaveLabel">实际通道 · 原始信号</span><div><button id="imsWavePrev" aria-label="上一组通道">‹</button> <button id="imsWaveAll">32路</button> <button id="imsWaveNext" aria-label="下一组通道">›</button></div></div><div class="ims-wave-stack" id="imsWaves"><canvas class="ims-wave" id="imsWave0" aria-label="原始EEG波形第1组"></canvas><canvas hidden class="ims-wave" id="imsWave1" aria-label="原始EEG波形第2组"></canvas><canvas hidden class="ims-wave" id="imsWave2" aria-label="原始EEG波形第3组"></canvas><canvas hidden class="ims-wave" id="imsWave3" aria-label="原始EEG波形第4组"></canvas></div><p class="ims-note" id="imsQualityNote">软件电气观察与设备有效性分开显示；接触阻抗未测量。</p>`)+
 panel('vr','03','VR 画面与执行','VR画面与执行',`<div class="ims-vr-viewbar"><div class="ims-pills"><button id="imsViewCamera" aria-pressed="true">VR视角</button><button id="imsViewMonitor" aria-pressed="false">系统监控</button></div><select id="imsReceiver" aria-label="目标VR设备"></select></div><div class="ims-scene"><span id="imsVideoValidity" class="ims-video-validity"></span><img id="imsCamera" alt="目标VR应用中心视角" hidden><canvas id="imsArena" aria-label="目标接收器实际遥测场景" hidden></canvas><div class="ims-scene-empty" id="imsSceneEmpty"><span>等待目标 VR</span><small>画面与执行回执独立确认</small></div></div><div class="ims-pose"><span>实际速度 <b id="imsSpeed">—</b> m/s</span><span>角速度 <b id="imsYaw">—</b> °/s</span><span id="imsPose">x — / z —</span></div><p id="imsVrNote" class="ims-note">等待新鲜回执；历史画面不作为当前状态。</p>`)+
 panel('output','04','导航证据与眼动状态','导航与眼动证据',`<div class="ims-output-top"><strong id="imsDecision"><span id="imsDecisionCurrent">推理尚未开始</span></strong><span id="imsDuration">等待连续输入</span></div><div class="ims-score-grid">${names.map((name,i)=>`<div class="ims-score" style="--score-color:${colors[i]}"><label>${name}</label><b id="imsScore${i}">—</b><div class="ims-bar"><i id="imsScoreBar${i}"></i></div></div>`).join('')}</div><div id="imsOutputValidity" class="ims-validity" role="status">等待开始</div><canvas class="ims-chart" id="imsHistory" aria-label="最近60秒模型导航分数"></canvas><div class="ims-direction-row">${names.slice(1).map((name,i)=>`<div class="ims-direction"><b>${name}</b><span id="imsDirection${i}">条件分数 —</span><em id="imsThreshold${i}">门槛 —</em></div>`).join('')}</div><div id="imsLedger" class="ims-ledger">活动证据与方向证据分别判断</div><div class="ims-eye"><div class="ims-eye-title"><b>眼动瞬时分类 · 模型估计</b><span id="imsEyeActivity">事件活动 —</span></div><div class="ims-eye-scores">${eyeNames.map((name,i)=>`<div class="ims-eye-score"><span>${name}<b id="imsEye${i}">—</b></span><div class="ims-bar"><i id="imsEyeBar${i}" style="background:#bb9de8"></i></div></div>`).join('')}</div><div id="imsEyeMemory" class="ims-eye-memory">模型保留的方向线索：—</div><div id="imsEyeEvents" class="ims-event-strip" aria-label="确认眼动事件"></div></div><p id="imsOutputNote" class="ims-note">眼动事件不代表客观注视位置；方向记忆不等于眼睛仍停在某方向。</p>`)+`</div>`;
 document.body.append(root);
 let active=false,snapshot=null,at=-Infinity,polling=false,signalPolling=false,pending=false,actionEpoch=0,selectedReceiver='',page=0,allWaves=false,cameraView=true,frameKey='',frameAt=-Infinity,frameLoading=false,channelSelected=0;
 let history=[],lastHistory='',historyIdentity='',lastIdle=0,drawPending=false,previousActiveElement=null,lastFocusedPanel=null,cameraSequence=0,frameStarted=-Infinity,theme='dark',decisionAnimation=null,decisionOld=null,layoutAnimations=[],themeAnimation=null,signalAt=-Infinity,lastWavePaint=0;
 const scene=EEGTelemetryScene.create($('imsArena'),{dark:true}),channels=[];
 const panels=[...root.querySelectorAll('.ims-panel')];
 const knownStates={ACTIVE:'方向已接纳',REST:'静止已接纳',UNKNOWN:'未确认',STOP:'已停止',MOVING:'正在运动',LIVE:'实时',TEST:'链路测试',WARMUP:'预热中'};
 const transportNames={lan_ws:'局域网直连',tls_ingest:'TLS加密传输',public_relay:'公网转发',isolated_record_replay:'隔离记录回放'};
 const runtimeNames={idle:'静止',experimental_model_active:'实验性模型指令',stream_confirming_or_rejected:'等待连续确认',eeg_stream_stale:'脑电输入过期',server_stale:'计算返回过期',local_emergency_stop:'头显急停已锁定',watchdog_timeout:'指令超时停止',SOFTWARE_REPLAY_NO_REAL_HEADSET:'软件回放夹具 · 非真实头显',frame_stall:'帧循环暂停',paired_waiting_command:'已配对，等待指令',receiver_update_required:'接收器需要更新'};
 const readable=(value,map,fallback='状态待确认')=>map[value]||fallback;
 function observationKey(s){const model=s.model?.model_id,epoch=s.source?.source_epoch;return model&&epoch!==undefined?model+':'+epoch+':'+(s.source?.stream_id||''):null}
 function setDecision(value){
  const el=$('imsDecisionCurrent');if(el.textContent===value)return;
  const old=el.textContent;decisionAnimation?.cancel();decisionOld?.remove();decisionOld=null;put(el,value);
  if(matchMedia('(prefers-reduced-motion: reduce)').matches||!el.animate)return;
  const ghost=document.createElement('span');ghost.className='ims-decision-old';ghost.setAttribute('aria-hidden','true');ghost.textContent=old;$('imsDecision').append(ghost);decisionOld=ghost;
  ghost.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:'translateY(-12px)'}],{duration:220,easing:'ease-out'}).finished.then(()=>{ghost.remove();if(decisionOld===ghost)decisionOld=null}).catch(()=>{});
  decisionAnimation=el.animate([{opacity:0,transform:'translateY(14px)'},{opacity:1,transform:'translateY(0)'}],{duration:360,easing:'cubic-bezier(.2,.8,.2,1)'});
 }
 function applyTheme(next,animate=false){
  theme=next==='light'?'light':'dark';root.dataset.theme=theme;
  text('imsTheme',theme==='dark'?'☀ 亮色':'☾ 暗色');$('imsTheme').setAttribute('aria-label',theme==='dark'?'切换到亮色主题':'切换到暗色主题');
  try{localStorage.setItem('eegmovement.immersive.theme',theme)}catch{}
  scene.setTheme?.(theme==='dark');
  if(animate&&!matchMedia('(prefers-reduced-motion: reduce)').matches&&root.animate){themeAnimation?.cancel();themeAnimation=root.animate([{filter:'brightness(.96)'},{filter:'brightness(1)'}],{duration:280,easing:'ease-out'})}
  scheduleDraw();
 }
 try{theme=localStorage.getItem('eegmovement.immersive.theme')||'dark'}catch{}applyTheme(theme);

 async function request(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-EEGHub':'1'},body:JSON.stringify(data),signal:AbortSignal.timeout(8000)});const j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}
 function command(path,data={}){return window.EEGIntent?.transport?window.EEGIntent.transport(path,data):request(path,data)}
 function current(){return C.view(snapshot,performance.now()-at)}
 function link(id,ok,title,detail){const e=$('imsLink'+id);e.dataset.ok=String(!!ok);put(e.querySelector('strong'),title);put(e.querySelector('small'),detail)}
 function options(select,items,selected){const signature=JSON.stringify(items);if(select.dataset.options!==signature){select.replaceChildren();for(const row of items){const o=document.createElement('option');o.value=row.id;o.textContent=row.title;o.disabled=!!row.disabled;select.append(o)}select.dataset.options=signature}if([...select.options].some(o=>o.value===selected))select.value=selected}
 function render(s,stamp){
  snapshot=s;at=stamp;const v=current(),i=s.intent||{},q=s.quality||{},model=s.model;
  $('imsEnter').disabled=!v.entry||pending;text('imsEnter',s.mode==='OFFLINE_REPLAY'?'进入开发回放预览 ↗':'进入沉浸式推理 ↗');const blockers=s.blockers||[];
  text('imsEntryReasons',blockers.map(item=>item.message).join('；'));
  if(!blockers.length)$('imsEntryReasons').textContent='四端与模型输入已匹配'+(!q.valid?' · 可进入排查通道质量':' · 进入后手动开始推理');
  if(s.mode==='OFFLINE_REPLAY')text('imsEntryReasons','隔离开发回放 · 连接与VR图样为软件夹具 · 不接通真实动作');
  if(!active)return;
  link('Hub',v.current,v.current?'在线':'状态过期','本机中枢 · '+num(s.hub?.uptime_s,0)+' s');
  link('Source',v.source,v.source?'信号接收中':'等待数据',num(s.source?.age_ms,0)+' ms · '+readable(s.source?.transport,transportNames,'未知通道'));
  link('Compute',v.current&&s.compute?.fresh,s.compute?.fresh&&v.current?'计算已连接':'等待计算',num(s.compute?.roundtrip_ms,0)+' ms · SSH');
  link('Vr',v.vr,v.vr?'执行回执在线':'等待回执',num(s.vr?.target?.telemetry_age_ms,0)+' ms · '+(s.vr?.target?.receiver_id?.slice(0,6)||'未选择'));
  if(s.mode==='OFFLINE_REPLAY'){link('Source',v.source,'回放输入',num(s.source?.age_ms,0)+' ms · 原记录');link('Vr',v.vr,'软件夹具','合成回执 · 非真实头显')}
  const target=s.vr?.target;if(!selectedReceiver&&target)selectedReceiver=target.receiver_id;
  options($('imsReceiver'),[{id:'',title:'选择目标 VR'},...(s.vr?.clients||[]).map(c=>({id:c.receiver_id,title:(c.telemetry?.app_version||'Unity')+' · '+c.receiver_id.slice(0,8)+(c.online?'':' · 离线')}))],selectedReceiver);
  const selectedModel=$('imsModels').value;
  options($('imsModels'),(s.models||[]).map(m=>({id:m.id,title:(m.deployment?.stage==='experimental_live_v1'?'阶段一 · 实验性实时推理':m.title)+' · '+m.id.slice(0,8)+(m.runtime_accepted?'':' · 离线'),disabled:!m.runtime_accepted})),selectedModel||model?.model_id);
  $('imsModels').disabled=pending||i.inference_enabled||i.control?.enabled||!s.compute?.fresh;
  $('imsLoadModel').disabled=$('imsModels').disabled||!$('imsModels').value||$('imsModels').value===model?.model_id;
  $('imsInference').disabled=pending||!i.inference_enabled&&!v.canStart;text('imsInference',i.inference_enabled?'停止推理':q.state==='warming'?'等待质量窗口':'开始推理');
  $('imsMotion').disabled=pending||!i.inference_enabled||!v.output||!v.canStart||s.mode!=='LIVE';text('imsMotion',i.control?.enabled?'关闭模型动作':s.mode==='OFFLINE_REPLAY'?'回放不驱动动作':'启用模型动作');
  if(i.control?.enabled)$('imsMotion').disabled=pending;
  $('imsInference').setAttribute('aria-pressed',String(!!i.inference_enabled));$('imsMotion').setAttribute('aria-pressed',String(!!i.control?.enabled));
  text('imsModelId',model?model.model_id.slice(0,12)+' · '+(model.deployment?.stage==='experimental_live_v1'?'阶段一实验性模型':'已加载模型'):'等待已加载模型');
  text('imsStage',s.mode==='OFFLINE_REPLAY'?'开发回放 · 不驱动真实设备':model?.deployment?.stage==='experimental_live_v1'?'阶段一 · 实验性实时推理':'实时推理');
  root.dataset.source=s.mode||'LIVE';
  const conditions=blockers.map(x=>x.message);if(!q.valid&&v.source)conditions.push(q.state==='warming'?'软件质量窗口预热中':'软件质检未通过，推理与动作保持门控');
  text('imsAlert',conditions.join('；'));
  text('imsRate',num(s.source?.configured_rate,0)+' Hz');text('imsArrival',num(s.source?.arrival_rate,1)+' 样本/秒');text('imsInputMatch',s.input_match?'身份已匹配':'尚未匹配');
  text('imsPackets',String(s.source?.gap_count??'—')+' / '+readable(s.source?.transport,transportNames,'—'));text('imsAck',num(s.vr?.input_to_execution_ack_p95_ms,0)+' ms（上界）');
  const config=s.descriptor?.configuration||{};text('imsDeviceConfig',config.SamplingRate?config.SamplingRate+' Hz / 原厂滤波':'等待回读');
  text('imsReference','参考：'+(s.descriptor?.reference||'原记录 REF（物理位置未记录）')+' · 接地：'+(s.descriptor?.ground||'物理位置未记录')+'；不进行二次滤波。');
  renderQuality(s,v);renderOutput(s,v);renderVr(s,v);scheduleDraw();
 }
 function renderQuality(s,v){
  const cached=qualityCache.show(v.quality?s.quality:null,observationKey(s)),q=cached||s.quality||{},held=!v.quality&&!!cached;
  const identity=(s.descriptor?.channels||buffer.descriptor?.channels||[]).filter(c=>c.kind==='EEG').map((c,index)=>({...c,name:s.input_match&&s.model?.channels?.[index]||c.name}));
  root.querySelector('[data-panel=signal]').dataset.validity=v.quality?'current':'stale';
  text('imsGood',(v.quality?v.good:held?q.good_channels:'—')+' / '+(identity.length||32));text('imsQualityCount',v.quality?'合格 '+v.good+' · 异常 '+v.bad+' · 未知 '+v.unknown:held?'上次评估 · 已过期，等待更新':'软件电气质量 · '+(q.state==='warming'?'预热中':'未知'));
  if(channels.length!==identity.length||channels.some((row,index)=>row.name!==identity[index].name)){
   $('imsChannelGrid').replaceChildren();channels.length=0;
   identity.forEach((channel,index)=>{const e=document.createElement('button');e.className='ims-channel';e.setAttribute('data-no-focus','');e.append(document.createTextNode(channel.name));const small=document.createElement('span');e.append(small);e.onclick=()=>{channelSelected=index;renderQuality(snapshot,current())};$('imsChannelGrid').append(e);channels.push({name:channel.name,el:e,small})});
  }
  channels.forEach((c,index)=>{const row=q.channels?.[index],known=typeof row?.valid==='boolean'&&(v.quality||held);c.el.dataset.valid=known?String(row.valid):'unknown';put(c.small,known?(row.valid?'合格':(qualityNames[row.reasons?.[0]]||'异常')):'未知');c.el.dataset.historical=String(held);c.el.title=c.name+' · '+(row?.reasons||[]).map(r=>qualityNames[r]||r).join(' / ');c.el.setAttribute('aria-pressed',String(index===channelSelected))});
  const row=v.quality||held?q.channels?.[channelSelected]:null;
  text('imsQualityDetail',row?row.name+' · 峰峰值 '+num(row.peak_to_peak_uv)+' µV / RMS '+num(row.rms_uv)+' µV / 工频 '+pct(row.line_ratio)+(row.reasons?.length?' · '+row.reasons.map(r=>qualityNames[r]||r).join('、'):' · 软件检查合格')+(held?'（上次评估，非当前）':''):'等待足够的新鲜信号；未知不计为合格');
  text('imsQualityNote','窗口 '+num(q.window_s,1)+' s · 样本 '+(q.sample_index_start??'—')+'—'+(q.sample_index_end??'—')+' · 设备有效性 '+(q.device_valid===true?'有效':q.device_valid===false?'无效':'未提供')+' · 接触阻抗未知');
 }
 function renderOutput(s,v){
  const r=outputCache.show(v.output,observationKey(s),s.intent?.inference_enabled===true),decision=r?.navigation_decision||{},o=r?.observation,held=!!r&&!v.output;
  root.querySelector('[data-panel=output]').dataset.validity=v.output?'current':held?'stale':'waiting';
  text('imsOutputValidity',v.output?'当前判断 · 模型分数':held?'结果已过期 · 保留上次判断，等待新数据':s.intent?.inference_enabled?'等待新鲜模型结果':'推理已停止');
  setDecision(r?(r.prediction==='UNKNOWN'?'证据不足 · 拒识':names[['REST','FORWARD','BACKWARD','LEFT','RIGHT'].indexOf(r.prediction)]||'等待证据'):s.intent?.inference_enabled?'等待新鲜模型结果':'推理尚未开始');
  text('imsDuration',r?num(o?.state_duration_s,1)+' s · '+readable(decision.status,knownStates):'当前输出无效');
  names.forEach((_,index)=>{text('imsScore'+index,pct(r?.probabilities?.[index]));$('imsScoreBar'+index).style.width=(r?.probabilities?.[index]||0)*100+'%'});
  for(let index=0;index<4;index++){const threshold=decision.effective_thresholds?.[index];text('imsDirection'+index,'条件 '+pct(r?.conditional_direction_scores?.[index]));text('imsThreshold'+index,threshold?'门槛 活 '+pct(threshold.activity)+' / 向 '+pct(threshold.direction_score):'实际生效门槛 —')}
  text('imsLedger',r?'活动 '+pct(r.activity_score)+' · '+readable(decision.reason,reasonNames,'等待证据')+' · 确认 '+(decision.confirmation_count??0)+' / '+(decision.confirmation_required??'—'):'活动与条件方向分开判断；无效输出不保留为当前判断');
  eyeNames.forEach((_,index)=>{text('imsEye'+index,pct(r?.eye_scores?.[index]));$('imsEyeBar'+index).style.width=(r?.eye_scores?.[index]||0)*100+'%'});
  text('imsEyeActivity','事件活动 '+pct(r?.eye_activity_score));text('imsEyeMemory',r?'模型保留的方向线索：'+(decision.cue_direction?eyeNames[decision.cue_direction]:'无')+' · 记忆 '+num(o?.cue_duration_s,1)+' s'+(decision.pending_direction?' · '+eyeNames[decision.pending_direction]+'待确认':''):'模型保留的方向线索：—');
  const events=o?.eye_events||[];const eventKey=JSON.stringify(events);
  if($('imsEyeEvents').dataset.key!==eventKey){$('imsEyeEvents').replaceChildren();for(const e of events.slice(-7)){const chip=document.createElement('span');chip.textContent='#'+e.sequence+' '+eyeNames[e.direction]+' @ '+num(e.session_sample/(o.sampling_rate||250),1)+'s';$('imsEyeEvents').append(chip)}$('imsEyeEvents').dataset.key=eventKey}
  const identity=r?r.model_id+':'+r.source_epoch:'';
  if(r&&identity!==historyIdentity){history=[];historyIdentity=identity;lastHistory=''}
  const key=v.output?identity+':'+r.sample_index_end:'';
  if(v.output&&key!==lastHistory){lastHistory=key;history.push({sample:r.sample_index_end,values:r.probabilities});history=history.filter(x=>r.sample_index_end-x.sample<=15000).slice(-600)}
  text('imsOutputNote',held?'上次结果已过期，不能用于当前动作。历史分数保留显示，等待同一信号流恢复。':r?'输入年龄 '+num(r.input_age_ms,0)+' ms · 计算 '+num(r.worker_ms,1)+' ms · 返回 '+num(r.roundtrip_ms,0)+' ms。眼动是模型估计，记忆不代表实际注视位置。':'眼动与导航状态在断流、换模型或重置后重新建立；历史轨迹只作历史显示。');
 }
 function renderVr(s,v){
  const mirror=s.vr?.mirror||{},target=s.vr?.target,displayPeer=vrCache.show(v.vr?v.peer:null,selectedReceiver||target?.receiver_id||null),telemetry=displayPeer?.telemetry,held=!!displayPeer&&!v.vr;
  root.querySelector('[data-panel=vr]').dataset.validity=v.vr?'current':'stale';
  text('imsSpeed',telemetry?num(telemetry.actual_speed,2):'—');text('imsYaw',telemetry?num(telemetry.actual_yaw_rate,1):'—');text('imsPose',telemetry?'x '+num(telemetry.x,2)+' / z '+num(telemetry.z,2)+' / '+num(telemetry.yaw,0)+'°':'位置与朝向未知');
  $('imsArena').hidden=cameraView;$('imsCamera').hidden=!cameraView||performance.now()-frameAt>=2000;
  const cameraReady=performance.now()-frameAt<2000;
  text('imsVideoValidity',cameraView?(cameraReady?(v.vr?'':'执行回执过期 · 画面独立接收'):'等待新鲜画面'):v.vr?'':'执行回执过期 · 历史场景');
  $('imsSceneEmpty').hidden=cameraView?cameraReady:v.vr;
  put($('imsSceneEmpty').querySelector('span'),!v.vr?'等待目标 VR 新鲜回执':cameraView?'暂无该设备的新鲜画面':'等待遥测');
  put($('imsSceneEmpty').querySelector('small'),cameraView?'可切换系统监控；画面与回执分别核验':'历史位置不作为当前执行状态');
  text('imsVrNote',v.vr?'回执 '+num(target.telemetry_age_ms,0)+' ms · 序号 '+target.applied_seq+' / '+(target.sent_seq??'—')+' · '+readable(v.telemetry.reason,runtimeNames,readable(v.telemetry.state,knownStates))+' · '+(cameraView?'应用中心视角 · 非系统透视画面':'实际遥测场景'):held?'执行回执已过期；以上为上次速度与位置，当前状态未知。':'执行状态未知；停止是否生效仍以新鲜接收回执确认。');
  if(!cameraView||!active||!v.current||!target||!mirror.mirror_fresh)return;
  const key=target.device_id+':'+mirror.frame_seq;
  if(frameLoading&&performance.now()-frameStarted>2000){frameLoading=false;cameraSequence++;$('imsCamera').removeAttribute('src')}
  if(key===frameKey||frameLoading)return;
  frameStarted=performance.now();frameLoading=true;const device=target.device_id,sequence=++cameraSequence,sourceAt=at-(mirror.frame_age_ms||0);
  $('imsCamera').onload=()=>{if(sequence!==cameraSequence)return;frameLoading=false;if(snapshot?.vr?.target?.device_id!==device)return;frameAt=sourceAt;frameKey=key;if(active)renderVr(snapshot,current())};
  $('imsCamera').onerror=()=>{if(sequence!==cameraSequence)return;frameLoading=false};
  $('imsCamera').src='/api/quest/frame.jpg?device_id='+encodeURIComponent(device)+'&seq='+mirror.frame_seq;
 }
 async function poll(){
  if(polling)return;lastIdle=performance.now();polling=true;
  const stamp=performance.now(),epoch=actionEpoch;
  try{const r=await fetch('/api/immersive/status'+(selectedReceiver?'?receiver_id='+encodeURIComponent(selectedReceiver):''),{cache:'no-store',signal:AbortSignal.timeout(1500)});if(!r.ok)throw Error('无法读取沉浸式状态');const s=await r.json();if(epoch===actionEpoch)render(s,stamp)}
  catch(e){if(epoch===actionEpoch){at=-Infinity;$('imsEnter').disabled=true;text('imsEntryReasons','中枢状态暂不可用');expire()}}finally{polling=false}
 }
 async function pollSignal(){
  if(!active||signalPolling)return;signalPolling=true;
  const epoch=actionEpoch;
  try{const query=buffer.epoch===null?'':'?epoch='+buffer.epoch+'&after='+buffer.end;const r=await fetch('/api/immersive/signal'+query,{cache:'no-store',signal:AbortSignal.timeout(1200)});if(!r.ok)throw Error();const s=await r.json();if(epoch===actionEpoch){buffer.ingest(s);signalAt=performance.now();scheduleDraw()}}
  catch(e){buffer.fresh=false;scheduleDraw()}finally{signalPolling=false}
 }
 function scheduleDraw(){if(!active||drawPending)return;drawPending=true;requestAnimationFrame(()=>{drawPending=false;if(!active)return;const state=buffer.snapshot(current().source&&C.signalFresh(buffer,snapshot,performance.now()-signalAt));const scroll=Math.min(.2,Math.max(0,(performance.now()-signalAt)/1000));if(snapshot?.input_match&&state.gnautilus.descriptor){let index=0;state.gnautilus.descriptor={...state.gnautilus.descriptor,channels:state.gnautilus.descriptor.channels.map(c=>c.kind==='EEG'?{...c,name:snapshot.model?.channels?.[index++]||c.name}:c)}}for(let index=0;index<4;index++){const canvas=$('imsWave'+index);canvas.hidden=index>0&&!allWaves;if(!canvas.hidden)EEGUI.drawSignal(canvas,state,allWaves?index:page,'raw',{dark:theme==='dark',retainHistory:true,scrollOffsetSeconds:scroll})}text('imsWaveLabel',(allWaves?'全部32路':'通道 '+(page*8+1)+'—'+Math.min(page*8+8,channels.length||32))+(state.source.fresh?' · 原始信号':' · 上次波形，等待新数据'));drawHistory()})}
 function drawHistory(){
  const canvas=$('imsHistory'),box=canvas.getBoundingClientRect();if(!box.width||!box.height)return;const dpr=Math.min(devicePixelRatio||1,2);if(canvas.width!==Math.round(box.width*dpr))canvas.width=Math.round(box.width*dpr);if(canvas.height!==Math.round(box.height*dpr))canvas.height=Math.round(box.height*dpr);const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);const w=box.width,h=box.height;c.clearRect(0,0,w,h);c.strokeStyle=theme==='dark'?'#26384e':'#d6e1eb';c.lineWidth=1;
  for(let i=0;i<3;i++){c.beginPath();c.moveTo(0,6+i*(h-12)/2);c.lineTo(w,6+i*(h-12)/2);c.stroke()}
  if(!history.length)return;const end=history.at(-1).sample;c.globalAlpha=1;
  colors.forEach((color,index)=>{c.strokeStyle=color;c.beginPath();let previous=null;for(const point of history){const x=w*(1-(end-point.sample)/15000),y=h-5-point.values[index]*(h-10);if(previous===null||point.sample-previous>50)c.moveTo(x,y);else c.lineTo(x,y);previous=point.sample}c.stroke()});c.globalAlpha=1;
 }
 function expire(){if(!active)return;const v=current();if(!v.current||!v.source||!v.output){if(snapshot)renderOutput(snapshot,v);if(!v.current){text('imsAlert','中枢状态已过期；当前推理和执行状态无法确认');link('Hub',false,'状态已过期','等待连接')}}if(snapshot){renderVr(snapshot,v);if(!v.quality)renderQuality(snapshot,v)}if(!v.canStart&&!snapshot?.intent?.inference_enabled)$('imsInference').disabled=true;if(!v.vr||!v.output)$('imsMotion').disabled=!snapshot?.intent?.control?.enabled||pending}
 async function operate(task,message){if(pending)return;pending=true;actionEpoch++;if(snapshot)renderOutput({...snapshot,intent:{...snapshot.intent,fresh:false}},C.view(null));text('imsActionFeedback',message);try{await task();text('imsActionFeedback','请求已接收，等待新鲜状态与执行回执。')}catch(e){text('imsActionFeedback',errorText(e)+'；请核对当前状态。');throw e}finally{pending=false;lastIdle=0;poll()}}
 async function stop(){return operate(()=>command('/api/intent/stop'),'正在请求停止推理与模型动作…')}
 async function fullscreen(){try{if(!document.fullscreenElement)await root.requestFullscreen();}catch(e){text('imsActionFeedback','浏览器未允许全屏，已使用铺满窗口布局。可点击“全屏”重试。')}}
 async function enter(){if(!snapshot?.entry_eligible||!current().current||pending){lastIdle=0;poll();return}active=true;layout.reset();applyLayout(false);previousActiveElement=document.activeElement;root.hidden=false;document.body.classList.add('immersive-active');buffer.clear();history=[];frameKey='';frameAt=-Infinity;window.EEGIntent?.inputChanged();fullscreen();render(snapshot,at);$('imsStop').focus();pollSignal()}
 async function exit(){if(!active)return;try{await stop()}catch{return}active=false;root.hidden=true;document.body.classList.remove('immersive-active');if(document.fullscreenElement)await document.exitFullscreen().catch(()=>{});window.EEGIntent?.inputChanged();previousActiveElement?.focus?.();lastIdle=0;poll()}
 function applyLayout(animate=true){
  const before=panels.map(p=>p.getBoundingClientRect());if(layout.focus)$('imsGrid').dataset.focused=layout.focus;else delete $('imsGrid').dataset.focused;
  const areas=matchMedia('(max-width: 700px)').matches?(layout.focus?[layout.focus,...C.panels.filter(p=>p!==layout.focus)]:C.panels).map(p=>[p]):layout.areas();
  $('imsGrid').style.gridTemplateAreas=areas.map(row=>'"'+row.join(' ')+'"').join(' ');
  panels.forEach(p=>{p.classList.toggle('ims-focused',p.dataset.panel===layout.focus);p.querySelector('.ims-head').setAttribute('aria-expanded',String(p.dataset.panel===layout.focus))});
  layoutAnimations.forEach(a=>a.cancel());layoutAnimations=[];
  if(animate&&!matchMedia('(prefers-reduced-motion: reduce)').matches)panels.forEach((p,i)=>{const after=p.getBoundingClientRect(),b=before[i];if(after.width&&after.height&&p.animate){p.style.zIndex=p.dataset.panel===layout.focus?'2':'1';const animation=p.animate([{transform:`translate(${b.x-after.x}px,${b.y-after.y}px) scale(${b.width/after.width},${b.height/after.height})`,transformOrigin:'top left'},{transform:'none',transformOrigin:'top left'}],{duration:460,easing:'cubic-bezier(.22,1,.36,1)'});layoutAnimations.push(animation)}});

  if(lastFocusedPanel!==layout.focus){allWaves=layout.focus==='signal';lastFocusedPanel=layout.focus}scheduleDraw();
 }
 panels.forEach(p=>{p.addEventListener('click',e=>{if(e.target.closest('button,select,input,a,canvas,[data-no-focus]'))return;layout.select(p.dataset.panel);applyLayout()});p.querySelector('.ims-head').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();layout.select(p.dataset.panel);applyLayout()}})});
 $('imsTheme').onclick=()=>applyTheme(theme==='dark'?'light':'dark',true);
 $('imsEnter').onclick=enter;$('imsExit').onclick=exit;$('imsStop').onclick=()=>stop().catch(()=>{});$('imsFullscreen').onclick=fullscreen;$('imsGridReset').onclick=()=>{layout.reset();applyLayout()};
 $('imsInference').onclick=()=>snapshot?.intent?.inference_enabled?stop().catch(()=>{}):operate(()=>command('/api/intent/start',{immersive:true,receiver_id:selectedReceiver}),'正在启动实验性推理…').catch(()=>{});
 $('imsMotion').onclick=()=>operate(()=>command('/api/intent/control',{immersive:true,receiver_id:selectedReceiver,enabled:!snapshot?.intent?.control?.enabled}),'正在设置模型动作…').catch(()=>{});
 $('imsModels').onchange=()=>{if(snapshot)render(snapshot,at)};
 $('imsLoadModel').onclick=()=>operate(()=>request('/api/models/activate',{id:$('imsModels').value,require_stopped:true}),'正在加载模型，输入状态将重新建立…').then(()=>{history=[];buffer.clear()}).catch(()=>{});
 $('imsReceiver').onchange=async()=>{const chosen=$('imsReceiver').value,previous=selectedReceiver;try{if(snapshot?.intent?.control?.enabled)await command('/api/intent/control',{enabled:false});selectedReceiver=chosen;cameraSequence++;frameAt=-Infinity;frameLoading=false;frameKey='';$('imsCamera').hidden=true;$('imsCamera').removeAttribute('src');vrCache.reset();if(snapshot){snapshot={...snapshot,vr:{...snapshot.vr,target:null,mirror:{mirror_fresh:false}}};renderVr(snapshot,current())}actionEpoch++;poll()}catch(e){$('imsReceiver').value=previous;text('imsActionFeedback','目标未切换：'+errorText(e))}};
 function chooseView(camera){cameraView=camera;$('imsViewCamera').setAttribute('aria-pressed',String(camera));$('imsViewMonitor').setAttribute('aria-pressed',String(!camera));if(snapshot)renderVr(snapshot,current())}
 $('imsViewCamera').onclick=()=>chooseView(true);$('imsViewMonitor').onclick=()=>chooseView(false);
 $('imsWavePrev').onclick=()=>{allWaves=false;page=Math.max(0,page-1);scheduleDraw()};$('imsWaveNext').onclick=()=>{allWaves=false;page=Math.min(Math.max(0,Math.ceil(channels.length/8)-1),page+1);scheduleDraw()};$('imsWaveAll').onclick=()=>{allWaves=!allWaves;scheduleDraw()};
 document.addEventListener('keydown',e=>{if(active&&e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();stop().catch(()=>{});if(document.fullscreenElement)document.exitFullscreen().catch(()=>{})}},true);
 document.addEventListener('fullscreenchange',()=>{if(active&&!document.fullscreenElement)stop().catch(()=>{})});
 window.addEventListener('pagehide',()=>{if(active)fetch('/api/intent/stop',{method:'POST',headers:{'Content-Type':'application/json','X-EEGHub':'1'},body:'{}',keepalive:true}).catch(()=>{})});
 window.addEventListener('resize',()=>{if(active)applyLayout(false);scheduleDraw()});if(typeof ResizeObserver!=='undefined')new ResizeObserver(scheduleDraw).observe($('imsGrid'));
 window.EEGImmersive={get active(){return active},getState:()=>snapshot,exit};
 setInterval(poll,200);setInterval(pollSignal,200);setInterval(expire,100);setInterval(()=>text('imsClock',new Date().toLocaleTimeString('zh-CN',{hour12:false})),1000);
 let frameLast=0;function drawFrame(now){if(active&&now-frameLast>=32){frameLast=now;if(current().source&&now-lastWavePaint>45){lastWavePaint=now;scheduleDraw()}if(!cameraView){const v=current();scene.render(v.peer,now,v.vr)}}requestAnimationFrame(drawFrame)}requestAnimationFrame(drawFrame);poll();
})();
