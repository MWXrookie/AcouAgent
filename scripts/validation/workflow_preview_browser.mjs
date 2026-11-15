/** Source-accurate previews across existing initial/frequency contracts. */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON||path.join(root,'package.json'));
const {chromium}=require('playwright');
const base=process.env.GW_BASE_URL,output=process.env.WORKFLOW_PREVIEW_OUTPUT,caseFile=process.env.WORKFLOW_PREVIEW_CASES;
if(!base||!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||!output||!caseFile)throw Error('explicit_preview_qa_required');
const cases=JSON.parse(await readFile(caseFile,'utf8'));await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1100}});
page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
const checks=[],errors=[],results=[];let confirmations=0;
page.on('pageerror',error=>errors.push(error.message));page.on('request',r=>{if(r.url().endsWith('/api/task-intake/confirm'))confirmations++});
await page.addInitScript(()=>{const original=window.fetch;window.__preview=null;window.fetch=async(...args)=>{const r=await original(...args);if(args[0]==='/api/task-intake')r.clone().json().then(v=>window.__preview=v);return r}});
const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name)};
const watchdog=setTimeout(async()=>{errors.push('global_timeout');await writeFile(path.join(output,'timeout-report.json'),JSON.stringify({checks,errors,results},null,2));browser.close().catch(()=>{});process.exit(2)},180000);
try{
 await page.goto(base+'/portal');await page.waitForFunction(()=>document.getElementById('model-readiness').dataset.state==='configured');
 const before=await page.evaluate(async()=>(await fetch('/dashboard/api/executions')).json());
 for(const item of cases){
  await page.evaluate(()=>window.__preview=null);
  await page.locator('#query-input').fill(item.query);await page.locator('#ask-btn').click();await page.locator('#task-review[open]').waitFor({timeout:110000});
  const result=await page.evaluate(()=>window.__preview);results.push({id:item.id,...result});
  check(result?.contract.task_kind===item.kind,item.id+'_kind');check(result.contract.status===item.status,item.id+'_status');
  check(await page.locator('#task-source').inputValue()===item.query,item.id+'_original_source_preserved');
  if(item.missing){const field=result.contract.fields.find(f=>f.name===item.missing);check(field?.status==='missing',item.id+'_missing_field_not_invented');check((await page.locator('.task-parameters').innerText()).includes(field.label),item.id+'_required_missing_field_visible')}
  if(item.kind==='initial_pressure_propagation')check(!result.contract.fields.some(f=>f.name==='source_frequency'),item.id+'_no_driven_frequency_requested');
  if(item.kind==='frequency_domain_field'&&item.status==='ready_for_confirmation')check((await page.locator('.task-parameters').innerText()).includes('数值源归一化'),item.id+'_result_contract_readable');
  await page.screenshot({path:path.join(output,item.id+'.png'),animations:'disabled'});
  if(item.status!=='ready_for_confirmation')check(await page.locator('#task-review .primary').isDisabled(),item.id+'_cannot_execute');
  await page.getByRole('button',{name:'取消',exact:true}).click();
 }
 const after=await page.evaluate(async()=>(await fetch('/dashboard/api/executions')).json());
 check(confirmations===0&&before.stats.total===after.stats.total,'all_previews_zero_executions');check(errors.length===0,'no_page_javascript_errors');
 console.log(JSON.stringify({passed:checks.length,cases:results.length}));
}catch(error){errors.push(error.message);await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});process.exitCode=1}
finally{clearTimeout(watchdog);await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_multitype_workflow_preview',checks,errors,results,solver_calls:0},null,2));await browser.close()}
