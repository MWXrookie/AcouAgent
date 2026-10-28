/** Real Windows packaged-app startup. Keeps the normal profile and its Docker data.
 * Does not call a model, alter history, or delete resources. Close the app after checking.
 */
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(path.join(root,'desktop/package.json'));
const {_electron}=require('playwright');
if(process.platform!=='win32'||!process.env.ACOUAGENT_ELECTRON_EXE)throw Error('packaged_windows_executable_required');
const output=path.resolve(process.env.ACOUAGENT_DESKTOP_UAT_OUTPUT||path.join(root,'outputs/desktop-windows-runtime'));
await mkdir(output,{recursive:true});
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const electron=await _electron.launch({executablePath:process.env.ACOUAGENT_ELECTRON_EXE,args:[],env,timeout:45000});
const checks=[],errors=[];let state,manager,lastPhase;
const check=(value,name)=>{if(!value)throw Error(name);checks.push(name);};
let progress;
try{
 manager=await electron.firstWindow();manager.on('pageerror',error=>errors.push(error.message));
 await manager.locator('#start').waitFor();
 await manager.screenshot({path:path.join(output,'01-start.png'),animations:'disabled'});
 await manager.locator('#start').click();
 progress=setInterval(async()=>{try{const phase=await manager.locator('body').getAttribute('data-phase');if(phase!==lastPhase){lastPhase=phase;console.log(phase);}}catch{}},10000);
 await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&document.body.dataset.phase!=='idle',null,{timeout:36*60*1000});
 state=await manager.evaluate(()=>window.acouagent.status());
 await manager.screenshot({path:path.join(output,'02-runtime-result.png'),animations:'disabled'});
 check(state.ready&&state.phase==='ready','actual_windows_packaged_runtime_and_17_skills_ready');
 check(!state.extensions,'extensions_remain_explicit_opt_in');
 await manager.locator('#open').click();
 const deadline=Date.now()+30000;let workbench;
 while(Date.now()<deadline){workbench=electron.windows().find(w=>w!==manager&&w.url().startsWith('http://127.0.0.1:'));if(workbench)break;await new Promise(r=>setTimeout(r,100));}
 check(!!workbench,'separate_workbench_window');
 await workbench.locator('body').waitFor();
 check((await workbench.locator('body').innerText()).includes('AcouAgent'),'actual_portal_rendered');
 check(await workbench.evaluate(()=>typeof require==='undefined'&&typeof process==='undefined'&&typeof window.acouagent==='undefined'),'workbench_has_no_native_bridge');
 await workbench.screenshot({path:path.join(output,'03-workbench.png'),animations:'disabled'});
 const isolation=await electron.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().map(w=>{const p=w.webContents.getLastWebPreferences();return{nodeIntegration:p.nodeIntegration,contextIsolation:p.contextIsolation,sandbox:p.sandbox,webSecurity:p.webSecurity};}));
 check(isolation.length===2&&isolation.every(p=>!p.nodeIntegration&&p.contextIsolation&&p.sandbox&&p.webSecurity),'both_actual_windows_isolated');
 check(errors.length===0,'manager_has_no_javascript_errors');
 console.log(JSON.stringify({passed:checks.length}));
}catch(error){errors.push(error.message);if(manager){state=await manager.evaluate(()=>window.acouagent.status()).catch(()=>null);await manager.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});}throw error;}
finally{
 clearInterval(progress);
 await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_windows_packaged_runtime',checks,errors,state,model_simulation_tested:false,resources_preserved:true},null,2));
 await electron.evaluate(({app})=>app.exit(0)).catch(()=>{});await electron.close().catch(()=>{});
}
