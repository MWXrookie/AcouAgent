/** Simulated model-config boundary checks; NOT live model connectivity evidence.
 * Uses real candidate HTML with controlled GET responses. No POST is allowed.
 */
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('playwright');
const base=process.env.GW_BASE_URL;if(!base)throw Error('GW_BASE_URL_required');
const out=path.resolve(process.env.FRONTEND_BROWSER_OUTPUT||'outputs/frontend-model-fixtures');await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.SKILL_BROWSER_CHANNEL||'chrome'});
const checks=[];
function check(value,name){if(!value)throw Error(name);checks.push(name);}
try{
 for(const fixture of ['unavailable','stale-selection','configured','dify']){
  const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),writes=[];
  if(fixture==='stale-selection')await context.addInitScript(()=>localStorage.setItem('acouagent_model_profile','removed-profile'));
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==new URL(base).origin){await route.continue();return;}
   if(!['GET','HEAD','OPTIONS'].includes(request.method())){writes.push(url.pathname);await route.abort();return;}
   if(url.pathname==='/api/model-profiles'){
    if(fixture==='unavailable'){await route.fulfill({status:500,contentType:'application/json',body:'{"error":"fixture_unavailable"}'});return;}
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({orchestration_backend:fixture==='dify'?'dify':'local',default_profile:'available-profile',profiles:[{id:'available-profile',label:'Fixture model',model:'fixture-model-name',configured:true}]})});return;
   }
   await route.continue();
  });
  await page.goto(base+'/portal');await page.waitForFunction(()=>document.querySelector('#model-readiness').dataset.state!=='loading');
  const state=await page.locator('#model-readiness').getAttribute('data-state');
  if(fixture==='unavailable'||fixture==='stale-selection'){
   check(state===(fixture==='unavailable'?'error':'missing'),fixture+'_state');
   await page.locator('#query-input').fill('保留这条输入');await page.locator('#ask-btn').click();await page.locator('#model-modal.open').waitFor();
   check(await page.locator('#query-input').inputValue()==='保留这条输入',fixture+'_preserves_input');
   await page.keyboard.press('Escape');check(!await page.locator('#model-modal').isVisible(),fixture+'_escape');
  }else if(fixture==='configured'){
   check(state==='configured','configured_state');
   check((await page.locator('#model-readiness-title').innerText()).includes('fixture-model-name'),'configured_model_name');
   check((await page.locator('#model-readiness-detail').innerText()).includes('配置'),'configured_not_connectivity_claim');
  }else{
   check(state==='configured','dify_platform_state');
   check((await page.locator('#model-readiness-detail').innerText()).includes('Dify'),'dify_explains_platform_model');
  }
  check(writes.length===0,fixture+'_no_chat_or_other_writes');
  await page.screenshot({path:path.join(out,fixture+'.png'),fullPage:true});await context.close();
 }
 // Delay the dialog GET independently of initial readiness, then release it only
 // after Escape. This models a slow network without arbitrary fixed sleeps.
 const slow=await browser.newContext(),slowPage=await slow.newPage();
 let requests=0,release;const gate=new Promise(resolve=>release=resolve);
 await slow.route('**/api/model-profiles',async route=>{
  requests++;if(requests>1)await gate;
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({orchestration_backend:'local',default_profile:null,profiles:[]})});
 });
 await slowPage.goto(base+'/portal');await slowPage.waitForFunction(()=>document.querySelector('#model-readiness').dataset.state==='missing');
 await slowPage.locator('.header .model-btn').click();
 await slowPage.waitForFunction(()=>document.activeElement.id==='model-modal');
 await slowPage.keyboard.press('Escape');
 check(!await slowPage.locator('#model-modal').isVisible(),'slow_config_escape_during_loading');
 const response=slowPage.waitForResponse(r=>new URL(r.url()).pathname==='/api/model-profiles');release();await response;
 await slowPage.waitForFunction(()=>document.querySelector('#model-profile').options.length>0);
 check(await slowPage.evaluate(()=>document.activeElement.classList.contains('model-btn')),'late_config_response_does_not_steal_focus');
 await slowPage.screenshot({path:path.join(out,'slow-loading-closed.png'),animations:'disabled'});await slow.close();
 await writeFile(path.join(out,'fixture-report.json'),JSON.stringify({kind:'simulated_model_configuration_boundaries',not_live_connectivity_evidence:true,checks},null,2));
 console.log(JSON.stringify({simulated_checks_passed:checks.length}));
}finally{await browser.close();}
