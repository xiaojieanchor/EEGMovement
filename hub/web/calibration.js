'use strict';
(()=>{
 const host=document.getElementById('intentPanel');if(!host)return;
 if(document.body.dataset.deviceTransition==='gnautilus'){const mount=document.getElementById('modelCheckMount');if(mount){const note=document.createElement('p');note.className='intent-note';note.textContent='新模型尚未配置，模型性能校验暂不可用。';mount.append(note)}return;}
 const $=id=>document.getElementById(id);
 // v2 prompt is a 0.5s onset cue; its label stays valid for the entire phase.
 const cues={REST:['保持静止','放松身体，保持不动'],FORWARD:['想象向前移动','持续想象向前移动，直到提示切换'],BACKWARD:['想象向后移动','持续想象向后移动，直到提示切换'],LEFT:['想象向左转','持续想象向左转，直到提示切换'],RIGHT:['想象向右转','持续想象向右转，直到提示切换']};
 const stages={baseline:'静息基线',adapt:'方向适配',check:'校准检查'};
 const seconds=value=>Math.max(0,Number(value)||0);
 const time=value=>{const n=Math.ceil(seconds(value));return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`};
 const box=document.createElement('section');box.className='intent-calibration';
 box.innerHTML='<h3>本次模型性能校验 · 可选</h3><p>实时推理与模型动作可独立手动启动，不受校验结果限制。校验仅测量当前模型，保留全部原始信号、质量记录与逐帧预测；校验采集期间人物暂停执行。</p><div class="intent-actions"><button id="calibrationStart" class="button">静息检查 · 15秒</button><label for="calibrationRounds">方向校验轮数 <select id="calibrationRounds"><option value="1">1轮 · 约1分钟</option><option value="3">3轮 · 约3分钟</option><option value="6">6轮 · 约6分钟</option></select></label><button id="calibrationDirections" class="button primary-action">开始方向性能校验</button><button id="calibrationCancel" class="button">结束并保存已采数据</button><button id="calibrationNew" class="button">标记重新佩戴</button><button id="diagnosticStart" class="button">训练回放诊断</button></div><p id="calibrationState" role="status">正在读取校验状态</p><p id="calibrationOutcome" class="calibration-timing"></p><p id="calibrationTiming" class="calibration-timing">每种指令12秒，前2秒准备，不做现场拟合</p><progress id="calibrationProgress" max="60" value="0" aria-label="模型校验流程进度"></progress><section id="calibrationResult" class="model-check-result" aria-label="本次模型校验结果"></section><details><summary>历史校验记录</summary><div id="calibrationHistory"></div></details>';
 (document.getElementById('modelCheckMount')||host).prepend(box);
 const screen=document.createElement('div');screen.className='calibration-screen';screen.hidden=true;screen.setAttribute('role','dialog');screen.setAttribute('aria-modal','true');screen.setAttribute('aria-labelledby','calibrationPrompt');
 screen.innerHTML='<div class="calibration-content"><p id="calibrationStage" class="calibration-stage"></p><h2 id="calibrationPrompt" aria-live="polite"></h2><p id="calibrationInstruction" class="calibration-instruction"></p><div class="calibration-phase-clock"><span>本条指令剩余</span><strong id="calibrationPhaseTimer" role="timer" aria-live="off">00:20</strong></div><p id="calibrationScreenState" class="calibration-screen-state" role="status"></p><progress id="calibrationScreenProgress" max="120" value="0" aria-label="总合格数据进度"></progress><div class="calibration-clocks"><div><span>校验流程剩余</span><strong id="calibrationRemaining">02:00</strong></div><div><span>已用实际时间</span><strong id="calibrationWall">00:00</strong></div></div><p class="calibration-help">提示会持续显示；质量拒识仍保留并计入覆盖率。<br>切换页面或按 Esc 会结束并保存已采数据。</p><button id="calibrationScreenCancel" class="button">结束并保存已采数据</button></div>';
 document.body.append(screen);
 let token=null,id=null,seq=0,busy=false,pending=false,generation=0,lastAccepted=null,lastAdvance=0,previousFocus=null,latestActive=false,lastState=null,lastRead=0,resultKey=null;
 window.EEGCalibration={getState:()=>performance.now()-lastRead<1000?lastState:null};
 function hide(){screen.hidden=true;if(previousFocus){previousFocus.focus();previousFocus=null}}
 function show(){if(screen.hidden){previousFocus=document.activeElement;screen.hidden=false;$('calibrationScreenCancel').focus()}}
 async function api(path,data={}){const r=await fetch(path,{method:'POST',headers:{'X-EEGHub':'1','Content-Type':'application/json'},body:JSON.stringify(data),signal:AbortSignal.timeout(path.endsWith('/present')?1000:8000)});const j=await r.json();if(!r.ok)throw Error(j.error||'请求未完成');return j}
 function render(s){
   lastState=s;lastRead=performance.now();
   const accepted=seconds(s.elapsed_seconds??s.accepted_seconds),required=seconds(s.required_seconds)||60,remaining=Math.max(0,required-accepted);
   $('calibrationState').textContent=`${s.reason||'等待校验状态'} · ${accepted.toFixed(1)} / ${required}秒`;
   $('calibrationTiming').textContent=`流程剩余 ${time(remaining)} · 实际用时 ${time(s.wall_seconds)} · 合格数据 ${seconds(s.accepted_seconds).toFixed(1)}秒`;
   for(const name of ['calibrationProgress','calibrationScreenProgress']){$(name).max=required;$(name).value=Math.min(required,accepted)}
   latestActive=!!s.active;$('calibrationStart').disabled=latestActive||pending;$('calibrationCancel').disabled=!s.active;
   $('calibrationDirections').disabled=latestActive||pending;$('calibrationRounds').disabled=latestActive||pending;
   $('calibrationOutcome').textContent='校验不授予或撤销启动资格；结束后可独立启动实时推理和模型动作。';
   if(s.last_result&&resultKey!==s.last_result.check_id){renderResult(s.last_result);resultKey=s.last_result.check_id;history()}
   const own=!!(s.active&&s.surface==='desktop'&&token&&id===s.calibration_id);
   if(!own){hide();if(!s.active)token=null;return false}
   const cue=cues[s.label];
   // Do not acknowledge an unidentified instruction as presented.
   if(!cue){show();$('calibrationPrompt').textContent='等待有效指令';$('calibrationInstruction').textContent='计时暂停，请等待状态恢复';return false}
   const now=performance.now();if(lastAccepted===null||accepted!==lastAccepted){lastAccepted=accepted;lastAdvance=now}
   const paused=now-lastAdvance>=1200;
   $('calibrationStage').textContent=`${s.mode==='signal'?'静息模型检查':'方向性能校验'} · 第 ${Number(s.cursor)+1} / ${s.phase_count||5} 条`;
   const preparing=s.settle_seconds>seconds(s.phase_elapsed);
   $('calibrationPrompt').textContent=cue[0];$('calibrationInstruction').textContent=preparing?`先准备动作 · ${Math.ceil(s.settle_seconds-seconds(s.phase_elapsed))}秒后开始计分`:s.mode==='signal'?'保持放松；只检查静息表现，不衡量全部方向准确率':cue[1];
   // v2 schedule: 20s baseline + 15 x 5s adaptation + 5 x 5s check.
   // All countdown values come from accepted EEG, never browser wall time.
   const duration=seconds(s.phase_seconds)||(s.stage==='baseline'?20:5);
   $('calibrationPhaseTimer').textContent=time(duration-seconds(s.phase_elapsed));
   $('calibrationRemaining').textContent=time(remaining);$('calibrationWall').textContent=time(s.wall_seconds);
   $('calibrationScreenState').textContent=paused?`计时已暂停 · ${s.reason||'等待合格连续数据'}；恢复后继续`:(s.reason||'等待合格连续数据');
   screen.dataset.paused=String(paused);show();return true;
 }
 async function action(path,data={}){
   if(pending)return;pending=true;$('calibrationStart').disabled=true;
   const epoch=++generation;
   try{
     const s=await api(path,data);if(epoch!==generation)return;
     if(s.presentation_token){token=s.presentation_token;id=s.calibration_id;seq=0;lastAccepted=null}
     if(path.includes('cancel')||path.includes('new-placement')){token=null;hide()}
     render(s);await poll();
   }catch(e){$('calibrationState').textContent=e.message}
   finally{pending=false;$('calibrationStart').disabled=latestActive}
 }
 async function poll(){if(busy)return;busy=true;const epoch=generation;try{
   const r=await fetch('/api/calibration/status',{cache:'no-store',signal:AbortSignal.timeout(1000)});if(!r.ok)throw Error('校准状态不可用');const s=await r.json();if(epoch!==generation)return;
   const own=render(s);
   if(own&&!document.hidden){
     await new Promise(requestAnimationFrame);
     if(epoch!==generation||!token||document.hidden)return;
     await api('/api/calibration/present',{calibration_id:id,presentation_token:token,seq:++seq,cursor:s.cursor});
   }
 }catch(e){
   if(epoch!==generation)return;
   $('calibrationState').textContent=`校验连接异常 · ${e.message}`;
   if(token){show();screen.dataset.paused='true';$('calibrationPrompt').textContent='计时已暂停';$('calibrationInstruction').textContent='连接恢复后继续跟随指令';$('calibrationScreenState').textContent=`${e.message} · 正在重新读取状态`}
 }finally{busy=false}}
 $('calibrationStart').onclick=()=>action('/api/calibration/start',{mode:'signal'});$('calibrationDirections').onclick=()=>action('/api/calibration/start',{mode:'directions',repetitions:Number($('calibrationRounds').value)||1});$('calibrationCancel').onclick=cancel;$('calibrationScreenCancel').onclick=cancel;$('calibrationNew').onclick=()=>action('/api/calibration/new-placement');
 $('diagnosticStart').onclick=async()=>{try{await api('/api/intent/start',{purpose:'diagnostic'});$('calibrationState').textContent='训练回放诊断已启动；真实EEG请点击“开始实时推理”'}catch(e){$('calibrationState').textContent=e.message}};
 function cancel(){
   ++generation;const owned=!!token;token=null;lastState=null;hide();
   if(owned||!$('calibrationCancel').disabled){
     $('calibrationState').textContent='正在取消校准';
     return fetch('/api/calibration/cancel',{method:'POST',headers:{'X-EEGHub':'1','Content-Type':'application/json'},body:'{}',keepalive:true}).then(r=>{if(!r.ok)throw Error('取消请求未完成');return poll()}).catch(e=>{$('calibrationState').textContent=e.message});
   }
 }
 document.addEventListener('visibilitychange',()=>{if(document.hidden)cancel()});window.addEventListener('pagehide',cancel);document.addEventListener('keydown',e=>{
   if(screen.hidden)return;
   if(e.key==='Escape'){e.preventDefault();cancel()}
   if(e.key==='Tab'){e.preventDefault();$('calibrationScreenCancel').focus()}
 });
 function renderResult(r){
   const target=$('calibrationResult');target.replaceChildren();const m=r.metrics||{};
   const append=(tag,value)=>{const e=document.createElement(tag);e.textContent=value;target.append(e);return e};
   const pct=value=>Number.isFinite(value)?`${(value*100).toFixed(1)}%`:'尚无可计分数据';
   append('h4',`${r.completed?'本次校验结果':'本次部分结果'} · ${r.mode==='signal'?'仅静息':'方向检查'}`);
   append('p',`指令一致率（含拒识）${pct(m.accuracy_including_rejection)} · 合格数据覆盖 ${pct(m.coverage)} · 事件正确 ${m.event_correct||0}/${m.event_count||0} · 静止误启动判定 ${m.rest_false_starts||0}次`);
   append('p',`模型 ${r.model_id||r.model_binding?.model?.[0]||'—'} · 录制 ${r.recording||'—'} · ${r.created_at||''}`);
   const table=append('table','');const header=document.createElement('tr');
   for(const value of ['指令','有效秒数','含拒识召回']){const th=document.createElement('th');th.textContent=value;header.append(th)}table.append(header);
   for(const item of m.classes||[]){const tr=document.createElement('tr');for(const value of [cues[item.label]?.[0]||item.label,`${seconds(item.eligible_seconds).toFixed(1)} / ${seconds(item.instructed_seconds).toFixed(1)}`,pct(item.recall_including_rejection)]){const td=document.createElement('td');td.textContent=value;tr.append(td)}table.append(tr)}
   append('p','这是本次提示任务的结果，尚未验证用户实际意图；静息检查不代表全部方向效果。'+(r.storage_complete===false?'部分文件保存异常，请查看完整报告。':'数据已保存。')+'未自动加入训练。');
   const a=append('a','查看完整报告与数据哈希');a.href=`/api/calibration/result?check_id=${encodeURIComponent(r.check_id)}`;a.target='_blank';a.rel='noopener';
 }
 async function history(){try{
   const r=await fetch('/api/calibration/history',{cache:'no-store',signal:AbortSignal.timeout(2000)});if(!r.ok)return;
   const data=await r.json(),target=$('calibrationHistory');target.replaceChildren();
   for(const item of data.results||[]){const p=document.createElement('p'),a=document.createElement('a');a.textContent=`${item.created_at||''} · ${item.mode==='signal'?'静息':'方向'} · ${item.recording||item.check_id} · ${item.completed?'完整':'部分'}结果`;a.href=`/api/calibration/result?check_id=${encodeURIComponent(item.check_id)}`;a.target='_blank';a.rel='noopener';p.append(a);target.append(p)}
 }catch(e){$('calibrationHistory').textContent='历史结果暂不可读取，稍后刷新重试'}}
 setInterval(poll,200);poll();history();
})();
