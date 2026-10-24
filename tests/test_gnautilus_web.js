'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {DesktopCapture}=require('../hub/web/capture-core.js');

(async()=>{
 let now=0,focused=true,events=[],active=true;
 const api=async(path,data)=>{
  if(path.endsWith('/sync'))return {hub_monotonic_ms:now};
  if(path.endsWith('/start'))return {active:true,session_id:'session',client_token:'token',recording:'record'};
  if(path.endsWith('/event')){events.push(data);return {ok:true,ack_seq:data.seq};}
  if(path.endsWith('/stop')){active=false;return {ok:true};}
 };
 const c=new DesktopCapture({api,clock:()=>now,focused:()=>focused});
 assert.equal(await c.start(),true);
 assert.equal(c.press('FORWARD','p'),true);await c.chain;
 assert.equal(c.release('p'),true);await c.chain;
 assert.equal(c.held,null);assert.equal(events[0].phase,'hold_start');assert.equal(events[1].phase,'hold_end');
 assert.equal(c.press('LEFT','p'),true);await c.chain;focused=false;c.heartbeat();await c.chain;
 assert.equal(c.held,null);assert.equal(events.filter(e=>e.phase==='hold_end').length,2);
 await c.stop();assert.equal(active,false);
 const source=fs.readFileSync('hub/web/gnautilus.js','utf8');
 new vm.Script(source);assert(source.includes('/api/gnautilus/emergency'));
 assert(!source.includes('.innerHTML='));
 const nodes=new Map();const node=()=>({textContent:'',value:'0',children:[],append(x){this.children.push(x)},replaceChildren(){this.children=[]},getBoundingClientRect(){return {width:600,height:300}},getContext(){return new Proxy({},{get:()=>()=>{}})}});
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node};
 const fixture={gnautilus:{enabled:true,port:8767,connected:false,agent_health:{sdk_state:'closed',disk_state:'failed',queue_blocks:1,error:'disk full fixture'}},source:{fresh:false},quality:{},recording:{active:false},waveforms:{raw:[]},events:[]};
 vm.runInNewContext(source,{document,window:{addEventListener(){}},devicePixelRatio:1,setInterval(){},AbortSignal:{timeout(){}},fetch:async()=>({ok:true,json:async()=>fixture})});
 await new Promise(resolve=>setImmediate(resolve));
 assert(nodes.get('checkpoints').children.some(n=>n.textContent.includes('disk full fixture')));
 assert(nodes.get('checkpoints').children.some(n=>n.textContent.includes('failed')));
 console.log(JSON.stringify({passed:9,scope:'active-label controller, actual health DOM render and new UI JavaScript; browser rendering not tested'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
