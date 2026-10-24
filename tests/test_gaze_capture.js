'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require('linkedom');
const {DesktopCapture}=require('../hub/web/capture-core.js');
const labels=['FORWARD','BACKWARD','LEFT','RIGHT','REST'];
const session={active:true,session_id:'gaze',client_token:'token',recording:'fixture',labels,experiment_mode:'gaze_only',task_binding_version:'gaze_navigation_v2',task_instructions_list:labels.map((label,i)=>({label,instruction:['向上看','向下看','向左看','向右看','自然中央注视'][i]+'；无需肢体想象，可正常操作手柄。'}))};
async function main(){
 let sent=[];
 const c=new DesktopCapture({api:async(path,data)=>{if(path.endsWith('/sync'))return {hub_monotonic_ms:0};if(path.endsWith('/start')){sent.push(data);return session}return {ok:true,ack_seq:data?.seq}},clock:()=>0,focused:()=>true});
 await c.start();assert.equal(sent[0].task_guidance_version,'gaze_navigation_v2');assert.equal(c.experimentMode,'gaze_only');
 assert.match(c.instructions.FORWARD,/向上看/);assert.equal(c.press('FORWARD_LEFT','p'),false);assert.equal(c.press('FORWARD','p'),true);await c.chain;
 const {window,document}=parseHTML('<html><head></head><body><div id="sessions"></div></body></html>');document.hasFocus=()=>true;
 const ctx=vm.createContext({window,document,EEGCapture:{DesktopCapture},performance:{now:()=>0},requestAnimationFrame:()=>{},setInterval:()=>{},AbortSignal:{timeout:()=>null},fetch:async(path,options)=>({ok:true,json:async()=>path.endsWith('/sync')?{hub_monotonic_ms:0}:path.endsWith('/start')?session:{active:false}})});
 vm.runInContext(fs.readFileSync('hub/web/capture.js','utf8'),ctx);document.getElementById('captureCombos').checked=true;document.getElementById('captureStart').click();for(let i=0;i<12;i++)await new Promise(setImmediate);
 assert.match(document.querySelector('[data-label="FORWARD"]').textContent,/向上看/);
 assert.match(document.querySelector('[data-label="BACKWARD"]').textContent,/向下看/);
 assert.equal(document.querySelector('[data-label="FORWARD_LEFT"]').hidden,true);
 assert.match(document.getElementById('captureDetail').textContent,/无需肢体想象/);
 assert.match(document.getElementById('captureHint').textContent,/无需肢体想象/);
 assert.equal(document.getElementById('desktopCapture').classList.contains('with-combos'),false);
 console.log('PASS: gaze session guidance is consumed by actual desktop controller and DOM; illegal directions cannot mark');
}
main().catch(e=>{console.error(e);process.exitCode=1});
