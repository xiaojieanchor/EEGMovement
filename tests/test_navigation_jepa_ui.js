'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{parseHTML}=require('linkedom');
async function main(){
 const {window,document}=parseHTML('<html><body data-device-transition="gnautilus"><div id="liveInference"></div></body></html>');
 const fixture={schema:'gnautilus.intent.v1',mode:'LIVE',inference_enabled:false,fresh:false,generation:1,control:{enabled:false},
  health:{model_id:'fixture',runtime_schema:'gnautilus.causal_jepa_stream.v1',algorithm_title:'JEPA',evidence_ms:1500,hop_ms:100,labels:['REST','FORWARD','BACKWARD','LEFT','RIGHT'],
   decision_policy:{aligned_threshold:.7,conservative_threshold:.8},training_fit:{balanced_accuracy:.7},development:{balanced_accuracy:.4},internal_test:{balanced_accuracy:.3,coverage:.2,accepted_macro_recall:.1}},readiness:{can_start:true}};
 const ctx=vm.createContext({window,document,performance,Number,Array,JSON,AbortSignal,console,setInterval:()=>{},fetch:async()=>({ok:true,json:async()=>fixture})});
 vm.runInContext(fs.readFileSync('hub/web/intent.js','utf8'),ctx);for(let i=0;i<5;i++)await new Promise(setImmediate);
 const $=id=>document.getElementById(id);
 assert.equal($('intentApplyThreshold').disabled,true);assert.equal($('intentActiveThreshold').disabled,true);
 assert.match($('intentActiveLabel').textContent,/同向/);assert.match($('intentDirectionLabel').textContent,/回中/);
 assert.match($('intentValidation').textContent,/同次佩戴内部评估/);assert.match($('intentValidation').textContent,/接纳覆盖/);
 assert.ok(!$('intentValidation').textContent.includes('跨记录开发集'));
 assert.match($('intentModel').textContent,/预热 1500/);assert.match($('intentActive').textContent,/分数/);
 console.log('PASS: streaming scope, frozen thresholds and score semantics remain explicit');
}
main().catch(e=>{console.error(e);process.exitCode=1});
