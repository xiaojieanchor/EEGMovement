'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const C=require('../hub/web/immersive-core.js'),{parseHTML}=require('linkedom');
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate)};
const descriptor={sampling_rate:250,channels:Array.from({length:32},(_,i)=>({name:'C'+i,kind:'EEG',unit:'uV',physical_index:i})),configuration:{SamplingRate:250}};
function state(){return {schema:'gnautilus.immersive.v1',mode:'OFFLINE_REPLAY',entry_eligible:true,blockers:[],can_start:true,hub:{uptime_s:10},source:{fresh:true,age_ms:0,source_epoch:1,configured_rate:250},descriptor,
 quality:{fresh:true,valid:true,age_ms:0,good_channels:32,bad_channels:0,unknown_channels:0,window_s:1.5,channels:descriptor.channels.map(c=>({...c,valid:true,reasons:[],peak_to_peak_uv:20,rms_uv:7,line_ratio:0}))},
 model:{model_id:'m',channels:descriptor.channels.map(c=>c.name)},models:[{id:'m',title:'Fixture',runtime_accepted:true},{id:'offline',title:'Offline',runtime_accepted:false}],compute:{fresh:true},
 intent:{inference_enabled:false,fresh:false,control:{enabled:false}},vr:{ready:true,target:{receiver_id:'v',device_id:'dev',online:true,telemetry_age_ms:0,applied_seq:4,telemetry:{actual_speed:0,actual_yaw_rate:0,state:'STOP',mode:'LIVE'}},clients:[{receiver_id:'v',online:true,telemetry:{}}],mirror:{mirror_fresh:false}}};}
async function boot({reducedMotion=true,mobile=false}={}){
 const {window,document}=parseHTML(fs.readFileSync('hub/web/lab.html','utf8'));let now=1000;const s=state(),writes=[],timers=[],animations=[],storage=new Map();
 Object.defineProperty(window.HTMLSelectElement.prototype,'options',{get(){return [...this.querySelectorAll('option')]}});
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.getAttribute('data-value')||this.firstElementChild?.value||''},set(v){this.setAttribute('data-value',v)}});
 window.HTMLElement.prototype.getBoundingClientRect=()=>({x:0,y:0,width:600,height:300});window.HTMLElement.prototype.focus=function(){};window.HTMLElement.prototype.animate=function(frames,options){animations.push({id:this.id,frames,options});return {cancel(){},finished:Promise.resolve()}};
 const drawing=new Proxy({createLinearGradient:()=>({addColorStop(){}})},{get:(o,k)=>o[k]||(()=>{})});window.HTMLCanvasElement.prototype.getContext=()=>drawing;
 window.HTMLElement.prototype.requestFullscreen=async()=>{throw Error('fixture denial')};
 document.exitFullscreen=async()=>{};
 const fetch=async(url,o={})=>{if(s.networkFails)throw TypeError('Failed to fetch');if(o.method==='POST'){writes.push({url,data:JSON.parse(o.body)});if(url==='/api/intent/start')s.intent.inference_enabled=true;if(url==='/api/intent/stop'){if(s.stopFails)return {ok:false,json:async()=>({error:'fixture stop failure'})};s.intent.inference_enabled=false;s.intent.control.enabled=false}return {ok:true,json:async()=>({})}}if(url.startsWith('/api/immersive/signal'))return {ok:true,json:async()=>({reset:true,fresh:true,sample_index_start:0,sample_index_end:25,source_epoch:1,descriptor,sampling_rate:250,samples:Array.from({length:25},()=>Array(32).fill(1))})};return {ok:true,json:async()=>JSON.parse(JSON.stringify(s))}};
 const ctx=vm.createContext({window,document,performance:{now:()=>now},console,AbortSignal,Date,devicePixelRatio:1,EEGImmersiveCore:C,EEGUI:require('../hub/web/ui-state.js'),EEGTelemetryScene:{create:()=>({render(){}})},matchMedia:query=>({matches:query.includes('prefers-reduced-motion')?reducedMotion:query.includes('max-width')?mobile:false}),localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},setInterval:(fn,ms)=>timers.push({fn,ms}),requestAnimationFrame:fn=>{},fetch});
 vm.runInContext(fs.readFileSync('hub/web/immersive.js','utf8'),ctx);await flush();
 return {window,document,s,writes,animations,storage,$:id=>document.getElementById(id),advance(ms){now+=ms},async tick(){for(const t of timers)if(t.ms===200)t.fn();await flush()},async expire(){for(const t of timers)if(t.ms===100)t.fn();await flush()},async click(id){document.getElementById(id).click();await flush()}};
}
test('four-grid layout, swap focus, deterministic sidebar order',()=>{const l=new C.Layout();assert.deepEqual(l.areas(),[['information','vr'],['signal','output']]);l.select('signal');assert.deepEqual(l.areas(),[['signal','information'],['signal','vr'],['signal','output']]);l.select('vr');assert.equal(l.focus,'vr');l.reset();assert.equal(l.focus,null)});
test('incremental signals cannot silently join gaps; expired view keeps a labelled historical trace',()=>{const b=new C.SignalBuffer();b.ingest({fresh:true,reset:true,source_epoch:1,sample_index_start:0,sample_index_end:2,sampling_rate:250,samples:[[1],[2]]});assert.throws(()=>b.ingest({fresh:true,reset:false,source_epoch:1,sample_index_start:3,sample_index_end:4,samples:[[3]]}),/discontinuity/);b.ingest({fresh:false});assert.equal(b.rows.length,2);assert.equal(b.epoch,1);assert.equal(b.fresh,false)});
test('view expiry clears all live outputs without converting them to zero scores',()=>{const s=state();s.model.model_id='m';s.intent={inference_enabled:true,fresh:true,age_ms:0,latest:{model_id:'m',input_age_ms:0}};assert.ok(C.view(s,10).output);assert.equal(C.view(s,300).output,null);assert.equal(C.view(s,600).good,0)});
test('actual product DOM has no implicit start, fullscreen fallback and focus preserves buffers',async()=>{const b=await boot();assert.equal(b.writes.length,0);await b.click('imsEnter');assert.equal(b.window.EEGImmersive.active,true);assert.equal(b.$('imsGrid').children.length,4);assert.match(b.$('imsActionFeedback').textContent,/铺满窗口/);assert.equal(b.writes.length,0);const signal=b.document.querySelector('[data-panel="signal"] .ims-head');signal.click();assert.equal(b.$('imsGrid').dataset.focused,'signal');await b.click('imsWaveAll');assert.equal(b.$('imsGrid').dataset.focused,'signal');await b.click('imsGridReset');assert.equal(b.$('imsGrid').hasAttribute('data-focused'),false);assert.equal(b.$('imsChannelGrid').children.length,32);assert.equal(b.$('imsModels').options.find(o=>o.value==='offline').disabled,true)});
test('start and model switch are explicit and require-stopped is sent; exit fails visibly',async()=>{const b=await boot();await b.click('imsEnter');await b.click('imsInference');assert.deepEqual(b.writes[0],{url:'/api/intent/start',data:{immersive:true,receiver_id:'v'}});await b.tick();assert.equal(b.$('imsLoadModel').disabled,true);b.s.stopFails=true;await b.click('imsExit');assert.equal(b.window.EEGImmersive.active,true);assert.match(b.$('imsActionFeedback').textContent,/fixture stop failure/);b.s.stopFails=false;await b.click('imsExit');assert.equal(b.window.EEGImmersive.active,false);assert.equal(b.$('immersiveWorkspace').hidden,true)});
test('outage keeps layout and clears current values while STOP remains enabled',async()=>{const b=await boot();await b.click('imsEnter');b.advance(900);for(const t of [])void t;await b.tick();b.s.entry_eligible=false;b.s.source.fresh=false;b.s.vr.target.online=false;await b.tick();assert.equal(b.window.EEGImmersive.active,true);assert.equal(b.$('imsMotion').disabled,true);assert.equal(b.$('imsStop').disabled,false);assert.equal(b.$('imsSpeed').textContent,'0.00');assert.match(b.$('imsVrNote').textContent,/上次|过期/)});

function prediction(sample=250,direction=1){return {model_id:'m',source_epoch:1,sample_index_end:sample,input_age_ms:5,probabilities:[.1,.6,.1,.1,.1],eye_scores:[.1,.6,.1,.1,.1],activity_score:.9,conditional_direction_scores:[.7,.1,.1,.1],prediction:['REST','FORWARD','BACKWARD','LEFT','RIGHT'][direction],navigation_decision:{status:'ACTIVE',direction,reason:'accepted',effective_thresholds:[]},observation:{state_duration_s:1,cue_duration_s:1,eye_events:[]}}}
test('brief expiry keeps scores and judgment in place but labels them historical and disables motion',async()=>{
 const b=await boot();b.s.mode='LIVE';b.s.intent={inference_enabled:true,fresh:true,age_ms:0,latest:prediction(),control:{enabled:false}};await b.tick();await b.click('imsEnter');
 const value=b.$('imsScore1').textContent,channel=b.$('imsChannelGrid').firstElementChild;
 b.advance(280);await b.expire();
 assert.equal(b.$('imsScore1').textContent,value,'must not clear numeric panels between receipts');assert.match(b.$('imsOutputValidity').textContent,/过期|等待/);
 assert.equal(b.$('imsMotion').disabled,true);assert.equal(b.$('imsChannelGrid').firstElementChild,channel);
 b.s.intent.fresh=false;b.s.intent.latest=null;await b.tick();assert.equal(b.$('imsScore1').textContent,value);
 b.s.source.source_epoch=2;await b.tick();assert.equal(b.$('imsScore1').textContent,'—','state from another source epoch must not be retained');
});
test('judgment animation runs only on a changed decision, never on periodic rendering',async()=>{
 const b=await boot({reducedMotion:false});b.s.intent={inference_enabled:true,fresh:true,age_ms:0,latest:prediction(),control:{enabled:false}};await b.tick();await b.click('imsEnter');b.animations.length=0;
 await b.tick();assert.equal(b.animations.filter(x=>x.id==='imsDecisionCurrent').length,0);
 b.s.intent.latest=prediction(275,4);await b.tick();assert.equal(b.$('imsDecisionCurrent').textContent,'右转');assert.ok(b.animations.some(x=>x.id==='imsDecisionCurrent'));
 const count=b.animations.length;await b.tick();assert.equal(b.animations.length,count);
});
test('theme switches colors without re-entering, writing control requests, or changing the layout',async()=>{
 const b=await boot();await b.click('imsEnter');const target=b.document.querySelector('[data-panel="vr"] .ims-head');target.click();
 await b.click('imsTheme');assert.equal(b.$('immersiveWorkspace').dataset.theme,'light');assert.equal(b.$('imsGrid').dataset.focused,'vr');assert.equal(b.writes.length,0);assert.equal(b.storage.get('eegmovement.immersive.theme'),'light');
 await b.click('imsTheme');assert.equal(b.$('immersiveWorkspace').dataset.theme,'dark');
});
test('Chinese visible labels and animated layout preserve live panel nodes',async()=>{
 const b=await boot({reducedMotion:false});await b.click('imsEnter');const nodes=[...b.document.querySelectorAll('.ims-panel')];b.animations.length=0;
 nodes[3].querySelector('.ims-head').click();assert.ok(b.animations.length>=4);assert.ok(b.animations.every(x=>x.options.duration>=350));
 assert.doesNotMatch(b.$('immersiveWorkspace').textContent,/LINKS & CONTROL|UNKNOWN|32-CHANNEL SIGNAL|NAVIGATION & EYE STATE/);
 await b.click('imsGridReset');assert.deepEqual([...b.document.querySelectorAll('.ims-panel')],nodes);
});

test('receiver switch invalidates in-flight image before accepting the next status',async()=>{
 const b=await boot();b.s.vr.mirror={mirror_fresh:true,frame_age_ms:0,frame_seq:1};await b.tick();await b.click('imsEnter');const image=b.$('imsCamera'),oldLoad=image.onload;
 b.$('imsReceiver').value='different-device';const changing=b.$('imsReceiver').onchange();
 oldLoad();assert.equal(image.hidden,true,'old device image must stay hidden after target selection');await changing;await flush();
});
test('retained wave endpoint is explicitly historic, not the current time marker',()=>{
 const ui=require('../hub/web/ui-state.js'),labels=[],writes=[];
 const context=new Proxy({fillText:s=>labels.push(s),createLinearGradient:()=>({addColorStop(){}})},{get:(o,k)=>o[k]||(()=>{})});
 let width=600,height=300;const canvas={getBoundingClientRect:()=>({width:600,height:300}),getContext:()=>context,get width(){return width},set width(v){width=v;writes.push('width')},get height(){return height},set height(v){height=v;writes.push('height')}};
 const data={source:{device:'g.Nautilus',fresh:false},gnautilus:{descriptor},waveforms:{raw:Array.from({length:25},()=>Array(32).fill(1)),display_sfreq:250,seconds:3}};
 ui.drawSignal(canvas,data,0,'raw',{retainHistory:true});ui.drawSignal(canvas,data,0,'raw',{retainHistory:true});
 assert.equal(writes.length,0,'unchanged canvas backing size must not be reset on each render');
 assert.ok(labels.includes('上次样本'));assert.ok(!labels.includes('现在'));
});

test('narrow display uses one column for both equal and focused arrangements',async()=>{
 const b=await boot({mobile:true});await b.click('imsEnter');assert.equal(b.$('imsGrid').style.gridTemplateAreas,'"information" "signal" "vr" "output"');
 b.document.querySelector('[data-panel="vr"] .ims-head').click();assert.equal(b.$('imsGrid').style.gridTemplateAreas,'"vr" "information" "signal" "output"');
});
test('only explicitly reduced motion skips decision and layout animation',async()=>{
 const b=await boot({reducedMotion:true});b.s.intent={inference_enabled:true,fresh:true,age_ms:0,latest:prediction(),control:{enabled:false}};await b.tick();await b.click('imsEnter');
 b.document.querySelector('[data-panel="vr"] .ims-head').click();b.s.intent.latest=prediction(275,2);await b.tick();assert.equal(b.animations.length,0);assert.equal(b.$('imsDecisionCurrent').textContent,'后退');
});

test('waveform freshness needs its own timely receipt and the same source epoch',()=>{
 const b=new C.SignalBuffer();b.ingest({fresh:true,reset:true,source_epoch:1,sample_index_start:0,sample_index_end:2,sampling_rate:250,samples:[[1],[2]]});
 const s={source:{source_epoch:1}};assert.equal(C.signalFresh(b,s,100),true);assert.equal(C.signalFresh(b,s,501),false);
 assert.equal(C.signalFresh(b,{source:{source_epoch:2}},20),false);assert.equal(b.rows.length,2,'historical trace remains available');
});


test('common network failures use Chinese feedback while keeping STOP available',async()=>{
 const b=await boot();await b.click('imsEnter');b.s.networkFails=true;
 await b.click('imsStop');
 assert.match(b.$('imsActionFeedback').textContent,/中枢连接失败/);
 assert.doesNotMatch(b.$('imsActionFeedback').textContent,/Failed to fetch/);
 assert.equal(b.$('imsStop').disabled,false);
});

test('quality wait has persistent explanation and a disabled action button without implicit writes',async()=>{
 const b=await boot();b.s.mode='LIVE';b.s.intent={inference_enabled:true,fresh:false,error:'Electrical quality rejected; hybrid state reset',control:{enabled:false}};
 b.s.quality={...b.s.quality,valid:false,state:'invalid',good_channels:0,bad_channels:32,channels:descriptor.channels.map(c=>({...c,valid:false,reasons:['flatline']}))};b.s.can_start=false;
 await b.tick();await b.click('imsEnter');
 assert.match(b.$('imsAlert').textContent,/推理已开启.*等待有效脑电/);assert.match(b.$('imsAlert').textContent,/32路通道变化幅度过低/);
 assert.equal(b.$('imsMotion').disabled,true);assert.equal(b.$('imsMotion').textContent,'等待有效信号');
 const text=b.$('imsAlert').textContent;await b.tick();await b.tick();assert.equal(b.$('imsAlert').textContent,text);assert.equal(b.writes.length,0);
 b.s.intent.control.enabled=true;await b.tick();assert.equal(b.$('imsMotion').getAttribute('aria-pressed'),'true');assert.equal(b.$('imsMotion').disabled,false);
});
