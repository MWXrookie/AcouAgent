/** Real UI -> bound confirmation -> isolated durable ledger -> real executor. */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON||path.join(root,'package.json'));
const {chromium}=require('playwright');
const base=process.env.GW_BASE_URL,output=process.env.TASK_BROWSER_OUTPUT;
if(!base||!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||!output||process.env.ROOM_REAL_SANDBOX_QA!=='true')throw Error('explicit_real_sandbox_qa_required');
await mkdir(output,{recursive:true});
const fixture=JSON.parse(await readFile(path.join(root,'tests/fixtures/task_intake/room_reference.json'),'utf8'));
const cases=[fixture.original_query,
 '二维矩形房间，Lx=400cm，Ly=3000mm，声速343m/s，四壁刚性，前10个模态频率，不包括零模态',
 '二维矩形房间，Lx=3m，Ly=3m，声速343m/s，四壁刚性，前9个模态频率，包含零模态'];
const browser=await chromium.launch({channel:process.env.SKILL_BROWSER_CHANNEL||'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
const checks=[],errors=[],runs=[];let confirms=0;
page.on('pageerror',error=>errors.push(error.message));
page.on('request',r=>{if(r.url().endsWith('/api/task-intake/confirm'))confirms++});
await page.addInitScript(()=>{const original=window.fetch;window.__roomResult=null;window.__roomBody=null;window.fetch=async(...args)=>{const response=await original(...args);if(args[0]==='/api/task-intake/confirm'){window.__roomBody=JSON.parse(args[1].body);response.clone().json().then(value=>window.__roomResult=value).catch(()=>{});}return response;};});
const watchdog=setTimeout(async()=>{errors.push('global_timeout');await writeFile(path.join(output,'timeout-report.json'),JSON.stringify({checks,errors,runs},null,2));browser.close().catch(()=>{});process.exit(2)},180000);
function check(ok,name){if(!ok)throw Error(name);checks.push(name)}
try{
 await page.goto(base+'/portal');await page.waitForFunction(()=>document.getElementById('model-readiness').dataset.state!=='loading');
 check((await page.locator('#model-readiness-title').innerText())==='房间模态可直接计算','room_capability_does_not_require_or_claim_model_connection');
 for(let index=0;index<cases.length;index++){
  await page.evaluate(()=>{window.__roomResult=null});
  await page.locator('#query-input').fill(cases[index]);await page.locator('#ask-btn').click();await page.locator('#task-review[open]').waitFor();
  check(await page.locator('#task-review .primary').isDisabled(),`case_${index+1}_assumptions_require_acceptance`);
  for(const checkbox of await page.locator('#task-review fieldset input').all())await checkbox.check();
  check(await page.locator('#task-review .primary').isEnabled(),`case_${index+1}_confirmed_contract_can_execute`);
  await page.screenshot({path:path.join(output,`case-${index+1}-confirmation.png`)});
  await page.locator('#task-review .primary').click();
  await page.waitForFunction(()=>window.__roomResult!==null,null,{timeout:55000});
  const result=await page.evaluate(()=>window.__roomResult);runs.push(result);
  check(result.workflow_status==='succeeded'&&result.validation?.verdict==='normal',`case_${index+1}_actual_solver_and_physics_passed`);
  check(result.validation.max_relative_error<.001&&result.skill_admitted===false&&result.validation.quality_level===null,`case_${index+1}_independent_room_gate_no_false_skill_admission`);
  await page.locator('#chat-log .assistant').last().locator('table').waitFor();
  check((await page.locator('#chat-log .assistant').last().innerText()).includes('沙箱数值解'),`case_${index+1}_actual_table_visible`);
  await page.locator('#chat-log .assistant').last().screenshot({path:path.join(output,`case-${index+1}-result.png`)});
  await page.locator('#chat-log').evaluate(log=>{log.scrollTop=log.lastElementChild.offsetTop-log.offsetTop});
  await page.screenshot({path:path.join(output,`case-${index+1}-result-overview.png`)});
  check(await page.locator('#chat-log .assistant').last().getByRole('link',{name:'公式及刚性壁依据：MathWorks'}).count()===1,`case_${index+1}_reference_link_visible`);
  const row=await page.evaluate(async id=>(await fetch('/dashboard/api/executions/by-run/'+id)).json(),result.run_id);
  check(row.execution?.run_id===result.run_id&&row.execution.tool_name==='controlled_room_modes_2d',`case_${index+1}_dashboard_matches_real_run`);
  const replay=await page.evaluate(async()=>{const r=await fetch('/api/task-intake/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(window.__roomBody)});return r.status});
  check(replay===400,`case_${index+1}_confirmation_replay_rejected`);
 }
 check(new Set(runs.map(r=>r.run_id)).size===cases.length,'each_new_request_has_a_unique_real_run');
 check(JSON.stringify(runs[0].validation.modes)===JSON.stringify(runs[1].validation.modes),'equivalent_units_have_identical_results');
 await page.reload();await page.getByRole('button',{name:'查询上次确认任务'}).click();
 await page.waitForFunction(()=>document.getElementById('chat-log').innerText.includes('独立解析核对通过'));
 check(true,'reload_recovers_durable_result_without_resubmitting');
 const before=confirms;
 await page.locator('#query-input').fill('二维矩形房间，Lx=4m，Lx=5m，Ly=3m，声速343m/s，四壁刚性，前10个模态频率');await page.locator('#ask-btn').click();await page.locator('#task-review[open]').waitFor();
 check(await page.locator('#task-review .primary').isDisabled(),'conflicting_parameters_still_cannot_execute');
 await page.getByRole('button',{name:'取消',exact:true}).click();check(confirms===before,'negative_case_has_zero_solver_submissions');
 await page.goto(base+'/dashboard');await page.waitForFunction(()=>document.querySelectorAll('tbody tr').length>=3);
 check((await page.locator('tbody').innerText()).includes('最低非零模态'),'dashboard_shows_modal_hz_instead_of_pressure');
 await page.locator('tr.execution-row').first().click();
 check((await page.locator('.detail-row.open').innerText()).includes('压力幅值与传播信号指标不适用'),'modal_report_does_not_claim_no_pressure_signal_failure');
 await page.screenshot({path:path.join(output,'dashboard.png')});
 check(errors.length===0,'no_page_javascript_errors');
 console.log(JSON.stringify({passed:checks.length,actual_runs:runs.length}));
}catch(error){errors.push(error.message);await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});process.exitCode=1;}
finally{clearTimeout(watchdog);await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'real_room_browser_sandbox_qa',checks,errors,runs,model_calls:0,transport:process.env.ROOM_PRODUCTION_QA==='true'?'browser_direct_production_gateway':'local_isolated_gateway_ssh_relay_to_existing_executor',production_database_writes:process.env.ROOM_PRODUCTION_QA==='true',production_executions:process.env.ROOM_PRODUCTION_QA==='true'?runs.length:0,skill_admissions:0},null,2));await browser.close();}
