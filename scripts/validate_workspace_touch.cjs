'use strict';
const {chromium}=require('C:/Users/19410/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true}),cases=[],writes=[];
 await context.route('**/*',async r=>{const q=r.request();if(q.method()!=='GET'){writes.push(q.url());return r.abort()}if(!q.url().startsWith('http://127.0.0.1:8765/'))return r.abort();await r.continue()});
 try{for(const [name,path] of [['overview','/'],['architecture','/?view=architecture'],['lab','/lab'],['training','/training'],['downloads','/downloads']]){
  const page=await context.newPage();await page.goto('http://127.0.0.1:8765'+path,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.querySelector('main').__studioMounted===true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'outputs/frontend-redesign/layout/'+name+'-touch.png',animations:'disabled'});
  await page.locator('.studio-menu').tap();assert.equal(await page.locator('.studio-menu').getAttribute('aria-expanded'),'true');assert.equal(await page.locator('.studio-rail').evaluate(el=>el.getBoundingClientRect().width),232);
  if(name==='overview')await page.screenshot({path:'outputs/frontend-redesign/layout/overview-touch-menu.png',animations:'disabled'});
  await page.locator('.studio-rail-close').tap();assert.equal(await page.locator('.studio-menu').getAttribute('aria-expanded'),'false');
  if(name==='architecture'){await page.locator('.architecture-node').nth(3).tap();assert.equal(await page.locator('#inspectorTitle').textContent(),'意图模型推理');}
  if(name==='lab'){if(await page.locator('#inputTestTab').isDisabled()){assert.equal(await page.locator('#intentStart').isDisabled(),true);assert.ok(await page.locator('#inputSignal').isVisible());}else{await page.locator('#inputTestTab').tap();assert.ok(await page.locator('#inputTestPad').isVisible());await page.locator('#inputLiveTab').tap();}}
  cases.push({path,passed:true});await page.close();
 }assert.equal(writes.length,0);
 }finally{await browser.close()}
 await fs.writeFile('outputs/frontend-redesign/layout/touch-validation.json',JSON.stringify({state:'passed',checked_at:new Date().toISOString(),profile:{width:390,height:844,isMobile:true,hasTouch:true},cases,writes},null,2));console.log('Touch acceptance: '+cases.length+' pages passed; no control writes.');
})().catch(error=>{console.error(error);process.exitCode=1});
