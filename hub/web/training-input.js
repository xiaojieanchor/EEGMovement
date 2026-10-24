'use strict';
(()=>{
 const host=document.getElementById('trainingInputMount');if(!host)return;
 host.className='training-input';host.innerHTML='<div class="input-tabs" role="tablist" aria-label="推理输入测试"><button id="inputLiveTab" role="tab" aria-selected="true" aria-controls="inputLiveView">实时输入</button><button id="inputTestTab" role="tab" aria-selected="false" aria-controls="inputTestView" tabindex="-1">训练数据测试</button></div><div id="inputLiveView" role="tabpanel" aria-labelledby="inputLiveTab"><span class="input-source-caption">LIVE · 当前设备</span></div><div id="inputTestView" role="tabpanel" aria-labelledby="inputTestTab" hidden><div class="input-test-heading"><h3>按住方向，松开恢复实时输入</h3><span class="input-test-badge">已训练数据回放</span></div><div class="input-test-layout"><div class="input-test-pad" id="inputTestPad" aria-label="按住发送对应类别的已训练原始脑电"></div><div class="input-test-info"><strong id="inputTestState" role="status">读取片段中</strong><p id="inputTestProgress"></p><p class="input-test-hint">方向键 / 空格 · Esc 释放<br>链路测试，不代表新数据准确率</p><details><summary>片段信息</summary><p id="inputTestClip"></p><p>原始 EEG 与历史质检；不写入个人录制。</p></details></div></div></div>';
 const liveSignal=document.getElementById('inputSignal');if(liveSignal)document.getElementById('inputLiveView').append(liveSignal);
 const switchHost=document.getElementById('consoleControls'),switches=document.querySelector('.console-switches');if(switchHost&&switches)switchHost.append(switches);
 const settings=document.querySelector('.intent-advanced'),settingsHost=document.getElementById('modelSettingsMount');if(settings&&settingsHost){settings.classList.add('console-drawer');settingsHost.append(settings);}
 if(document.body.dataset.deviceTransition==='gnautilus'){
  document.getElementById('inputTestTab').disabled=true;document.getElementById('inputTestTab').textContent='旧训练数据已归档';document.getElementById('inputTestState').textContent='新设备未配置训练数据测试';document.getElementById('inputLiveView').querySelector('.input-source-caption').textContent='g.Nautilus · 当前设备';return;
 }

 const $=id=>document.getElementById(id),names={FORWARD:'向前',BACKWARD:'向后',LEFT:'向左',RIGHT:'向右',REST:'静止',FORWARD_LEFT:'前进左转',FORWARD_RIGHT:'前进右转',BACKWARD_LEFT:'后退左转',BACKWARD_RIGHT:'后退右转'},arrows={FORWARD:'↑',BACKWARD:'↓',LEFT:'←',RIGHT:'→',REST:'Ⅱ',FORWARD_LEFT:'↖',FORWARD_RIGHT:'↗',BACKWARD_LEFT:'↙',BACKWARD_RIGHT:'↘'},positions={FORWARD:[2,1],LEFT:[1,2],REST:[2,2],RIGHT:[3,2],BACKWARD:[2,3],FORWARD_LEFT:[1,1],FORWARD_RIGHT:[3,1],BACKWARD_LEFT:[1,3],BACKWARD_RIGHT:[3,3]};
 let catalog=null,catalogError='',tab='live',lastHold=null,catalogKey='',catalogFetching=false;const buttons=[];
 const invalidate=()=>window.EEGIntent?.inputChanged?.();
 const held=new EEGTrainingInput.HeldEEGTest({api:async(data,keepalive)=>{const r=await fetch('/api/intent/test',{method:'POST',headers:{'Content-Type':'application/json','X-EEGHub':'1'},body:JSON.stringify(data),keepalive,signal:AbortSignal.timeout(1200)});const j=await r.json();if(data.action!=='renew')invalidate();if(!r.ok)throw Error(j.error||'训练数据测试请求失败');return j},changed:()=>{const id=held.hold?.id||null;if(id!==lastHold){lastHold=id;invalidate()}render()}});
 for(const [label,[x,y]]of Object.entries(positions)){
  const b=document.createElement('button');b.className='input-test-key';b.type='button';b.dataset.label=label;b.style.gridColumn=x;b.style.gridRow=y;b.innerHTML='<span aria-hidden="true">'+arrows[label]+'</span><b>'+names[label]+'</b>';b.setAttribute('aria-label','按住回放'+names[label]+'训练脑电');$('inputTestPad').append(b);buttons.push(b);
  b.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();if(held.press(label,e.pointerId))b.setPointerCapture(e.pointerId)});
  b.addEventListener('pointermove',e=>{if(held.hold?.pointer!==e.pointerId)return;const r=b.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)held.release(e.pointerId)});
  for(const event of ['pointerup','pointercancel','lostpointercapture','pointerleave'])b.addEventListener(event,e=>held.release(e.pointerId));b.addEventListener('contextmenu',e=>e.preventDefault());
 }
 function select(value){if(value!==tab)held.release();tab=value;for(const [key,id]of [['live','inputLiveTab'],['test','inputTestTab']]){$(id).setAttribute('aria-selected',String(key===tab));$(id).tabIndex=key===tab?0:-1;}$('inputLiveView').hidden=tab!=='live';$('inputTestView').hidden=tab!=='test';render()}
 $('inputLiveTab').onclick=()=>select('live');$('inputTestTab').onclick=()=>select('test');

 function render(){
  const s=window.EEGIntent?.getState?.(),t=s?.training_test,h=held.hold,matched=!!(catalog&&catalog.model_id===s?.health?.model_id&&catalog.artifact_sha256===s?.health?.artifact_sha256);held.enabled=!!(matched&&s?.inference_enabled&&s.health?.weights_loaded);
  if(h&&!s){held.error='推理状态已过期，测试已停止';held.release();return}
  if(h&&s&&(!s.inference_enabled||t&&h.confirmed&&Number.isInteger(t.revision)&&t.revision>=h.revision&&!t.active)){held.release();return}
  for(const b of buttons){b.hidden=!catalog?.clips?.[b.dataset.label]&&b.dataset.label.includes('_');b.disabled=!held.enabled&&h?.label!==b.dataset.label;b.classList.toggle('is-held',h?.label===b.dataset.label);b.setAttribute('aria-pressed',String(h?.label===b.dataset.label))}
  host.classList.toggle('is-testing',!!t?.active);const label=t?.active?t.label:h?.label,clip=catalog?.clips?.[label];
  $('inputTestState').textContent=held.error||catalogError||(!catalog||!matched?'正在同步当前模型的训练片段':!s?.inference_enabled?'请先启动推理':!s?.health?.weights_loaded?'等待计算服务器及模型就绪':t?.active?'回放中 · '+names[t.label]:h?'切换中 · '+names[h.label]:'松开 · 实时输入');
  $('inputTestClip').textContent=clip?'训练记录 '+clip.recording+' · 每轮 '+clip.loop_play_seconds.toFixed(1)+' 秒 · '+(catalog.version===2?'CH2/3/4/5/7及历史质检':'原始7槽及历史质检'):catalog?'已匹配 '+Object.keys(catalog.clips).length+' 类 · 最新训练录制 '+Object.values(catalog.clips).map(c=>c.recording).sort().at(-1):'按当前模型锁定的训练清单同步原始 EEG；保留至少4路历史质量门。';
  $('inputTestProgress').textContent=t?.active?'当前第 '+(t.loops+1)+' 轮 · 已推进 '+Math.max(0,t.playhead_samples-1000)+' 样本 · '+(s.fresh?'模型概率已返回':'等待模型返回'):'按住开始 · 松开结束';
 }
 const keys={ArrowUp:'FORWARD',ArrowDown:'BACKWARD',ArrowLeft:'LEFT',ArrowRight:'RIGHT',' ':'REST'};
 document.addEventListener('keydown',e=>{if(tab!=='test'||e.target?.matches('input,textarea,select'))return;if(e.key==='Escape'){held.release();return}if(keys[e.key]&&!e.repeat){e.preventDefault();held.press(keys[e.key],'key:'+e.key)}});
 document.addEventListener('keyup',e=>{if(keys[e.key])held.release('key:'+e.key)});
 window.addEventListener('blur',()=>held.release());window.addEventListener('pagehide',()=>held.release());document.addEventListener('visibilitychange',()=>{if(document.hidden)held.release()});
 window.addEventListener('pointerup',e=>held.release(e.pointerId));window.addEventListener('pointercancel',e=>held.release(e.pointerId));
 window.EEGTrainingInputRelease=()=>held.release();$('globalStop')?.addEventListener('click',()=>held.release());
 async function refreshCatalog(){
  if(catalogFetching)return;const s=window.EEGIntent?.getState?.(),key=s?.health?.artifact_sha256||'';
  if(key!==catalogKey){held.release();held.error='';catalog=null;catalogError='';catalogKey=key}
  if(catalog&&catalog.artifact_sha256===key)return;catalogFetching=true;
  try{const r=await fetch('/api/intent/test/catalog',{cache:'no-store',signal:AbortSignal.timeout(5000)}),j=await r.json();if(!r.ok)throw Error(j.error||'测试片段不可用');if(key!==catalogKey)return;catalog=j;catalogError='';held.error=''}catch(e){catalogError=e.message}finally{catalogFetching=false;render()}
 }
 setInterval(refreshCatalog,2000);refreshCatalog();
 setInterval(()=>held.heartbeat(),150);setInterval(render,150);render();
})();
