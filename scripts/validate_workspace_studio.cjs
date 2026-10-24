'use strict';
// DOM integration checks only. This does not launch or control a browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {parseHTML}=require('linkedom');
const results=[];
function setup(page,url){
 const {window,document}=parseHTML(fs.readFileSync('hub/web/'+page+'.html','utf8'));
 const calls=[],timers=[],frames=[];
 const context=vm.createContext({window,document,location:new URL(url,'http://127.0.0.1:8765'),URLSearchParams,performance:{now:()=>100},MutationObserver:class{observe(){}},ResizeObserver:class{observe(){}},matchMedia:()=>({matches:true}),requestAnimationFrame:fn=>frames.push(fn),setInterval:(fn,ms)=>{timers.push({fn,ms})},setTimeout:fn=>{},AbortSignal,fetch:async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>path==='/api/status'?{source:{mode:'live',fresh:false},quality:{},server:{},vr:{clients:[]}}:{fresh:false}}},Date,Math,Number,JSON,console});
 vm.runInContext(fs.readFileSync('hub/web/ui-state.js','utf8'),context);
 return {window,document,context,calls,timers,frames,mount:()=>window.EEGStudio.mount()};
}
for(const [page,url] of [['index','/'],['lab','/lab'],['training','/training'],['downloads','/downloads'],['index','/?view=architecture']]){
 const h=setup(page,url),ids=[...h.document.querySelectorAll('[id]')].map(el=>el.id),stop=h.document.getElementById('globalStop'),settings=h.document.getElementById('settings');
 let stopCalls=0,settingsCalls=0;if(stop)stop.onclick=()=>stopCalls++;if(settings)settings.onclick=()=>settingsCalls++;
 h.mount();
 for(const id of ids)assert.ok(h.document.getElementById(id),'Retained #'+id+' on '+url);
 assert.equal(h.document.querySelectorAll('.studio-rail').length,1);
 assert.equal(h.document.querySelectorAll('.studio-primary a').length,5);
 assert.equal(h.document.querySelectorAll('.studio-primary a[aria-current="page"]').length,1);
 if(stop){h.document.getElementById('globalStop').click();assert.equal(stopCalls,1);assert.equal(stop,h.document.getElementById('globalStop'));}
 if(settings){h.document.getElementById('settings').click();assert.equal(settingsCalls,1);assert.equal(settings,h.document.getElementById('settings'));}
 const menu=h.document.querySelector('.studio-menu');menu.click();assert.equal(menu.getAttribute('aria-expanded'),'true');assert.ok(h.document.body.classList.contains('menu-open'));menu.click();assert.equal(menu.getAttribute('aria-expanded'),'false');
 assert.equal(h.calls.some(c=>c.options?.method==='POST'),false,'boot must not start inference, recording, or motion');
 const allIds=[...h.document.querySelectorAll('[id]')].map(el=>el.id);assert.equal(new Set(allIds).size,allIds.length,'unique IDs on '+url);
 if(url.includes('architecture')){
  const components=[...h.document.querySelectorAll('.architecture-node')];assert.equal(components.length,6);
  components[3].click();assert.equal(h.document.querySelector('#inspectorTitle').textContent,'意图模型推理');assert.equal(components.filter(b=>b.getAttribute('aria-pressed')==='true').length,1);
  assert.equal(h.document.querySelector('.inspector-action').getAttribute('href'),'/lab#liveInference');
 }
 if(page==='training')assert.ok(h.document.querySelector('.studio-history #personalV2'),'history remains accessible');
 results.push({page:url,passed:true,checks:['stable control identities','complete navigation','mobile menu toggle','unique IDs','read-only initialization',...(url.includes('architecture')?['component selection updates inspector']:[])]});
}
fs.mkdirSync('outputs/frontend-redesign',{recursive:true});fs.writeFileSync('outputs/frontend-redesign/dom-validation.json',JSON.stringify({state:'passed',checked_at:new Date().toISOString(),scope:'DOM integration; no browser or visual rendering',results},null,2));console.log(JSON.stringify(results,null,2));
