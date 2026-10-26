/** Run-bound negative -> explicit new validation -> readmission through real UI. */
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('playwright');
const base=process.env.GW_BASE_URL,token=process.env.SKILL_TEST_ADMIN_TOKEN;
if(!base||!token||!process.env.SKILL_TEST_AUTHORING_REPORT||!process.env.SKILL_TEST_REUSE_REPORT||process.env.SKILL_TEST_DISPOSABLE!=='true')throw Error('explicit_disposable_reports_and_token_required');
const author=JSON.parse(await readFile(process.env.SKILL_TEST_AUTHORING_REPORT,'utf8'));
const reused=JSON.parse(await readFile(process.env.SKILL_TEST_REUSE_REPORT,'utf8')).result;
const out=path.resolve(process.env.SKILL_BROWSER_OUTPUT||'outputs/skill-feedback-browser');await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.SKILL_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),checks=[];
const headers={'X-Admin-Token':token};
function check(value,name){if(!value)throw Error(name);checks.push(name);}
async function click(id){await page.locator('#'+id).click();}
async function admin(){await page.locator('#admin').fill(token);await page.locator('#reason').fill('阶段6人工反馈与独立重验检查');}
try{
 const draft=await (await page.request.get(base+'/skills/api/v1/drafts/'+author.draft_id,{headers})).json();
 const listed=await (await page.request.get(base+'/skills/api')).json();
 const item=listed.items.find(x=>x.scenario_type==='point_source_2d'&&x.quality_level==='Q4'&&x.state_text===draft.spec.name);
 if(!item)throw Error('manual_general_skill_required');
 await page.goto(base+'/skills?skill_id='+encodeURIComponent(item.skill_id));
 await page.locator('#detail:not([hidden])').waitFor();
 await admin();await page.locator('#run').fill(reused.run_id);await click('negative');
 await page.waitForFunction(()=>!document.querySelector('#revalidate').hidden);
 let detail=await (await page.request.get(base+'/skills/api/'+item.skill_id)).json();
 check(detail.item.status==='questioned','negative_updates_visible_lifecycle');
 // Full input comes from the protected draft, not a display label.
 const searchBody={scenario_type:draft.spec.scenario_type,parameters:{normalizer_version:'normalizer_v2',parameters:draft.spec.parameters,result_spec:draft.spec.result_spec}};
 let search=await (await page.request.post(base+'/skills/search',{data:searchBody})).json();
 check(!search.skills.some(s=>s.skill_id===item.skill_id),'negative_stops_delivery_immediately');
 await page.screenshot({path:path.join(out,'01-questioned.png'),fullPage:true});
 await admin();await click('revalidate');
 await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('已退回 Q2'));
 detail=await (await page.request.get(base+'/skills/api/'+item.skill_id)).json();
 check(detail.item.status==='candidate'&&detail.item.quality_level==='Q2','explicit_request_does_not_restore_trust');
 const oldJob=await (await page.request.get(base+'/skills/api/v1/jobs/'+author.job.job_id,{headers})).json();
 const old=await page.request.post(base+'/skills/api/v1/jobs/'+author.job.job_id+'/admit',{headers,data:{expected_revision:oldJob.revision}});
 check(old.status()===409||(await old.json()).admitted===false,'old_admission_cannot_restore');
 await page.goto(base+'/skills/authoring');
 await page.getByText('标准模板已就绪。先解锁以保存和验证。',{exact:true}).waitFor();
 await page.locator('#token').fill(token);await click('unlock');
 await page.waitForFunction(()=>!document.querySelector('#save').disabled);
 await page.locator('#drafts button').filter({hasText:draft.spec.name}).click();
 await page.waitForFunction(()=>!document.querySelector('#validate').disabled);
 await click('validate');
 await page.waitForFunction(()=>document.querySelector('#job-state').textContent.startsWith('独立验证通过'),{timeout:120000});
 const jobs=await (await page.request.get(base+'/skills/api/v1/drafts/'+author.draft_id+'/jobs',{headers})).json();
 const fresh=jobs.items.find(j=>j.state==='validated');
 check(fresh&&fresh.job_id!==author.job.job_id&&fresh.run_id!==author.job.run_id,'fresh_independent_run_required');
 await click('admit');await page.waitForFunction(()=>document.querySelector('#job-state').textContent.startsWith('已接纳'));
 await click('result');await page.locator('#result-panel:not([hidden])').waitFor();
 await page.screenshot({path:path.join(out,'02-new-proof-readmitted.png'),fullPage:true});
 search=await (await page.request.post(base+'/skills/search',{data:searchBody})).json();
 check(search.skills.some(s=>s.skill_id===item.skill_id),'fresh_manual_proof_restores_delivery');
 const events=await (await page.request.get(base+'/skills/api/'+item.skill_id+'/events?limit=50')).json();
 await writeFile(path.join(out,'feedback-report.json'),JSON.stringify({checks,skill_id:item.skill_id,negative_run:reused.run_id,new_validation:fresh,events,production_database_writes:0},null,2));
 console.log(JSON.stringify({feedback_checks:checks.length,skill_id:item.skill_id,new_run:fresh.run_id}));
}finally{await browser.close();}
