/** Existing-history display acceptance. Isolated browser fixture, GET only. */
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(process.env.PLAYWRIGHT_PACKAGE_JSON||import.meta.url)('playwright');
const base=process.env.GW_BASE_URL,out=process.env.SCIENTIFIC_TEXT_OUTPUT;
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')||!out)throw Error('explicit_loopback_fixture_required');
await mkdir(out,{recursive:true});
const question=String.raw`考虑二维矩形房间，尺寸为 $L_x = 4.0\ \mathrm{m}$，$L_y = 3.0\ \text{m}$。刚性壁面，空气密度 $\rho_0 = 1.21\ \text{kg/m}^3$，声速 $c = 343\ \mathrm{m/s}$。计算前10个模态。`;
const answer=String.raw`模态公式：\[f_{n_x,n_y}=\frac{c}{2}\sqrt{(n_x/L_x)^2+(n_y/L_y)^2}\]`+
 '\n\n| 阶数 | 频率 |\n|---|---|\n| (1,0) | 42.875 Hz |\n\n```python\nraw = r"$x^2$"\n```\n\n[参考](https://example.com/$x$)';
const fixture=[{id:'plain-math-fixture',title:question.slice(0,20),messages:[{role:'user',content:question},{role:'assistant',content:answer}],requirement:question,updatedAt:Date.now()}];
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[],mutations=[],checks=[];
await context.addInitScript(({fixture})=>{
 if(!localStorage.getItem('acouagent_sessions')){
  localStorage.setItem('acouagent_sessions',JSON.stringify(fixture));localStorage.setItem('acouagent_current',fixture[0].id);
 }
},{fixture});
await context.route('**/*',async route=>{const r=route.request();if(!['GET','HEAD','OPTIONS'].includes(r.method())){mutations.push(r.method()+' '+r.url());await route.abort()}else await route.continue()});
const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
const check=(value,name)=>{if(!value)throw Error(name);checks.push(name)};
async function preserved(){
 const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('acouagent_sessions'))[0]);
 check(saved.messages[0].content===question&&saved.messages[1].content===answer&&saved.requirement===question,'stored_request_answer_and_binding_unchanged');
}
try{
 await page.goto(base+'/portal');await page.locator('#chat-log .msg.user').waitFor();
 const body=await page.locator('#chat-log .msg.user').innerText();
 check(body.includes('Lx = 4.0 m')&&body.includes('Ly = 3.0 m'),'existing_question_dimensions_plain');
 check(body.includes('ρ₀ = 1.21 kg/m³')&&body.includes('c = 343 m/s'),'density_and_speed_units_plain');
 check(!body.includes('$')&&!body.includes('\\'),'no_latex_markup_in_question');
 const report=await page.locator('#chat-log .msg.assistant').innerText();
 check(report.includes('(c)/(2)sqrt((nx/Lx)²+(ny/Ly)²)'),'modal_formula_preserves_grouping');
 check(await page.locator('.msg.assistant table td').last().innerText()==='42.875 Hz','numeric_result_and_table_preserved');
 check((await page.locator('.msg.assistant pre code').textContent()).includes('$x^2$'),'raw_code_unchanged');
 check(await page.locator('.msg.assistant a').getAttribute('href')==='https://example.com/$x$','reference_url_unchanged');
 const title=await page.locator('.session-open').innerText();check(!/[\\$]/.test(title),'history_title_plain');
 await preserved();
 await page.locator('#query-input').fill(question);check(await page.locator('#query-input').inputValue()===question,'editable_source_not_rewritten');
 await page.screenshot({path:path.join(out,'desktop-history.png'),fullPage:true});
 await page.reload();await page.locator('#chat-log .msg.user').waitFor();await preserved();
 check(!(await page.locator('#chat-log .msg.user').innerText()).includes('\\mathrm'),'plain_display_survives_reload');
 await page.getByRole('button',{name:'切换深色主题'}).click();
 check(await page.locator('html').getAttribute('data-theme')==='dark','dark_theme_works');
 await page.setViewportSize({width:390,height:844});
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile_no_overflow');
 await page.screenshot({path:path.join(out,'mobile-history.png'),fullPage:true});
 await page.evaluate(()=>{conversation=[{role:'assistant',content:'$\\text{<img src=x onerror=alert(1)>}$'}];renderChat()});
 check(await page.locator('#chat-log img').count()===0,'formula_html_is_not_executed');
 check(errors.length===0,'no_browser_script_errors');check(mutations.length===0,'no_backend_mutation_or_model_call');
 await writeFile(path.join(out,'report.json'),JSON.stringify({fixture:'isolated browser history; no simulation performed',checks,errors,mutations,source:base},null,2));
 console.log(JSON.stringify({passed:checks.length,fixture:'isolated stored history',backend_writes:0}));
}finally{await browser.close()}
