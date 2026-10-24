'use strict';
const {chromium}=require('C:/Users/19410/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises');
(async()=>{
 const browser=await chromium.launch({headless:true}),results=[];
 try{for(const route of ['/','/?view=architecture','/lab','/training','/downloads','/vr-preview','/#settings-audit','/lab#test-audit']){
  const page=await browser.newPage({viewport:{width:1440,height:1000}});await page.route('**/*',r=>r.request().url().startsWith('http://127.0.0.1:8765/')&&r.request().method()==='GET'?r.continue():r.abort());
  await page.goto('http://127.0.0.1:8765'+route,{waitUntil:'domcontentloaded'});await page.waitForTimeout(1000);
  if(route.includes('settings-audit')){await page.locator('#settings').click();await page.locator('#settingsDialog').waitFor({state:'visible'});await page.waitForTimeout(400);}
  if(route.includes('test-audit')){await page.locator('#inputTestTab').click();await page.waitForTimeout(350);}
  const report=await page.evaluate(()=>{
   function rgba(value){const a=value.match(/[\d.]+/g)?.map(Number);return a?.length>=3?[a[0],a[1],a[2],a[3]??1]:null;}
   function luminance(rgb){const a=rgb.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});return a[0]*.2126+a[1]*.7152+a[2]*.0722;}
   function background(el){for(let p=el;p;p=p.parentElement){const style=getComputedStyle(p);if(style.backgroundImage!=='none')return null;const c=rgba(style.backgroundColor);if(c?.[3]===1)return c;}return [255,255,255,1];}
   const failures=[],seen=new Set();let checked=0;
   for(const el of (document.querySelector('dialog[open]')||document.body).querySelectorAll('*')){
    if(![...el.childNodes].some(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim())||el.closest('[aria-hidden=true],button:disabled,[hidden]')||!el.getClientRects().length)continue;
    let muted=false;for(let p=el;p;p=p.parentElement)if(+getComputedStyle(p).opacity<.99)muted=true;if(muted)continue;
    const s=getComputedStyle(el),bg=background(el),fg=rgba(s.color);if(!bg||!fg||fg[3]<1)continue;
    const x=luminance(fg),y=luminance(bg),ratio=(Math.max(x,y)+.05)/(Math.min(x,y)+.05),large=parseFloat(s.fontSize)>=24||parseFloat(s.fontSize)>=18.66&&+s.fontWeight>=700,required=large?3:4.5;checked++;
    if(ratio+.05>=required)continue;
    const key=el.tagName+'.'+el.className+'|'+s.color+'|'+s.backgroundColor;if(seen.has(key))continue;seen.add(key);
    failures.push({tag:el.tagName,id:el.id,class:el.className,text:el.textContent.trim().slice(0,70),size:s.fontSize,color:s.color,background:bg.slice(0,3),ratio:+ratio.toFixed(2),required});
   }return {checked,failures};
  });if(!report.checked)throw Error('No rendered text checked on '+route);results.push({route,...report});await page.close();
 }}finally{await browser.close();}
 const failures=results.reduce((sum,r)=>sum+r.failures.length,0),report={state:failures?'failed':'passed',checked_at:new Date().toISOString(),scope:'Rendered visible direct text, solid backgrounds only; disabled, translucent and gradient content excluded',results};
 await fs.writeFile('outputs/frontend-redesign/layout/contrast-audit.json',JSON.stringify(report,null,2));console.log(JSON.stringify(results.map(r=>({route:r.route,checked:r.checked,failures:r.failures.length,examples:r.failures.slice(0,8)})),null,2));if(failures)process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1});
