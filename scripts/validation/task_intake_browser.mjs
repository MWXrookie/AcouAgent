/** Actual browser preview and interaction checks. Not solver/model acceptance. */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON||path.join(root,'package.json'));
const {chromium}=require('playwright');
const base=process.env.GW_BASE_URL;
if(!base||!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||process.env.TASK_PREVIEW_ONLY!=='true')throw Error('explicit_loopback_preview_required');
const output=path.resolve(process.env.TASK_BROWSER_OUTPUT||path.join(root,'auxiliary/work/task-contract-browser'));
await mkdir(output,{recursive:true});
const original=JSON.parse(await readFile(path.join(root,'tests/fixtures/task_intake/room_reference.json'),'utf8')).original_query;
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),checks=[],errors=[];
page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
const requests=[];page.on('request',r=>{if(r.url().includes('/api/task-intake'))requests.push({method:r.method(),url:r.url()})});
const watchdog=setTimeout(async()=>{errors.push('browser_acceptance_global_timeout');await writeFile(path.join(output,'timeout-report.json'),JSON.stringify({checks,errors,requests,not_accepted:true},null,2));browser.close().catch(()=>{});process.exit(2)},60000);
await page.addInitScript(()=>{const original=window.fetch;window.__captureCancellation=false;window.__taskCancellation=null;window.fetch=async(...args)=>{const capture=window.__captureCancellation&&args[0]==='/api/task-intake/cancel';const response=await original(...args);if(capture)response.clone().json().then(value=>window.__taskCancellation=value).catch(()=>{});return response;};});
const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name)};
page.on('pageerror',e=>errors.push(e.message));
async function screenshot(name){await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(output,name),animations:'disabled'});}
try{
 await page.goto(base+'/portal');await page.waitForFunction(()=>document.getElementById('model-readiness').dataset.state!=='loading');
 await page.locator('#query-input').fill(original);await page.locator('#ask-btn').click();await page.locator('#task-review[open]').waitFor();
 const text=await page.locator('.task-parameters').innerText();
 check(text.includes('房间长度')&&text.includes('4 m')&&text.includes('3 m')&&text.includes('343 m/s')&&text.includes('1.21 kg/m³'),'original_latex_recognised_and_shown_in_SI');
 check(await page.locator('#task-source').inputValue()===original,'original_latex_preserved');
 check(await page.locator('#task-review .primary').isDisabled(),'unsupported_runtime_cannot_execute');
 await screenshot('01-original-parameters.png');
 await page.locator('#task-review details').first().locator('summary').click();
 check((await page.locator('#task-review').innerText()).includes('\\text{m}'),'original_source_evidence_visible');
 await screenshot('02-source-evidence.png');
 const edit=page.locator('#task-source');await page.locator('#task-review details').last().locator('summary').click();
 await edit.fill('二维矩形房间，刚性壁面，Lx=400cm，c=343m/s，前10个模态频率');
 await page.getByRole('button',{name:'重新识别',exact:true}).click();
 await page.waitForFunction(()=>document.getElementById('task-review').getAttribute('aria-busy')==='false'&&document.querySelector('.task-issues').textContent.includes('房间宽度'));
 check((await page.locator('.task-parameters').innerText()).includes('单位已换算'),'centimetres_converted_without_filling_missing_width');
 check(!await page.locator('.task-issues').innerText().then(t=>t.includes('声速')),'does_not_reask_explicit_speed');
 await screenshot('03-targeted-missing-width.png');
 await edit.fill('二维矩形房间，刚性壁面，Lx=4m，Lx=5m，Ly=3m，c=343m/s，前10个模态频率');
 await page.getByRole('button',{name:'重新识别',exact:true}).click();
 await page.waitForFunction(()=>document.getElementById('task-review').getAttribute('aria-busy')==='false'&&document.querySelector('.task-issues').textContent.includes('多个不同值'));
 check(await page.locator('#task-review .primary').isDisabled(),'conflicting_lengths_cannot_execute');
 await screenshot('04-conflict.png');
 await page.setViewportSize({width:390,height:844});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile_page_no_horizontal_overflow');
 check(await page.locator('#task-review').evaluate(e=>e.scrollWidth<=e.clientWidth),'mobile_review_no_horizontal_overflow');
 await screenshot('05-mobile.png');
 await page.evaluate(()=>{window.__captureCancellation=true;window.__taskCancellation=null;});
 await page.getByRole('button',{name:'取消',exact:true}).click();check(!await page.locator('#task-review').evaluate(e=>e.open),'cancel_closes_without_execution');
 // Read the actual native fetch body; CDP response.json can wait indefinitely
 // after a short-lived cancellation request has left its resource buffer.
 await page.waitForFunction(()=>window.__taskCancellation?.cancelled===true,null,{timeout:8000});
 check(true,'cancel_invalidates_server_confirmation');
 check(await page.locator('#query-input').inputValue()===original,'cancel_preserves_original_input');
 check(errors.length===0,'no_page_javascript_errors');
 console.log(JSON.stringify({passed:checks.length}));
}catch(error){errors.push(error.message);await screenshot('failure.png').catch(()=>{});process.exitCode=1;}
finally{clearTimeout(watchdog);await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_browser_task_contract_preview',checks,errors,requests,model_calls:0,solver_calls:0,not_solver_acceptance:true},null,2));await browser.close();}
