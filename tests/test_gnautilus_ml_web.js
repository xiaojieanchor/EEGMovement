'use strict';
// Isolated DOM only; deliberately no browser or real API/data.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require('linkedom');
async function main(){
 const {window,document}=parseHTML('<html><head></head><body data-device-transition="gnautilus"><div id="host"></div><div id="modelLibrary"></div></body></html>'),writes=[],timers=[];
 // linkedom exposes select.value as getter-only; browser selects support setting it.
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.getAttribute('data-fixture-value')||this.firstElementChild?.value||''},set(value){this.setAttribute('data-fixture-value',value)}});
 const job={id:'a'.repeat(32),state:'completed',attempt:2,spec:{algorithm:'csp_lda'},model_id:'b'.repeat(64)};
 const routes={
  '/api/ml/capabilities':{server_connected:true,algorithms:[{id:'csp_lda',title:'CSP'},{id:'cnn_gru',title:'CNN/GRU'}]},
  '/api/ml/local-records':{records:[]},'/api/ml/datasets':{datasets:[{id:'d'.repeat(64),title:'合成测试',records:[{role:'train',date:'2026-10-22',wearing_id:'wear',experiment_mode:'paired',channels:['C3','C4'],sampling_rate:128},{role:'development',reader:'gnautilus',channels:['C3','C4'],sampling_rate:128}]}]},
  '/api/training/jobs':{jobs:[job],fresh:true,server:{worker_connected:true}},'/api/ml/collection-profile':{experiment_mode:'motor_only'}
 };
 const version={id:job.model_id,version:'numeric-csp',experiment:'csp_lda',dataset_title:'模型来源合成记录',seed:17,completed:true,current:false,can_activate:true,weights_sha256:'c'.repeat(64),training:{ba:.9},development:{ba:.6,recall:{LEFT_HAND_MI:.7,RIGHT_HAND_MI:.5},selective:{coverage:.8,accuracy:.75}},availability_reason:'合成数值分类器',details:{selected_epoch:9},dataset_sha256:'d'.repeat(64),class_labels:['LEFT_HAND_MI','RIGHT_HAND_MI'],channels:['C3','C4'],sampling_rate:128,window_s:1.5,evaluation_stage:'public_method_validation'};
 routes['/api/models']={schema:'gnautilus.models.v1',groups:[{id:'csp_lda',title:'CSP数值分类器',versions:[version]}],total_versions:1,remote_fresh:true,server_connected:true,refreshed_at:Date.now()/1000};
 const ctx=vm.createContext({window,document,console,performance,crypto:require('node:crypto').webcrypto,AbortSignal,Date,JSON,Number,CustomEvent:window.CustomEvent,setInterval:fn=>timers.push(fn),setTimeout:fn=>timers.push(fn),fetch:async(url,options)=>{
  if(options.method==='POST')writes.push({url,body:JSON.parse(options.body)});
  if(options.method==='POST'&&url==='/api/models/activate'){version.current=true;routes['/api/models'].current_model_id=job.model_id;return {ok:true,json:async()=>routes['/api/models']}}
  return {ok:true,json:async()=>url.includes('/events?')?{events:[{seq:1,utc:Date.now()/1000,message:'registered',data:{model_id:job.model_id}}]}:options.method==='POST'&&url==='/api/training/jobs'?job:routes[url]||{}};
 }});
 vm.runInContext(fs.readFileSync('hub/web/gnautilus-ml.js','utf8'),ctx);window.GnautilusTraining.mount(document.getElementById('host'));
 for(let i=0;i<15;i++)await new Promise(setImmediate);
 const $=id=>document.getElementById(id);
 assert.equal(writes.length,0,'opening workbench cannot launch a task');
 assert.match($('gmDataset').textContent,/合成测试/,'already-connected server datasets load on page entry');
 assert.match($('gmJobs').textContent,/完成并已收录/);assert.match($('gmJobs').textContent,/bbbbbbbbbbbb/);
 assert.equal($('gmCaptureMode').value,'motor_only');
 assert.equal($('gmMode').firstElementChild.value,'navigation_mi_gaze');
 assert.match(document.getElementById('host').textContent,/运动想象为主/);
 assert.match(document.getElementById('host').textContent,/回中/);
 const fixtureRecord=routes['/api/ml/datasets'].datasets[0].records[0];routes['/api/ml/datasets'].datasets[0].records=Array.from({length:274},(_,i)=>({...fixtureRecord,wearing_id:'wear-'+i,role:i%2?'train':'development'}));
 $('gmDatasetsRefresh').click();for(let i=0;i<10;i++)await new Promise(setImmediate);
 assert.match($('gmDatasetInfo').textContent,/共274段/);assert.match($('gwRecordDetails').textContent,/其余266段/);assert.ok($('gmDatasetInfo').textContent.length<1800,'large source lists cannot bury the training controls');
 $('gmDataset').firstElementChild.selected=true;$('gmAlgorithm').firstElementChild.selected=true;
 $('gmTrain').click();for(let i=0;i<15;i++)await new Promise(setImmediate);
 assert.equal(writes[0].url,'/api/training/jobs');assert.match(writes[0].body.request_key,/^[a-f0-9-]{36}$/);
 assert.match($('gmLogs').textContent,/registered/);assert.match($('gmChart').textContent,/无逐轮损失/);
 vm.runInContext(fs.readFileSync('hub/web/model-library.js','utf8'),ctx);
 for(let i=0;i<8;i++)await new Promise(setImmediate);
 assert.match($('mlCards').textContent,/CSP数值分类器/);assert.match($('mlCards').textContent,/60.0%/);
 assert.match($('mlCards').textContent,/9入选轮次/);assert.match($('mlCards').textContent,/LEFT_HAND_MI/);assert.match($('mlCards').textContent,/公开方法开发验证/);
 assert.match($('mlCards').querySelector('option').textContent,/模型来源合成记录.*seed 17/);
 assert.match($('mlCards').textContent,/分类器接纳覆盖/);
 assert.ok($('mlSearch'),'model versions need searchable filters');
 $('mlSearch').value='does-not-exist';$('mlSearch').dispatchEvent(new window.Event('input'));
 assert.equal($('mlCards').querySelectorAll('.ml-card').length,0,'search removes unmatched model versions');
 $('mlSearch').value='';$('mlSearch').dispatchEvent(new window.Event('input'));
 assert.equal($('mlCards').querySelectorAll('.ml-card').length,1);
 assert.match($('mlCards').textContent,/LEFT_HAND_MI 70.0%/);
 assert.equal($('mlCurrent').textContent,'尚未手动选择模型');
 document.querySelector('.ml-activate').click();for(let i=0;i<10;i++)await new Promise(setImmediate);
 assert.equal(writes.at(-1).url,'/api/models/activate');assert.equal(writes.at(-1).body.id,job.model_id);
 assert.equal($('mlCurrent').textContent,job.model_id);assert.ok(!writes.some(w=>w.url.includes('/intent/')),'model selection must not start inference or motion');
 console.log('PASS: isolated training DOM, persistent completed job/model identity, initial datasets, explicit submit, cursor logs');
}
main().catch(e=>{console.error(e);process.exitCode=1});
