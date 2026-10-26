/** Browser natural-language reuse against an explicitly disposable gateway. */
import {createRequire} from 'node:module';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('playwright');
const base=process.env.GW_BASE_URL,profile=process.env.SKILL_TEST_MODEL_PROFILE;
if(!base||!profile||!process.env.SKILL_TEST_SPEC||process.env.SKILL_TEST_DISPOSABLE!=='true')throw Error('explicit_disposable_model_profile_and_spec_required');
const spec=JSON.parse(await readFile(process.env.SKILL_TEST_SPEC,'utf8'));
const out=path.resolve(process.env.SKILL_BROWSER_OUTPUT||'outputs/skill-reuse-browser');await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.SKILL_BROWSER_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
try{
 await page.goto(base+'/portal');
 await page.evaluate(id=>sessionStorage.setItem('acouagent_byok_profile',id),profile);
 await page.reload();await page.locator('#query-input').waitFor();
 const request=process.env.SKILL_TEST_QUERY||('请执行二维频域稳态声学点源仿真，使用以下明确的完整物理参数与结果约定。返回真实求解的幅值热力图、峰值与独立验证结果。若有完全匹配的已验证技能请实际重执行复用；不要返回历史图或编造结果。\n'+JSON.stringify({scenario_type:spec.scenario_type,simulation_params:spec.parameters,result_spec:spec.result_spec}));
 await page.locator('#query-input').fill(request);
 // Tee the actual streamed response inside the browser; CDP response.body may
 // discard streaming resources before the automation client can read them.
 await page.evaluate(()=>{const original=window.fetch;window.__chatCapture=null;window.fetch=async(...args)=>{const r=await original(...args);if(args[0]==='/chat')r.clone().text().then(t=>window.__chatCapture=t);return r;};});
 await page.locator('#ask-btn').click();
 await page.waitForFunction(()=>typeof window.__chatCapture==='string',null,{timeout:180000});
 const text=await page.evaluate(()=>window.__chatCapture);const events=text.trim().split('\n').map(line=>JSON.parse(line));
 const result=events.filter(e=>e.event==='result').at(-1);
 await page.waitForFunction(()=>!document.querySelector('#ask-btn').disabled,null,{timeout:180000});
 if(result?.workflow_status==='succeeded')await page.waitForFunction(()=>{const images=[...document.querySelectorAll('#chat-log img')];return images.length>0&&images.every(i=>i.complete&&i.naturalWidth>0);},null,{timeout:15000});
 await page.screenshot({path:path.join(out,'natural-language-reuse.png'),fullPage:true});
 await writeFile(path.join(out,'natural-language-report.json'),JSON.stringify({kind:'real_model_browser_natural_language_reuse',result,events,production_database_writes:0},null,2));
 if(result?.workflow_status!=='succeeded'||result?.execution_reuse_verified!==true)throw Error('natural_language_reuse_not_verified:'+JSON.stringify({status:result?.workflow_status,error:result?.error_code,verified:result?.execution_reuse_verified}));
 console.log(JSON.stringify({natural_language_reuse_verified:true,run_id:result.run_id,workflow_run_id:result.workflow_run_id,model_profile:result.model_profile}));
}finally{await browser.close();}
