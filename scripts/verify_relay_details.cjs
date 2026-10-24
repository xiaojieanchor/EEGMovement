'use strict';
// Own Hub UI, read-only local API; screenshots retain truthful expired status.
const {chromium}=require('C:/Users/19410/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
(async()=>{
 const out='docs/gnautilus/status_link_fix_20261023',browser=await chromium.launch({headless:true}),errors=[],writes=[],checks=[];
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(String(e)));
 await page.route('**/*',async route=>{const r=route.request();if(r.method()!=='GET'){writes.push(r.url());return route.abort()}return route.continue()});
 for(const [name,width,height]of [['desktop',1440,1000],['mobile',390,844]]){
  await page.setViewportSize({width,height});await page.goto('http://127.0.0.1:8765/');
  if(width<900)await page.locator('.studio-menu').click();await page.locator('#settings').click();
  await page.locator('#acquisitionClientDetails').waitFor();await page.waitForTimeout(500);
  const view=await page.locator('#settingsDialog').evaluate(e=>({scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,rows:e.querySelectorAll('#acquisitionClientDetails>div').length,text:e.textContent}));
  assert(view.rows>=15);assert(view.scrollWidth<=view.clientWidth+2,JSON.stringify(view));assert(view.text.includes('尚未测量'));
  await page.screenshot({path:out+'/hub-details-'+name+'.png'});checks.push({name,width,height,rows:view.rows,horizontalOverflow:false});
 }
 assert.equal(errors.length,0,JSON.stringify(errors));assert.equal(writes.length,0);
 await fs.writeFile(out+'/hub-details-ui.json',JSON.stringify({checks,errors,writes,scope:'real local Hub dialog, no EEG or control writes'},null,2));await browser.close();
 console.log(JSON.stringify({passed:true,checks}));
})().catch(e=>{console.error(e);process.exitCode=1});
