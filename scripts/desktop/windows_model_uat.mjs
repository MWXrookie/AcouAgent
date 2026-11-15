/** Installed Windows GUI: BYOK, actual first solves, automatic admission and reuse.
 * Reuses an explicitly selected existing SSH credential only in memory. No traces,
 * request headers, credentials, browser storage dumps or reference arrays are saved.
 * Creates real execution/learning records in the normal desktop data, preserving it.
 */
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(path.join(root,'desktop/package.json'));
const {_electron}=require('playwright');
const {execFileSync}=require('node:child_process');
const historyOnly=process.env.ACOUAGENT_DESKTOP_UAT_CAPTURE_REPORT;
if(process.platform!=='win32'||!process.env.ACOUAGENT_ELECTRON_EXE||(!historyOnly&&!process.env.ACOUAGENT_TEST_SSH_ALIAS))throw Error('explicit_installed_windows_program_and_existing_credential_source_required');
const output=path.resolve(process.env.ACOUAGENT_DESKTOP_UAT_OUTPUT||path.join(root,'outputs/desktop-model-uat'));
await mkdir(output,{recursive:true});
const checks=[],results=[],errors=[];
const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name);};
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
let app,manager,page,key='',progress,lastPhase;
const dockerExe=path.join(process.env.LOCALAPPDATA,'Programs/DockerDesktop/resources/bin/docker.exe');
function docker(args){return execFileSync(dockerExe,['--context','desktop-linux',...args],{encoding:'utf8',windowsHide:true,timeout:60000,maxBuffer:2000000}).trim();}
async function capture(target,name){
 await target.bringToFront();
 await app.evaluate(({BrowserWindow},u)=>{const w=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()===u);w.show();w.focus();},target.url());
 await target.evaluate(()=>document.fonts.ready);
 await target.evaluate(()=>Promise.all(document.getAnimations().filter(a=>Number.isFinite(a.effect.getTiming().iterations)).map(a=>a.finished.catch(()=>{}))));
 await target.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const url=target.url();
 const png=await app.evaluate(async({BrowserWindow},u)=>{const w=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()===u);w.show();return(await w.capturePage()).toPNG().toString('base64');},url);
 await writeFile(path.join(output,name),Buffer.from(png,'base64'));
}
async function waitReady(title=''){
 await manager.waitForFunction(text=>document.body.dataset.busy==='false'&&document.body.dataset.phase!=='idle'&&(document.body.dataset.phase==='error'||!text||document.getElementById('state-title').textContent.includes(text)),title,{timeout:600000});
 const state=await manager.evaluate(()=>window.acouagent.status());check(state.ready,'desktop_ready');return state;
}
function counts(){return JSON.parse(docker(['exec','acouagent-desktop-gateway-1','python','-c','import json;from app.skill_general_library import get_library;from app.skill_auto_learning import AutoLearning;print(json.dumps(AutoLearning(get_library()).counts()))']));}
try{
 // Capture remote stdout in process memory; never print it or put it in env/files.
 if(!historyOnly){key=execFileSync('ssh',['-o','BatchMode=yes','-o','ClearAllForwardings=yes','-o','ConnectTimeout=8',process.env.ACOUAGENT_TEST_SSH_ALIAS,
  "docker exec dify-mcp python -c 'import os;print(os.environ.get(\"DEEPSEEK_API_KEY\",\"\"),end=\"\")'"],{encoding:'utf8',windowsHide:true,timeout:20000,maxBuffer:4096}).trim();
 check(key.length>=16&&key.length<=512,'existing_model_credential_available');}
 app=await _electron.launch({executablePath:process.env.ACOUAGENT_ELECTRON_EXE,args:[],env,timeout:45000});
 manager=await app.firstWindow();
 progress=setInterval(async()=>{try{const phase=await manager.locator('body').getAttribute('data-phase');if(phase!==lastPhase){lastPhase=phase;console.log('desktop:',phase);}}catch{}},10000);
 await manager.locator('#start').click();let state=await waitReady();
 await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false});});
 if(!historyOnly&&!state.extensions){
  await manager.locator('#skill-settings > summary').click();await manager.locator('#prepare-skills').click();
  state=await waitReady('手动技能已准备');check(state.extensions&&!state.learning,'manual_preparation_does_not_enable_learning');
 }
 if(!historyOnly&&!state.learning){
  if(!await manager.locator('#learning').isVisible())await manager.locator('#skill-settings > summary').click();
  await manager.locator('#learning').click();state=await waitReady('自动学习已开启');
 }
 check(state.extensions&&state.learning,'explicit_automatic_learning_enabled');
 await capture(manager,'01-learning-ready.png');
 await manager.locator('#open').click();
 const deadline=Date.now()+30000;
 while(Date.now()<deadline){page=app.windows().find(w=>w!==manager&&w.url().startsWith('http://127.0.0.1:'));if(page)break;await new Promise(r=>setTimeout(r,100));}
 check(!!page,'separate_installed_workbench');
 check(await page.evaluate(()=>typeof require==='undefined'&&typeof process==='undefined'&&typeof window.acouagent==='undefined'),'workbench_has_no_host_bridge');
 if(historyOnly){
  const report=JSON.parse(await readFile(historyOnly,'utf8'));
  check(report.kind==='actual_installed_windows_model_learning_reuse'&&!report.errors.length&&report.results.length===4,'successful_actual_report_required_for_history_capture');
  for(const row of report.results){
   const name=row.case+'-'+row.arm;
   const sample=JSON.parse(await readFile(path.join(path.dirname(historyOnly),name+'.json'),'utf8'));
   const index=await page.evaluate(prompt=>loadSessions().slice().sort((a,b)=>b.updatedAt-a.updatedAt).findIndex(s=>s.messages?.some(m=>m.role==='user'&&m.content===prompt)),sample.prompt);
   check(index>=0,name+'_original_session_found');await page.locator('.session-open').nth(index).click();
   check(await page.evaluate(data=>conversation.some(m=>m.role==='user'&&m.content===data.prompt)&&conversation.some(m=>m.role==='assistant'&&m.content.startsWith(data.result.report)),sample),name+'_stored_actual_prompt_and_result_match');
   await page.locator('#chat-log img').first().scrollIntoViewIfNeeded();await capture(page,name+'-field.png');
   await page.locator('#chat-log').evaluate(e=>{e.scrollTop=e.scrollHeight;});await capture(page,name+'-conclusion.png');
   results.push(row);
  }
  console.log(JSON.stringify({history_screenshots:results.length*2,model_calls:0}));
 }else{
 await page.locator('.model-btn').click();await page.locator('#model-profile').selectOption('__byok__');
 await page.locator('#byok-provider').selectOption('deepseek');
 await page.locator('#byok-model-preset').selectOption('deepseek-flash');
 await page.locator('#byok-api-key').fill(key);
 await page.locator('#model-test-btn').click();
 await page.waitForFunction(()=>!document.getElementById('model-test-btn').disabled,null,{timeout:180000});
 const connection=await page.locator('#model-preflight-result').getAttribute('data-state');
 check(await page.locator('#byok-api-key').inputValue()==='','model_key_cleared_after_registration');
 await capture(page,'02-real-model-connection.png');
 await writeFile(path.join(output,'connection.json'),JSON.stringify({state:connection,title:await page.locator('#model-preflight-title').innerText(),detail:await page.locator('#model-preflight-detail').innerText()},null,2));
 check(connection==='success','real_model_compatibility_preflight');
 await page.locator('#model-modal .primary').click();await page.locator('#model-modal').waitFor({state:'hidden'});
 const before=counts();await writeFile(path.join(output,'learning-before.json'),JSON.stringify(before,null,2));
 const templates=await page.evaluate(async()=>{const r=await fetch('/skills/api/v1/spec-schema');if(!r.ok)throw Error('template_api_unavailable');return(await r.json()).templates;});
 for(const id of ['B01','B02']){
  const spec=templates[id];
  const contract=JSON.stringify({scenario_type:spec.scenario_type,simulation_params:spec.parameters,result_spec:spec.result_spec});
  const prompts=[
   '请按以下完整物理条件执行二维单频稳态声学仿真。需要完整复压力用于独立核验，并在界面显示幅值热力图和结果说明。若尚无技能，请完成真实首次求解及受控自动学习。物理条件和输出约定：\n'+contract,
   '重新计算这个点声源在给定介质中的稳态声场。请保持以下网格、频率、介质分布和结果定义，返回这一次新运行的幅值图；如有已经独立验证的技能，请复用并重新求解，给出本次数值核验。\n'+contract
  ];
  for(let repetition=0;repetition<2;repetition++){
   await page.locator('.reset-btn').click();
   await page.evaluate(()=>{if(!window.__desktopOriginalFetch)window.__desktopOriginalFetch=window.fetch;window.__desktopChat=null;window.fetch=async(...args)=>{const response=await window.__desktopOriginalFetch(...args);if(args[0]==='/chat')response.clone().text().then(text=>window.__desktopChat=text);return response;};});
   await page.locator('#query-input').fill(prompts[repetition]);
   const started=Date.now();await page.locator('#ask-btn').click();
   await page.waitForFunction(()=>typeof window.__desktopChat==='string',null,{timeout:360000});
   const text=await page.evaluate(()=>window.__desktopChat);
   check(!text.includes(key),'model_credential_absent_from_response');
   const events=text.trim().split('\n').filter(Boolean).map(line=>JSON.parse(line));
   const result=events.filter(e=>e.event==='result').at(-1);
   const name=id+'-'+(repetition===0?'first':'reuse');
   results.push({case:id,arm:repetition===0?'first':'reuse',wall_ms:Date.now()-started,result});
   await writeFile(path.join(output,name+'.json'),JSON.stringify({prompt:prompts[repetition],events,result},null,2));
   await page.waitForFunction(()=>!document.getElementById('ask-btn').disabled,null,{timeout:30000});
   if(result?.workflow_status==='succeeded')await page.waitForFunction(()=>{const images=[...document.querySelectorAll('#chat-log img')];return images.length>0&&images.every(i=>i.complete&&i.naturalWidth>0);},null,{timeout:30000});
   await capture(page,name+'.png');
   check(result?.workflow_status==='succeeded',name+'_actual_solve_succeeded');
   check(result.model_parameter_extraction_available&&result.model_review_available&&result.model_explanation_available,name+'_real_model_stages_available');
   check(typeof result.run_id==='string'&&!results.slice(0,-1).some(r=>r.result?.run_id===result.run_id),name+'_new_unique_execution');
   if(repetition===0)check(result.learning?.admitted===true&&!result.execution_reuse_verified,name+'_automatic_learning_independently_admitted');
   else check(result.execution_reuse_verified===true,name+'_actual_reexecution_reuse_verified');
   console.log(name,JSON.stringify({run_id:result.run_id,quality:result.quality_level,reuse:result.execution_reuse_verified,learning:result.learning?.admitted}));
  }
 }
 const after=counts();await writeFile(path.join(output,'learning-after.json'),JSON.stringify(after,null,2));
 check(after.admitted>=before.admitted+2&&after.reused>=before.reused+2,'two_learned_cases_and_two_verified_reuses_recorded');
 await page.goto('http://127.0.0.1:'+state.port+'/skills');await capture(page,'07-learned-skills.png');
 console.log(JSON.stringify({passed:checks.length,real_model_runs:results.length}));
 }
}catch(error){
 const message=String(error.message).split(key||'__no_secret__').join('[redacted]');errors.push(message);console.error(message);
 if(page){await page.locator('#byok-api-key').fill('').catch(()=>{});await capture(page,'failure.png').catch(()=>{});}
 process.exitCode=1;
}finally{
 clearInterval(progress);
 if(page&&!historyOnly)await page.evaluate(async()=>{const profile=sessionStorage.getItem('acouagent_byok_profile');if(profile)await fetch('/api/byok/session',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:profile})});sessionStorage.removeItem('acouagent_byok_profile');}).catch(()=>{});
 key='';
 await writeFile(path.join(output,historyOnly?'capture-report.json':'report.json'),JSON.stringify({kind:historyOnly?'actual_stored_desktop_history_capture':'actual_installed_windows_model_learning_reuse',checks,errors,results,reference_scope:'B01/B02 exact parameters, k-Wave magnitude only',resources_preserved:true},null,2));
 if(app){await app.evaluate(({app})=>app.exit(0)).catch(()=>{});await app.close().catch(()=>{});}
}
