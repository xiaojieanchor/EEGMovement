'use strict';
(function(root){
 class DesktopCapture {
  constructor({api,clock,focused,changed=()=>{},frame=()=>0}){Object.assign(this,{api,clock,focused,changed,frame});this.session=null;this.token=null;this.allowedLabels=null;this.instructions={};this.experimentMode=null;this.held=null;this.pointer=null;this.seq=this.ack=0;this.offset=this.rtt=0;this.busy=this.stopping=this.failed=false;this.error='';this.chain=Promise.resolve();}
  notify(){this.changed();}
  async sync(){let best=null;for(let i=0;i<3;i++){const t=this.clock(),r=await this.api('/api/acquisition/sync'),end=this.clock();if(!best||end-t<best.rtt)best={rtt:end-t,offset:r.hub_monotonic_ms-(t+end)/2};}this.rtt=best.rtt;this.offset=best.offset;}
  async start(){if(this.session||this.busy)return false;this.busy=true;this.error='';this.notify();try{await this.sync();if(this.rtt>100)throw Error('同步延迟超过100ms，请连接稳定后重试');const s=await this.api('/api/acquisition/start',{build_id:'desktop-web-selfpaced-v1',paradigm:'self_paced_motor_imagery',presentation_protocol:'self_paced_axes_v2',input_surface:'desktop',task_guidance_version:'gaze_navigation_v2'});if(!s.active||!s.session_id||!s.client_token)throw Error('中枢未确认电脑采集会话');this.allowedLabels=Array.isArray(s.labels)?s.labels:null;this.experimentMode=s.experiment_mode;this.instructions=Object.fromEntries((s.task_instructions_list||[]).map(t=>[t.label,t.instruction]));this.session=s.session_id;this.token=s.client_token;this.recording=s.recording;this.seq=this.ack=0;this.failed=false;this.held=null;this.chain=Promise.resolve();return true;}catch(e){this.error=e.message;return false;}finally{this.busy=false;this.notify();}}
  enqueue(phase,label,source='desktop_pointer'){
   if(!this.session||this.failed)return this.chain;
   const e={session_id:this.session,client_token:this.token,seq:++this.seq,label,phase,sender_monotonic_ms:this.clock(),clock_offset_ms:this.offset,sync_rtt_ms:this.rtt,sender_frame:this.frame(),xr_active:false,page_active:this.focused(),pointer_source:source};
   this.chain=this.chain.then(async()=>{if(e.session_id!==this.session||this.failed)return;let error;for(let i=0;i<2;i++){try{const r=await this.api('/api/acquisition/event',e);if(!r.ok||r.ack_seq!==e.seq)throw Error('打标回执不匹配');this.ack=r.ack_seq;this.notify();return;}catch(err){error=err;}}throw error;}).catch(e=>this.fail(e));return this.chain;
  }
  press(label,pointer,source='desktop_pointer'){if(!this.session||this.busy||this.stopping||this.failed||this.held||!this.focused()||(this.allowedLabels&&!this.allowedLabels.includes(label)))return false;this.held=label;this.pointer=pointer;this.source=source;this.enqueue('hold_start',label,source);this.notify();return true;}
  release(pointer=null){if(!this.held||pointer!==null&&pointer!==this.pointer)return false;const label=this.held,source=this.source;this.held=null;this.pointer=null;this.enqueue('hold_end',label,source);this.notify();return true;}
  heartbeat(){if(!this.session||this.busy||this.stopping||this.failed)return;if(this.held&&!this.focused())this.release();if(this.seq-this.ack>=4){this.fail(Error('打标积压，已停止采集'));return;}this.enqueue('heartbeat',this.held,this.held?this.source:'desktop_pointer');}
  async fail(error){if(this.failed)return;this.failed=true;this.error=error.message;this.held=null;const data={session_id:this.session,client_token:this.token,reason:'transport_error'};try{await this.api('/api/acquisition/stop',data);}catch{}this.session=null;this.token=null;this.notify();}
  async stop(){if(!this.session||this.stopping)return;this.stopping=true;this.release();this.notify();try{await this.chain;if(this.session)await this.api('/api/acquisition/stop',{session_id:this.session,client_token:this.token,reason:'completed'});this.session=null;this.token=null;this.error='';}catch(e){this.failed=true;this.error='结束尚未确认：'+e.message+'；中枢失联后会自动关闭并保存';}finally{this.stopping=false;this.notify();}}
  lost(){this.release();}
  ended(){this.held=null;this.session=null;this.token=null;this.notify();}
 }
 if(typeof module!=='undefined'&&module.exports)module.exports={DesktopCapture};else root.EEGCapture={DesktopCapture};
})(typeof globalThis!=='undefined'?globalThis:this);
