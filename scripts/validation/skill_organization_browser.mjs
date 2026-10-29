/** Real copied library, read-only product handlers, no mocked API responses. */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON||path.join(root,'package.json'));
const {chromium}=require('playwright');
const base=process.env.GW_BASE_URL,output=process.env.SKILL_BROWSER_OUTPUT,db=process.env.SKILL_COPY_PATH;
if(!base||!/^http:\/\/127\.0\.0\.1:\d+$/.test(base)||!output||!db)throw Error('explicit_readonly_library_qa_required');
const hash=async()=>createHash('sha256').update(await readFile(db)).digest('hex');
const before=await hash();await mkdir(output,{recursive:true});
const checks=[],errors=[],mutations=[];const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')mutations.push({method:r.method(),url:r.url()})});
const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name)};
const watchdog=setTimeout(async()=>{errors.push('global_timeout');await writeFile(path.join(output,'timeout-report.json'),JSON.stringify({checks,errors},null,2));browser.close().catch(()=>{});process.exit(2)},60000);
let metadata=null;
try{
 await page.goto(base+'/skills');await page.waitForFunction(()=>document.querySelectorAll('#list button.skill').length>0);
 metadata=await page.evaluate(async()=>(await fetch('/skills/api?limit=50&archived=false')).json());
 const stats=metadata.organization;
 check(stats?.scope==='whole_registered_library'&&stats.trust_verified===false,'counts_are_whole_registered_library_not_trust_grants');
 const displayed=await page.locator('#organization strong').allTextContents();
 check(JSON.stringify(displayed)===JSON.stringify(['family_count','case_count','version_count','reexecution_count'].map(k=>String(stats[k]))),'four_distinct_counts_match_real_database_api');
 check(metadata.items.length<=stats.case_count,'current_list_is_separate_from_whole_library');
 const modal=metadata.items.filter(i=>i.state_text.includes('周期')||i.state_text.includes('模态'));
 const titles=[];
 for(const item of modal){const text=await page.locator(`button[data-skill-id="${item.skill_id}"] strong`).innerText();titles.push(text);check(text.includes(item.state_text.split('；')[0])||item.state_text.startsWith('受控执行器'),'scientific_case_prefix_preserved_'+item.skill_id)}
 check(new Set(titles).size===new Set(modal.map(i=>i.state_text)).size,'distinct_periodic_cases_do_not_appear_as_duplicate_titles');
 await page.locator('button.skill').first().click();await page.waitForFunction(()=>!document.getElementById('detail').hidden&&document.getElementById('message').textContent==='详情已读取。');
 check((await page.locator('#friendly-summary').innerText()).includes('解决什么问题'),'purpose_visible_before_technical_evidence');
 check(await page.locator('#detail details').first().getAttribute('open')===null,'technical_evidence_collapsed_by_default');
 check((await page.locator('#detail').innerText()).includes('不是原始自然语言提问'),'copy_described_as_structured_request_not_original_prompt');
 await page.screenshot({path:path.join(output,'desktop.png')});
 await page.locator('#skill-search').fill('不存在的案例xyz');await page.waitForFunction(()=>![...document.querySelectorAll('#list button.skill')].some(b=>!b.hidden));
 check((await page.locator('#list-state').innerText()).includes('暂无匹配技能'),'empty_search_has_meaningful_feedback');
 await page.locator('#skill-search').fill('');
 await page.setViewportSize({width:390,height:844});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile_no_horizontal_overflow');
 await page.locator('#organization').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'mobile.png')});
 await page.setViewportSize({width:1440,height:1100});await page.goto(base+'/portal');await page.locator('.design-reference-row').first().waitFor();
 for(const link of await page.locator('.design-reference-row').all()){
  const id=await link.getAttribute('title'),item=metadata.items.find(i=>i.skill_id===id);
  const text=await link.innerText();
  check(!!item&&(text.includes(item.state_text.split('；')[0])||item.state_text.split('；')[0].includes('受控执行器')||item.state_text.includes('未认证候选')&&text.includes('待验证案例')),'chat_reference_preserves_scientific_title_or_clear_candidate_label');
  if(item?.state_text.includes('未认证候选')&&item.display_parameters?.length)check(text.includes(String(item.display_parameters[0].value)),'candidate_reference_includes_actual_physical_parameters');
 }
 const candidateLinks=await page.locator('.design-reference-row').allTextContents();
 const candidateLabels=candidateLinks.filter(text=>text.includes('待验证案例'));
 check(new Set(candidateLabels).size===candidateLabels.length,'candidate_references_with_different_end_times_are_distinguishable');
 check(mutations.length===0,'read_only_review_makes_no_write_requests');check(errors.length===0,'no_page_javascript_errors');
 console.log(JSON.stringify({passed:checks.length,counts:stats}));
}catch(error){errors.push(error.message);await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});process.exitCode=1;}
finally{clearTimeout(watchdog);const after=await hash();if(before!==after){errors.push('copied_database_changed');process.exitCode=1}else checks.push('copied_database_byte_hash_unchanged');await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_copied_library_browser_review',checks,errors,mutations,organization:metadata?.organization,source_is_online_backup_copy:true,production_writes:0},null,2));await browser.close();}
