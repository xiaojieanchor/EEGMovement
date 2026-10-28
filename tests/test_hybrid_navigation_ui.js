'use strict';
// Isolated DOM fixture: no browser, real EEG, API writes or server state.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{parseHTML}=require('linkedom');
async function main(){
 const {window,document}=parseHTML('<html><body><div id="modelLibrary"></div></body></html>');let writes=0;
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return (this.querySelector('option[selected]')||this.firstElementChild)?.getAttribute('value')??''}});
 const row={id:'a'.repeat(64),runtime_schema:'gnautilus.hybrid_navigation.v1',runtime_accepted:false,completed:true,can_activate:true,channels:Array(32).fill('fixture'),sampling_rate:250,window_s:1.5,training:{ba:.7},development:{ba:.23},class_labels:['REST','FORWARD','BACKWARD','LEFT','RIGHT']};
 const fixture={schema:'gnautilus.models.v1',groups:[{id:'hybrid_navigation',title:'hybrid_navigation',versions:[row]}],total_versions:1,remote_fresh:true,server_connected:true};
 const ctx=vm.createContext({window,document,console,performance,Date,JSON,Number,CustomEvent:window.CustomEvent,AbortSignal,setInterval:()=>{},fetch:async(url,opts={})=>{writes+=opts.method==='POST';return {ok:true,json:async()=>fixture}}});
 vm.runInContext(fs.readFileSync('hub/web/model-library.js','utf8'),ctx);for(let i=0;i<6;i++)await new Promise(setImmediate);
 const text=document.getElementById('modelLibrary').textContent;assert.match(text,/联合导航与眼动状态模型/);assert.match(text,/全量拟合 BA/);assert.match(text,/分组回放 BA/);assert.match(text,/非独立评估/);assert.doesNotMatch(text,/内部测试 BA/);assert.equal(writes,0);
 console.log('PASS: full-data candidate labels cannot be shown as independent test scores; no action on page load');
}
main().catch(e=>{console.error(e);process.exitCode=1});
