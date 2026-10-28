(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.EEGUI=api})(typeof globalThis!=='undefined'?globalThis:this,function(){
 'use strict';
 const stageLabels={acquisition:'数据采集',processing:'本机预处理',ssh:'加密连接',gpu:'GPU 处理',model:'意图模型',unity_install:'Unity 工具链',unity_build:'程序构建',unity_runtime:'Unity 运行',execution:'最终执行',training_software:'训练软件',training_data:'个人数据',montage:'设备线路'};
 const statusLabels={ok:'已通过',warn:'待确认',blocked:'未满足',offline:'离线',unknown:'待检查',working:'检查中'};
 const number=n=>typeof n==='number'&&Number.isFinite(n);
 function signalView(snapshot,page=0,size=8){
  const s=snapshot||{},newDevice=!!s.gnautilus||s.source?.device==='g.Nautilus';
  const descriptor=s.gnautilus?.descriptor,identity=descriptor?.channels||s.quality?.channels||[];
  const channels=identity.map((c,index)=>({...c,index,name:c.name||s.processing?.channel_names?.[index]||'通道 '+(index+1)})).filter(c=>!newDevice||c.kind==='EEG');
  const pages=Math.max(1,Math.ceil(channels.length/size)),current=Math.max(0,Math.min(pages-1,Number.isInteger(page)?page:0));
  return {newDevice,channels,count:channels.length,page:current,pages,visible:channels.slice(current*size,(current+1)*size),configuredRate:s.source?.configured_sfreq??s.source?.nominal_sfreq??descriptor?.sampling_rate??null};
 }
  function drawSignal(canvas,snapshot,page=0,mode='raw',options={}){
  if(!canvas)return;const view=signalView(snapshot,page),box=canvas.getBoundingClientRect(),w=box.width,h=box.height,dpr=typeof window!=='undefined'?window.devicePixelRatio||1:1;
   if(!w||!h)return;if(canvas.width!==Math.round(w*dpr))canvas.width=Math.round(w*dpr);if(canvas.height!==Math.round(h*dpr))canvas.height=Math.round(h*dpr);const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);c.fillStyle=options.dark?'#0c1623':'#fff';c.fillRect(0,0,w,h);
  const left=58,right=78,top=10,bottom=25,row=(h-top-bottom)/Math.max(1,view.visible.length),seconds=snapshot?.waveforms?.seconds||3,fs=snapshot?.waveforms?.display_sfreq,rows=snapshot?.source?.fresh||options.retainHistory?snapshot?.waveforms?.[mode]||[]:[];
  c.font='11px Consolas,monospace';c.textAlign='left';
  view.visible.forEach((channel,i)=>{const y=top+row*(i+.5);c.fillStyle=options.dark?'#9fb2c6':'#52616e';c.fillText(channel.name,8,y+3);c.strokeStyle=options.dark?'#213043':'#edf2f3';c.beginPath();c.moveTo(left,y);c.lineTo(w-right,y);c.stroke();
   const values=rows.map(r=>r[channel.index]);if(!values.length||!values.every(Number.isFinite))return;
   const sorted=[...values].sort((a,b)=>a-b),mid=sorted[Math.floor(sorted.length/2)];let peak=1e-6;for(const value of values)peak=Math.max(peak,Math.abs(value-mid));const scale=row*.36/peak;
   c.strokeStyle=options.dark?'#5bd4c2':'#298f88';c.save();c.beginPath();c.rect(left,top,w-left-right,h-top-bottom);c.clip();c.beginPath();values.forEach((value,j)=>{const offset=Number.isFinite(fs)&&fs>0?(values.length-1-j)/(fs*seconds):(values.length-1-j)/Math.max(1,values.length-1),x=left+(w-left-right)*(1-offset-(options.scrollOffsetSeconds||0)/seconds),yy=y-(value-mid)*scale;j?c.lineTo(x,yy):c.moveTo(x,yy)});c.stroke();c.restore();c.fillStyle=options.dark?'#9fb2c6':'#52616e';c.fillText('±'+peak.toFixed(1)+' '+(channel.unit||'单位未知'),w-right+5,y+3);
  });
  c.fillStyle='#627b73';c.textAlign='center';for(let i=0;i<=3;i++)c.fillText(i===3?(snapshot?.source?.fresh?'现在':options.retainHistory?'上次样本':'现在'):(-seconds+seconds*i/3).toFixed(0)+' s',left+(w-left-right)*i/3,h-5);
  if(!rows.length)c.fillText(view.count?'等待新鲜数据流…':'等待实际通道信息…',w/2,h/2);c.textAlign='left';
 }
 function receiverState(clients,elapsed=0){
  const candidates=(Array.isArray(clients)?clients:[]).map(peer=>{
   const age=number(peer.telemetry_age_ms)&&peer.telemetry_age_ms>=0?peer.telemetry_age_ms+Math.max(0,elapsed):Infinity;
   const t=peer.telemetry||{};
   return {peer,age,fresh:peer.online===true&&age<300&&number(t.actual_speed)&&number(t.actual_yaw_rate)&&['STOP','MOVING'].includes(t.state)&&['LIVE','TEST'].includes(t.mode)};
  }).sort((a,b)=>Number(b.fresh)-Number(a.fresh)||Number(b.peer.role==='unity')-Number(a.peer.role==='unity')||a.age-b.age);
  const chosen=candidates[0],peer=chosen?.peer||null,t=peer?.telemetry||{},fresh=!!chosen?.fresh;
  return {peer,telemetry:t,fresh,age:chosen?.age??null,label:peer?(peer.role==='unity'?'Unity':peer.role==='csharp_harness'?'C# 联调':String(peer.role||'未知接收器')):'暂无接收器',speed:fresh?t.actual_speed:null,yawRate:fresh?t.actual_yaw_rate:null,modeLabel:fresh?(t.mode==='TEST'?'TEST · 合成指令':'LIVE'):'回执未知',motion:!fresh?'执行未知':Math.abs(t.actual_speed)>.005?'正在移动':Math.abs(t.actual_yaw_rate)>.5?'正在转向':'实际静止'};
 }
 function testAvailability(snapshot,elapsed=0,pending=false,blockedGeneration=null){
  const receiver=receiverState(snapshot?.vr?.clients,elapsed);
  let reason='已允许 TEST，每次动作 2 秒，到期停止。';
  if(!snapshot||elapsed>=300)reason='等待中枢的新鲜状态。';
  else if(!Number.isSafeInteger(snapshot.vr?.control_generation)||snapshot.vr.control_generation<0)reason='等待中枢提供可核验的控制版本。';
  else if(snapshot.vr.control_generation===blockedGeneration)reason='停止已请求，等待中枢的新控制版本。';
  else if(!receiver.fresh)reason='等待新鲜的接收器执行回执。';
  else if(!receiver.peer.allow_test)reason='请先在接收器中明确允许 TEST。';
  else if(!snapshot.vr.ready)reason='指令传输尚未就绪，请检查链路。';
  else if(snapshot.source?.fresh!==true)reason='请先恢复新鲜采集数据，再运行链路测试。';
  else if(!snapshot.server?.worker_connected)reason='请先连接 GPU 处理服务。';
  else if(pending)reason='动作请求正在处理，请等待回执；随时可以停止。';
  else return {enabled:true,reason,receiver};
  return {enabled:false,reason,receiver};
 }
 function createRequestGate(){const pending=new Set();return {has:key=>pending.has(key),async run(key,fn){if(pending.has(key))return;pending.add(key);try{return await fn()}finally{pending.delete(key)}}}}
 function preparationStages(data){const rows=new Map((Array.isArray(data?.stages)?data.stages:[]).map(s=>[s.id,s]));return [...Object.keys(stageLabels),...[...rows.keys()].filter(k=>!stageLabels[k])].map(id=>{const row=rows.get(id);return {...row,id,label:row?.label||stageLabels[id]||id,status:statusLabels[row?.status]?row.status:'unknown',summary:row?.summary||'暂无可核验证据',source:row?.source||'尚无来源',checked_at:row?.checked_at||null,age_ms:number(row?.age_ms)?row.age_ms:null}})}
 function ageText(ms){return !number(ms)?'时间未知':ms<1000?Math.round(ms)+' ms':ms<60000?(ms/1000).toFixed(1)+' 秒':ms<3600000?Math.floor(ms/60000)+' 分钟':Math.floor(ms/3600000)+' 小时'}
 function createPreparationView(element){
  if(!element)return {refresh:async()=>{}};
  const make=(tag,className,text)=>{const el=document.createElement(tag);el.className=className||'';if(text!==undefined)el.textContent=text;return el};
  element.className='preparation panel';element.id='preparation';
  const head=make('div','preparation-heading'),intro=make('div'),eyebrow=make('div','eyebrow','SYSTEM READINESS'),title=make('h2','','准备与系统状态'),note=make('p','preparation-note','正在读取证据；采集、软件准备与正式训练分别判断。');intro.append(eyebrow,title,note);
  const button=make('button','button','重新检查');button.type='button';head.append(intro,button);
  const summary=make('div','readiness-summary'),grid=make('div','readiness-grid'),blockers=make('div','blockers'),error=make('p','control-feedback');grid.tabIndex=0;grid.setAttribute('role','region');grid.setAttribute('aria-label','系统检查明细，可滚动查看');blockers.tabIndex=0;blockers.setAttribute('role','region');blockers.setAttribute('aria-label','待处理条件，可滚动查看');error.setAttribute('role','status');element.append(head,summary,grid,blockers,error);
  let busy=false,lastAt=0,lastData=null;const maximumAge=12000;
  function render(data,unavailable=!data){
   summary.replaceChildren();[['软件准备',data?.software_ready],['正式训练',data?.formal_training_ready],['LIVE 控制',data?.live_control_enabled]].forEach(([label,ready])=>{const el=make('div','readiness-summary-item');el.append(make('span','',label),make('strong',ready===true?'status-ok':'status-blocked',unavailable?'状态未知':ready===true?'已满足':'未放行'));summary.append(el)});
   grid.replaceChildren();for(const row of preparationStages(unavailable?null:data)){const el=make('article','readiness-stage');el.dataset.stage=row.id;const line=make('div','readiness-title');line.append(make('strong','',row.label),make('span','stage-badge status-'+row.status,statusLabels[row.status]));el.append(line,make('p','',row.summary));const checked=row.checked_at?new Date(row.checked_at):null;const time=checked&&!Number.isNaN(checked.getTime())?checked.toLocaleString('zh-CN',{hour12:false}):'时间未知';el.append(make('small','',row.source+' · '+ageText(row.age_ms===null?null:row.age_ms+Math.max(0,performance.now()-lastAt))),make('small','evidence-time','检测 '+time));grid.append(el)}
   blockers.replaceChildren();for(const item of (unavailable?[]:data?.blockers||[])){const row=make('div','blocker');row.append(make('strong','',item.label||item.id),make('span','',item.reason||''),make('p','','下一步：'+(item.next_action||'重新检查相关证据。')));blockers.append(row)}
  }
  async function refresh(){if(busy)return;busy=true;button.disabled=true;button.textContent='检查中…';error.textContent='';const requestedAt=performance.now();try{const r=await fetch('/api/preparation',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!r.ok)throw Error('HTTP '+r.status);const data=await r.json();if(data.version!==1||!Array.isArray(data.stages))throw Error('接口格式不完整');lastAt=requestedAt;lastData=data;render(data);note.textContent='来源与检测时间随各阶段列出；构建记录不代表当前运行验收。'}catch(e){lastData=null;render(null,true);note.textContent='准备接口暂不可用，实时采集与会话功能仍可使用。';error.textContent='无法读取系统准备状态。请稍后点击“重新检查”。'}finally{busy=false;button.disabled=false;button.textContent='重新检查'}}
  button.onclick=refresh;render(null);refresh();const ageTimer=setInterval(()=>{if(!lastData)return;if(performance.now()-lastAt>maximumAge){lastData=null;render(null,true);note.textContent='准备状态已超过刷新期限，当前条件需要重新检查。';error.textContent='状态已过期。请点击“重新检查”，或等待中枢恢复。'}else render(lastData)},1000);const timer=setInterval(()=>{if(!document.hidden)refresh()},10000);return {refresh,destroy:()=>{clearInterval(timer);clearInterval(ageTimer)}};
 }
 return {receiverState,testAvailability,createRequestGate,preparationStages,ageText,createPreparationView,signalView,drawSignal};
});

/* EEGMovement research workspace. Presentation logic only; no control POSTs. */
(function(root,factory){
 const studio=factory();
 if(typeof module==='object'&&module.exports)module.exports.studio=studio;
 else {root.EEGStudio=studio;if(typeof document!=='undefined'&&document.querySelector&&typeof MutationObserver!=='undefined')document.addEventListener('DOMContentLoaded',()=>studio.mount());}
})(typeof window!=='undefined'?window:this,function(){
 'use strict';
 function architectureStates(s,intent,elapsed){
  const unknown=()=>Array.from({length:6},()=>({tone:'unknown',label:'状态未知'}));
  if(!s||!Number.isFinite(elapsed)||elapsed>1000||elapsed<0)return unknown();
  const src=s.source||{},server=s.server||{},control=intent?.control||{};
  const sourceFresh=src.fresh===true&&Number.isFinite(src.age_ms)&&src.age_ms>=0&&src.age_ms+elapsed<250;
  const modelFresh=intent?.fresh===true&&Number.isFinite(intent.age_ms)&&intent.age_ms>=0&&intent.age_ms+elapsed<500;
  const peer=(s.vr?.clients||[]).find(c=>c.online===true&&c.telemetry&&Number.isFinite(c.telemetry_age_ms)&&c.telemetry_age_ms>=0&&c.telemetry_age_ms+elapsed<250&&Number.isFinite(c.telemetry.actual_speed)&&Number.isFinite(c.telemetry.actual_yaw_rate)&&['STOP','MOVING'].includes(c.telemetry.state)&&['LIVE','TEST'].includes(c.telemetry.mode));
  if(s.gnautilus||src.device==='g.Nautilus')return [
   {tone:sourceFresh?'ready':'waiting',label:sourceFresh?'g.Nautilus 数据接收中':src.mode==='paused'?'本端接收已暂停':'等待 g.Nautilus 数据'},
   {tone:'waiting',label:s.quality?.contact_quality_known===true?'接触质量以实际核验为准':'接触质量未测量'},
   {tone:server.ssh_connected&&server.worker_connected?'ready':'waiting',label:server.ssh_connected&&server.worker_connected?'私有A100服务已连接':'等待私有计算服务'},
   {tone:modelFresh?'ready':'waiting',label:modelFresh?'新设备模型实时返回':intent?.inference_enabled?'等待有效模型输入':'模型推理未启动'},
   {tone:control.enabled&&modelFresh?'ready':'waiting',label:control.enabled&&modelFresh?'模型动作已允许':'动作关闭 · STOP'},
   {tone:peer?'ready':'unknown',label:peer?'接收端回执新鲜':'无新鲜执行回执'}
  ];
  return [
   {tone:sourceFresh?'ready':'waiting',label:src.mode==='live'?(sourceFresh?'设备数据接收中':'等待设备数据'):src.mode==='paused'?'采集已暂停':'记录回放 · 非当前设备'},
   {tone:sourceFresh&&s.quality?.valid?'ready':'waiting',label:sourceFresh&&s.quality?.valid?'软件质检通过':'等待合格通道'},
   {tone:server.ssh_connected&&server.worker_connected?'ready':'waiting',label:server.ssh_connected&&server.worker_connected?'计算服务已连接':'等待计算服务'},
   {tone:modelFresh?'ready':'waiting',label:intent?.mode==='TRAINING_TEST'?'训练数据测试':intent?.mode==='PERSONAL_REPLAY'?'个人记录回放':modelFresh?'模型返回新鲜结果':'等待模型结果'},
   {tone:control.enabled&&modelFresh&&control.decision?.model_ready===true?'ready':'waiting',label:!control.enabled?'模型动作未启用':!modelFresh||!control.decision?.model_ready?'已启用 · 等待动作条件':control.decision.active?'门控已确认 · 动作已接纳':'门控已确认 · 保持静止'},
   {tone:peer?'ready':'unknown',label:peer?(peer.telemetry.mode==='TEST'?'合成指令回执':peer.telemetry.mode==='TRAINING_TEST'?'训练测试回执':'接收端回执新鲜'):'无新鲜执行回执'}
  ];
 }
 const icons={
  signal:'<path d="M2 12h4l3-8 5 16 3-8h5"/>',
  lab:'<path d="M9 3h6m-5 0v6l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M8 14h8"/>',
  layers:'<path d="m12 3 10 6-10 6L2 9Zm-9 11 9 5 9-5M3 18l9 5 9-5"/>',
  data:'<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v7c0 4 18 4 18 0V5M3 12v7c0 4 18 4 18 0v-7"/>',
  train:'<path d="M3 3v18h18M7 16l4-6 4 3 6-8"/>',
  vr:'<rect x="2" y="6" width="20" height="12" rx="4"/><path d="m8 18 4-4 4 4M6 10h3m6 0h3"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  capture:'<path d="M4 8V4h4m8 0h4v4m0 8v4h-4M8 20H4v-4"/><circle cx="12" cy="12" r="4"/>',
  settings:'<circle cx="12" cy="12" r="4"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  cpu:'<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/>'
 };
 function icon(name){return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(icons[name]||icons.layers)+'</svg>';}
 function mount(){
  const body=document.body,main=document.querySelector('main');if(!main||body.classList.contains('preview-page')||main.__studioMounted)return;main.__studioMounted=true;
  const path=location.pathname,arch=path==='/'&&new URLSearchParams(location.search).get('view')==='architecture';
  body.classList.add('studio');if(arch)body.classList.add('architecture-view');
  const page=path==='/lab'?'lab':path==='/training'?'training':path==='/downloads'?'downloads':path==='/annotations'?'annotations':arch?'architecture':'overview';
  body.dataset.page=page;
  const routes=[['overview','/','signal','实时中枢','SIGNAL MONITOR'],['lab','/lab','lab','实时推理','INFERENCE LAB'],['architecture','/?view=architecture','layers','系统架构','SYSTEM TOPOLOGY'],['training','/training','train','模型训练','MODEL TRAINING'],['downloads','/downloads','data','数据资源','DATA LIBRARY'],['annotations','/annotations','capture','眼动人工打标','OCULAR ANNOTATION']];
  const oldRail=document.querySelector('#studioRail,.rail');
  const rail=oldRail||document.createElement('aside');rail.className='studio-rail';rail.id='studioRail';
  const brand='<a class="studio-brand" href="/" aria-label="EEGMovement首页"><span class="studio-brand-mark">'+icon('signal')+'</span><span>EEG<span class="studio-brand-light">Movement</span><small>NEURAL INTERFACE LAB</small></span></a>';
  // Preserve the existing settings button and live hub indicator bindings.
  const settings=document.getElementById('settings'),hubDot=document.getElementById('hubDot'),address=document.getElementById('localAddress');
  rail.innerHTML=brand+'<button type="button" class="studio-rail-close" aria-label="收起工作空间导航">×</button><div class="studio-rail-label">研究工作空间 <span>01 — 06</span></div><nav class="studio-primary" aria-label="研究工作空间">'+routes.map(([key,url,ico,label,en],i)=>'<a class="studio-link '+(page===key?'active':'')+'" href="'+url+'" '+(page===key?'aria-current="page"':'')+'>'+icon(ico)+'<span>'+label+'<small>'+en+'</small></span><em>0'+(i+1)+'</em></a>').join('')+'</nav><div class="studio-rail-label studio-rail-tools-label">实验工具</div><nav class="studio-tools" aria-label="实验工具"><a class="studio-link" href="/#desktopCapture">'+icon('capture')+'<span>自主采集</span></a><a class="studio-link" href="/vr-preview">'+icon('vr')+'<span>VR 实时画面</span></a><a class="studio-link" href="/#preparation">'+icon('check')+'<span>准备与系统状态</span></a></nav><div class="studio-rail-bottom"><div class="rail-runtime"></div><div class="rail-project"><b>EEG → INTENT → VR</b><span>本地采集 · 远端计算 · 闭环反馈</span></div></div>';
  if(settings){settings.className='studio-link';settings.innerHTML=icon('settings')+'<span>连接信息</span>';rail.querySelector('.studio-tools').append(settings);}
  const runtime=rail.querySelector('.rail-runtime');if(hubDot)runtime.append(hubDot);runtime.append(document.createTextNode('本地研究工作台'));if(address)runtime.append(address);
  if(!oldRail)body.prepend(rail);
  let nav=main.querySelector(':scope > nav');if(!nav){nav=document.createElement('nav');main.prepend(nav);}
  nav.className='workspace-nav';nav.setAttribute('aria-label','页面导航');
  const stop=document.getElementById('globalStop');
  nav.innerHTML='<button type="button" class="studio-menu" aria-controls="studioRail" aria-expanded="false" aria-label="展开工作空间导航">'+icon('menu')+'</button><div class="workspace-crumb"><span>工作空间</span><i>/</i><strong>'+routes.find(r=>r[0]===page)[3]+'</strong></div><div class="workspace-shortcuts"><a href="/">中枢</a><a href="/lab">推理</a><a href="/training">训练</a></div>';
  if(stop)nav.append(stop);else{const status=document.createElement('span');status.className='workspace-readonly';status.textContent='研究工作台';nav.append(status);}
  const menu=nav.querySelector('.studio-menu');
  const narrow=matchMedia('(max-width:900px)'),railClose=rail.querySelector('.studio-rail-close');
  const setMenu=open=>{body.classList.toggle('menu-open',open);menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'收起工作空间导航':'展开工作空间导航');rail.inert=narrow.matches&&!open;if(open)railClose.focus();};
  const syncMenu=()=>{if(!narrow.matches)setMenu(false);else rail.inert=!body.classList.contains('menu-open');};syncMenu();narrow.addEventListener?.('change',syncMenu);
  railClose.addEventListener('click',()=>{setMenu(false);menu.focus();});
  rail.addEventListener('keydown',e=>{if(e.key!=='Tab'||!narrow.matches||!body.classList.contains('menu-open'))return;const controls=[...rail.querySelectorAll('a,button')],first=controls[0],last=controls[controls.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}});
  const scrim=document.querySelector('.studio-scrim')||document.createElement('button');scrim.className='studio-scrim';scrim.type='button';scrim.setAttribute('aria-label','关闭导航');if(!scrim.isConnected)body.append(scrim);menu.addEventListener('click',()=>setMenu(!body.classList.contains('menu-open')));scrim.addEventListener('click',()=>{setMenu(false);menu.focus()});
  rail.addEventListener('click',e=>{if(e.target.closest('a'))setMenu(false);if(narrow.matches&&e.target.closest('#settings')){setMenu(false);menu.focus();}});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&body.classList.contains('menu-open')){e.preventDefault();e.stopImmediatePropagation();setMenu(false);menu.focus();}},{capture:true});
  const header=main.querySelector(':scope > header');
  if(header){
   const copy=header.firstElementChild;if(copy?.tagName==='P'){const wrap=document.createElement('div');while(header.firstChild)wrap.append(header.firstChild);header.append(wrap);}
   header.classList.add('studio-hero');
   if(arch){const title=header.querySelector('h1'),tag=title.querySelector('#liveTag');title.textContent='从脑电，到眼前的行动';if(tag){tag.hidden=true;title.append(tag);}header.querySelector('.eyebrow').textContent='EEGMOVEMENT / SYSTEM ARCHITECTURE';header.querySelector('p').textContent='一条可追踪的信号链路。点击组件，查看它的职责、输入与输出。';}
   const art=document.createElement('div');art.className='hero-art';art.setAttribute('aria-hidden','true');
   art.innerHTML='<svg viewBox="0 0 400 200"><defs><pattern id="heroGrid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0v24" fill="none" stroke="currentColor" stroke-width=".4"/></pattern></defs><rect width="400" height="200" fill="url(#heroGrid)"/><circle cx="270" cy="100" r="74"/><circle cx="270" cy="100" r="52"/><circle cx="270" cy="100" r="27"/><path class="hero-signal" d="M0 100h70l10-12 12 27 18-65 22 110 18-60h28l12-18 12 35 17-47 20 51 13-21h148"/><circle class="hero-node" cx="270" cy="100" r="5"/><path d="M270 16v14m0 140v14M185 100h14m142 0h14"/></svg><span>NEURAL SIGNAL / CLOSED LOOP</span>';
   if(!header.querySelector('.hero-art'))header.append(art);
  }
  if(page==='overview'||page==='architecture'){
   const existingTabs=main.querySelector('.studio-view-tabs'),tabs=existingTabs||document.createElement('nav');tabs.className='studio-view-tabs';tabs.setAttribute('aria-label','中枢视图');
   tabs.innerHTML='<a href="/" '+(!arch?'aria-current="page"':'')+'>'+icon('signal')+'实时总览</a><a href="/?view=architecture" '+(arch?'aria-current="page"':'')+'>'+icon('layers')+'系统架构</a><span>ACQUIRE · DECODE · FEEDBACK</span>';
   if(!existingTabs)header.after(tabs);
  }
  if(page==='lab'&&!body.classList.contains('inference-dashboard')){
   const existingChapters=main.querySelector('.studio-chapters'),chapters=existingChapters||document.createElement('nav');chapters.className='studio-chapters';chapters.setAttribute('aria-label','推理页面章节');
   const signal=document.querySelector('.lab-signal'),execution=document.querySelector('.scene-panel');if(signal)signal.id='inputSignal';if(execution)execution.id='executionScene';
   chapters.innerHTML='<a href="#liveInference">01 模型推理</a><a href="#inputSignal">02 输入信号</a><a href="#executionScene">03 执行验证</a><a href="#preparation">04 准备检查</a>';if(!existingChapters)header.after(chapters);
  }
  if(page==='training'){
   const history=document.createElement('details');history.className='studio-history';history.innerHTML='<summary><span>历史训练与资源核验</span><small>展开查看历史结果、底模与容量检查</small></summary>';
   const start=[...main.children].find(el=>el.tagName==='H2'&&el.textContent.includes('此前电脑端'));
   if(start){main.insertBefore(history,start);let node=start;while(node&&node.tagName!=='FOOTER'&&node.id!=='error'){const next=node.nextSibling;history.append(node);node=next;}}
  }
  if(arch)mountArchitecture(main,header);
  const verification=document.getElementById('verificationList');if(verification){verification.tabIndex=0;verification.setAttribute('role','region');verification.setAttribute('aria-label','软件验证项目，可滚动查看');}
  installMotion();
 }
 const nodes=[
  {id:'acquire',name:'脑电采集',en:'SIGNAL ACQUISITION',icon:'signal',caption:'实际通道 · 配置采样率',tag:'设备输入',description:'从原厂正在运行的共享会话接收 g.Nautilus 数据，独立采集端主动连接中枢。通道、单位和配置采样率以实际描述符为准。',input:'原厂共享会话 / 独立采集端',output:'原始通道值、配置身份与样本位置',detail:'原厂负责设备配置和采集启停；独立端只控制自身 streaming。通道位置、参考与接触质量需要实际核验。',href:'/',action:'查看信号总览'},
  {id:'process',name:'信号预处理',en:'CAUSAL PREPROCESSING',icon:'layers',caption:'原厂配置 · 质量待测',tag:'本机处理',description:'中枢保留原厂输出，不重复套用旧设备滤波或质量规则。设备有效性、数据有限值与接触质量分别记录。',input:'实际通道值与设备配置',output:'原始序列、样本位置与观测状态',detail:'接触质量尚未测量时保持未知；软件观测或网络连通不能替代原厂质量检查和实机验收。',href:'/lab#inputSignal',action:'检查输入信号'},
  {id:'compute',name:'远端计算',en:'REMOTE COMPUTE',icon:'cpu',caption:'私有 A100 · SSH 隧道',tag:'计算基础设施',description:'数据、训练和分类器推理运行在 A100二号机。只有本机中枢通过SSH隧道访问；公网转发不代理内网计算服务。',input:'受保护通道传输的处理窗口',output:'服务健康、GPU 资源与计算结果',detail:'计算服务连接成功只表示基础设施可用。意图模型是否正在返回结果，需查看下一个组件。',href:'/#top',action:'查看计算节点'},
  {id:'decode',name:'意图模型推理',en:'INTENT REPRESENTATION',icon:'lab',caption:'统一分类器 · 手动启动',tag:'模型推理',description:'频带功率、CSP/LDA、眼动、融合和轻量因果网络共享模型库与输入合同。选择分类器后，推理和动作分别手动启动。',input:'通过门控的实时序列 / 已标记测试记录',output:'分类分数、分支来源、质量与样本身份',detail:'后续模型需绑定真实通道、采样率和配置身份，并通过会话分组与独立现场验收；当前不以软件联调代替识别效果。',href:'/lab#liveInference',action:'进入实时推理'},
  {id:'control',name:'动作门控',en:'CONTROL POLICY',icon:'settings',caption:'连续确认 · 有效期',tag:'执行策略',description:'将模型分数经过动静阈值、方向确认、通道质量与新鲜度检查，再生成有有效期的运动命令。',input:'模型分数、输入来源与链路状态',output:'STOP / 方向 / 平滑速度控制命令',detail:'推理启动与动作启用分别控制。停止、断线、旧包或无效输入会触发相应的门控与接收端保护。',href:'/lab#liveInference',action:'查看动作门控'},
  {id:'feedback',name:'Unity / VR 反馈',en:'EMBODIED FEEDBACK',icon:'vr',caption:'执行回执 · 画面回传',tag:'闭环反馈',description:'接收端执行有时效的控制指令，并返回实际速度、朝向和位置。Quest 应用画面独立回传至中枢。',input:'签名控制指令与接收端配对',output:'实际运动遥测 / Quest 单眼应用画面',detail:'显示状态以新鲜的实际执行回执为准。无回执时显示未知；画面回传不等于头显物理显示延迟测量。',href:'/lab#executionScene',action:'查看执行轨迹'}
 ];
 function mountArchitecture(main,header){
  const section=document.createElement('section');section.id='systemArchitecture';section.className='system-architecture';section.setAttribute('aria-labelledby','architectureTitle');
  section.innerHTML='<div class="architecture-heading"><div><div class="eyebrow">SYSTEM MAP / INTERACTIVE</div><h2 id="architectureTitle">让每一步，都看得见</h2><p>选择组件查看数据流与实现边界。状态读取自当前中枢。</p></div><span class="architecture-live" id="architectureStamp">读取实时状态…</span></div><div class="architecture-layout"><div class="architecture-map"><div class="architecture-lane"><span>FORWARD PATH / 信号处理与执行</span><span>01 → 02 → 03 → 04 → 05 → 06</span></div><div class="architecture-nodes" role="group" aria-label="系统组件">'+nodes.map((n,i)=>'<button type="button" class="architecture-node '+(i===0?'is-selected':'')+'" data-node="'+i+'" aria-pressed="'+(i===0)+'" aria-controls="architectureInspector"><span class="node-top"><span class="node-symbol">'+icon(n.icon)+'</span><small>0'+(i+1)+'</small></span><small class="node-en">'+n.en+'</small><strong>'+n.name+'</strong><span class="node-caption">'+n.caption+'</span><span class="node-status" data-tone="unknown">状态未知</span><span class="node-out">'+icon('arrow')+'</span></button>').join('')+'</div><div class="architecture-return">'+icon('arrow')+'执行回执返回中枢 · 实际状态持续核验</div><div class="architecture-legend"><span><i class="ready"></i>就绪 / 新鲜</span><span><i class="waiting"></i>等待条件</span><span><i class="unknown"></i>未知 / 过期</span></div></div><aside class="architecture-inspector" id="architectureInspector" aria-labelledby="inspectorTitle"><span class="inspector-tag"></span><h3 id="inspectorTitle"></h3><p class="inspector-description"></p><dl><dt>输入 INPUT</dt><dd id="inspectorInput"></dd><dt>输出 OUTPUT</dt><dd id="inspectorOutput"></dd></dl><p class="inspector-detail"></p><a class="button primary-button inspector-action"></a></aside></div><div class="architecture-foot"><span>'+icon('layers')+'当前实现架构</span><p>服务就绪、模型返回与接收端执行分别判断。测试输入会明确标记。</p></div>';
  main.querySelector('.studio-view-tabs').after(section);
  const buttons=[...section.querySelectorAll('.architecture-node')];let selected=0;
  const select=i=>{selected=i;buttons.forEach((b,k)=>{b.classList.toggle('is-selected',k===i);b.setAttribute('aria-pressed',String(k===i));});const n=nodes[i];const write=(sel,value)=>section.querySelector(sel).textContent=value;write('.inspector-tag',n.tag);write('#inspectorTitle',n.name);write('.inspector-description',n.description);write('#inspectorInput',n.input);write('#inspectorOutput',n.output);write('.inspector-detail',n.detail);const link=section.querySelector('.inspector-action');link.textContent=n.action+' ↗';link.href=n.href;};
  buttons.forEach((b,i)=>{b.addEventListener('click',()=>select(i));b.addEventListener('keydown',e=>{let next;if(e.key==='ArrowRight'||e.key==='ArrowDown')next=(i+1)%nodes.length;if(e.key==='ArrowLeft'||e.key==='ArrowUp')next=(i+nodes.length-1)%nodes.length;if(e.key==='Home')next=0;if(e.key==='End')next=nodes.length-1;if(next!==undefined){e.preventDefault();select(next);buttons[next].focus();}});});select(0);
  let snapshot=null,intent=null,receivedAt=-Infinity,busy=false;
  const paint=()=>{const elapsed=performance.now()-receivedAt;const states=architectureStates(snapshot,intent,elapsed);if(snapshot?.gnautilus||snapshot?.source?.device==='g.Nautilus'){const view=EEGUI.signalView(snapshot);buttons[0].querySelector('.node-caption').textContent=(view.count?view.count+' 路 EEG':'通道未知')+' · '+(view.configuredRate?view.configuredRate+' Hz 配置':'采样率未知')}states.forEach((state,i)=>{const badge=buttons[i].querySelector('.node-status');badge.dataset.tone=state.tone;badge.textContent=state.label;buttons[i].dataset.tone=state.tone;});section.querySelector('#architectureStamp').textContent=elapsed<=1000?'状态持续更新 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false}):'中枢状态未知 · 等待恢复';};
  async function poll(){if(busy||document.hidden)return;busy=true;const at=performance.now();try{const [a,b]=await Promise.all([fetch('/api/status',{cache:'no-store',signal:AbortSignal.timeout(900)}),fetch('/api/intent/status',{cache:'no-store',signal:AbortSignal.timeout(900)})]);if(!a.ok)throw Error();snapshot=await a.json();intent=b.ok?await b.json():null;receivedAt=at;}catch{snapshot=null;intent=null;}finally{busy=false;paint();}}
  poll();setInterval(poll,200);setInterval(paint,100);
 }
 function installMotion(){
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  // Motion Prompt #085 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#085
  document.addEventListener('pointerdown',e=>{
   if(reduced.matches)return;const b=e.target.closest('button,.button');if(!b||b.disabled||b.matches('.studio-scrim'))return;
   const r=b.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top,R=Math.max(Math.hypot(x,y),Math.hypot(r.width-x,y),Math.hypot(x,r.height-y),Math.hypot(r.width-x,r.height-y));
   const ripple=document.createElement('i');ripple.className='studio-ripple';ripple.setAttribute('aria-hidden','true');Object.assign(ripple.style,{left:x+'px',top:y+'px',width:R*2+'px',height:R*2+'px'});b.append(ripple);ripple.addEventListener('animationend',()=>ripple.remove(),{once:true});setTimeout(()=>ripple.remove(),900);
  },{passive:true});
  // Motion Prompt #057 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#057
  // Motion Prompt #060 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#060
  const groups=new WeakSet();
  function bindGroup(group){if(groups.has(group))return;groups.add(group);group.querySelector(':scope > .studio-selection')?.remove();group.classList.add('studio-sliding-group');const indicator=document.createElement('i');indicator.className='studio-selection';indicator.setAttribute('aria-hidden','true');group.append(indicator);
   const update=()=>{const active=group.querySelector('[aria-selected="true"],.selected,[aria-current="page"]');if(!active)return;indicator.style.width=active.offsetWidth+'px';indicator.style.height=active.offsetHeight+'px';indicator.style.transform='translate('+active.offsetLeft+'px,'+active.offsetTop+'px)';};
   const mo=new MutationObserver(update);mo.observe(group,{subtree:true,attributes:true,attributeFilter:['class','aria-selected','aria-current']});if(typeof ResizeObserver!=='undefined')new ResizeObserver(update).observe(group);requestAnimationFrame(update);
   group.addEventListener('keydown',e=>{if(!group.matches('[role="tablist"]'))return;const tabs=[...group.querySelectorAll('[role="tab"]')],i=tabs.indexOf(document.activeElement);if(i<0)return;let next;if(e.key==='ArrowRight')next=(i+1)%tabs.length;if(e.key==='ArrowLeft')next=(i+tabs.length-1)%tabs.length;if(e.key==='Home')next=0;if(e.key==='End')next=tabs.length-1;if(next!==undefined){e.preventDefault();e.stopPropagation();tabs[next].focus();tabs[next].click();}});
  }
  function scan(){document.querySelectorAll('.segmented,.input-tabs,.studio-view-tabs').forEach(bindGroup);document.querySelectorAll('[role="tab"]').forEach(tab=>{tab.tabIndex=tab.getAttribute('aria-selected')==='true'?0:-1;const panel=document.getElementById(tab.getAttribute('aria-controls'));if(panel&&tab.id){panel.setAttribute('aria-labelledby',tab.id);panel.tabIndex=0;}});}
  scan();let scanFrame=false;const scanObserver=new MutationObserver(()=>{if(scanFrame)return;scanFrame=true;requestAnimationFrame(()=>{scanFrame=false;scan();});});scanObserver.observe(document.querySelector('main'),{subtree:true,childList:true,attributes:true,attributeFilter:['aria-selected']});
  // Motion Prompt #074 · MIT License (c) 2026 Shohei Ohtaki · https://showheyohtaki.github.io/motion-prompt/#074
  if(!reduced.matches&&typeof IntersectionObserver!=='undefined'){
   const observer=new IntersectionObserver(entries=>entries.forEach(e=>{if(!e.isIntersecting)return;e.target.animate([{opacity:0,transform:'translateY(28px)'},{opacity:1,transform:'translateY(0)'}],{duration:700,easing:'cubic-bezier(.215,.61,.355,1)'});observer.unobserve(e.target);}),{rootMargin:'0px 0px -15% 0px'});
   // One entrance per content block; telemetry never restarts the animation.
   document.querySelectorAll('.metric,.pipeline,.chain,.scene-panel,.training-detail,.vr-replay,.summary,.system-architecture').forEach(el=>observer.observe(el));
  }
 }
 return {mount,architectureStates};
});
