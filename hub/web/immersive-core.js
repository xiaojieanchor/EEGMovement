(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.EEGImmersiveCore=api})(globalThis,function(){
 'use strict';
 const panels=['information','signal','vr','output'];
 class Layout{
  constructor(){this.focus=null}
  select(id){if(!panels.includes(id))throw Error('Unknown panel');this.focus=id;return this.areas()}
  reset(){this.focus=null;return this.areas()}
  areas(){return this.focus?[0,1,2].map((_,i)=>[this.focus,panels.filter(p=>p!==this.focus)[i]]):[['information','vr'],['signal','output']]}
 }
 class SignalBuffer{
  constructor(){this.clear()}
  clear(){this.epoch=null;this.end=null;this.rows=[];this.descriptor=null;this.rate=null;this.fresh=false}
  ingest(d){
   if(!d.fresh){if(this.epoch!==null&&d.source_epoch!==undefined&&d.source_epoch!==this.epoch)this.clear();this.fresh=false;return}
   const rows=d.samples||[];if(d.sample_index_end-d.sample_index_start!==rows.length)throw Error('Signal range differs');
   if(!d.reset&&(d.source_epoch!==this.epoch||d.sample_index_start!==this.end))throw Error('Signal cursor discontinuity');
   if(d.reset)this.rows=[];
   this.descriptor=d.descriptor;this.rate=d.sampling_rate;this.epoch=d.source_epoch;this.end=d.sample_index_end;this.fresh=true;
   this.rows=this.rows.concat(rows).slice(-Math.ceil(this.rate*3));
  }
  snapshot(fresh=this.fresh){return {source:{device:'g.Nautilus',fresh,configured_sfreq:this.rate},gnautilus:{descriptor:this.descriptor},waveforms:{raw:this.rows,display_sfreq:this.rate,seconds:3}}}
 }
 class ObservationCache{
  constructor(){this.reset()}
  reset(){this.key=null;this.value=null}
  show(value,key,enabled=true){
   if(!enabled){this.reset();return null}
   if(key!==null&&key!==this.key){this.value=null;this.key=key}
   if(value)this.value=value;
   return this.value;
  }
 }
 function view(s,elapsed=0){
  const current=!!s&&elapsed>=0&&elapsed<500;
  const source=current&&s.source?.fresh&&Number.isFinite(s.source.age_ms)&&s.source.age_ms+elapsed<250;
  const q=s?.quality||{},quality=current&&source&&q.fresh&&Number.isFinite(q.age_ms)&&q.age_ms+elapsed<500;
  const peer=s?.vr?.target,t=peer?.telemetry||{};
  const vr=current&&peer?.online&&Number.isFinite(peer.telemetry_age_ms)&&peer.telemetry_age_ms+elapsed<300;
  const intent=s?.intent,r=intent?.latest;
  const age=(r?.input_age_ms||0)+(intent?.age_ms||0)+elapsed;
  const output=current&&source&&intent?.inference_enabled&&intent.fresh&&r&&age<250&&r.model_id===s?.model?.model_id?r:null;
  return {current,source,quality,vr,peer,telemetry:t,output,entry:current&&source&&vr&&s.entry_eligible,
    canStart:current&&source&&vr&&quality&&s.can_start,good:quality?q.good_channels:0,bad:quality?q.bad_channels:0,
    unknown:quality?q.unknown_channels:(s?.descriptor?.channels||[]).filter(c=>c.kind==='EEG').length};
 }
 function signalFresh(buffer,status,receiptAge){return !!buffer.fresh&&buffer.epoch===status?.source?.source_epoch&&Number.isFinite(receiptAge)&&receiptAge>=0&&receiptAge<500}
 return {Layout,SignalBuffer,ObservationCache,signalFresh,view,panels};
});
