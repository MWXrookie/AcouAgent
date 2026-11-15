/** Real, GET-only readability acceptance against a copied or live library. */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
const {chromium}=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON||import.meta.url)('playwright');
const base=process.env.GW_BASE_URL,out=process.env.SKILL_READABILITY_OUTPUT,copy=process.env.SKILL_READABILITY_COPY;
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')||!out)throw Error('explicit_readability_fixture_required');
await mkdir(out,{recursive:true});
const hash=async()=>copy?createHash('sha256').update(await readFile(copy)).digest('hex'):null;
const before=await hash(),checks=[],errors=[],mutations=[],items=[];
const browser=await chromium.launch({channel:'chrome',headless:true});const context=await browser.newContext({viewport:{width:1440,height:1100},permissions:['clipboard-read','clipboard-write']});
await context.route('**/*',async route=>{const r=route.request();if(new URL(r.url()).origin===new URL(base).origin&&!['GET','HEAD','OPTIONS'].includes(r.method())){mutations.push(r.method()+' '+r.url());await route.abort()}else await route.continue()});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
const check=(v,name)=>{if(!v)throw Error(name);checks.push(name)};
try{
 await page.goto(base+'/skills');await page.locator('#list button.skill').first().waitFor();
 const meta=await page.evaluate(async()=>(await fetch('/skills/api?limit=50')).json());
 check(meta.items.length===meta.organization.case_count,'all_current_cases_fit_bounded_metadata_response');
 const titles=await page.locator('#list button.skill strong').allTextContents();
 check(new Set(titles).size===titles.length,'every_current_case_has_a_distinct_readable_title');
 check(await page.locator('.notice').count()===0,'no_instruction_or_warning_banners');
 check(!(await page.locator('main').innerText()).includes('先看案例用途'),'no_tutorial_boilerplate');
 check(titles.every(t=>!/(Automatic|受控执行器|固定初压模态|待验证案例|仿真方案|；)/.test(t)),'internal_and_repetitive_prefixes_removed');
 for(const item of meta.items){
  await page.locator('button[data-skill-id="'+item.skill_id+'"]').click();
  await page.waitForFunction(()=>document.getElementById('message').textContent==='详情已读取。');
  const title=await page.locator('#summary').innerText(),body=await page.locator('#friendly-summary').innerText();
  check(title===item.presentation.title,'list_and_detail_names_match_'+item.skill_id);
  check(body.includes('用途')&&body.includes('输出')&&body.includes('参数'),'purpose_output_settings_readable_'+item.skill_id);
  check(!/(rad\/m|normalizer|source_|managed-|波数分量|Q[0-4])/.test(body),'technical_payload_not_in_main_introduction_'+item.skill_id);
  check(await page.locator('#detail details').first().getAttribute('open')===null,'raw_evidence_remains_folded_'+item.skill_id);
  check(!body.includes('如何使用')&&!body.includes('使用前需确认'),'no_repetitive_usage_prompts_'+item.skill_id);
  items.push({title,introduction:body});
 }
 check(new Set(items.map(i=>i.introduction.split('输出')[0])).size>=8,'introductions_explain_distinct_physical_tasks');
 const first=meta.items[0];await page.locator('button[data-skill-id="'+first.skill_id+'"]').click();await page.waitForFunction(()=>document.getElementById('message').textContent==='详情已读取。');
 await page.evaluate(()=>window.scrollTo(0,0));
 await page.screenshot({path:path.join(out,'after-desktop.png'),fullPage:true,animations:'disabled'});
 await page.locator('#copy-request').click();const copied=await page.evaluate(()=>navigator.clipboard.readText());
 const request=JSON.parse(copied.slice(copied.indexOf('{')));const detail=await page.evaluate(async id=>(await fetch('/skills/api/'+id)).json(),first.skill_id);
 const raw=detail.item.selected_version.normalized_params;
 check(JSON.stringify(request.simulation_params)===JSON.stringify(raw.parameters)&&JSON.stringify(request.result_spec)===JSON.stringify(raw.result_spec),'copy_preserves_full_precision_frequency_contract');
 await page.locator('#skill-search').fill('水中水平驻波');
 await page.waitForFunction(()=>[...document.querySelectorAll('button.skill')].filter(b=>!b.hidden).length===1);
 check(true,'readable_name_search_finds_one_case');await page.locator('#skill-search').fill('');
 const mode=meta.items.find(i=>i.presentation.title==='水中水平驻波');await page.locator('button[data-skill-id="'+mode.skill_id+'"]').click();await page.waitForFunction(()=>document.getElementById('message').textContent==='详情已读取。');
 await page.evaluate(()=>window.scrollTo(0,0));
 await page.screenshot({path:path.join(out,'after-standing-wave.png'),fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile_no_horizontal_overflow');
 await page.locator('#detail').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'after-mobile.png'),animations:'disabled'});
 await page.setViewportSize({width:720,height:550});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'narrow_layout_preserves_readable_content');
 await page.setViewportSize({width:1440,height:1100});await page.goto(base+'/portal');await page.locator('.design-reference-row').first().waitFor();
 for(const row of await page.locator('.design-reference-row').all()){
  const id=await row.getAttribute('title'),item=meta.items.find(i=>i.skill_id===id);
  if(item)check((await row.innerText()).includes(item.presentation.title),'chat_reference_uses_same_human_name_'+id);
 }
 check(mutations.length===0&&errors.length===0,'no_server_mutations_model_calls_or_javascript_errors');
 if(copy)check(before===await hash(),'copied_database_byte_hash_unchanged');
 console.log(JSON.stringify({passed:checks.length,cases:items.length}));
}catch(e){errors.push(e.message);process.exitCode=1;await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{})}
finally{await writeFile(path.join(out,'report.json'),JSON.stringify({checks,errors,mutations,items,source:base,production_database_writes:0},null,2));await browser.close()}
