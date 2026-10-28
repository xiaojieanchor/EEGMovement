'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{parseHTML}=require('linkedom');
const flush=async()=>{for(let i=0;i<20;i++)await new Promise(setImmediate);};
async function boot(options={}){
 const html=fs.readFileSync('hub/web/ocular-annotations.html','utf8'),{window,document}=parseHTML(html),writes=[],storage=new Map();
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.getAttribute('data-value')||this.firstElementChild?.value||'';},set(v){this.setAttribute('data-value',String(v));}});
 const tids=['a'.repeat(64),'b'.repeat(64)],trials=tids.map((trial_id,i)=>({trial_id,split:i?'test':'train',label:i?'LEFT':'RIGHT',duration_s:12,task_start:1000,task_end:4000,windows:{onset:{start:1000,end:2250},offset:{start:2750,end:4000}},default_directions:i?{onset:'LEFT',offset:'RIGHT'}:{onset:'RIGHT',offset:'LEFT'},waveform_sha256:'c'.repeat(64),annotation:{revision:0,phases:{onset:{status:'pending'},offset:{status:'pending'}}}}));
 const catalog={dataset_id:'d'.repeat(64),trials,channels:['Fp1','Fp2','AF3','AF4','F7','F8','F3','F4',...Array.from({length:24},(_,i)=>'C'+i)],sampling_rate:250};
 const wave=q=>({metadata:q,dataset_id:catalog.dataset_id,annotation:q.annotation,channels:catalog.channels,sampling_rate:250,waveform:{reference_names:['H1','H2','H3','H4','V'],windows:Object.fromEntries(Object.entries(q.windows).map(([k,w])=>[k,{...w,values:Array.from({length:32},()=>Array(1250).fill(0)),references:Array.from({length:5},()=>Array(1250).fill(0))}]))}});
 const ctx=new Proxy({}, {get:(t,k)=>k==='measureText'?()=>({width:30}):()=>{},set:()=>true});window.HTMLCanvasElement.prototype.getContext=()=>ctx;window.HTMLElement.prototype.getBoundingClientRect=()=>({left:100,top:0,width:600,height:600});window.HTMLElement.prototype.setPointerCapture=()=>{};window.HTMLElement.prototype.releasePointerCapture=()=>{};
 const fetch=async(url,opts={})=>{
  if(opts.method==='POST'){const data=JSON.parse(opts.body);writes.push(data);if(options.save)return options.save(data);return {ok:true,json:async()=>({revision:data.revision+1,phases:data.phases})};}
  if(url==='/api/ocular-annotations')return {ok:true,json:async()=>catalog};
  const q=trials.find(x=>url.endsWith(x.trial_id));if(options.trial)return options.trial(q,wave(q));return {ok:true,json:async()=>wave(q)};
 };
 const sandbox={window,document,console,fetch,URL,Blob,devicePixelRatio:1,requestAnimationFrame:fn=>{fn();return 1;},ResizeObserver:class{observe(){}},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},setTimeout,clearTimeout};sandbox.globalThis=sandbox;window.OcularAnnotationCore=require('../hub/web/ocular-annotations-core.js');
 vm.runInNewContext(fs.readFileSync('hub/web/ocular-annotations.js','utf8'),sandbox);await flush();
 const $=id=>document.getElementById(id),pointer=(el,type,x)=>{const e=new window.Event(type);Object.defineProperties(e,{clientX:{value:x},button:{value:0},pointerId:{value:1}});el.dispatchEvent(e);};
 return {window,document,$,writes,storage,catalog,trials,pointer};
}
test('entry shows raw frontal rows and direction defaults without creating annotations',async()=>{
 const b=await boot();assert.equal(b.writes.length,0);assert.equal(b.$('oa-onset-direction').value,'RIGHT');assert.equal(b.$('oa-offset-direction').value,'LEFT');assert.equal(b.$('oa-onset-start').value,'');assert.match(b.$('oa-onset-canvas').getAttribute('aria-label'),/Fp1.*F3/);assert.equal(b.$('oaTrialList').children.length,2);
});
test('reverse drag selects shared time region and save carries absolute source identity',async()=>{
 const b=await boot(),c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',420);b.pointer(c,'pointermove',220);b.pointer(c,'pointerup',220);
 assert.notEqual(b.$('oa-onset-start').value,'');b.$('oaSave').click();await flush();assert.equal(b.writes.length,1);const p=b.writes[0];assert.equal(p.dataset_id,'d'.repeat(64));assert.equal(p.phases.onset.direction,'RIGHT');assert.ok(p.phases.onset.sample_start<p.phases.onset.sample_end);assert.equal(p.phases.offset.status,'pending');assert.match(b.$('oaSaveStatus').textContent,/已保存/);
});
test('save error retains the human range and does not advance trial',async()=>{
 const b=await boot({save:async()=>({ok:false,status:409,json:async()=>({error:'版本冲突'})})});const c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);b.$('oaSaveNext').click();await flush();assert.equal(b.$('oa-onset-start').value!=='',true);assert.match(b.$('oaSaveStatus').textContent,/版本冲突/);assert.equal(b.writes[0].trial_id,'a'.repeat(64));
});
test('switching trial keeps an unsaved manual draft and restores it',async()=>{
 const b=await boot(),c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);const start=b.$('oa-onset-start').value;
 b.$('oaNext').click();await flush();assert.equal(b.$('oa-onset-direction').value,'LEFT');b.$('oaPrevious').click();await flush();assert.equal(b.$('oa-onset-start').value,start);assert.match(b.$('oaSaveStatus').textContent,/草稿/);assert.equal(b.writes.length,0);
});
test('no-event choice clears ranges and does not fabricate REST labels',async()=>{
 const b=await boot();b.document.querySelector('[data-phase="onset"][data-outcome="absent"]').click();b.$('oaSave').click();await flush();assert.equal(b.writes[0].phases.onset.status,'absent');assert.equal(b.writes[0].phases.onset.sample_start,undefined);assert.equal(b.writes[0].phases.onset.direction,undefined);
});
test('late prior-trial response cannot replace the trial selected afterwards',async()=>{
 let release;const b=await boot({trial:(q,wave)=>q.trial_id.startsWith('b')?new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>wave});}):Promise.resolve({ok:true,json:async()=>wave})});
 b.$('oaNext').click();await flush();b.document.querySelector('[data-trial="'+('a'.repeat(64))+'"]').click();await flush();release();await flush();assert.match(b.$('oaTrialTitle').textContent,/右转/);assert.equal(b.$('oa-onset-direction').value,'RIGHT');
});
test('all 32 and difference rows are available without changing a human interval',async()=>{
 const b=await boot(),c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);const first=b.$('oa-onset-start').value;
 b.$('oaChannels').value='all';b.$('oaChannels').dispatchEvent(new b.window.Event('change'));b.$('oaReferences').checked=true;b.$('oaReferences').dispatchEvent(new b.window.Event('change'));
 assert.match(c.getAttribute('aria-label'),/C23.*H1/);assert.equal(b.$('oa-onset-start').value,first);assert.equal(b.writes.length,0);
});
test('shared studio navigation includes the new page and keeps existing routes',async()=>{
 const b=await boot();const sandbox={document:b.document,window:b.window,location:new URL('http://127.0.0.1:8765/annotations'),URLSearchParams,MutationObserver:class{observe(){}},ResizeObserver:class{observe(){}},matchMedia:()=>({matches:false,addEventListener(){}}),requestAnimationFrame(){},performance,console};sandbox.globalThis=sandbox;
 vm.runInNewContext(fs.readFileSync('hub/web/ui-state.js','utf8'),sandbox);b.document.dispatchEvent(new b.window.Event('DOMContentLoaded'));
 assert.equal(b.document.body.dataset.page,'annotations');assert.equal(b.document.querySelector('.studio-primary a[aria-current="page"]').getAttribute('href'),'/annotations');
 for(const path of ['/','/lab','/training','/downloads'])assert.ok(b.document.querySelector('.studio-primary a[href="'+path+'"]'));
});
test('saving during an unfinished drag waits for pointer release and never drops the newer range',async()=>{
 let finish;const b=await boot({save:data=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({revision:1,phases:data.phases})});})}),c=b.$('oa-onset-canvas');
 b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);b.pointer(c,'pointerdown',350);b.pointer(c,'pointermove',500);
 const key=new b.window.Event('keydown');Object.defineProperties(key,{ctrlKey:{value:true},key:{value:'Enter'}});b.document.dispatchEvent(key);await flush();assert.equal(b.writes.length,0);assert.match(b.$('oaSaveStatus').textContent,/松开/);
 b.pointer(c,'pointerup',500);b.$('oaSave').click();await flush();const sent=b.writes[0].phases.onset.sample_start;finish();await flush();assert.equal(Math.round(Number(b.$('oa-onset-start').value)*250)+1000,sent);assert.equal(b.$('oaSave').disabled,true);
});
test('a newer detail receipt updates the directory after leaving that trial',async()=>{
 const b=await boot({trial:(q,wave)=>Promise.resolve({ok:true,json:async()=>q.trial_id.startsWith('b')?{...wave,annotation:{revision:1,phases:{onset:{status:'absent'},offset:{status:'absent'}}}}:wave})});
 b.$('oaNext').click();await flush();assert.match(b.$('oaProgress').textContent,/1 \/ 2/);b.$('oaPrevious').click();await flush();assert.match(b.$('oaProgress').textContent,/1 \/ 2/);
});
test('cleared direction stays visibly unspecified and is never silently replaced by the default',async()=>{
 const b=await boot(),c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);b.$('oa-onset-direction').value='';b.$('oa-onset-direction').dispatchEvent(new b.window.Event('change'));assert.equal(b.$('oa-onset-direction').value,'');b.$('oaSave').click();await flush();assert.equal(b.writes.length,0);
});
test('invalid edited endpoints cannot silently save an older valid range',async()=>{
 const b=await boot(),c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);b.$('oa-onset-end').value='-1';b.$('oa-onset-end').dispatchEvent(new b.window.Event('change'));b.$('oaSave').click();await flush();assert.equal(b.writes.length,0);assert.match(b.$('oaSaveStatus').textContent,/起止|范围|端点/);
});
test('clearing both endpoints of a marked interval cannot save its stale hidden range',async()=>{
 const b=await boot(),c=b.$('oa-onset-canvas');b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);
 for(const key of ['start','end']){b.$('oa-onset-'+key).value='';b.$('oa-onset-'+key).dispatchEvent(new b.window.Event('change'));}
 b.$('oaSave').click();await flush();assert.equal(b.writes.length,0);assert.match(b.$('oaSaveStatus').textContent,/起止|范围|端点/);
});
test('a stale local draft never hides a newer completed server revision in the directory',async()=>{
 let newer=false;const b=await boot({trial:(q,wave)=>Promise.resolve({ok:true,json:async()=>newer&&q.trial_id.startsWith('a')?{...wave,annotation:{revision:1,phases:{onset:{status:'absent'},offset:{status:'absent'}}}}:wave})}),c=b.$('oa-onset-canvas');
 b.pointer(c,'pointerdown',220);b.pointer(c,'pointerup',300);b.$('oaNext').click();await flush();newer=true;b.$('oaPrevious').click();await flush();b.$('oaNext').click();await flush();assert.match(b.$('oaProgress').textContent,/1 \/ 2/);
});
