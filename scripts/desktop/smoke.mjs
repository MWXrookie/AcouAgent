/** Actual Electron window check. Does not install Docker or start a solver.
 * ACOUAGENT_ELECTRON_EXE can point to a packaged AcouAgent.exe.
 * ACOUAGENT_DESKTOP_SMOKE_OUTPUT controls isolated app profiles and screenshots.
 */
import {createRequire} from 'node:module';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const desktop=path.join(root,'desktop');
const require=createRequire(path.join(desktop,'package.json')),{_electron}=require('playwright');
const output=path.resolve(process.env.ACOUAGENT_DESKTOP_SMOKE_OUTPUT||path.join(root,'outputs/desktop-smoke'));
await mkdir(output,{recursive:true});
const packaged=process.env.ACOUAGENT_ELECTRON_EXE;
const executablePath=packaged||path.join(desktop,'node_modules/electron/dist/electron.exe');
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const useDefault=process.env.ACOUAGENT_DESKTOP_SMOKE_DEFAULT_PROFILE==='true';
const profile=useDefault?path.join(process.env.LOCALAPPDATA||process.env.APPDATA,'AcouAgent','Desktop'):path.join(output,'profile');
const electron=await _electron.launch({executablePath,args:[...(!packaged?[desktop]:[]),...(!useDefault?['--acouagent-data-dir='+profile]:[])],env,timeout:45000});
const checks=[],errors=[];
const check=(v,name)=>{if(!v)throw Error(name);checks.push(name);};
try{
 const window=await electron.firstWindow();window.on('pageerror',e=>errors.push(e.message));
 await window.locator('#start').waitFor();
 check(await window.title()==='AcouAgent · 本地运行管理','actual_desktop_window');
 check(await window.evaluate(()=>typeof require==='undefined'&&typeof process==='undefined'),'renderer_no_node_access');
 check(await window.evaluate(()=>Object.keys(window.acouagent).sort().join(','))==='backup,copyAdminToken,diagnostics,dockerGuide,onState,open,prepareSkills,restore,start,status,stop,toggleLearning','limited_preload_api');
 await window.screenshot({path:path.join(output,'01-desktop-start.png'),animations:'disabled'});
 await window.locator('#start').click();
 await window.waitForFunction(()=>document.body.dataset.busy==='false'&&document.body.dataset.phase!=='idle',{timeout:60000});
 const phase=await window.locator('body').getAttribute('data-phase');
 check(phase==='error','missing_runtime_blocks_workspace');
 check((await window.locator('#state-detail').innerText()).includes('Docker'),'actionable_runtime_message');
 check(!await window.locator('#open').isVisible(),'unready_workspace_not_enabled');
 check(await window.locator('#admin').isDisabled(),'admin_copy_unavailable_until_ready');
 await window.screenshot({path:path.join(output,'02-desktop-runtime-needed.png'),animations:'disabled'});
 const vault=await readFile(path.join(profile,'credentials.bin'));
 check(vault.length>100&&!vault.toString('utf8').includes('MCP_AUTH_TOKEN'),'credentials_not_plaintext');
 const isolation=await electron.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().map(w=>{const p=w.webContents.getLastWebPreferences();return{nodeIntegration:p.nodeIntegration,contextIsolation:p.contextIsolation,sandbox:p.sandbox,webSecurity:p.webSecurity};}));
 check(isolation.every(p=>!p.nodeIntegration&&p.contextIsolation&&p.sandbox&&p.webSecurity),'actual_window_isolation');
 check(errors.length===0,'no_renderer_javascript_errors');
 await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_electron_missing_runtime_smoke',packaged:!!packaged,checks,errors,isolation,not_full_solver_acceptance:true},null,2));
 console.log(JSON.stringify({passed:checks.length,packaged:!!packaged}));
}catch(error){const windows=electron.windows();if(windows[0])await windows[0].screenshot({path:path.join(output,'failure.png')}).catch(()=>{});throw error;}
finally{await electron.evaluate(({app})=>app.exit(0)).catch(()=>{});await electron.close().catch(()=>{});}
