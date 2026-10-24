'use strict';
// Execute the real page scripts in a DOM emulator against read-only local APIs.
// No browser, desktop, hardware command, or control POST is used by this check.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
const {parseHTML}=require('linkedom');
const results=[];
const failures=[];
process.on('unhandledRejection',error=>failures.push(String(error.stack||error)));
async function check(name,route){
 const htmlResponse=await fetch(new URL(route,'http://127.0.0.1:8765'));assert.ok(htmlResponse.ok,'served page must be available');const html=await htmlResponse.text(),{window,document}=parseHTML(html),calls=[],pending=[],intervals=[],frames=[];
 const paint=new Proxy({measureText:s=>({width:String(s).length*7}),createLinearGradient:()=>({addColorStop(){}}),createRadialGradient:()=>({addColorStop(){}})}, {get:(target,key)=>key in target?target[key]:(()=>{})});
 window.HTMLCanvasElement.prototype.getContext=()=>paint;
 window.HTMLElement.prototype.getBoundingClientRect=()=>({x:0,y:0,left:0,right:600,top:0,bottom:350,width:600,height:350});
 window.HTMLElement.prototype.animate=()=>({cancel(){}});
 window.HTMLElement.prototype.focus=function(){document.activeElement=this};
 window.HTMLSelectElement.prototype.__defineSetter__('value',function(value){this.setAttribute('value',value)});
 window.HTMLSelectElement.prototype.__defineGetter__('value',function(){return this.getAttribute('value')||this.firstElementChild?.value||''});
 document.hasFocus=()=>true;window.devicePixelRatio=1;
 const context=vm.createContext({window,document,location:new URL(route,'http://127.0.0.1:8765'),URLSearchParams,MutationObserver:class{observe(){}},ResizeObserver:class{observe(){}},matchMedia:()=>({matches:true}),performance,crypto:webcrypto,requestAnimationFrame:fn=>frames.push(fn),cancelAnimationFrame(){},setInterval:(fn,ms)=>intervals.push({fn,ms}),setTimeout(){},clearTimeout(){},clearInterval(){},AbortSignal,queueMicrotask,Date,Math,Number,JSON,console,devicePixelRatio:1,fetch:(path,options)=>{
  assert.ok(!options?.method||options.method==='GET','Page boot attempted a mutation: '+path);
  calls.push(path);const task=fetch(new URL(path,'http://127.0.0.1:8765'),options);pending.push(task);return task;
 }});
 const files=[...html.matchAll(/<script[^>]*src="\/([^\"]+)"[^>]*>/g)].map(m=>m[1]);
 for(const file of files)vm.runInContext(fs.readFileSync('hub/web/'+file,'utf8'),context,{filename:file});
 document.dispatchEvent(new window.Event('DOMContentLoaded'));
 for(let i=0;i<12;i++){await Promise.allSettled(pending);await new Promise(resolve=>setImmediate(resolve));}
 const frameBatch=frames.splice(0);for(const frame of frameBatch)frame(performance.now());
 for(const timer of intervals.filter(t=>t.ms<=200))timer.fn();
 await Promise.allSettled(pending);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(failures.length,0,failures.join('\n'));
 if(name!=='vr-preview'){
  assert.ok(document.body.classList.contains('studio'));
  assert.equal(document.querySelectorAll('.studio-primary a').length,5);
 }
 if(name==='lab'&&document.body.dataset.deviceTransition==='gnautilus'){
  assert.equal(document.getElementById('intentStart').disabled,true);assert.equal(document.getElementById('inputTestTab').disabled,true);assert.equal(document.getElementById('labNotch').disabled,true);assert.ok(document.getElementById('inputSignal'));
 }else if(name==='lab'){
  assert.equal(document.querySelectorAll('.input-test-key').length,5);
  assert.equal(document.querySelectorAll('.intent-score').length,5);
  assert.ok(document.getElementById('inputLiveTab').getAttribute('aria-selected')==='true');
  let leakedArrowKeys=0;document.addEventListener('keydown',e=>{if(e.key.startsWith('Arrow'))leakedArrowKeys++});
  const key=(target,value)=>{target.focus();const event=new window.Event('keydown',{bubbles:true,cancelable:true});Object.defineProperty(event,'key',{value});target.dispatchEvent(event);};
  key(document.getElementById('inputLiveTab'),'ArrowRight');
  assert.equal(document.getElementById('inputTestTab').getAttribute('aria-selected'),'true');
  assert.equal(document.getElementById('inputTestView').hidden,false);
  assert.equal(leakedArrowKeys,0,'Arrow tab navigation must not reach EEG replay hotkeys');
  key(document.getElementById('inputTestTab'),'ArrowLeft');
  assert.equal(document.getElementById('inputLiveTab').getAttribute('aria-selected'),'true');
  assert.equal(document.getElementById('inputLiveView').hidden,false);
  document.getElementById('labRaw').click();assert.ok(document.getElementById('labRaw').classList.contains('selected'));
  document.getElementById('labFiltered').click();assert.ok(document.getElementById('labFiltered').classList.contains('selected'));
 }
 if(route.includes('architecture')){
  assert.equal(document.querySelectorAll('.architecture-node').length,6);
  assert.ok(document.getElementById('liveTag'),'Retain dashboard update target');
 }
 results.push({route,passed:true,scripts:files,read_requests:calls.length});
}
(async()=>{
 for(const [file,url] of [['index','/'],['lab','/lab'],['training','/training'],['downloads','/downloads'],['index','/?view=architecture'],['vr-preview','/vr-preview']])await check(file,url);
 fs.writeFileSync('outputs/frontend-redesign/boot-validation.json',JSON.stringify({state:'passed',scope:'Real scripts in DOM emulator with read-only local API requests; no visual layout validation',checked_at:new Date().toISOString(),results},null,2));console.log(JSON.stringify(results,null,2));
})().catch(error=>{console.error(error);process.exitCode=1});
