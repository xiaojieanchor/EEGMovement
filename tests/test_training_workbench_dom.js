'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require('linkedom');
const flush=async()=>{for(let i=0;i<18;i++)await new Promise(setImmediate);};
function data(id,reader='malta_target_stationary'){return {id,title:'数据 '+id,prepared_window_s:1.5,records:['train','development'].map((role,i)=>({reader,role,subject:'S'+i,reference:'R',channels:['EOG_H','EOG_V'],sampling_rate:256}))};}
function job(id,dataset_id,state='completed'){return {id,state,updated:100,attempt:1,spec:{dataset_id,algorithm:'gaze_pipeline'},model_id:state==='completed'?'model-'+id:null};}
async function boot(options={}){
 const {window,document}=parseHTML('<html><body><span id="trainingSync"></span><button id="trainingRefresh"></button><div id="host"></div><section id="modelLibrary"></section></body></html>');
 Object.defineProperty(window.HTMLSelectElement.prototype,'value',{configurable:true,get(){return this.getAttribute('data-value')||this.firstElementChild?.value||'';},set(v){this.setAttribute('data-value',String(v));}});
 const datasets=options.datasets||[data('a'),data('b','halt')],jobs=options.jobs||[job('j1','a'),job('j2','b','cancelled')],writes=[],timers=[];
 let now=100000;
 class Clock extends Date{static now(){return now;}}
 const routes={'/api/ml/capabilities':{algorithms:[{id:'gaze_pipeline',title:'眼动低频形态'},{id:'cnn_gru',title:'CNN/GRU'}]},'/api/ml/local-records':{records:[]},'/api/ml/collection-profile':{experiment_mode:'gaze_only'},'/api/ml/datasets':{datasets},'/api/training/jobs':{jobs,fresh:true,server:{worker_connected:true}},'/api/models':{groups:[],total_versions:0,current_model_id:null}};
 const ctx=vm.createContext({window,document,CustomEvent:window.CustomEvent,console,Date:Clock,JSON,Number,Map,Set,AbortSignal,crypto:require('node:crypto').webcrypto,setInterval:(fn,ms)=>{timers.push({fn,ms});},fetch:async(url,o)=>{
  if(o.method==='POST'){writes.push({url,data:JSON.parse(o.body)});if(options.post)return options.post(url,o);return {ok:true,json:async()=>({})};}
  if(url.includes('/events?'))return options.events?options.events(url):{ok:true,json:async()=>({events:[{seq:1,utc:100,message:'training_progress',data:{epoch:1,total_epochs:3,training_loss:.6,development_loss:.8,training_balanced_accuracy:.9,development_balanced_accuracy:0}}]})};
  return {ok:true,json:async()=>routes[url]||{}};
 }});
 vm.runInContext(fs.readFileSync('hub/web/gnautilus-ml.js','utf8'),ctx);window.GnautilusTraining.mount(document.getElementById('host'));await flush();
 return {window,document,$:id=>document.getElementById(id),writes,routes,timers,advance:ms=>{now+=ms;},input(id,v){const el=document.getElementById(id);el.value=v;el.dispatchEvent(new window.Event(el.tagName==='SELECT'?'change':'input'));}};
}
test('read-only entry, bounded directories, search and unknown-state recovery',async()=>{
 const jobs=Array.from({length:150},(_,i)=>job('job'+i,'a',i===0?'future_stage':'completed'));
 const b=await boot({datasets:Array.from({length:90},(_,i)=>data('d'+i)),jobs});
 assert.equal(b.writes.length,0);assert.equal(b.$('gwDatasets').children.length,8);assert.equal(b.$('gmJobs').children.length,10);
 b.input('gwJobSearch','job0');assert.match(b.$('gmJobs').textContent,/future_stage/);assert.equal(b.$('gmJobs').children.length,1);
 b.input('gwDataSearch','impossible');assert.match(b.$('gwDatasets').textContent,/没有匹配/);
});
test('missing development cannot submit and reviewed window cannot be edited',async()=>{
 const d=data('a');d.records=d.records.slice(0,1);const b=await boot({datasets:[d]});
 assert.equal(b.$('gmTrain').disabled,true);assert.match(b.$('gwTrainReadiness').textContent,/缺开发/);assert.equal(b.$('gmWindow').readOnly,true);
});
test('source to task and back keeps dataset identity, cancellation visible on request',async()=>{
 const b=await boot();b.$('gwDatasetJobs').click();assert.match(b.$('gwJobDataset').textContent,/数据 a/);assert.doesNotMatch(b.$('gmJobs').textContent,/j2/);
 b.$('gwClearJobs').click();assert.match(b.$('gmJobs').textContent,/已取消/);b.input('gwJobScope','motor');assert.match(b.$('gmJobs').textContent,/j2/);assert.doesNotMatch(b.$('gmJobs').textContent,/j1/);
});
test('curve swaps metric without mixing scales and preserves zero score',async()=>{
 const b=await boot();b.input('gwCurveMetric','ba');assert.match(b.$('gmChart').textContent,/0.0%/);assert.match(b.$('gmChart').textContent,/100%/);assert.match(b.$('gmChart').textContent,/第 1 轮/);
});
test('stale last receipt disables mutations, keeps logs readable and preserves form input',async()=>{
 const b=await boot({jobs:[job('j1','a','running')]});assert.equal(b.$('gmTrain').disabled,false);b.$('gmSubject').value='untouched';
 b.advance(9000);b.timers.find(t=>t.ms===1000).fn();assert.equal(b.$('gmTrain').disabled,true);assert.equal(b.document.querySelector('[data-job-action]').disabled,true);assert.match(b.$('gmLogs').textContent,/训练进度/);assert.equal(b.$('gmSubject').value,'untouched');assert.equal(b.writes.length,0);
});
test('refresh is GET-only and preserves selected pane and training configuration',async()=>{
 const b=await boot();b.$('gwTab-data').click();b.$('gmSeed').value='29';b.$('trainingRefresh').click();await flush();assert.equal(b.$('gwPane-data').hidden,false);assert.equal(b.$('gmSeed').value,'29');assert.equal(b.writes.length,0);
});
test('late prior-task event cannot overwrite newly selected job',async()=>{
 let release;const b=await boot({jobs:[job('j1','a'),job('j2','b')],events:url=>url.includes('/j1/')?new Promise(r=>{release=()=>r({ok:true,json:async()=>({events:[{seq:1,message:'OLD',utc:100,data:{}}]})});}):Promise.resolve({ok:true,json:async()=>({events:[{seq:2,message:'NEW',utc:100,data:{}}]})})});
 b.document.querySelector('[data-select-job="j2"]').click();await flush();release();await flush();assert.match(b.$('gmLogs').textContent,/NEW/);assert.doesNotMatch(b.$('gmLogs').textContent,/OLD/);assert.match(b.$('gwJobMeta').textContent,/j2/);
});
test('double submit creates one task with exact data identity and seed',async()=>{
 let finish;const b=await boot({post:()=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>job('new','a','queued')});})});
 b.$('gmSeed').value='41';b.$('gmTrain').click();b.$('gmTrain').click();assert.equal(b.writes.length,1);assert.equal(b.writes[0].data.dataset_id,'a');assert.equal(b.writes[0].data.seed,41);assert.match(b.writes[0].data.request_key,/^[a-f0-9-]{36}$/);finish();await flush();assert.equal(b.$('gwPane-jobs').hidden,false);
});

test('an empty related-task result never displays another dataset task detail',async()=>{
 const b=await boot({datasets:[data('a'),data('b')],jobs:[job('j1','a')]});
 b.$('gmDataset').value='b';b.$('gmDataset').dispatchEvent(new b.window.Event('change'));b.$('gwDatasetJobs').click();
 assert.match(b.$('gmJobs').textContent,/没有匹配/);assert.equal(b.document.querySelector('.gw-job-detail').hidden,true);
});

test('overview global queue action clears prior dataset, search and scope filters',async()=>{
 const b=await boot({jobs:[job('j1','a'),job('j2','b','running')]});
 b.$('gwDatasetJobs').click();b.input('gwJobSearch','nothing');b.input('gwJobScope','gaze');b.$('gwTab-overview').click();b.$('gwActiveJobs').click();
 assert.match(b.$('gmJobs').textContent,/j2/);assert.equal(b.$('gwJobSearch').value,'');assert.equal(b.$('gwJobScope').value,'all');
});

test('initial event loading follows server cursor pages through the current retry',async()=>{
 const first=Array.from({length:500},(_,i)=>({seq:i+1,message:'training_progress',utc:100,data:{epoch:i+1,loss:1}}));
 const b=await boot({events:url=>Promise.resolve({ok:true,json:async()=>({events:url.endsWith('after=0')?first:[{seq:501,message:'resumed',utc:100,data:{}},{seq:502,message:'training_progress',utc:100,data:{epoch:1,loss:.25}},{seq:503,message:'NEW_ATTEMPT',utc:100,data:{}}]})})});
 assert.match(b.$('gmLogs').textContent,/NEW_ATTEMPT/);assert.match(b.$('gwCurveNote').textContent,/1 个已提交轮次/);assert.doesNotMatch(b.$('gmChart').textContent,/第 500 轮/);
});
