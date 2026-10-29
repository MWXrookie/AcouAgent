/** Real browser first solve -> independent automatic admission -> fresh reuse. */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON);
const {chromium}=require('playwright');
const base=process.env.GW_BASE_URL,out=process.env.LEARNING_BROWSER_OUTPUT;
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')||!out||process.env.WORKFLOW_REAL_MODEL_QA!=='true')throw Error('explicit_learning_qa_required');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});
const checks=[],results=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));
await page.addInitScript(()=>{const original=window.fetch;window.fetch=async(...a)=>{const r=await original(...a);if(a[0]==='/api/task-intake')r.clone().json().then(v=>window.__prepared=v);if(a[0]==='/api/task-intake/confirm')r.clone().json().then(v=>window.__final=v);return r}});
function check(value,name){if(!value)throw Error(name);checks.push(name)}
async function save(){await writeFile(path.join(out,'report.json'),JSON.stringify({checks,results,errors,production_database_writes:0},null,2))}
try{
 for(const [caseId,file] of (process.env.LEARNING_BOUNDARY_ONLY==='true'?[]:[['B01','manual-skill.json'],['B02','b02-manual-skill.json'],['B03','b03-manual-skill.json']])){
  const spec=JSON.parse(await readFile('docs/skills-next/examples/'+file,'utf8'));
  const question={scenario_type:spec.scenario_type,simulation_params:spec.parameters,result_spec:spec.result_spec};
  for(const phase of (caseId==='B01'?['source','reuse','reuse-repeat-2','reuse-repeat-3']:['source','reuse'])){
   await page.goto(base+'/portal');await page.waitForFunction(()=>document.getElementById('model-readiness').dataset.state==='configured');
   await page.evaluate(()=>{window.__prepared=null;window.__final=null;currentRequirement=''});
   // Reorder explicit declarations on reuse; preserve every physical value.
   const request=phase==='source'?question:{result_spec:question.result_spec,simulation_params:Object.fromEntries(Object.entries(question.simulation_params).reverse()),scenario_type:question.scenario_type};
   await page.locator('#query-input').fill(JSON.stringify(request,null,phase==='source'?2:4));await page.locator('#ask-btn').click();
   await page.locator('#task-review[open]').waitFor({timeout:110000});
   const prepared=await page.evaluate(()=>window.__prepared);
   check(prepared?.contract.status==='ready_for_confirmation',caseId+' '+phase+' complete parameters');
   for(const c of await page.locator('#task-review fieldset input').all())await c.check();
   await page.screenshot({path:path.join(out,caseId+'-'+phase+'-confirmation.png'),animations:'disabled'});
   await page.locator('#task-review .primary').click();
   await page.waitForFunction(()=>window.__final!=null,null,{timeout:240000});
   const final=await page.evaluate(()=>window.__final);
   results.push({case:caseId,phase,prepared,final});await save();
   check(final.workflow_status==='succeeded',caseId+' '+phase+' actual successful execution');
   check(phase==='source'?final.quality_level==='Q2':final.quality_level==='Q4'&&final.execution_reuse_verified===true,caseId+' '+phase+' expected independent quality');
   await page.waitForFunction(()=>!document.getElementById('ask-btn').disabled);
   await page.locator('#chat-log').evaluate(n=>{n.scrollTop=n.lastElementChild.offsetTop-n.offsetTop});
   await page.screenshot({path:path.join(out,caseId+'-'+phase+'-result.png'),animations:'disabled'});
   const row=await page.evaluate(async id=>(await fetch('/dashboard/api/executions/by-run/'+id)).json(),final.run_id);
   check(row.execution?.run_id===final.run_id,caseId+' '+phase+' same-run dashboard');
   if(phase==='source'){
    // Worker advances existing durable jobs; no manual admission API is called.
    await page.goto(base+'/skills');
    await page.waitForFunction(async()=>{const r=await(await fetch('/skills/api')).json();return JSON.stringify(r).includes('general_validated')},null,{timeout:150000,polling:2500});
    await page.reload();await page.screenshot({path:path.join(out,caseId+'-learned-library.png'),animations:'disabled'});
   }else{
    const source=results.find(r=>r.case===caseId&&r.phase==='source');
    check(final.run_id!==source.final.run_id,caseId+' reuse is fresh solver execution');
   }
   console.log(JSON.stringify({case:caseId,phase,status:final.workflow_status,quality:final.quality_level}));await save();
  }
 }
 // Changed physics cannot inherit the exact-case independent certification.
 await page.goto(base+'/portal');
 const before=await page.evaluate(async()=>(await fetch('/dashboard/api/executions')).json());
 let boundaryRuns=0;
 for(const [caseId,file] of [['B01','manual-skill.json'],['B02','b02-manual-skill.json'],['B03','b03-manual-skill.json']]){
  const spec=JSON.parse(await readFile('docs/skills-next/examples/'+file,'utf8'));
  const params=structuredClone(spec.parameters);
  if(caseId==='B01')params.frequency_hz*=1.01;
  if(caseId==='B02')params.material_regions[0].sound_speed_m_s=2400;
  if(caseId==='B03')params.source_index=[33,64];
  await page.goto(base+'/portal');await page.waitForFunction(()=>document.getElementById('model-readiness').dataset.state==='configured');
  await page.evaluate(()=>{window.__prepared=null;window.__final=null;currentRequirement=''});
  await page.locator('#query-input').fill(JSON.stringify({scenario_type:spec.scenario_type,simulation_params:params,result_spec:spec.result_spec}));await page.locator('#ask-btn').click();
  await page.locator('#task-review[open]').waitFor({timeout:110000});
  const preview=await page.evaluate(()=>window.__prepared);
  if(preview.contract.status==='ready_for_confirmation'){
   for(const c of await page.locator('#task-review fieldset input').all())await c.check();
   await page.locator('#task-review .primary').click();await page.waitForFunction(()=>window.__final!=null,null,{timeout:90000});
   const final=await page.evaluate(()=>window.__final);results.push({case:caseId,phase:'changed-physics-negative',final});
   check(final.quality_level!=='Q4'&&!final.execution_reuse_verified&&final.learning?.admitted!==true,caseId+' changed physics cannot silently inherit certified reuse');
   if(final.run_id){boundaryRuns++;const row=await page.evaluate(async id=>(await fetch('/dashboard/api/executions/by-run/'+id)).json(),final.run_id);check(row.execution?.run_id===final.run_id,caseId+' changed physics fresh execution is traceable');}
  }else{
   check(await page.locator('#task-review .primary').isDisabled(),caseId+' changed physics blocked by explicit contract');
   await page.getByRole('button',{name:'取消',exact:true}).click();
  }
 }
 const after=await page.evaluate(async()=>(await fetch('/dashboard/api/executions')).json());
 check(after.stats.total===before.stats.total+boundaryRuns,'changed physics executions are fully accounted for');
 check(errors.length===0,'no browser javascript errors');
}catch(e){errors.push(e.message);process.exitCode=1;await page.screenshot({path:path.join(out,'failure.png'),animations:'disabled'}).catch(()=>{})}
finally{await save();await browser.close()}
