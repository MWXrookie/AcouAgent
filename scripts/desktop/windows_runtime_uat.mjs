/** Real Windows packaged-app startup. Keeps the normal profile and its Docker data.
 * Does not call a model, alter history, or delete resources. Close the app after checking.
 */
import {createRequire} from 'node:module';
import {mkdir,writeFile,readFile,readdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const require=createRequire(path.join(root,'desktop/package.json'));
const {_electron}=require('playwright');
const {spawnSync}=require('node:child_process');
const {once}=require('node:events');
const {createHash}=require('node:crypto');
if(process.platform!=='win32'||!process.env.ACOUAGENT_ELECTRON_EXE)throw Error('packaged_windows_executable_required');
const output=path.resolve(process.env.ACOUAGENT_DESKTOP_UAT_OUTPUT||path.join(root,'outputs/desktop-windows-runtime'));
await mkdir(output,{recursive:true});
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
let electron=await _electron.launch({executablePath:process.env.ACOUAGENT_ELECTRON_EXE,args:[],env,timeout:45000});
const checks=[],errors=[];let state,manager,lastPhase;
const check=(value,name)=>{if(!value)throw Error(name);checks.push(name);};
async function capture(page,name){
 await electron.evaluate(({BrowserWindow},url)=>{
  const window=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()===url);
  if(window.isMinimized())window.restore();window.show();window.focus();
 },page.url());
 await page.waitForFunction(()=>document.readyState==='complete');
 await page.evaluate(async()=>{await document.fonts.ready;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
 const png=await electron.evaluate(async({BrowserWindow},url)=>{
  const window=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL()===url);
  if(!window)throw Error('capture_window_missing');
  if(window.isMinimized())window.restore();window.show();window.focus();
  return (await window.capturePage(undefined,{stayAwake:true})).toPNG().toString('base64');
 },page.url());
 await writeFile(path.join(output,name),Buffer.from(png,'base64'));
}
let progress;
const testRestore=process.env.ACOUAGENT_DESKTOP_UAT_RESTORE==='true';
const dockerPath=path.join(process.env.LOCALAPPDATA||'','Programs','DockerDesktop','resources','bin','docker.exe');
function docker(args){
 const result=spawnSync(dockerPath,['--context','desktop-linux',...args],{encoding:'utf8',windowsHide:true,timeout:60000,env:{...process.env,Path:path.dirname(dockerPath)+';'+(process.env.Path||process.env.PATH||'')}});
 if(result.status!==0)throw Error('desktop_uat_docker_operation_failed');return result.stdout.trim();
}
try{
 manager=await electron.firstWindow();manager.on('pageerror',error=>errors.push(error.message));
 await manager.locator('#start').waitFor();
 if(process.env.ACOUAGENT_DESKTOP_UAT_EXPECT_VERSION){
  const initial=await manager.evaluate(()=>window.acouagent.status());
  check(initial.appVersion===process.env.ACOUAGENT_DESKTOP_UAT_EXPECT_VERSION,'actual_installed_version_matches_expected');
 }
 await capture(manager,'01-start.png');
 await manager.locator('#start').click();
 progress=setInterval(async()=>{try{const phase=await manager.locator('body').getAttribute('data-phase');if(phase!==lastPhase){lastPhase=phase;console.log(phase);}}catch{}},10000);
 await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&document.body.dataset.phase!=='idle',null,{timeout:36*60*1000});
 state=await manager.evaluate(()=>window.acouagent.status());
 await manager.bringToFront();
 await capture(manager,'02-runtime-result.png');
 check(state.ready&&state.phase==='ready','actual_windows_packaged_runtime_and_17_skills_ready');
 check(!state.extensions,'extensions_remain_explicit_opt_in');
 await manager.locator('#open').click();
 const deadline=Date.now()+30000;let workbench;
 while(Date.now()<deadline){workbench=electron.windows().find(w=>w!==manager&&w.url().startsWith('http://127.0.0.1:'));if(workbench)break;await new Promise(r=>setTimeout(r,100));}
 check(!!workbench,'separate_workbench_window');
 await workbench.locator('body').waitFor();
 check((await workbench.locator('body').innerText()).includes('AcouAgent'),'actual_portal_rendered');
 check(await workbench.evaluate(()=>typeof require==='undefined'&&typeof process==='undefined'&&typeof window.acouagent==='undefined'),'workbench_has_no_native_bridge');
 await capture(workbench,'03-workbench.png');
 const isolation=await electron.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().map(w=>{const p=w.webContents.getLastWebPreferences();return{nodeIntegration:p.nodeIntegration,contextIsolation:p.contextIsolation,sandbox:p.sandbox,webSecurity:p.webSecurity};}));
 check(isolation.length===2&&isolation.every(p=>!p.nodeIntegration&&p.contextIsolation&&p.sandbox&&p.webSecurity),'both_actual_windows_isolated');
 check(errors.length===0,'manager_has_no_javascript_errors');
 if(process.env.ACOUAGENT_DESKTOP_UAT_SKILLS==='true'){
  if(process.env.ACOUAGENT_DESKTOP_UAT_BACKUP==='true')throw Error('select_separate_skill_preparation_mode');
  const schema=()=>Number(docker(['exec','acouagent-desktop-gateway-1','python','-c',"import sqlite3;c=sqlite3.connect('/app/data/skill_store.db');print(c.execute('select max(version) from schema_migrations').fetchone()[0]);c.close()"]));
  const probeSource=await readFile(path.join(root,'scripts/desktop/data_fingerprint.py'),'utf8');
  const snapshot=()=>JSON.parse(docker(['exec','acouagent-desktop-gateway-1','python','-c',probeSource,'--root','/app/data','--online']));
  function preserved(before,after){
   for(const [name,hash] of Object.entries(before.files))if(after.files[name]!==hash)return false;
   for(const [name,tables] of Object.entries(before.databases))for(const [table,old] of Object.entries(tables)){
    const next=after.databases[name]?.[table];if(!next||JSON.stringify(old.columns)!==JSON.stringify(next.columns)||old.definitionSha256!==next.definitionSha256)return false;
    for(const [hash,count] of Object.entries(old.rows))if((next.rows[hash]||0)<count)return false;
   }return true;
  }
  check(schema()===17,'skill_preparation_starts_from_schema17');
  const originalData=snapshot();await writeFile(path.join(output,'schema17-before.json'),JSON.stringify(originalData));
  const backupRoot=path.join(process.env.LOCALAPPDATA,'AcouAgent','Desktop','backups');
  const oldBackups=new Set(await readdir(backupRoot).catch(error=>{if(error.code==='ENOENT')return[];throw error;}));
  await electron.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false});});
  await capture(manager,'07-before-skill-preparation.png');
  await manager.locator('#skill-settings > summary').click();
  await manager.locator('#prepare-skills').click();
  await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&(document.body.dataset.phase==='error'||document.getElementById('state-title').textContent.includes('手动技能已准备')),null,{timeout:600000});
  state=await manager.evaluate(()=>window.acouagent.status());
  check(state.ready&&state.extensions&&!state.learning,'manual_skills_prepared_without_automatic_learning');
  check(schema()===18,'actual_database_migration_to_schema18');
  const migratedData=snapshot();await writeFile(path.join(output,'schema18-after.json'),JSON.stringify(migratedData));
  check(preserved(originalData,migratedData),'schema18_preserves_all_original_rows_and_assets');
  const created=(await readdir(backupRoot)).filter(name=>name.startsWith('before-skill-extensions-')&&name.endsWith('.zip')&&!oldBackups.has(name));
  check(created.length===1,'complete_pre_migration_backup_created');
  const preMigration=path.join(backupRoot,created[0]);
  check(Number(docker(['exec','acouagent-desktop-gateway-1','python','-c',"import sqlite3;c=sqlite3.connect('/app/data/skill_store.db');print(c.execute(\"select count(*) from skill_validation_profiles where profile_id like 'external-helmholtz-%'\").fetchone()[0]);c.close()"]))===3,'three_exact_external_reference_profiles_installed');
  await capture(manager,'08-manual-skills-ready-learning-off.png');
  await manager.locator('#open').click();
  const manualDeadline=Date.now()+30000;let manualWorkbench;
  while(Date.now()<manualDeadline){manualWorkbench=electron.windows().find(w=>w!==manager&&w.url().startsWith('http://127.0.0.1:'+state.port+'/'));if(manualWorkbench)break;await new Promise(resolve=>setTimeout(resolve,100));}
  check(!!manualWorkbench,'prepared_workbench_reopens_current_capabilities');
  await manualWorkbench.goto('http://127.0.0.1:'+state.port+'/skills/authoring');
  await manualWorkbench.waitForFunction(()=>document.getElementById('status').textContent.includes('标准模板已就绪'),null,{timeout:30000});
  check(!await manualWorkbench.locator('#availability').isVisible(),'actual_manual_authoring_page_available');
  check((await manualWorkbench.locator('#template option').allTextContents()).join(',')==='B01,B02,B03','actual_standard_templates_available');
  await capture(manualWorkbench,'09-manual-authoring-available.png');
  await manager.locator('#learning').click();
  await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&(document.body.dataset.phase==='error'||document.getElementById('state-title').textContent.includes('自动学习已开启')),null,{timeout:600000});
  state=await manager.evaluate(()=>window.acouagent.status());
  check(state.ready&&state.extensions&&state.learning,'automatic_learning_explicitly_enabled');
  check(docker(['exec','acouagent-desktop-gateway-1','python','-c',"import os;print(os.environ['SKILL_LEARNING_ENABLED'])"])==='true','running_gateway_learning_enabled');
  await capture(manager,'10-learning-enabled.png');
  await manager.locator('#learning').click();
  await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&(document.body.dataset.phase==='error'||document.getElementById('state-title').textContent.includes('自动学习已关闭')),null,{timeout:600000});
  state=await manager.evaluate(()=>window.acouagent.status());
  check(state.ready&&state.extensions&&!state.learning,'learning_disabled_without_disabling_manual_skills');
  check(docker(['exec','acouagent-desktop-gateway-1','python','-c',"import os;print(os.environ['SKILL_LEARNING_ENABLED'],os.environ['SKILL_AUTHORING_ENABLED'])"])==='false true','actual_gateway_flags_keep_manual_skills_only');
  const invalid=path.join(output,'invalid-backup.zip');await writeFile(invalid,'not a ZIP archive');
  const previousPointer=JSON.parse(await readFile(path.join(process.env.LOCALAPPDATA,'AcouAgent','Desktop','settings.json'),'utf8')).dataVolume;
  await electron.evaluate(({dialog},source)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[source]});},invalid);
  await manager.locator('#restore').click();
  await manager.waitForFunction(()=>document.body.dataset.phase==='error'&&document.body.dataset.busy==='false',null,{timeout:120000});
  check(JSON.parse(await readFile(path.join(process.env.LOCALAPPDATA,'AcouAgent','Desktop','settings.json'),'utf8')).dataVolume===previousPointer,'invalid_restore_cannot_change_active_data_pointer');
  check(await manager.locator('#restore').isEnabled(),'restore_remains_available_after_failure');
  await electron.evaluate(({dialog},source)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[source]});},preMigration);
  await manager.locator('#restore').click();
  await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&(document.body.dataset.phase==='error'||document.getElementById('state-title').textContent.includes('数据已恢复')),null,{timeout:900000});
  state=await manager.evaluate(()=>window.acouagent.status());
  check(state.ready&&!state.extensions&&!state.learning,'preparation_backup_restores_original_feature_choices');
  check(schema()===17,'actual_schema18_to_schema17_backup_rollback');
  const rolledBackData=snapshot();await writeFile(path.join(output,'schema17-restored.json'),JSON.stringify(rolledBackData));
  check(preserved(originalData,rolledBackData),'schema_rollback_preserves_all_original_rows_and_assets');
  await capture(manager,'11-original-schema-and-flags-restored.png');
 }
 if(process.env.ACOUAGENT_DESKTOP_UAT_BACKUP==='true'){
  const destination=path.join(output,'actual-data-backup.zip');
  let previousVolume;
  if(testRestore){
   previousVolume=JSON.parse(docker(['inspect','acouagent-desktop-gateway-1','--format','{{json .Mounts}}'])).find(m=>m.Destination==='/app/data').Name;
   check(/^(?:acouagent-desktop_desktop_data|acouagent-desktop-data-[a-f0-9]{32})$/.test(previousVolume),'original_desktop_volume_identified');
   docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;Path('/app/data/desktop-restore-uat-marker').write_text('before backup')"]);
  }
  // Only the native file-picker response is automated. UI, IPC, Docker export,
  // SQLite snapshot and archive bytes all use the actual packaged application.
  await electron.evaluate(({dialog},destination)=>{
    dialog.showMessageBox=async()=>({response:1,checkboxChecked:false});
    dialog.showSaveDialog=async()=>({canceled:false,filePath:destination});
  },destination);
  await manager.bringToFront();await manager.locator('#backup').click();
  await manager.waitForFunction(()=>['stopped','error'].includes(document.body.dataset.phase)&&document.body.dataset.busy==='false',null,{timeout:600000});
  state=await manager.evaluate(()=>window.acouagent.status());
  check(state.phase==='stopped','actual_windows_data_backup_export');
  await capture(manager,'04-backup-complete.png');
  const verified=spawnSync('py.exe',['-3.12','-X','utf8','-c',
    "import hashlib,json,sys,zipfile,sqlite3,tempfile;from pathlib import Path;z=zipfile.ZipFile(sys.argv[1]);m=json.loads(z.read('manifest.json'));assert m['kind']=='acouagent-desktop-data';assert {'data/skill_store.db','data/execution_history.db'}<={f['path'] for f in m['files']};assert all(hashlib.sha256(z.read(f['path'])).hexdigest()==f['sha256'] and len(z.read(f['path']))==f['size'] for f in m['files']);assert not any('credentials.bin' in f['path'] for f in m['files']);print(json.dumps({'verifiedFiles':len(m['files']),'totalBytes':m['totalBytes']}))",destination],{encoding:'utf8',windowsHide:true,timeout:60000});
  check(verified.status===0,'actual_backup_file_hashes_and_expected_databases');
  await writeFile(path.join(output,'backup-verification.json'),verified.stdout);
  await manager.locator('#start').click();
  await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&['ready','error'].includes(document.body.dataset.phase),null,{timeout:600000});
  state=await manager.evaluate(()=>window.acouagent.status());
  check(state.ready,'actual_windows_restart_after_backup');
  if(testRestore){
   docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;Path('/app/data/desktop-restore-uat-marker').write_text('after backup')"]);
   await electron.evaluate(({dialog},destination)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[destination]});},destination);
   await manager.locator('#restore').click();
   await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&(document.body.dataset.phase==='error'||(document.body.dataset.phase==='ready'&&document.getElementById('state-title').textContent.includes('恢复'))),null,{timeout:900000});
   state=await manager.evaluate(()=>window.acouagent.status());
   check(state.ready&&state.title.includes('恢复'),'actual_windows_restore_completed');
   const currentVolume=JSON.parse(docker(['inspect','acouagent-desktop-gateway-1','--format','{{json .Mounts}}'])).find(m=>m.Destination==='/app/data').Name;
   check(currentVolume!==previousVolume,'restored_into_new_volume');
   check(docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;print(Path('/app/data/desktop-restore-uat-marker').read_text())"])==='before backup','restored_backup_state_not_latest_state');
   const oldOwner=docker(['volume','inspect',previousVolume,'--format','{{index .Labels "io.acouagent.desktop.install_id"}}']);
   const newOwner=docker(['volume','inspect',currentVolume,'--format','{{index .Labels "io.acouagent.desktop.install_id"}}']);
   check(oldOwner===newOwner&&/^[a-f0-9]{64}$/.test(oldOwner),'both_volumes_remain_owned_by_this_installation');
   const images=JSON.parse(await readFile(path.join(root,'desktop/runtime/images.json'),'utf8'));
   const retained=docker(['run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','128m','--cpus','0.5','--pids-limit','32','--mount','type=volume,src='+previousVolume+',dst=/qa,readonly','--entrypoint','python',images.gateway,'-c',"from pathlib import Path;print(Path('/qa/desktop-restore-uat-marker').read_text())"]);
   check(retained==='after backup','original_volume_preserves_newer_state');
   await capture(manager,'05-restore-complete.png');
   await manager.locator('#open').click();
   const reopenDeadline=Date.now()+30000;let restoredWorkbench;
   while(Date.now()<reopenDeadline){restoredWorkbench=electron.windows().find(w=>w!==manager&&w.url().startsWith('http://127.0.0.1:'+state.port+'/'));if(restoredWorkbench)break;await new Promise(r=>setTimeout(r,100));}
   check(!!restoredWorkbench,'reopened_workbench_uses_current_service_port');
   await restoredWorkbench.locator('body').waitFor();
   check(await restoredWorkbench.evaluate(()=>typeof window.acouagent==='undefined'),'restored_workbench_keeps_no_native_bridge');
   // Remove only the exact markers created by this run; preserve both data volumes.
   docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;Path('/app/data/desktop-restore-uat-marker').unlink(missing_ok=True)"]);
   docker(['run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','128m','--cpus','0.5','--pids-limit','32','--mount','type=volume,src='+previousVolume+',dst=/qa','--entrypoint','python',images.gateway,'-c',"from pathlib import Path;Path('/qa/desktop-restore-uat-marker').unlink(missing_ok=True)"]);
   if(process.env.ACOUAGENT_DESKTOP_UAT_INTERRUPT==='true'){
    const profile=path.join(process.env.LOCALAPPDATA,'AcouAgent','Desktop');
    const journalPath=path.join(profile,'restore-journal.json');
    const vaultPath=path.join(profile,'credentials.bin');
    const vaultBefore=createHash('sha256').update(await readFile(vaultPath)).digest('hex');
    const mainProcess=await electron.evaluate(()=>({pid:process.pid,executable:process.execPath}));
    check(mainProcess.pid!==process.pid&&mainProcess.executable.toLowerCase()===env.ACOUAGENT_ELECTRON_EXE.toLowerCase(),'actual_desktop_main_process_identified');
    docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;Path('/app/data/desktop-restore-uat-marker').write_text('state before interrupted restore')"]);
    await manager.locator('#restore').click();
    let interruptedJournal;const interruptDeadline=Date.now()+120000;
    while(Date.now()<interruptDeadline){
     try{
      const journal=JSON.parse(await readFile(journalPath,'utf8'));
      const settings=JSON.parse(await readFile(path.join(profile,'settings.json'),'utf8'));
      if(settings.dataVolume===journal.next.dataVolume&&journal.previous.dataVolume===currentVolume){interruptedJournal=journal;break;}
     }catch(error){if(error.code!=='ENOENT')throw error;}
     await new Promise(resolve=>setTimeout(resolve,10));
    }
    check(!!interruptedJournal,'actual_restore_interrupted_after_pointer_write');
    clearInterval(progress);
    // On Windows Playwright's process handle is cmd.exe (shell:true), not the
    // Electron main process. Kill only the exact main PID identified above.
    const exited=once(electron.process(),'exit');
    check(process.kill(mainProcess.pid,'SIGKILL'),'own_desktop_main_process_terminated_for_test');await exited;
    check(JSON.parse(await readFile(journalPath,'utf8')).previous.dataVolume===currentVolume,'uncommitted_journal_survives_actual_process_exit');
    await electron.close().catch(()=>{});
    electron=await _electron.launch({executablePath:env.ACOUAGENT_ELECTRON_EXE,args:[],env,timeout:45000});
    manager=await electron.firstWindow();manager.on('pageerror',error=>errors.push(error.message));
    await manager.locator('#start').waitFor();
    await manager.waitForFunction(()=>document.getElementById('state-title').textContent.includes('上次恢复未完成'),null,{timeout:10000});
    check((await manager.locator('#state-title').innerText()).includes('上次恢复未完成'),'fresh_process_reports_interrupted_recovery');
    check(JSON.parse(await readFile(path.join(profile,'settings.json'),'utf8')).dataVolume===currentVolume,'fresh_process_selects_original_data_pointer');
    await manager.locator('#start').click();
    await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&['ready','error'].includes(document.body.dataset.phase),null,{timeout:900000});
    state=await manager.evaluate(()=>window.acouagent.status());
    check(state.ready,'actual_process_crash_recovery_reaches_17_skills_ready');
    check(docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;print(Path('/app/data/desktop-restore-uat-marker').read_text())"])==='state before interrupted restore','original_newer_data_survives_actual_crash');
    const vaultAfter=createHash('sha256').update(await readFile(vaultPath)).digest('hex');
    check(vaultBefore===vaultAfter,'system_encrypted_credentials_unchanged_after_crash');
    await capture(manager,'06-crash-recovered.png');
    docker(['exec','acouagent-desktop-gateway-1','python','-c',"from pathlib import Path;Path('/app/data/desktop-restore-uat-marker').unlink(missing_ok=True)"]);
   }
  }
 }
 if(process.env.ACOUAGENT_DESKTOP_UAT_STOP==='true'){
  await electron.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:1,checkboxChecked:false});});
  await manager.locator('#stop').click();
  await manager.waitForFunction(()=>document.body.dataset.busy==='false'&&['stopped','error'].includes(document.body.dataset.phase),null,{timeout:120000});
  state=await manager.evaluate(()=>window.acouagent.status());check(state.phase==='stopped','actual_services_stopped_preserving_data');
 }
 console.log(JSON.stringify({passed:checks.length}));
}catch(error){errors.push(error.message);if(manager){state=await manager.evaluate(()=>window.acouagent.status()).catch(()=>null);}throw error;}
finally{
 clearInterval(progress);
 await writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_windows_packaged_runtime',checks,errors,state,model_simulation_tested:false,process_crash_tested:checks.includes('original_newer_data_survives_actual_crash'),native_backup_file_selection_automated:process.env.ACOUAGENT_DESKTOP_UAT_BACKUP==='true',resources_preserved:true},null,2));
 await electron.evaluate(({app})=>app.exit(0)).catch(()=>{});await electron.close().catch(()=>{});
}
