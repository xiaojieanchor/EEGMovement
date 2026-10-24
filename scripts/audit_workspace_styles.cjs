'use strict';
// Cascade audit for real page styles. This is not a browser/layout renderer.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const runtime='';
const {parseHTML}=require(runtime+'linkedom'),css=require(runtime+'cssom'),selectors=require(runtime+'css-what');
const widths=[360,390,480,481,540,720,721,780,820,899,900,901,999,1000,1001,1099,1100,1101,1230,1350,1366,1440,1920];
const matrix=[],violations=[];
function specificity(tokens){return tokens.reduce((score,t)=>{
 if(t.type==='tag'||t.type==='pseudo-element')return score+1;
 if(t.type==='attribute')return score+(t.name==='id'?10000:100);
 if(t.type==='pseudo'){
  if(t.name==='where')return score;
  if(['is','not','has','matches'].includes(t.name)&&Array.isArray(t.data))return score+Math.max(0,...t.data.map(specificity));
  return score+100;
 }
 return score;
},0)}
function activeMedia(query,width){
 return query.split(',').some(part=>{
  if(/print|forced-colors|prefers-(?:reduced-motion|reduced-transparency|contrast)/.test(part))return false;
  for(const m of part.matchAll(/(min|max)-width\s*:\s*([\d.]+)px/g))if(m[1]==='min'?width<+m[2]:width>+m[2])return false;
  return true;
 });
}
function makeRules(document,width){
 const rules=[];
 const walk=list=>{for(const rule of list){if(rule.type===1){
   let parsed;try{parsed=selectors.parse(rule.selectorText)}catch{continue}
   for(const tokens of parsed){const selector=selectors.stringify([tokens]);if(/:(?:hover|active|focus|focus-visible|fullscreen|before|after|backdrop)\b/.test(selector))continue;rules.push({selector,weight:specificity(tokens),style:rule.style,order:rules.length});}
  }else if(rule.type===4&&activeMedia(rule.media.mediaText,width))walk(rule.cssRules);
 }};
 for(const link of document.querySelectorAll('link[rel=stylesheet]'))walk(css.parse(fs.readFileSync('hub/web/'+link.getAttribute('href').slice(1),'utf8')).cssRules);
 return rules;
}
function readStyle(el,property,rules,depth=0){
 if(!el||depth>30)return '';
 let win=null;
 for(const r of rules){let matches=false;try{matches=el.matches(r.selector)}catch{}if(!matches)continue;
  for(let i=0;i<r.style.length;i++){
   const name=r.style[i];let value=r.style.getPropertyValue(name);if(name!==property){
    if(name==='margin'&&property==='margin-left'){const v=value.trim().split(/\s+/);value=v[3]||v[1]||v[0];}
    else if(name==='font'&&property==='font-size')value=value.match(/[\d.]+px/)?.[0]||'';
    else continue;
   }
   const rank=(r.style.getPropertyPriority(name)==='important'?1e8:0)+r.weight;
   if(!win||rank>win.rank||rank===win.rank&&r.order>=win.order)win={value,rank,order:r.order};
  }
 }
 let value=win?.value||'';
 if(!value&&(property.startsWith('--')||property==='font-size'))value=readStyle(el.parentElement,property,rules,depth+1);
 if(value==='inherit')value=readStyle(el.parentElement,property,rules,depth+1);
 value=value.replace(/var\((--[\w-]+)\)/g,(_,name)=>readStyle(el,name,rules,depth+1));
 if(!value&&property==='display')value=['SPAN','SMALL','EM','I','B','STRONG','A'].includes(el.tagName)?'inline':'block';
 return value;
}
for(const [page,route] of [['index','/'],['index','/?view=architecture'],['lab','/lab'],['training','/training'],['downloads','/downloads']]){
 for(const width of widths){
  const {document,window}=parseHTML(fs.readFileSync('hub/web/'+page+'.html','utf8'));
  const context=vm.createContext({window,document,location:new URL(route,'http://127.0.0.1:8765'),URLSearchParams,MutationObserver:class{observe(){}},ResizeObserver:class{observe(){}},matchMedia:query=>({matches:query.includes('prefers-reduced-motion')||query.includes('max-width:900')&&width<=900}),requestAnimationFrame(){},setTimeout(){},setInterval(){},Date,Math,Number,JSON,performance,AbortSignal,fetch:async()=>({ok:true,json:async()=>({source:{},server:{},vr:{clients:[]}})})});
  vm.runInContext(fs.readFileSync('hub/web/ui-state.js','utf8'),context);
  const records=[];
  for(const phase of ['initial','hydrated']){
   if(phase==='hydrated')window.EEGStudio.mount();
   const rules=makeRules(document,width),get=(el,prop)=>readStyle(el,prop,rules),rail=document.querySelector('.studio-rail');
   if(!rail){violations.push({route,width,phase,error:'Missing initial sidebar'});continue;}
   const values={rail_width:get(rail,'width'),rail_display:get(rail,'display'),rail_align:get(rail,'align-items'),rail_gap:get(rail,'gap'),main_offset:get(document.querySelector('main'),'margin-left'),brand_display:get(document.querySelector('.studio-brand>span:last-child'),'display'),brand_size:get(document.querySelector('.studio-brand'),'font-size'),label_display:get(document.querySelector('.studio-rail-label'),'display'),footer_display:get(document.querySelector('.studio-rail-bottom'),'display'),nav_display:get(document.querySelector('.studio-primary a'),'display')};
   try{
    assert.equal(values.rail_width,'232px');assert.equal(values.rail_display,'flex');assert.equal(values.rail_align,'stretch');assert.equal(values.rail_gap,'0');assert.equal(values.main_offset,width<=900?'0':'232px');assert.notEqual(values.brand_display,'none');assert.equal(values.brand_size,'17px');assert.equal(values.label_display,'flex');assert.notEqual(values.footer_display,'none');assert.equal(values.nav_display,'flex');
   }catch(error){violations.push({route,width,phase,values,error:error.message});}
   records.push({phase,...values});
  }
  matrix.push({route,width,records});
 }
}
const report={state:violations.length?'failed':'passed',scope:'CSS cascade across page styles, widths and initial/hydrated DOM; no browser geometry or visual acceptance',checked_at:new Date().toISOString(),viewports:widths,cases:matrix.length*2,matrix,violations};
fs.writeFileSync('outputs/frontend-redesign/style-audit.json',JSON.stringify(report,null,2));
console.log(JSON.stringify({state:report.state,cases:report.cases,violations:violations.length,examples:violations.slice(0,4)},null,2));if(violations.length)process.exitCode=1;
