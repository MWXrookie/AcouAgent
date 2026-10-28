/** Real Docker lifecycle acceptance on a disposable Linux CI runner.
 * Uses the exact bundled OCI digests and physical bootstrap. No model API calls.
 * Refuses hosts already containing the desktop project or its volume.
 */
'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),crypto=require('node:crypto');
const {Runtime,command}=require('../../desktop/src/runtime.cjs');
const root=path.resolve(__dirname,'../..'),output=path.resolve(process.env.ACOUAGENT_RUNTIME_SMOKE_OUTPUT||path.join(root,'outputs/desktop-runtime-smoke'));
async function main(){
 if(process.platform!=='linux'||process.env.CI!=='true')throw Error('explicit_disposable_linux_ci_required');
 await fs.mkdir(output,{recursive:true});const temp=await fs.mkdtemp(path.join(os.tmpdir(),'acouagent-desktop-runtime-'));
 const secrets=Object.fromEntries(['MCP_AUTH_TOKEN','EXECUTOR_SHARED_TOKEN','PORTAL_ADMIN_TOKEN','ACOUAGENT_INSTALL_ID'].map(k=>[k,crypto.randomBytes(32).toString('hex')]));
 const runtime=new Runtime({resourceDir:path.join(root,'desktop/runtime'),dataDir:temp,vault:{load:async()=>secrets}});await runtime.initialize();
 const checks=[];function check(v,name){if(!v)throw Error(name);checks.push(name);}
 const old=await runtime.docker(['ps','-aq','--filter','label=com.docker.compose.project=acouagent-desktop'],{context:false});
 if(old.trim())throw Error('existing_desktop_project_refused');
 try{await runtime.docker(['volume','inspect','acouagent-desktop_desktop_data'],{context:false});throw Error('existing_desktop_volume_refused');}catch(e){if(!/no such volume|not found/i.test(e.message))throw e;}
 runtime.on('state',s=>{if(s.phase!==main.phase){main.phase=s.phase;console.log(s.phase);}});
 try{
  await runtime.start();check(runtime.state.ready,'real_start_and_17_physical_skills_ready');
  const executorId=(await runtime.compose(['ps','-q','jwave-executor'])).trim();
  const inspected=JSON.parse(await runtime.docker(['inspect',executorId]));const h=inspected[0].HostConfig;
  check(h.ReadonlyRootfs&&h.CapDrop.includes('ALL')&&h.SecurityOpt.includes('no-new-privileges:true')&&h.PidsLimit===256,'executor_security_preserved');
  const network=JSON.parse(await runtime.docker(['network','inspect','acouagent-desktop_executor_internal']));check(network[0].Internal===true,'executor_network_internal');
  const gatewayId=(await runtime.compose(['ps','-q','gateway'])).trim();
  const gateway=JSON.parse(await runtime.docker(['inspect',gatewayId]));check(gateway[0].HostConfig.PortBindings['8001/tcp'].every(p=>p.HostIp==='127.0.0.1'),'gateway_loopback_only');
  await runtime.compose(['exec','-T','gateway','python','-c',"from pathlib import Path;Path('/app/data/desktop-retention-marker').write_text('kept')"]);
  await runtime.stop();check(runtime.state.phase==='stopped','stop_success');
  await runtime.start();check(runtime.state.ready,'restart_revalidates_existing_skills');
  const marker=await runtime.compose(['exec','-T','gateway','python','-c',"from pathlib import Path;print(Path('/app/data/desktop-retention-marker').read_text())"]);check(marker==='kept','data_preserved_after_restart');
  check(await (await fetch(runtime.baseURL()+'/portal')).text().then(x=>x.includes('AcouAgent')),'actual_portal_available');
  await fs.writeFile(path.join(output,'report.json'),JSON.stringify({kind:'actual_disposable_linux_desktop_runtime',checks,images:runtime.images,windows_full_solver_acceptance:false},null,2));
  console.log(JSON.stringify({passed:checks.length}));
 }finally{
  // Resource ownership was established before startup; cleanup only this disposable project.
  await runtime.assertOwnership();await runtime.compose(['down','--volumes','--remove-orphans'],{timeout:90000}).catch(e=>console.error(e.message));
  await fs.rm(temp,{recursive:true,force:true});
 }
}
main().catch(e=>{console.error(e.message);process.exitCode=1});
