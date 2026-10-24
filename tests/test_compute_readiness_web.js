'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require('../outputs/frontend-redesign/qa-runtime/node_modules/linkedom');
async function boot(running){
 const {window,document}=parseHTML('<html><body data-device-transition="gnautilus"><div id="liveInference"></div></body></html>');const writes=[];
 const state={schema:'gnautilus.intent.v1',mode:'LIVE',inference_state:running?'waiting_input':'stopped',inference_enabled:running,control:{enabled:false},fresh:false,readiness:{can_start:false,code:'offline_model',message:'已加载离线分类器；无实时导航合同'}};
 const ctx=vm.createContext({window,document,performance,console,AbortSignal,setInterval(){},fetch:async(url,opts)=>{if(opts?.method==='POST')writes.push(url);return{ok:true,json:async()=>state}}});
 vm.runInContext(fs.readFileSync('hub/web/intent.js','utf8'),ctx);for(let i=0;i<8;i++)await new Promise(setImmediate);
 return {document,writes};
}
(async()=>{
 const stopped=await boot(false),button=stopped.document.getElementById('intentStart');
 assert.equal(button.disabled,true,'offline classifier must explain why live start is unavailable');
 assert.match(stopped.document.getElementById('intentDetail').textContent,/无实时导航合同/);assert.equal(stopped.writes.length,0);
 const running=await boot(true);assert.equal(running.document.getElementById('intentStart').disabled,false,'a requested running mode must retain its manual stop even if readiness is lost');assert.equal(running.writes.length,0);
 console.log('PASS: inference readiness clearly blocks offline live-start while preserving STOP and zero implicit writes');
})().catch(e=>{console.error(e);process.exitCode=1});
