'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const file=require('node:path').join(__dirname,'../hub/web/ocular-annotations-core.js');
test('ocular interval core exists',()=>assert.ok(fs.existsSync(file)));
test('reverse drag clamps and maps CSS coordinates to absolute half-open samples',()=>{
 const c=require(file),w={start:1000,end:2250};assert.deepEqual(c.dragRange(460,160,{left:100,width:500},w),{sample_start:1150,sample_end:1900});
 assert.deepEqual(c.dragRange(-10,900,{left:100,width:500},w),{sample_start:1000,sample_end:2250});
 assert.equal(c.dragRange(100,100,{left:100,width:500},w),null);
});
test('default direction relates navigation to eye movement without inventing intervals',()=>{
 const c=require(file);assert.deepEqual(c.defaultDirections('FORWARD'),{onset:'UP',offset:'DOWN'});assert.deepEqual(c.defaultDirections('LEFT'),{onset:'LEFT',offset:'RIGHT'});assert.deepEqual(c.defaultDirections('REST'),{onset:null,offset:null});
 assert.equal(c.emptyPhases('RIGHT').onset.status,'pending');assert.equal(c.emptyPhases('RIGHT').onset.sample_start,undefined);
});
test('phase summary treats uncertain as review, never inferred rest',()=>{
 const c=require(file);assert.equal(c.reviewState({onset:{status:'marked'},offset:{status:'pending'}}),'partial');assert.equal(c.reviewState({onset:{status:'absent'},offset:{status:'uncertain'}}),'uncertain');
});
