(function(root){
 'use strict';
 const EYE={FORWARD:'UP',BACKWARD:'DOWN',LEFT:'LEFT',RIGHT:'RIGHT'},OPPOSITE={UP:'DOWN',DOWN:'UP',LEFT:'RIGHT',RIGHT:'LEFT'};
 function defaultDirections(label){const d=EYE[label]||null;return {onset:d,offset:OPPOSITE[d]||null};}
 function emptyPhases(label){const d=defaultDirections(label);return Object.fromEntries(['onset','offset'].map(k=>[k,{status:'pending',direction:d[k]}]));}
 function dragRange(x1,x2,rect,w){
  if(![x1,x2,rect.left,rect.width,w.start,w.end].every(Number.isFinite)||rect.width<=0||w.end<=w.start)return null;
  const at=x=>Math.max(w.start,Math.min(w.end,Math.round(w.start+(x-rect.left)/rect.width*(w.end-w.start))));
  const a=at(x1),b=at(x2);return a===b?null:{sample_start:Math.min(a,b),sample_end:Math.max(a,b)};
 }
 function reviewState(phases){const s=Object.values(phases).map(p=>p.status);return s.every(x=>x==='pending')?'pending':s.some(x=>x==='pending')?'partial':s.includes('uncertain')?'uncertain':'complete';}
 function cleanPhase(p){return p.status==='marked'?{status:p.status,direction:p.direction,sample_start:p.sample_start,sample_end:p.sample_end}:{status:p.status};}
 const api={defaultDirections,emptyPhases,dragRange,reviewState,cleanPhase};
 if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.OcularAnnotationCore=api;
})(typeof globalThis!=='undefined'?globalThis:this);
