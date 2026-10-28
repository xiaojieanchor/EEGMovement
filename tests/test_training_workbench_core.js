'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const core=require('../hub/web/gnautilus-ml.js');
const dataset=(id,reader='gnautilus')=>({id,title:'相同名称',records:[{reader,role:'train',subject:'p1',wearing_id:'w1',channels:['Fp1','Fp2'],sampling_rate:500,reference:'REF'},{reader,role:'development',subject:'p2',wearing_id:'w2',channels:['Fp1','Fp2'],sampling_rate:500,reference:'REF'}]});
test('source scope follows explicit reader and mode, unknown never becomes personal',()=>{
 assert.equal(core.scopeOf(dataset('a','malta_target_stationary')),'gaze');
 assert.equal(core.scopeOf(dataset('b','halt')),'motor');
 assert.equal(core.scopeOf({title:'something',records:[]}),'unknown');
 const d=dataset('c');d.records.forEach(r=>r.experiment_mode='gaze_only');assert.equal(core.scopeOf(d),'personal');
});
test('dataset summary separates role support, missing fields, actual input and public boundaries',()=>{
 const d=dataset('a','malta_target_stationary');d.prepared_window_s=1.5;
 const s=core.datasetSummary(d);assert.equal(s.roles.train,1);assert.equal(s.roles.development,1);assert.equal(s.channels,'2路');assert.equal(s.rate,'500 Hz');assert.equal(s.canSubmit,true);
 d.records=d.records.slice(0,1);assert.equal(core.datasetSummary(d).canSubmit,false);
 assert.equal(core.datasetSummary({records:[]}).rate,'未知');
});
test('job filters join dataset identity, default excludes cancellations, and retain unfamiliar future states',()=>{
 const a=dataset('a','malta_target_stationary'),b=dataset('b','halt');
 const jobs=[{id:'one',state:'running',spec:{dataset_id:'a',algorithm:'gaze_pipeline'}},{id:'two',state:'cancelled',spec:{dataset_id:'a'}},{id:'three',state:'new_phase',spec:{dataset_id:'b'}}];
 assert.deepEqual(core.filterJobs(jobs,[a,b],{state:'recent'}).map(j=>j.id),['one','three']);
 assert.deepEqual(core.filterJobs(jobs,[a,b],{scope:'gaze',query:'形态'}).map(j=>j.id),['one']);
 assert.deepEqual(core.filterJobs(jobs,[a,b],{dataset:'b'}).map(j=>j.id),['three']);
});
test('chart uses real epochs, keeps zero, excludes previous attempts and missing metrics',()=>{
 const rows=[{seq:1,message:'training_progress',data:{epoch:5,loss:2}},{seq:2,message:'resumed',data:{}},{seq:3,message:'training_progress',data:{epoch:1,loss:0,development_balanced_accuracy:0}},{seq:4,message:'input_verified',data:{}}];
 const points=core.curveRows(rows);assert.equal(points.length,1);assert.equal(points[0].epoch,1);assert.equal(points[0].loss,0);assert.equal(points[0].development_balanced_accuracy,0);
});
test('joint repair chart compares train and development CE instead of weighted total',()=>{
 const [p]=core.curveRows([{seq:1,message:'jepa_progress',data:{epoch:5,variant:'jepa_transfer',seed:17,loss:1.8,training_joint_ce:1.5,validation_joint_ce:1.7}}]);
 assert.equal(p.training_loss,1.5);assert.equal(p.development_loss,1.7);assert.equal(p.loss,1.8);
});
test('comparison requires identical data identity and class/input contract, never matching titles alone',()=>{
 const a={dataset_sha256:'a',class_labels:['UP','DOWN'],channels:['Fp1'],sampling_rate:500,window_s:1.5};
 assert.equal(core.comparable(a,{...a}),true);
 assert.equal(core.comparable(a,{...a,dataset_sha256:'b'}),false);
 assert.equal(core.comparable(a,{...a,window_s:2}),false);
 assert.equal(core.comparable({},{}),false);
});
