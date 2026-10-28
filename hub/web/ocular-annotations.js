(function(){
 'use strict';
 const C=window.OcularAnnotationCore,$=id=>document.getElementById(id),DIR={UP:'向上',DOWN:'向下',LEFT:'向左',RIGHT:'向右'},TASK={FORWARD:'前进',BACKWARD:'后退',LEFT:'左转',RIGHT:'右转',REST:'静止'},ROLE={train:'训练集',validation:'验证集',test:'测试集'},STATE={pending:'待框选',partial:'已标一处',uncertain:'待复核',complete:'已完成'};
 const M={catalog:null,current:null,currentId:null,phases:null,dirty:false,saving:false,generation:0,editRevision:0,drag:null,notice:''};
 const LABEL=66,RIGHT=12,ROW=63,TOP=14,BOTTOM=31;
 function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
 function message(text,error=false){$('oaSaveStatus').textContent=text;$('oaSaveStatus').style.color=error?'#986323':'';}
 async function api(url,options){const r=await fetch(url,options),data=await r.json();if(!r.ok){const e=new Error(data.error||'请求未完成，请重试');e.code=data.code;throw e;}return data;}
 function storageKey(q){return 'eeg-ocular:'+M.catalog.dataset_id+':'+q.trial_id+':'+q.waveform_sha256;}
 function draft(q){try{return JSON.parse(sessionStorage.getItem(storageKey(q))||'null');}catch{return null;}}
 function stash(){if(!M.current||!M.dirty)return;try{sessionStorage.setItem(storageKey(M.current.metadata),JSON.stringify({revision:M.current.annotation.revision,phases:M.phases}));}catch{}}
 function clearDraft(){try{sessionStorage.removeItem(storageKey(M.current.metadata));}catch{}}
 function ready(){return !!M.current&&!M.saving;}
 function list(){
  if(!M.catalog)return;const filter=$('oaFilter').value;let completed=0,uncertain=0;
  $('oaTrialList').innerHTML=M.catalog.trials.map((q,i)=>{const d=draft(q),validDraft=d?.revision===q.annotation.revision,ph=q.trial_id===M.currentId&&M.phases?M.phases:validDraft?d.phases:q.annotation.phases,s=C.reviewState(ph),isDraft=q.trial_id===M.currentId?M.dirty:!!validDraft;
   if(s==='complete'&&!isDraft)completed++;if(s==='uncertain')uncertain++;
   if(filter==='pending'&&s==='complete'&&!isDraft||filter==='complete'&&(s!=='complete'||isDraft))return '';
   return '<button class="oa-trial" type="button" data-trial="'+q.trial_id+'" aria-current="'+(q.trial_id===M.currentId)+'"><strong>'+String(i+1).padStart(2,'0')+' · '+esc(TASK[q.label]||q.label)+'</strong><small>'+esc(ROLE[q.split]||q.split)+' · '+q.duration_s.toFixed(2)+'秒</small><span class="oa-trial-status">'+(isDraft?'未保存草稿':STATE[s])+'</span></button>';
  }).join('');
  if(!$('oaTrialList').children.length)$('oaTrialList').innerHTML='<p class="oa-empty">没有匹配的试次</p>';
  $('oaProgress').textContent='已完成 '+completed+' / '+M.catalog.trials.length+' 段'+(uncertain?' · '+uncertain+' 待复核':'');
  $('oaTrialList').querySelectorAll('[data-trial]').forEach(b=>{b.disabled=M.saving;b.addEventListener('click',()=>load(b.dataset.trial));});
 }
 function windowMarkup(key){const onset=key==='onset';return '<section class="oa-window" data-window="'+key+'"><div class="oa-window-head"><div><h3>'+(onset?'开始 · 前5秒':'结束 · 后5秒')+'</h3><span id="oa-'+key+'-state" class="oa-phase-state"></span></div><label class="oa-direction"><b id="oa-'+key+'-default"></b><select id="oa-'+key+'-direction" aria-label="'+(onset?'开始':'结束')+'眼动方向"><option value="">未指定方向</option>'+Object.entries(DIR).map(([v,n])=>'<option value="'+v+'">'+n+'</option>').join('')+'</select></label></div><canvas id="oa-'+key+'-canvas" class="oa-canvas" tabindex="0" aria-label="首尾波形拖拽框选"></canvas><div class="oa-range"><label>起点（'+(onset?'开始后':'距结束')+'秒）<input type="number" step="0.004" id="oa-'+key+'-start" placeholder="拖拽后可微调"></label><label>终点（'+(onset?'开始后':'距结束')+'秒）<input type="number" step="0.004" id="oa-'+key+'-end" placeholder="拖拽后可微调"></label></div><div class="oa-outcomes">'+[['absent','无眼动'],['uncertain','无法判断'],['pending','清除范围']].map(([v,t])=>'<button type="button" data-phase="'+key+'" data-outcome="'+v+'" aria-pressed="false">'+t+'</button>').join('')+'</div><p id="oa-'+key+'-note" class="oa-phase-note"></p></section>';}
 function changed(){M.editRevision++;M.dirty=true;stash();message('未保存草稿');controls();list();render();}
 function controls(){
  const ok=ready(),index=M.catalog?.trials.findIndex(q=>q.trial_id===M.currentId)??-1;
  $('oaSave').disabled=$('oaSaveNext').disabled=!ok||!M.dirty||!!M.drag;$('oaPrevious').disabled=!ok||index<=0;$('oaNext').disabled=!ok||index>=M.catalog.trials.length-1;
  document.querySelectorAll('.oa-window input,.oa-window select,.oa-outcomes button').forEach(el=>el.disabled=!ok);
 }
 function ranges(){if(!M.current)return;const q=M.current.metadata;
  for(const key of ['onset','offset']){const p=M.phases[key],anchor=key==='onset'?q.task_start:q.task_end,w=M.current.waveform.windows[key];
   $('oa-'+key+'-direction').value=p.direction||'';
   $('oa-'+key+'-default').textContent=(key==='onset'?'默认同向':'默认反向')+'：'+(DIR[q.default_directions[key]]||'静止段不预设');
   $('oa-'+key+'-state').textContent=p.status==='marked'?'已框选 '+((p.sample_end-p.sample_start)/250).toFixed(3)+'秒':({pending:'待框选',absent:'已确认无眼动',uncertain:'无法判断'})[p.status];
   for(const edge of ['start','end']){const el=$('oa-'+key+'-'+edge);el.value=p.status==='marked'?((p['sample_'+edge]-anchor)/250).toFixed(3):'';el.min=((w.start-anchor)/250).toString();el.max=((w.end-anchor)/250).toString();}
   document.querySelectorAll('[data-phase="'+key+'"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.outcome===p.status)));
   const within=p.status!=='marked'||(key==='onset'?p.sample_end<=q.task_start+250:p.sample_start>=q.task_end-250),note=$('oa-'+key+'-note');note.classList.toggle('is-warning',!within);
   note.textContent=!within?'范围超出约定的首／末1秒，请核对后保存。':p.status==='marked'?(DIR[p.direction]||'请选择方向')+' · 可拖拽重新框选或微调端点。':'横向拖拽框出这一次眼动；不自动生成候选。';
  }
 }
 function rows(){const n=$('oaChannels').value==='all'?32:8;let result=M.current.channels.slice(0,n).map((label,i)=>({label,index:i,ref:false}));if($('oaReferences').checked)result.push(...M.current.waveform.reference_names.map((label,i)=>({label,index:i,ref:true})));return result;}
 function plotRect(canvas){const r=canvas.getBoundingClientRect();return {left:r.left+LABEL,width:Math.max(1,r.width-LABEL-RIGHT)};}
 function sampleX(sample,w,width){return LABEL+(sample-w.start)/(w.end-w.start)*(width-LABEL-RIGHT);}
 function draw(key){
  if(!M.current)return;const canvas=$('oa-'+key+'-canvas'),w=M.current.waveform.windows[key],rs=rows(),width=canvas.getBoundingClientRect().width||500,height=TOP+rs.length*ROW+BOTTOM,dpr=Math.min(2,window.devicePixelRatio||1);
  canvas.style.height=height+'px';canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);const ctx=canvas.getContext('2d');if(!ctx)return;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#fff';ctx.fillRect(0,0,width,height);
  const q=M.current.metadata,zone=key==='onset'?[q.task_start,q.task_start+250]:[q.task_end-250,q.task_end],zx=sampleX(zone[0],w,width),zx2=sampleX(zone[1],w,width);
  ctx.fillStyle='#f2f6fc';ctx.fillRect(zx,TOP,zx2-zx,rs.length*ROW);ctx.font='10px system-ui';ctx.textBaseline='middle';ctx.lineWidth=.7;
  const amplitude=Number($('oaAmplitude').value)||250;
  for(let i=0;i<rs.length;i++){const row=rs[i],values=(row.ref?w.references:w.values)[row.index],y=TOP+(i+.5)*ROW;
   ctx.strokeStyle='#e8edf3';ctx.beginPath();ctx.moveTo(LABEL,y);ctx.lineTo(width-RIGHT,y);ctx.stroke();ctx.fillStyle='#405e7a';ctx.textAlign='left';ctx.fillText(row.label.length>12?row.label.slice(0,11)+'…':row.label,6,y-7);ctx.fillStyle='#8292a3';ctx.font='8px system-ui';ctx.fillText(row.ref?'差分参考':'CH'+String(row.index+1).padStart(2,'0'),6,y+8);ctx.font='10px system-ui';
   const center=values.reduce((a,b)=>a+b,0)/values.length;ctx.save();ctx.beginPath();ctx.rect(LABEL,TOP+i*ROW,width-LABEL-RIGHT,ROW);ctx.clip();ctx.strokeStyle=row.ref?'#7a6b50':'#3e6c97';ctx.lineWidth=.8;ctx.beginPath();
   for(let j=0;j<values.length;j++){const x=sampleX(w.start+j,w,width),v=y-(values[j]-center)/amplitude*(ROW*.43);if(j===0)ctx.moveTo(x,v);else ctx.lineTo(x,v);}ctx.stroke();ctx.restore();
  }
  for(let tick=0;tick<=5;tick++){const sample=key==='onset'?q.task_start+tick*250:q.task_end-(5-tick)*250;if(sample<w.start||sample>w.end)continue;const x=sampleX(sample,w,width);ctx.strokeStyle='#e2e9f2';ctx.beginPath();ctx.moveTo(x,TOP);ctx.lineTo(x,height-BOTTOM);ctx.stroke();ctx.fillStyle='#718297';ctx.textAlign='center';ctx.fillText(String(key==='onset'?tick:tick-5),x,height-15);}
  const range=M.drag?.key===key?M.drag.preview:M.phases[key].status==='marked'?M.phases[key]:null;
  if(range){const x=sampleX(range.sample_start,w,width),right=sampleX(range.sample_end,w,width);ctx.fillStyle='#3b79c72b';ctx.fillRect(x,TOP,right-x,rs.length*ROW);ctx.strokeStyle='#3b79c7';ctx.lineWidth=1.2;ctx.strokeRect(x,TOP,right-x,rs.length*ROW);}
  canvas.setAttribute('aria-label',(key==='onset'?'开始后0到5秒':'结束前5秒')+'波形，'+rs.map(r=>r.label).join('、')+'；横向拖拽选择时间范围，也可用下方输入框微调');
 }
 function render(){if(!M.current)return;ranges();draw('onset');draw('offset');}
 function hookWindows(){
  for(const key of ['onset','offset']){const canvas=$('oa-'+key+'-canvas');
   canvas.addEventListener('pointerdown',e=>{if(!ready()||e.button!==0)return;const rect=plotRect(canvas);if(e.clientX<rect.left||e.clientX>rect.left+rect.width)return;e.preventDefault();canvas.setPointerCapture?.(e.pointerId);M.drag={key,pointer:e.pointerId,start:e.clientX,preview:null};controls();});
   canvas.addEventListener('pointermove',e=>{if(M.drag?.key!==key||M.drag.pointer!==e.pointerId)return;M.drag.preview=C.dragRange(M.drag.start,e.clientX,plotRect(canvas),M.current.waveform.windows[key]);draw(key);});
   canvas.addEventListener('pointerup',e=>{if(!ready()||M.drag?.key!==key||M.drag.pointer!==e.pointerId)return;const range=C.dragRange(M.drag.start,e.clientX,plotRect(canvas),M.current.waveform.windows[key]);M.drag=null;canvas.releasePointerCapture?.(e.pointerId);if(range){M.phases[key]={status:'marked',direction:$('oa-'+key+'-direction').value||null,...range};changed();}else{draw(key);controls();}});
   const cancel=()=>{if(M.drag?.key!==key)return;M.drag=null;draw(key);controls();};canvas.addEventListener('pointercancel',cancel);canvas.addEventListener('lostpointercapture',cancel);
   $('oa-'+key+'-direction').addEventListener('change',()=>{if(!ready())return;M.phases[key].direction=$('oa-'+key+'-direction').value||null;if(M.phases[key].status==='marked')changed();});
   for(const edge of ['start','end'])$('oa-'+key+'-'+edge).addEventListener('change',()=>{if(!ready())return;const q=M.current.metadata,anchor=key==='onset'?q.task_start:q.task_end,w=M.current.waveform.windows[key],a=$('oa-'+key+'-start').value,b=$('oa-'+key+'-end').value,start=anchor+Math.round(Number(a)*250),end=anchor+Math.round(Number(b)*250);
    if(a===''||b===''||!Number.isFinite(start)||!Number.isFinite(end)||start<w.start||end>w.end||start>=end){message('请输入对应5秒内的有效起止时间',true);return;}
    M.phases[key]={status:'marked',direction:$('oa-'+key+'-direction').value||null,sample_start:start,sample_end:end};changed();});
  }
  document.querySelectorAll('[data-outcome]').forEach(b=>b.addEventListener('click',()=>{if(!ready())return;M.phases[b.dataset.phase]={status:b.dataset.outcome,direction:M.current.metadata.default_directions[b.dataset.phase]};changed();}));
 }
 async function load(tid){
  if(M.saving)return;stash();const generation=++M.generation;M.currentId=tid;M.current=null;M.phases=null;M.drag=null;M.dirty=false;controls();list();$('oaWindows').innerHTML='<p class="oa-empty">正在读取这段首尾波形…</p>';message('读取波形…');
  try{const value=await api('/api/ocular-annotations/trials/'+tid);if(generation!==M.generation)return;
   if(value.dataset_id!==M.catalog.dataset_id||value.metadata.trial_id!==tid)throw new Error('波形身份不匹配，请刷新试次列表');
   M.current=value;M.catalog.trials.find(q=>q.trial_id===tid).annotation=value.annotation;M.phases=JSON.parse(JSON.stringify(value.annotation.phases));const defaults=C.defaultDirections(value.metadata.label);for(const k of ['onset','offset'])if(!M.phases[k].direction)M.phases[k].direction=defaults[k];
   const local=draft(value.metadata);if(local&&local.revision===value.annotation.revision){M.phases=local.phases;M.dirty=true;}
   $('oaWindows').innerHTML=windowMarkup('onset')+windowMarkup('offset');hookWindows();const index=M.catalog.trials.findIndex(q=>q.trial_id===tid),q=value.metadata;
   $('oaTrialTitle').textContent='第'+String(index+1).padStart(2,'0')+'段 · '+(TASK[q.label]||q.label);$('oaTrialMeta').textContent=(ROLE[q.split]||q.split)+' · 完整采集 '+q.duration_s.toFixed(3)+' 秒 · 原始250Hz · 首尾各5秒';
   message(M.dirty?'已恢复未保存草稿':local?'服务器版本已更新，本地旧草稿未覆盖，请重新核对':value.annotation.revision?'已保存 · 修订 '+value.annotation.revision:'尚未打标 · 请分别框选两次眼动');controls();render();list();
  }catch(e){if(generation!==M.generation)return;$('oaWindows').innerHTML='<p class="oa-empty">波形加载失败，请点击刷新重试。</p>';message(e.message,true);}
 }
 function move(delta){if(!ready())return;const i=M.catalog.trials.findIndex(q=>q.trial_id===M.currentId),q=M.catalog.trials[i+delta];if(q)load(q.trial_id);}
 async function save(next){
  if(!ready()||!M.dirty)return;if(M.drag){message('请先松开鼠标完成框选，再保存',true);return;}
  for(const key of ['onset','offset']){const p=M.phases[key],a=$('oa-'+key+'-start').value,b=$('oa-'+key+'-end').value,anchor=key==='onset'?M.current.metadata.task_start:M.current.metadata.task_end;
   if((p.status==='marked'||a!==''||b!=='')&&(p.status!=='marked'||a===''||b===''||!Number.isFinite(Number(a))||!Number.isFinite(Number(b))||anchor+Math.round(Number(a)*250)!==p.sample_start||anchor+Math.round(Number(b)*250)!==p.sample_end)){message('起止时间尚未形成有效范围，请先修正端点',true);return;}
   if(p.status==='marked'&&!DIR[p.direction]){message('请为框选的眼动确认方向',true);return;}
  }
  const a=M.phases.onset,b=M.phases.offset;if(a.status===b.status&&a.status==='marked'&&a.sample_start<b.sample_end&&a.sample_end>b.sample_start){message('开始与结束的眼动范围不能重叠，请调整',true);return;}
  const submittedRevision=M.editRevision;M.saving=true;controls();list();message('正在保存…');
  try{const q=M.current.metadata,value=await api('/api/ocular-annotations/save',{method:'POST',headers:{'Content-Type':'application/json','X-EEGHub':'1'},body:JSON.stringify({dataset_id:M.catalog.dataset_id,trial_id:q.trial_id,waveform_sha256:q.waveform_sha256,revision:M.current.annotation.revision,phases:Object.fromEntries(Object.entries(M.phases).map(([k,p])=>[k,C.cleanPhase(p)]))})});
   M.current.annotation=value;const row=M.catalog.trials.find(x=>x.trial_id===q.trial_id);row.annotation=value;M.dirty=M.editRevision!==submittedRevision;if(M.dirty){stash();message('已保存提交版本；较新的修改仍为草稿');}else{clearDraft();message('已保存 · 修订 '+value.revision);}M.saving=false;controls();list();render();if(next&&!M.dirty)move(1);
  }catch(e){M.saving=false;controls();list();message(e.message+'；草稿已保留',true);}
 }
 async function refresh(){if(M.saving)return;stash();$('oaError').hidden=true;try{const catalog=await api('/api/ocular-annotations');if(M.saving)return;M.catalog=catalog;list();$('oaExport').disabled=false;const id=catalog.trials.some(q=>q.trial_id===M.currentId)?M.currentId:catalog.trials[0]?.trial_id;if(id)await load(id);else $('oaWindows').innerHTML='<p class="oa-empty">目前没有可打标的完整试次。</p>';}catch(e){$('oaError').textContent=e.message;$('oaError').hidden=false;}}
 $('oaPrevious').addEventListener('click',()=>move(-1));$('oaNext').addEventListener('click',()=>move(1));$('oaSave').addEventListener('click',()=>save(false));$('oaSaveNext').addEventListener('click',()=>save(true));$('oaRefresh').addEventListener('click',refresh);$('oaFilter').addEventListener('change',list);
 for(const id of ['oaChannels','oaReferences','oaAmplitude'])$(id).addEventListener('change',render);
 $('oaExport').addEventListener('click',async()=>{try{const value=await api('/api/ocular-annotations/export'),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='个人眼动人工标注.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){message(e.message,true);}});
 window.addEventListener('beforeunload',()=>stash());window.addEventListener('resize',()=>{if(M.current)render();});if(typeof ResizeObserver!=='undefined')new ResizeObserver(()=>{if(M.current)render();}).observe($('oaWindows'));
 document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();save(true);}});
 refresh();
})();
