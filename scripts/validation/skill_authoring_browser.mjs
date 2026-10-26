/** Real browser authoring acceptance against an explicitly disposable gateway.
 * Operator: GW_BASE_URL, SKILL_TEST_ADMIN_TOKEN, SKILL_BROWSER_OUTPUT.
 * Uses installed Playwright; never installs a browser or edits production storage.
 */
import {createRequire} from 'node:module';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
const require=createRequire(import.meta.url),{chromium}=require('playwright');
const base=process.env.GW_BASE_URL,token=process.env.SKILL_TEST_ADMIN_TOKEN;
if(!base||!token||process.env.SKILL_TEST_DISPOSABLE!=='true')throw Error('explicit_disposable_gateway_and_token_required');
const out=path.resolve(process.env.SKILL_BROWSER_OUTPUT||'outputs/skill-authoring-browser');await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.SKILL_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000},...(process.env.SKILL_TEST_RECORD_VIDEO==='true'?{recordVideo:{dir:out,size:{width:1440,height:1000}}}:{})});
const issues=[];page.on('pageerror',e=>issues.push(e.message));
const checks=[];
function check(condition,name){if(!condition)throw Error(name);checks.push(name);}
async function click(id){await page.locator('#'+id).click();}
async function idle(){await page.waitForFunction(()=>!document.querySelector('#save').disabled,null,{timeout:20000});}
async function screenshot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
try{
  await page.goto(base+'/skills/authoring');
  await page.getByText('标准模板已就绪。先解锁以保存和验证。',{exact:true}).waitFor();
  await screenshot('01-locked');
  check(await page.locator('#save').isDisabled(),'locked_save_disabled');
  await page.locator('#token').fill('invalid');await click('unlock');
  await page.locator('#error-box:not([hidden])').waitFor();
  check((await page.locator('#error-message').innerText()).includes('管理员'),'invalid_admin_visible');
  await page.locator('#token').fill(token);await click('unlock');await idle();
  check(await page.locator('#token').inputValue()==='','token_field_cleared');
  await page.locator('#import').setInputFiles({name:'duplicate.json',mimeType:'application/json',buffer:Buffer.from('{"name":"one","name":"two"}')});
  await page.locator('#error-box:not([hidden])').waitFor();
  check((await page.locator('#error-message').innerText()).includes('重复字段'),'duplicate_import_rejected');
  const templates=await (await page.request.get(base+'/skills/api/v1/spec-schema')).json();
  await page.locator('#import').setInputFiles({name:'skill.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(templates.templates.B01))});
  await page.getByText('已导入，尚未保存。服务端将严格检查标准格式。',{exact:true}).waitFor();
  check(true,'standard_json_imported');
  await page.locator('#name').fill('');await click('save');
  await page.locator('#error-box:not([hidden])').waitFor();
  check(await page.locator('#error-link').getAttribute('href')==='#name','field_error_targets_name');
  check(await page.evaluate(()=>document.activeElement.id)==='error-box','error_summary_focused');
  await page.locator('#name').fill('浏览器手动 B01 '+Date.now());
  await click('save');await page.waitForFunction(()=>document.querySelector('#identity').textContent.includes('版本 1'));
  const draftId=(await page.locator('#identity').innerText()).split(' · ')[0];
  await click('preview');await page.locator('#derivation:not([hidden])').waitFor();
  check((await page.locator('#diff').innerText()).includes('submitted'),'controlled_diff_visible');
  await screenshot('02-code-diff');await click('apply');
  await page.waitForFunction(()=>document.querySelector('#identity').textContent.includes('版本 2'));
  await idle();await click('validate');
  await page.waitForFunction(()=>document.querySelector('#job-state').textContent.includes('独立验证通过'),null,{timeout:120000});
  await click('result');await page.locator('#result-panel:not([hidden])').waitFor();
  await page.waitForFunction(()=>{const i=document.querySelector('#heatmap');return i.complete&&i.naturalWidth>0;});
  check((await page.locator('#metrics').innerText()).includes('128 × 128'),'real_result_shape_displayed');
  check((await page.locator('#comparison').innerText()).includes('relative_magnitude_linf'),'independent_numeric_comparison_displayed');
  const fieldDownload=page.waitForEvent('download');await click('download-field');const fieldFile=await fieldDownload;
  const fieldPath=path.join(out,'complete-complex-pressure.npy');await fieldFile.saveAs(fieldPath);
  check((await readFile(fieldPath)).subarray(0,6).equals(Buffer.from([147,78,85,77,80,89])),'complete_complex_npy_downloaded');
  await screenshot('03-validated-result');await click('admit');
  await page.waitForFunction(()=>document.querySelector('#job-state').textContent.startsWith('已接纳'));
  await click('result');await page.waitForFunction(()=>document.querySelector('#metrics').textContent.includes('Q4'));
  const download=page.waitForEvent('download');await click('export');const file=await download;await file.saveAs(path.join(out,'manual-skill-export.json'));
  check(file.suggestedFilename()==='skill-spec.json','spec_export_download');
  // Editing makes a new immutable draft; the old validation stays attached to version 2.
  await page.locator('#description').fill('浏览器编辑后的草稿，必须重新验证。');await click('save');
  await page.waitForFunction(()=>document.querySelector('#identity').textContent.includes('版本 3'));
  const h={'X-Admin-Token':token};
  const items=await (await page.request.get(base+'/skills/api/v1/drafts/'+draftId+'/jobs',{headers:h})).json();
  check(items.items.length===1&&items.items[0].draft_version===2,'edit_does_not_inherit_validation');
  await screenshot('04-admitted-and-new-version');
  const draft=await (await page.request.get(base+'/skills/api/v1/drafts/'+draftId,{headers:h})).json();
  check(draft.version===3,'new_version_saved');
  const proof=await (await page.request.get(base+items.items[0].result_url,{headers:h})).json();
  check(proof.has_result&&proof.quality_level==='Q4'&&proof.comparison.numeric_pass,'api_agrees_with_rendered_result');
  // Same browser can restore durable job visibility after a page reload/unlock.
  await page.reload();await page.getByText('标准模板已就绪。先解锁以保存和验证。',{exact:true}).waitFor();
  check(await page.locator('#save').isDisabled(),'reload_clears_admin_session');
  await page.locator('#token').fill(token);await click('unlock');await idle();
  await page.locator('#drafts button').filter({hasText:draft.spec.name}).click();
  await page.waitForFunction(()=>document.querySelector('#job-state').textContent.startsWith('已接纳'));
  check(true,'reload_restores_job_from_storage');
  await page.setViewportSize({width:390,height:844});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile_no_horizontal_overflow');
  await screenshot('05-mobile');
  await click('lock');check(await page.locator('#spec').inputValue()==='','lock_clears_sensitive_draft');
  check(await page.evaluate(()=>!Object.keys(localStorage).some(k=>/token|admin|skill.*spec/i.test(k))),'admin_not_persisted');
  check(issues.length===0,'no_browser_javascript_errors');
  await writeFile(path.join(out,'browser-report.json'),JSON.stringify({kind:'real_browser_manual_authoring',checks,draft_id:draftId,job:items.items[0],result:proof,production_database_writes:0},null,2));
  console.log(JSON.stringify({passed:checks.length,draft_id:draftId,run_id:proof.job.run_id,error:proof.comparison.relative_magnitude_linf}));
}finally{const video=page.video();try{if(video){await page.close();await video.saveAs(path.join(out,'manual-authoring.webm'));}}finally{await browser.close();}}
