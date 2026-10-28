/** Read-only UI acceptance. No model requests, credentials or server writes.
 * GW_BASE_URL required; FRONTEND_BROWSER_OUTPUT, SKILL_BROWSER_CHANNEL optional.
 * Uses an isolated browser context. Run separately against disabled/enabled deployments.
 */
import {createRequire} from 'node:module';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('playwright');
const base=process.env.GW_BASE_URL;
if(!base)throw Error('GW_BASE_URL_required');
const out=path.resolve(process.env.FRONTEND_BROWSER_OUTPUT||'outputs/frontend-workbench-browser');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.SKILL_BROWSER_CHANNEL||'chrome'});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage(),checks=[],errors=[],mutations=[];
page.on('pageerror',e=>errors.push(e.message));
// Guard against accidental submission even if a page changes in a later release.
await context.route('**/*',async route=>{
 const r=route.request();
 if(new URL(r.url()).origin===new URL(base).origin&&!['GET','HEAD','OPTIONS'].includes(r.method())){
  mutations.push(r.method()+' '+new URL(r.url()).pathname);await route.abort();return;
 }
 await route.continue();
});
function check(value,name){if(!value)throw Error(name);checks.push(name);}
async function capture(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});}
async function open(route){
 await page.goto(base+route);await page.locator('.side-link').first().waitFor();
 if(route==='/portal')await page.waitForFunction(()=>document.querySelector('#model-readiness').dataset.state!=='loading');
 if(route==='/dashboard')await page.waitForFunction(()=>/^\d+$/.test(document.querySelector('#stat-total').textContent));
 if(route==='/cache')await page.waitForFunction(()=>window.cachedData!==undefined&&cachedData!==null);
 if(route==='/skills')await page.locator('#list button.skill').first().waitFor();
 if(route==='/skills/authoring')await page.waitForFunction(()=>!document.body.textContent.includes('正在读取标准模板…'));
}
async function noOverflow(name){check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),name);}
try{
 await open('/portal');await page.locator('#query-input').waitFor();
 check(await page.locator('.side-link[aria-current="page"]').count()===1,'portal_current_navigation');
 await page.waitForFunction(()=>document.querySelector('#model-readiness').dataset.state!=='loading');
 const modelState=await page.locator('#model-readiness').getAttribute('data-state');
 check(['missing','error','configured','dify'].includes(modelState),'model_readiness_explicit');
 await page.locator('#query-input').fill('本轮只检查界面，不提交求解');
 await page.locator('.header').getByRole('button',{name:'模型设置',exact:true}).click();
 await page.locator('#model-profile option').first().waitFor({state:'attached'});
 check(await page.locator('#model-modal').isVisible(),'model_settings_open');
 await capture('01-model-settings');
 await page.getByRole('button',{name:'取消',exact:true}).click();
 check(await page.locator('#query-input').inputValue()==='本轮只检查界面，不提交求解','cancel_preserves_question');
 if(modelState==='missing'||modelState==='error'){
  await page.locator('#ask-btn').click();
  await page.locator('#model-modal.open').waitFor();
  await page.waitForFunction(()=>document.activeElement?.id==='model-profile');
  check(await page.locator('#query-input').inputValue()==='本轮只检查界面，不提交求解','missing_model_preserves_question');
  check(await page.locator('#model-modal').isVisible(),'missing_model_opens_settings');
  await page.keyboard.press('Escape');
  check(!await page.locator('#model-modal').isVisible(),'model_escape_closes');
  check(await page.evaluate(()=>document.activeElement.id==='ask-btn'),'model_focus_returns_to_trigger');
 }
 await page.locator('#query-input').focus();await page.keyboard.press('Tab');
 check(await page.evaluate(()=>document.activeElement?.tagName==='BUTTON'),'input_keyboard_next_action');
 await capture('02-portal');
 await open('/skills');await page.locator('#list button.skill').first().waitFor();
 await page.locator('#list button.skill').first().click();
 await page.waitForFunction(()=>document.querySelector('#message').textContent==='详情已读取。');
 check(await page.locator('#list button.skill[aria-pressed="true"]').count()===1,'skill_selection_accessible');
 await capture('03-skill-detail');
 await open('/skills/authoring');
 await page.waitForFunction(()=>!document.body.textContent.includes('正在读取标准模板…'));
 const disabled=await page.locator('#availability').isVisible();
 if(disabled){
  check(!await page.locator('#error-box').isVisible(),'disabled_authoring_not_input_error');
  check(!await page.locator('#authoring-workspace').isVisible(),'disabled_authoring_workspace_hidden');
 }else{
  check(await page.locator('#save').isDisabled(),'enabled_authoring_locked_save');
  check(await page.locator('#step-edit').getAttribute('aria-current')==='step','authoring_first_step_visible');
 }
 await capture(disabled?'04-authoring-disabled':'04-authoring-locked');
 await open('/dashboard');
 await page.waitForFunction(()=>/^\d+$/.test(document.querySelector('#stat-total').textContent));
 check((await page.locator('#list-scope').innerText()).includes('已加载'),'dashboard_loaded_scope');
 check(!await page.locator('#fail-analysis').getAttribute('open'),'failure_analysis_collapsed');
 await page.locator('#search-input').fill('no-match-frontend-acceptance-8c370');
 check((await page.locator('#filter-count').innerText()).includes('0'),'dashboard_search_zero');
 await page.locator('#search-input').fill('');await capture('05-dashboard');
 await open('/cache');await page.waitForFunction(()=>window.cachedData!==undefined&&cachedData!==null);
 await page.locator('.filter-btn').filter({hasText:'连续失败'}).click();
 if(await page.getByRole('button',{name:'清除筛选',exact:true}).isVisible()){
  await page.getByRole('button',{name:'清除筛选',exact:true}).click();
  check(await page.locator('.filter-btn.active').innerText()==='全部','cache_clear_filter');
 }else{await page.locator('.filter-btn').filter({hasText:'全部'}).click();checks.push('cache_nonempty_at_risk_no_empty_state');}
 await capture('06-cache');
 await page.locator('.design-theme').click();
 check(await page.locator('html').getAttribute('data-theme')==='dark','dark_theme_switch');
 await page.waitForFunction(()=>document.querySelector('.design-theme').getAnimations().every(a=>a.playState==='finished'));
 await capture('07-cache-dark');await page.reload();
 check(await page.locator('html').getAttribute('data-theme')==='dark','theme_persists_reload');
 await page.locator('.design-theme').click();
 for(const route of ['/portal','/skills','/skills/authoring','/dashboard','/cache']){
  await page.setViewportSize({width:390,height:844});await open(route);
  if(route==='/portal'){
   check(!await page.locator('.session-section').isVisible(),'mobile_history_initially_collapsed');
   await page.locator('.history-toggle').click();
   check(await page.locator('.session-section').isVisible(),'mobile_history_opens');
   await page.locator('.session-open').first().click();
   check(!await page.locator('.session-section').isVisible(),'mobile_session_click_closes_history');
   await page.locator('.history-toggle').click();await page.keyboard.press('Escape');
   check(!await page.locator('.session-section').isVisible(),'mobile_history_escape_closes');
  }
  if(route==='/skills'){await page.locator('#list button.skill').first().click();await page.waitForFunction(()=>document.querySelector('#message').textContent==='详情已读取。');}
  await noOverflow('mobile_no_overflow_'+route);await capture('mobile-'+route.replaceAll('/','-'));
  await page.screenshot({path:path.join(out,'mobile-viewport-'+route.replaceAll('/','-')+'.png'),animations:'disabled'});
 }
 check(mutations.length===0,'no_server_mutations_or_model_calls');
 check(errors.length===0,'no_browser_javascript_errors');
 await writeFile(path.join(out,'report.json'),JSON.stringify({kind:'live_read_only_frontend_acceptance',checks,errors,mutations,authoring_enabled:!disabled},null,2));
 console.log(JSON.stringify({passed:checks.length,authoring_enabled:!disabled}));
}finally{await browser.close();}



