'use strict';
// Authorized local-only headless browser acceptance. Control writes are blocked.
const {chromium}=require('C:/Users/19410/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
const base='http://127.0.0.1:8765',out='outputs/frontend-redesign/layout';
const widths=[360,390,480,481,540,720,721,780,820,899,900,901,999,1000,1001,1099,1100,1101,1230,1350,1366,1440,1920];
const routes=[['overview','/'],['architecture','/?view=architecture'],['lab','/lab'],['training','/training'],['downloads','/downloads']];
const cases=[],errors=[],blockedWrites=[];
(async()=>{
 await fs.mkdir(out,{recursive:true});const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('**/*',async route=>{const request=route.request(),url=new URL(request.url());
  if(url.origin!==base)return route.abort();
  if(request.method()!=='GET'){blockedWrites.push({url:url.pathname,method:request.method()});return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Read-only browser acceptance: writes blocked'})});}
  await route.continue();
 });
 async function sample(page){return page.evaluate(()=>{
  const rail=document.querySelector('.studio-rail'),main=document.querySelector('main'),brand=document.querySelector('.studio-brand'),bar=document.querySelector('.workspace-nav');
  const r=rail.getBoundingClientRect(),m=main.getBoundingClientRect(),n=bar.getBoundingClientRect(),hero=document.querySelector('.studio-hero').getBoundingClientRect();
  return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,rail:{x:r.x,width:r.width,display:getComputedStyle(rail).display,align:getComputedStyle(rail).alignItems,gap:getComputedStyle(rail).gap},firstNavY:rail.querySelector('.studio-primary a').getBoundingClientRect().y,mainX:m.x,navX:n.x,navRight:n.right,heroRight:hero.right,brandSize:getComputedStyle(brand).fontSize,brandCaptionHeight:brand.querySelector('small').getBoundingClientRect().height,toolIconWidths:[...rail.querySelectorAll('.studio-tools svg')].map(el=>el.getBoundingClientRect().width),navHidden:[...rail.querySelectorAll('.studio-primary a')].some(a=>getComputedStyle(a).display==='none'),footerHidden:getComputedStyle(rail.querySelector('.studio-rail-bottom')).display==='none'};
 });}
 function verify(value,route,width,phase){
  const failures=[];if(Math.abs(value.rail.width-232)>.5)failures.push('sidebar width');
  if(Math.abs(value.mainX-(width<=900?0:232))>.5)failures.push('main offset');
  if(value.documentWidth>value.viewport+1)failures.push('horizontal page overflow');
  if(value.navRight>value.clientWidth+1||value.heroRight>value.clientWidth+1)failures.push('header overflow');
  if(value.navHidden||value.footerHidden||value.brandSize!=='17px'||value.rail.align!=='stretch')failures.push('sidebar content changed');
  if(value.brandCaptionHeight>12||value.toolIconWidths.some(w=>w>24))failures.push('sidebar label or icon overflow');
  if(value.rail.gap!=='0px')failures.push('page-specific aside spacing leaked into sidebar');
  const baseline=cases.find(c=>c.route==='/'&&c.width===width&&c.phase===phase);if(baseline&&Math.abs(value.firstNavY-baseline.firstNavY)>.5)failures.push('navigation shifts between pages');
  cases.push({route,width,phase,passed:!failures.length,failures,...value});
 }
 try{
  for(const [name,path] of routes){
   const page=await context.newPage();page.on('pageerror',e=>errors.push({route:path,error:e.message}));
   await page.goto(base+path,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.querySelector('main').__studioMounted===true);
   for(const width of widths){await page.setViewportSize({width,height:1000});await page.waitForTimeout(45);verify(await sample(page),path,width,'running');}
   await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:out+'/'+name+'-desktop.png',animations:'disabled',fullPage:true});await page.screenshot({path:out+'/'+name+'-desktop-viewport.png',animations:'disabled'});
   await page.setViewportSize({width:390,height:844});await page.screenshot({path:out+'/'+name+'-mobile.png',animations:'disabled',fullPage:true});await page.screenshot({path:out+'/'+name+'-mobile-viewport.png',animations:'disabled'});
   await page.locator('.studio-menu').click();assert.equal(await page.locator('.studio-menu').getAttribute('aria-expanded'),'true');
   assert.equal(await page.locator('.studio-rail').evaluate(el=>el.inert),false);
   const beforeWrites=blockedWrites.length;await page.keyboard.press('Escape');
   assert.equal(await page.locator('.studio-menu').getAttribute('aria-expanded'),'false');await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('.studio-rail')),false);
   cases.push({route:path,kind:'drawer_escape',passed:blockedWrites.length===beforeWrites,failures:blockedWrites.length===beforeWrites?[]:['Closing navigation sent a control write']});
   await page.locator('.studio-menu').click();await page.locator('.studio-rail-close').click();assert.equal(await page.locator('.studio-menu').getAttribute('aria-expanded'),'false');
   if(name==='overview'){
    await page.locator('.studio-menu').click();await page.locator('#settings').click();await page.locator('#settingsDialog').waitFor({state:'visible'});
    const menuClosed=await page.locator('.studio-menu').getAttribute('aria-expanded')==='false';cases.push({route:path,kind:'mobile_settings_dialog',passed:menuClosed,failures:menuClosed?[]:['Drawer stays open under connection dialog']});
    const bounds=await page.locator('#settingsDialog').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);await page.screenshot({path:out+'/settings-mobile.png'});await page.locator('#closeSettings').click();
   }
   await page.setViewportSize({width:1440,height:1000});
   if(name==='architecture'){
    await page.locator('.architecture-node').nth(3).click();assert.equal(await page.locator('#inspectorTitle').textContent(),'意图模型推理');
    await page.locator('.architecture-node').nth(3).press('ArrowRight');assert.equal(await page.locator('#inspectorTitle').textContent(),'动作门控');
    cases.push({route:path,kind:'component_mouse_and_keyboard',passed:true});
   }
   if(name==='lab'&&await page.locator('#inputTestTab').isDisabled()){
    assert.equal(await page.locator('#intentStart').isDisabled(),true);assert.equal(await page.locator('#labNotch').isDisabled(),true);assert.equal(await page.locator('#inputLiveView').isVisible(),true);cases.push({route:path,kind:'retired_model_controls_disabled_live_signal_retained',passed:true});
   }else if(name==='lab'){
    await page.locator('#inputTestTab').click();assert.equal(await page.locator('#inputTestView').isVisible(),true);
    await page.waitForTimeout(300);const indicator=await page.locator('.input-tabs .studio-selection').boundingBox(),selected=await page.locator('#inputTestTab').boundingBox();assert.ok(Math.abs(indicator.x-selected.x)<1&&Math.abs(indicator.width-selected.width)<1);
    await page.locator('#inputTestTab').press('ArrowLeft');assert.equal(await page.locator('#inputLiveTab').getAttribute('aria-selected'),'true');
    await page.locator('#labRaw').click();assert.match(await page.locator('#labRaw').getAttribute('class'),/selected/);
    await page.locator('#labFiltered').click();
    await page.emulateMedia({reducedMotion:'reduce'});await page.locator('#inputTestTab').click();assert.equal(await page.locator('.input-tabs .studio-selection').evaluate(el=>getComputedStyle(el).transitionDuration),'0s');await page.locator('#inputLiveTab').click();await page.emulateMedia({reducedMotion:'no-preference'});
    cases.push({route:path,kind:'input_tabs_indicator_keyboard_and_waveform',passed:true});
   }
   if(name==='training'&&await page.locator('#trainingRefresh').isDisabled()){
    assert.match(await page.locator('#trainingWorkbench').textContent(),/训练.*停用/);assert.equal(await page.locator('#legacyTrainingHistory').isVisible(),false);cases.push({route:path,kind:'archived_training_and_unconfigured_model',passed:true});
   }else if(name==='training'){
    assert.equal(await page.locator('.studio-history').getAttribute('open'),null);await page.locator('.studio-history>summary').click();assert.ok(await page.locator('#personalV2').isVisible());await page.locator('.studio-history>summary').click();
    cases.push({route:path,kind:'history_expand_collapse',passed:true});
   }
   await page.close();
  }
  const short=await context.newPage();await short.setViewportSize({width:1000,height:600});await short.goto(base+'/',{waitUntil:'domcontentloaded'});const scrollable=await short.locator('.studio-rail').evaluate(el=>{el.scrollTop=el.scrollHeight;return {width:el.getBoundingClientRect().width,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollTop:el.scrollTop}});assert.equal(scrollable.width,232);assert.ok(scrollable.scrollWidth<=scrollable.clientWidth&&scrollable.scrollTop>0);cases.push({route:'/',kind:'short_window_sidebar_scroll',passed:true,...scrollable});await short.close();
  const navigation=await context.newPage();await navigation.goto(base+'/',{waitUntil:'domcontentloaded'});
  for(const [name,path] of routes.slice(1)){await navigation.locator('.studio-primary a[href="'+path+'"]').click();await navigation.waitForURL(base+path);await navigation.waitForFunction(()=>document.querySelector('main').__studioMounted===true);assert.equal(await navigation.locator('.studio-rail').evaluate(el=>el.getBoundingClientRect().width),232);cases.push({route:path,kind:'cross_page_navigation',passed:true});}await navigation.close();
  const preview=await context.newPage();await preview.goto(base+'/vr-preview',{waitUntil:'domcontentloaded'});for(const width of [390,901,1440]){await preview.setViewportSize({width,height:900});assert.ok(await preview.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}await preview.screenshot({path:out+'/vr-preview-desktop.png',animations:'disabled'});cases.push({route:'/vr-preview',kind:'responsive_preview',passed:true});await preview.close();
  // Hold the shared script to measure first paint against the hydrated shell.
  for(const [name,path] of routes.filter(r=>r[0]!=='architecture'))for(const width of [390,1000,1440]){
   const page=await context.newPage();await page.setViewportSize({width,height:1000});let release;const held=new Promise(r=>release=r);
   await page.route('**/ui-state.js',async route=>{await held;await route.continue()});
   await page.goto(base+path,{waitUntil:'commit'});
   await page.waitForFunction(()=>getComputedStyle(document.querySelector('.studio-rail')).width==='232px');const initial=await sample(page);verify(initial,path,width,'before_script');
   release();await page.waitForLoadState('domcontentloaded');await page.waitForFunction(()=>document.querySelector('main').__studioMounted===true);const after=await sample(page);verify(after,path,width,'after_script');assert.equal(after.rail.width,initial.rail.width);assert.equal(after.mainX,initial.mainX);await page.close();
  }
 }catch(error){errors.push({error:error.stack||String(error)});}finally{await browser.close();}
 const failed=cases.filter(c=>!c.passed),report={state:failed.length||errors.length?'failed':'passed',checked_at:new Date().toISOString(),scope:'Actual Chromium rendering against local hub, control writes intercepted',cases,errors,blockedWrites,screenshots:routes.flatMap(([name])=>[name+'-desktop.png',name+'-mobile.png'])};
 await fs.writeFile(out+'/validation.json',JSON.stringify(report,null,2));console.log(JSON.stringify({state:report.state,cases:cases.length,failed:failed.slice(0,12),errors,blockedWrites},null,2));if(report.state!=='passed')process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1});
