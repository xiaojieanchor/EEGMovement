'use strict';
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.EEGTrainingInput=factory()})(globalThis,()=>{
 class HeldEEGTest{
  constructor({api,changed=()=>{},id=()=>crypto.randomUUID()}){this.api=api;this.changed=changed;this.id=id;this.client=id();this.seq=0;this.hold=null;this.enabled=false;this.error='';this.renewing=false}
  send(action,h,keepalive=false){return this.api({client_id:this.client,seq:++this.seq,action,hold_id:h.id,...(action==='press'?{label:h.label}:{})},keepalive)}
  press(label,pointer){if(!this.enabled||this.hold)return false;const h={id:this.id(),label,pointer,confirmed:false,revision:null};this.hold=h;this.error='';this.changed();this.send('press',h).then(s=>{if(this.hold!==h)return;h.confirmed=true;h.revision=s.revision;this.changed()}).catch(e=>{if(this.hold===h){this.error=e.message;this.release(pointer)}});return true}
  release(pointer=null){const h=this.hold;if(!h||pointer!==null&&pointer!==h.pointer)return;this.hold=null;this.changed();this.send('release',h,true).then(()=>this.changed()).catch(e=>{this.error='停止回执未确认，自动租约会结束测试：'+e.message;this.changed()})}
  async heartbeat(){const h=this.hold;if(!h||!h.confirmed||this.renewing)return;this.renewing=true;try{await this.send('renew',h)}catch(e){if(this.hold===h){this.error=e.message;this.release(h.pointer)}}finally{this.renewing=false;this.changed()}}
 }
 return {HeldEEGTest};
});
