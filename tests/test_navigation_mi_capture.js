'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {parseHTML}=require('linkedom');
const {DesktopCapture}=require('../hub/web/capture-core.js');
async function main(){
 const session={active:true,session_id:'synthetic',client_token:'fixture',labels:['FORWARD','BACKWARD','LEFT','RIGHT','REST'],experiment_mode:'navigation_mi_gaze',recording:'fixture',task_instructions_list:[{label:'FORWARD',instruction:'以第一人称想象自己向前行走；向上看，眼动仅作辅助。可正常操作手柄。'}]};
 const {window,document}=parseHTML('<html><head></head><body><div id="sessions"></div></body></html>');document.hasFocus=()=>true;
 const ctx=vm.createContext({window,document,EEGCapture:{DesktopCapture},performance:{now:()=>0},requestAnimationFrame:()=>{},setInterval:()=>{},AbortSignal:{timeout:()=>null},fetch:async(path,options)=>({ok:true,json:async()=>path.endsWith('/sync')?{hub_monotonic_ms:0}:path.endsWith('/start')?session:path.endsWith('/event')?{ok:true,ack_seq:JSON.parse(options.body).seq}:{active:false}})});
 vm.runInContext(fs.readFileSync('hub/web/capture.js','utf8'),ctx);document.getElementById('captureStart').click();for(let i=0;i<12;i++)await new Promise(setImmediate);
 assert.match(document.getElementById('captureHint').textContent,/运动想象为主/);
 const b=document.querySelector('[data-label="FORWARD"]');b.setPointerCapture=()=>{};b.dispatchEvent(Object.assign(new window.Event('pointerdown'),{button:0,pointerId:1}));
 assert.match(document.getElementById('captureHint').textContent,/想象自己向前行走/);
 assert.match(document.getElementById('captureHint').textContent,/眼动仅作辅助/);
 assert.doesNotMatch(document.getElementById('captureHint').textContent,/无需肢体想象/);
 assert.equal(document.querySelector('[data-label="FORWARD_LEFT"]').hidden,true);
 console.log('PASS: actual desktop navigation-MI guidance preserves imagery primary, eye auxiliary and legal labels');
}
main().catch(e=>{console.error(e);process.exitCode=1});
