'use strict';
(()=>{
 const anchor=document.getElementById('sessions');if(!anchor)return;
 const panel=document.createElement('section');panel.className='panel capture-panel';panel.id='desktopCapture';
 panel.innerHTML='<div class="panel-heading"><div><h2>电脑自主采集</h2><p>戴好脑电设备，开始后按中枢返回的任务说明操作，按键只用于标注。</p></div><span class="tag neutral">自动打标 · 保存到本机</span></div><div class="capture-actions"><button class="button" id="captureStart">开始自主采集</button><button class="button" id="captureStop" disabled>停止并保存</button><label id="captureComboOption"><input id="captureCombos" type="checkbox">加入转向组合</label></div><div class="capture-pad" id="capturePad" aria-label="按住标注任务方向"></div><p class="capture-state" id="captureState" role="status">尚未开始</p><p class="capture-hint" id="captureHint">按住才标注，松开为未标注；需要静止数据请按中央“静止”。也可按方向键／空格，Esc 保存退出。</p><p class="capture-detail" id="captureDetail">电脑和 VR 共用采集服务，同时只允许一个会话。按键操作本身可能产生动作伪迹。</p>';
 anchor.before(panel);const css=document.createElement('link');css.rel='stylesheet';css.href='/capture.css';document.head.append(css);
 const get=id=>document.getElementById(id),names={FORWARD:'向前',BACKWARD:'向后',LEFT:'向左',RIGHT:'向右',REST:'静止',FORWARD_LEFT:'前进左转',FORWARD_RIGHT:'前进右转',BACKWARD_LEFT:'后退左转',BACKWARD_RIGHT:'后退右转'};
 const positions={FORWARD:[2,1],LEFT:[1,2],REST:[2,2],RIGHT:[3,2],BACKWARD:[2,3],FORWARD_LEFT:[1,1],FORWARD_RIGHT:[3,1],BACKWARD_LEFT:[1,3],BACKWARD_RIGHT:[3,3]},buttons=[];
 let frame=0,polling=false,remoteActive=false,lastSync=0;
 function frames(){frame++;requestAnimationFrame(frames);}requestAnimationFrame(frames);
 const api=async(path,data,keepalive=false)=>{const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-EEGHub':'1'},...(data===undefined?{}:{body:JSON.stringify(data)}),cache:'no-store',signal:AbortSignal.timeout(1500),keepalive});const body=await r.json();if(!r.ok)throw Error(body.error||'中枢请求失败');return body;};
 const capture=new EEGCapture.DesktopCapture({api,clock:()=>performance.now(),focused:()=>!document.hidden&&document.hasFocus(),frame:()=>frame,changed:render});
 for(const [label,[x,y]]of Object.entries(positions)){
  const b=document.createElement('button');b.className='capture-key'+(label.includes('_')?' capture-diagonal':'');b.type='button';b.dataset.label=label;b.textContent=names[label];b.style.gridColumn=x;b.style.gridRow=y;b.setAttribute('aria-label','按住标注'+names[label]);get('capturePad').append(b);buttons.push(b);
  b.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();if(capture.press(label,e.pointerId)){b.setPointerCapture(e.pointerId);}});
  b.addEventListener('pointermove',e=>{if(capture.pointer!==e.pointerId)return;const r=b.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)capture.release(e.pointerId);});
  for(const type of ['pointerup','pointercancel','lostpointercapture','pointerleave'])b.addEventListener(type,e=>capture.release(e.pointerId));
  b.addEventListener('contextmenu',e=>e.preventDefault());
 }
 function render(){
  const active=!!capture.session;const combosAllowed=!capture.allowedLabels||capture.allowedLabels.some(v=>v.includes('_'));panel.classList.toggle('is-active',active);panel.classList.toggle('with-combos',combosAllowed&&get('captureCombos').checked);
  get('captureStart').disabled=active||capture.busy||remoteActive;get('captureStop').disabled=!active||capture.stopping;get('captureCombos').disabled=active||capture.busy;
  const gaze=capture.experimentMode==='gaze_only';const gazeNames={FORWARD:'向上看 · 前进',BACKWARD:'向下看 · 后退',LEFT:'向左看 · 左转',RIGHT:'向右看 · 右转',REST:'中央注视 · 无导航'};
  for(const b of buttons){const allowed=!capture.allowedLabels||capture.allowedLabels.includes(b.dataset.label);b.hidden=!allowed;b.style.display=allowed?'':'none';b.disabled=!active||!allowed||capture.failed||capture.stopping;b.textContent=(gaze?gazeNames[b.dataset.label]:names[b.dataset.label])||names[b.dataset.label];b.setAttribute('aria-label',capture.instructions[b.dataset.label]||'按住标注'+b.textContent);b.classList.toggle('is-held',capture.held===b.dataset.label);}
  get('captureState').textContent=capture.error||(capture.busy?'正在同步并创建会话…':active?(capture.stopping?'正在保存…':capture.held?'正在标注：'+names[capture.held]:'松开 · 未标注')+' · 已确认 '+capture.ack+' 个事件':remoteActive?'已有 VR／其它页面正在采集，请先结束该会话。':capture.recording?'已保存：'+capture.recording:'尚未开始');
  const navigation=capture.experimentMode==='navigation_mi_gaze'||capture.experimentMode==='navigation_mi_only';
  get('captureHint').textContent=active?(capture.instructions[capture.held]||(gaze?'前进向上看，后退向下看，左右转分别向左右看；无需肢体想象，可正常操作手柄。':navigation?'以第一人称想象自己行走或转向，运动想象为主；是否配合眼动以当前任务说明为准。可正常操作手柄。':'按中枢任务说明操作。'))+' 按住才标注，松开为未标注。':'按住才标注，松开为未标注；明确无导航意图请按中央键。方向键／空格标注，Esc保存退出。';
  get('captureDetail').textContent=active?(capture.instructions[capture.held]||(gaze?'无需肢体想象，可正常操作手柄；回中停止方向任务。':'按中枢任务说明操作。'))+' 文件 '+capture.recording+' · 采集中模型动作已关闭 · 自动保留原始信号及质量记录':'电脑和 VR 共用采集服务，同时只允许一个会话。按键操作本身可能产生动作伪迹。';
 }
 get('captureStart').onclick=()=>capture.start();get('captureStop').onclick=()=>capture.stop();get('captureCombos').onchange=render;
 const keys={ArrowUp:'FORWARD',ArrowDown:'BACKWARD',ArrowLeft:'LEFT',ArrowRight:'RIGHT',' ':'REST'};
 document.addEventListener('keydown',e=>{if(!capture.session||e.target?.matches('input,textarea,select'))return;if(e.key==='Escape'){e.preventDefault();capture.stop();return;}if(keys[e.key]&&!e.repeat){e.preventDefault();capture.press(keys[e.key],'key:'+e.key,'desktop_keyboard');}});
 document.addEventListener('keyup',e=>{if(keys[e.key])capture.release('key:'+e.key);});
 window.addEventListener('blur',()=>capture.lost());document.addEventListener('visibilitychange',()=>{if(document.hidden)capture.lost();});
 window.addEventListener('pagehide',()=>{if(!capture.session)return;capture.lost();api('/api/acquisition/stop',{session_id:capture.session,client_token:capture.token,reason:'page_exit'},true).catch(()=>{});});
 async function poll(){if(polling)return;polling=true;try{const state=await api('/api/acquisition/status');remoteActive=state.active&&state.session_id!==capture.session;if(capture.session&&(!state.active||state.session_id!==capture.session))capture.ended();render();}catch{if(!capture.session)get('captureState').textContent='中枢状态不可用，暂不能确认采集状态';}finally{polling=false;}}
 setInterval(()=>capture.heartbeat(),350);setInterval(poll,700);setInterval(()=>{if(capture.session&&performance.now()-lastSync>15000){lastSync=performance.now();capture.sync().catch(()=>{});}},2000);render();poll();
})();
