'use strict';
(()=>{
 const $=id=>document.getElementById(id);let state=null,polling=false,columnsSignature='';
 const text=(id,value)=>$(id).textContent=value;
 async function api(path,data){const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-EEGHub':'1'},...(data===undefined?{}:{body:JSON.stringify(data)}),cache:'no-store',signal:AbortSignal.timeout(3000)});const b=await r.json();if(!r.ok)throw Error(b.error||'请求失败');return b;}
 const act=(id,fn)=>$(id).onclick=async()=>{try{$(id).disabled=true;await fn();await poll();}catch(e){text('notice',e.message);}finally{$(id).disabled=false;}};
 for(const [id,role]of [['relayPair','eeg'],['questPair','quest']])if($(id))act(id,async()=>{const value=await api('/api/relay/enrollment?role='+role);const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=role==='eeg'?'gNautilus-public-pairing.json':'eeg_enrollment.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
 act('pair',async()=>{const r=await api('/api/gnautilus/pair',{host:$('hubHost').value.trim()});text('pairResult','配对文件已保存：'+r.pairing_file+'。复制给采集电脑后在采集端选择它。');});
 for(const [id,path]of [['pause','/api/gnautilus/pause'],['resume','/api/gnautilus/resume'],['recordStart','/api/record/start'],['recordStop','/api/record/stop']])act(id,()=>api(path,{}));
 act('emergency',()=>api('/api/gnautilus/emergency',{}));
 function item(parent,tag,value){const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;}
 function render(s){state=s;const relay=s.public_relay||{};if($('relayInfo'))text('relayInfo',relay.enabled?(relay.connected?'公网已认证 · ':'公网未连接 · ')+(relay.endpoint||'')+' · 中枢保存 '+(relay.blocks||0)+'块 · 历史补传 '+(relay.backfill_blocks||0)+'块'+(relay.error?' · '+relay.error:''):'公网配置尚未生成');const g=s.gnautilus||{},d=g.descriptor||{},src=s.source,q=s.quality,health=g.agent_health||{};
  text('notice',health.error|| (src.source_kind==='synthetic'?'合成样本仅用于软件检查，不能创建个人采集会话。':src.fresh?'正在接收；原始数值和主动事件可记录。接触质量尚未验证，模型未配置。':g.error||'等待原厂共享采集与独立采集端连接。'));
  const rows=[['中枢监听',g.enabled?'TLS :'+g.port:'尚未生成配对文件'],['采集端',g.connected?'身份认证已连接':'未连接'],['实际数据通路',src.transport_label||'等待数据'],['SDK参与状态',`${health.sdk_state||'尚无回报'}${g.connected?'':' · 最后回报'}`],['采集端写盘',`${health.disk_state||'未知'} · 排队 ${health.queue_blocks??'未知'} 块`],['采集端错误',health.error||'无已上报错误；未知状态不作通过'],['实际取数',src.fresh?`${d.sampling_rate} Hz · ${d.channels.length} 列`:'无新鲜数据'],['中枢保存',`${g.durable_blocks||0} 块已持久化`],['历史补传',`${g.backfill_blocks||0} 块；不会刷新实时状态`],['接触质量','未测量 / 未验证'],['新模型','未配置；动作停止']];
  $('checkpoints').replaceChildren();for(const [k,v]of rows){item($('checkpoints'),'dt',k);item($('checkpoints'),'dd',v);}
  const columns=d.channels||[],sig=JSON.stringify(columns.map(c=>c.id));
  if(sig!==columnsSignature){columnsSignature=sig;$('page').replaceChildren();for(let i=0;i<columns.length;i+=8){const o=item($('page'),'option',`${i+1}–${Math.min(i+8,columns.length)} 列`);o.value=i;}$('channels').replaceChildren();for(const c of columns){const box=item($('channels'),'div','');box.className='gna-channel';item(box,'strong',c.name);item(box,'small',`${c.kind} · ${c.unit}`);item(box,'small',c.kind==='EEG'?'接触质量未测量':'附加信号原值保留');}}
  text('recordInfo',s.recording.active?`正在记录：${s.recording.name} · ${s.recording.samples}行`:`记录已关闭。${g.last_session?'最近保存 '+g.last_session.samples+' 行；关闭清单已生成。':''}`);
  text('events',(s.events||[]).slice(0,8).map(e=>`${e.time} ${e.message}`).join('\n'));
  $('recordStart').disabled=!src.fresh||s.recording.active||src.source_kind!=='gtec_live';$('recordStop').disabled=!s.recording.active;
  draw();
 }
 function draw(){const canvas=$('wave'),r=canvas.getBoundingClientRect(),ratio=devicePixelRatio||1,w=r.width,h=r.height;canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.fillStyle='#fff';ctx.fillRect(0,0,w,h);const cols=state?.gnautilus?.descriptor?.channels||[],data=state?.waveforms?.raw||[],start=Number($('page').value)||0,n=Math.min(8,cols.length-start);if(n<=0)return;const row=(h-20)/n;
  for(let i=0;i<n;i++){const c=start+i,values=data.map(x=>x[c]).filter(Number.isFinite),scale=Math.max(1,...values.map(Math.abs)),y=10+(i+.5)*row;ctx.strokeStyle='#e5e9ec';ctx.beginPath();ctx.moveTo(80,y);ctx.lineTo(w,y);ctx.stroke();ctx.fillStyle='#52616e';ctx.font='11px sans-serif';ctx.fillText(cols[c].name,0,y);ctx.fillText(cols[c].unit,0,y+14);ctx.strokeStyle=state.source.fresh?'#146c74':'#9ca8af';ctx.beginPath();let active=false;for(let j=0;j<data.length;j++){const v=data[j][c];if(!Number.isFinite(v)){active=false;continue;}const x=80+j/Math.max(1,data.length-1)*(w-90),yy=y-v/scale*row*.35;if(active)ctx.lineTo(x,yy);else ctx.moveTo(x,yy);active=true;}ctx.stroke();}}
 async function poll(){if(polling)return;polling=true;try{render(await api('/api/status'));}catch(e){text('notice','中枢状态不可用：'+e.message);if(state)state.source.fresh=false;draw();}finally{polling=false;}}
 $('page').onchange=draw;window.addEventListener('resize',draw);setInterval(poll,400);poll();
})();
