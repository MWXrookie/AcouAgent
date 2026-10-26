/** Browser first solve -> automatic independent admission -> fresh actual reuse. */
import {createRequire} from 'node:module';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('playwright');
const base=process.env.GW_BASE_URL,profile=process.env.SKILL_TEST_MODEL_PROFILE;
if(!base||!profile||process.env.SKILL_TEST_DISPOSABLE!=='true')throw Error('explicit_disposable_gateway_and_model_required');
const out=path.resolve(process.env.SKILL_BROWSER_OUTPUT||'outputs/skill-auto-browser');await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.SKILL_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000},...(process.env.SKILL_TEST_RECORD_VIDEO==='true'?{recordVideo:{dir:out,size:{width:1440,height:1000}}}:{})}),reports=[];
async function run(spec,label){
 await page.goto(base+'/portal');await page.evaluate(p=>sessionStorage.setItem('acouagent_byok_profile',p),profile);
 await page.reload();await page.locator('#query-input').waitFor();
 await page.evaluate(()=>{const original=window.fetch;window.__chatCapture=null;window.fetch=async(...args)=>{const r=await original(...args);if(args[0]==='/chat')r.clone().text().then(t=>window.__chatCapture=t);return r;};});
 const query='请实际执行以下明确的二维频域声学仿真，输出本次求解的幅值图和数值核验。完全匹配的已验证技能可以重新实际执行复用；未命中时按受控流程首次求解和自动学习。不得猜测参数或用历史图代替。\n'+JSON.stringify({scenario_type:spec.scenario_type,simulation_params:spec.parameters,result_spec:spec.result_spec});
 await page.locator('#query-input').fill(query);await page.locator('#ask-btn').click();
 await page.waitForFunction(()=>typeof window.__chatCapture==='string',null,{timeout:180000});
 const events=(await page.evaluate(()=>window.__chatCapture)).trim().split('\n').map(s=>JSON.parse(s));
 const result=events.filter(e=>e.event==='result').at(-1);
 await page.waitForFunction(()=>!document.querySelector('#ask-btn').disabled,null,{timeout:180000});
 await page.screenshot({path:path.join(out,label+'.png'),fullPage:true});
 await writeFile(path.join(out,label+'.json'),JSON.stringify({events,result},null,2));
 if(result?.workflow_status!=='succeeded')throw Error(label+':'+(result?.error_code||result?.report));
 await page.waitForFunction(()=>{const images=[...document.querySelectorAll('#chat-log img')];return images.length>0&&images.every(i=>i.complete&&i.naturalWidth>0);},null,{timeout:15000});
 await page.locator('#chat-log img').last().scrollIntoViewIfNeeded();
 await page.screenshot({path:path.join(out,label+'-heatmap.png'),fullPage:true});
 return result;
}
try{
 await page.goto(base+'/skills/authoring');
 const templates=await (await page.request.get(base+'/skills/api/v1/spec-schema')).json();
 for(const caseId of ['B02','B03']){
  const spec=templates.templates[caseId];
  let first;
  if(process.env.SKILL_TEST_RESUME==='true'){
   try{first=JSON.parse(await readFile(path.join(out,caseId+'-first.json'),'utf8')).result;}
   catch(e){if(e.code!=='ENOENT')throw e;}
  }
  first=first||await run(spec,caseId+'-first');
  if(first.quality_level!=='Q2'||first.execution_reuse_verified===true||!first.learning?.recorded)throw Error(caseId+'_first_solve_not_ungraded_learning_source');
  const query={scenario_type:spec.scenario_type,parameters:{normalizer_version:'normalizer_v2',parameters:spec.parameters,result_spec:spec.result_spec}};
  const deadline=Date.now()+90000;let ready=false;
  while(Date.now()<deadline){const found=await (await page.request.post(base+'/skills/search',{data:query})).json();if(found.skills?.some(s=>s.scope==='general-helmholtz.v1')){ready=true;break;}await new Promise(r=>setTimeout(r,1500));}
  if(!ready)throw Error(caseId+'_automatic_admission_not_ready');
  const repeated=await run(spec,caseId+'-reuse');
  if(repeated.quality_level!=='Q4'||repeated.execution_reuse_verified!==true||repeated.run_id===first.run_id)throw Error(caseId+'_fresh_reuse_not_verified');
  reports.push({case:caseId,source_run:first.run_id,reuse_run:repeated.run_id,first_quality:first.quality_level,reuse_quality:repeated.quality_level,first_learning:first.learning});
 }
 await writeFile(path.join(out,'automatic-browser-report.json'),JSON.stringify({kind:'real_model_browser_automatic_learning',reports,manual_per_item_approval:0,production_database_writes:0},null,2));
 console.log(JSON.stringify({automatic_cases:reports.length,verified:reports}));
}finally{const video=page.video();try{if(video){await page.close();await video.saveAs(path.join(out,'automatic-learning.webm'));}}finally{await browser.close();}}
