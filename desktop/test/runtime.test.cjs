'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {Runtime, redact, parseRecords, dockerCandidates, waitForGatewayHealth, atomicWrite, OWNER_LABEL} = require('../src/runtime.cjs');
const {Vault} = require('../src/vault.cjs');
const resourceDir = path.resolve(__dirname, '../runtime');
const secrets = Object.fromEntries(['MCP_AUTH_TOKEN','EXECUTOR_SHARED_TOKEN','PORTAL_ADMIN_TOKEN','ACOUAGENT_INSTALL_ID'].map((key, i) => [key, String(i+1).repeat(64)]));
async function fixture(t, behavior) {
  const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'acouagent-desktop-test-'));
  t.after(() => fs.rm(dataDir, {recursive: true, force: true}));
  const calls = [];
  const runner = async (file, args, options) => {
    calls.push({file,args,options});
    if (behavior) {const value = await behavior(file,args,options);if (value !== undefined) return value;}
    const text = args.join(' ');
    if (text.includes('context ls')) return 'default\ndesktop-linux';
    if (text.includes('context inspect')) return JSON.stringify('npipe:////./pipe/dockerDesktopLinuxEngine');
    if (text.includes('.OSType')) return '"linux"';
    if (text.includes('.Architecture')) return '"x86_64"';
    if (text.includes('volume inspect')) throw Error('no such volume');
    return '';
  };
  const runtime = new Runtime({resourceDir,dataDir,vault:{load:async()=>({...secrets})},runner,platform:'win32'});
  await runtime.initialize(); return {runtime,calls,dataDir};
}

test('missing Docker produces actionable state without writing a plaintext secret file', async t => {
  const {runtime,dataDir} = await fixture(t, () => {throw Object.assign(Error('missing'),{code:'ENOENT'});});
  const state = await runtime.start();
  assert.equal(state.phase,'error');assert.match(state.detail,/Docker Desktop/);assert.equal(state.ready,false);
  assert.deepEqual(await fs.readdir(dataDir),[]);
});

test('new Docker installation is discoverable without restarting the desktop app for PATH', async t => {
  const previous = process.env.LOCALAPPDATA;
  process.env.LOCALAPPDATA = 'C:\\Users\\Tester\\AppData\\Local';
  t.after(() => {if(previous === undefined) delete process.env.LOCALAPPDATA; else process.env.LOCALAPPDATA = previous;});
  const candidates = dockerCandidates('win32', {LOCALAPPDATA: 'C:\\Users\\Tester\\AppData\\Local', ProgramFiles: 'C:\\Program Files'});
  assert.deepEqual(candidates, ['docker.exe', 'C:\\Users\\Tester\\AppData\\Local\\Programs\\DockerDesktop\\resources\\bin\\docker.exe', 'C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe']);
  const {runtime,calls} = await fixture(t, file => {if(file === 'docker.exe') throw Object.assign(Error('stale PATH'), {code:'ENOENT'});});
  await runtime.inspectEnvironment();
  assert.ok(runtime.dockerExecutable.endsWith('resources\\bin\\docker.exe'));
  assert.equal(calls.filter(c=>c.file==='docker.exe').length,1);
  assert.ok(calls.some(c=>c.args.includes('info')));
  const invocation=calls.find(c=>c.args.includes('info'));
  const pathKey=Object.keys(invocation.options.env).find(key=>key.toUpperCase()==='PATH');
  assert.ok(invocation.options.env[pathKey].startsWith(path.win32.dirname(runtime.dockerExecutable)+';'));
  assert.notEqual(process.env[pathKey],invocation.options.env[pathKey]);
});

test('Docker command errors never retry a different executable or repeat a mutation', async t => {
  const {runtime,calls} = await fixture(t, () => {throw Object.assign(Error('access denied'),{code:'EACCES'});});
  await assert.rejects(runtime.docker(['compose','up','-d']), {code:'EACCES'});
  assert.equal(calls.length,1);
});
test('remote context is refused before compose or resource mutation', async t => {
  const {runtime,calls}=await fixture(t, (_file,args)=>args.includes('inspect')&&args.includes('context')?JSON.stringify('tcp://remote.example:2375'):undefined);
  await runtime.start();assert.match(runtime.state.detail,/不是允许的本机/);
  assert.equal(calls.some(c=>c.args.includes('compose')),false);
});
test('Windows containers are not treated as the tested Linux executor', async t => {
  const {runtime,calls}=await fixture(t, (_file,args)=>args.join(' ').includes('.OSType')?'"windows"':undefined);
  await runtime.start();assert.match(runtime.state.detail,/Linux/);assert.equal(calls.some(c=>c.args.includes('up')),false);
});
test('foreign project containers cannot be stopped by a new installation', async t => {
  const {runtime,calls}=await fixture(t, (_file,args)=>{
    if(args.includes('ps')&&!args.includes('compose'))return 'abcdef123456';
    if(args.includes('inspect')&&args.includes('abcdef123456'))return JSON.stringify({[OWNER_LABEL]:'different-owner'});
  });
  await runtime.stop();assert.match(runtime.state.detail,/不属于本安装/);assert.equal(calls.some(c=>c.args.includes('stop')),false);
});
test('foreign data volume cannot be attached, even when no containers remain', async t => {
  const {runtime,calls}=await fixture(t, (_file,args)=>args.includes('volume')&&args.includes('inspect')?JSON.stringify({[OWNER_LABEL]:'foreign'}):undefined);
  await runtime.start();assert.match(runtime.state.detail,/同名数据卷/);assert.equal(calls.some(c=>c.args.includes('up')),false);
});
test('stop is limited to own compose project and never removes a volume', async t => {
  const {runtime,calls}=await fixture(t);
  await runtime.stop();assert.equal(runtime.state.phase,'stopped');
  const stop=calls.find(c=>c.args.includes('stop'));assert.ok(stop);assert.ok(stop.args.includes('acouagent-desktop'));
  assert.equal(calls.some(c=>c.args.includes('down')||c.args.includes('prune')||c.args.includes('rm')),false);
  assert.ok(stop.args.includes('--env-file'));assert.equal(stop.options.env.MCP_AUTH_TOKEN,secrets.MCP_AUTH_TOKEN);
});

test('backup refuses foreign data before pausing any service',async t=>{
 const {runtime,calls}=await fixture(t,(_file,args)=>args.includes('volume')&&args.includes('inspect')?JSON.stringify({[OWNER_LABEL]:'foreign'}):undefined);
 runtime.state.phase='ready';runtime.state.ready=true;
 await runtime.backup('unused.zip');
 assert.equal(runtime.state.phase,'error');assert.equal(calls.some(c=>c.args.includes('stop')),false);
});

test('backup refuses to export if a writer is still running after stop',async t=>{
 const {runtime,calls}=await fixture(t,(_file,args)=>args.includes('compose')&&args.includes('ps')?JSON.stringify([{Service:'gateway',State:'running'}]):undefined);
 runtime.state.phase='ready';runtime.state.ready=true;
 await runtime.backup('unused.zip');
 assert.equal(runtime.state.phase,'error');assert.match(runtime.state.logs.join(' '),/backup_writers_still_running/);
 assert.equal(calls.some(c=>c.args.includes('stop')),true);
});
test('inherited remote Docker and Compose override variables are removed', async t => {
  const oldHost=process.env.DOCKER_HOST,oldFile=process.env.COMPOSE_FILE;
  process.env.DOCKER_HOST='tcp://remote:2375';process.env.COMPOSE_FILE='foreign.yaml';
  t.after(()=>{oldHost===undefined?delete process.env.DOCKER_HOST:process.env.DOCKER_HOST=oldHost;oldFile===undefined?delete process.env.COMPOSE_FILE:process.env.COMPOSE_FILE=oldFile;});
  const {runtime}=await fixture(t);const env=runtime.environment();
  assert.equal(env.DOCKER_HOST,undefined);assert.equal(env.COMPOSE_FILE,undefined);assert.match(env.ACOUAGENT_GATEWAY_IMAGE,/@sha256:/);
});
test('concurrent lifecycle requests do not dispatch duplicate Docker operations', async t => {
  let release;const pending=new Promise(r=>release=r);
  const {runtime,calls}=await fixture(t, async()=>{await pending;throw Error('stopped test');});
  const first=runtime.start();await runtime.start();assert.equal(calls.length,1);release();await first;
});
test('diagnostic logs redact actual secrets and common credential formats', async t => {
  const {runtime}=await fixture(t);runtime.log('token='+secrets.MCP_AUTH_TOKEN+'\nBearer abcDEF123 API_KEY=private-value');
  const log=runtime.snapshot().logs.join(' ');assert.ok(!log.includes(secrets.MCP_AUTH_TOKEN));assert.ok(!log.includes('abcDEF123'));assert.ok(!log.includes('private-value'));
  assert.equal(redact('<img src=x>',[]),'<img src=x>');
});
test('invalid settings are preserved and rejected instead of silently reset', async t => {
  const {runtime,dataDir}=await fixture(t);const settings=path.join(dataDir,'settings.json');
  await fs.writeFile(settings,'{"port":"http://remote","extensions":false}');
  await assert.rejects(runtime.initialize(),/invalid_desktop_settings/);assert.match(await fs.readFile(settings,'utf8'),/remote/);
});
test('atomic settings replacement preserves all intended fields', async t => {
  const {dataDir}=await fixture(t);const target=path.join(dataDir,'settings.json');
  await atomicWrite(target,'{"port":18081}');await atomicWrite(target,'{"port":18082}');
  assert.equal(await fs.readFile(target,'utf8'),'{"port":18082}');assert.deepEqual(await fs.readdir(dataDir),['settings.json']);
});
test('Compose v2 array and newline-delimited JSON are both accepted',()=>{
  assert.deepEqual(parseRecords('[{"Service":"gateway"}]'),[{Service:'gateway'}]);
  assert.deepEqual(parseRecords('{"Service":"gateway"}\n{"Service":"skill-bootstrap"}'),[{Service:'gateway'},{Service:'skill-bootstrap'}]);
});
test('vault requires system encryption and never silently replaces an unreadable vault',async t=>{
  const {dataDir}=await fixture(t);
  await assert.rejects(new Vault(dataDir,{isEncryptionAvailable:()=>false}).load(),/system_encryption_unavailable/);
  await fs.writeFile(path.join(dataDir,'credentials.bin'),'damaged');
  const vault=new Vault(dataDir,{isEncryptionAvailable:()=>true,decryptString:()=>{throw Error('wrong user');}});
  await assert.rejects(vault.load(),/desktop_credentials_unreadable/);
  assert.equal(await fs.readFile(path.join(dataDir,'credentials.bin'),'utf8'),'damaged');
});
test('cached exact images start without pulling and readiness requires all 17 skills',async t=>{
  let started=false;
  const {runtime,calls}=await fixture(t,(_file,args)=>{
    if(args.includes('up')){started=true;return '';}
    if(args.includes('compose')&&args.includes('ps'))return started?JSON.stringify([{Service:'gateway',State:'running'},{Service:'skill-bootstrap',State:'exited',ExitCode:0}]):'[]';
    if(args.includes('status-all'))return JSON.stringify({ready:true,ready_count:17});
  });
  const oldFetch=global.fetch;global.fetch=async()=>({ok:true,text:async()=>'ok'});t.after(()=>{global.fetch=oldFetch;});
  await runtime.start();assert.equal(runtime.state.ready,true);assert.equal(runtime.state.phase,'ready');
  assert.equal(calls.some(c=>c.args.includes('pull')),false);
  const saved=JSON.parse(await fs.readFile(path.join(runtime.dataDir,'settings.json'),'utf8'));
  assert.equal(saved.extensions,false);assert.ok(!JSON.stringify(saved).includes(secrets.MCP_AUTH_TOKEN));
});
test('failed physical bootstrap cannot produce a ready workspace',async t=>{
  let started=false;
  const {runtime,calls}=await fixture(t,(_file,args)=>{
    if(args.includes('up')){started=true;return '';}
    if(args.includes('compose')&&args.includes('ps'))return started?JSON.stringify([{Service:'skill-bootstrap',State:'exited',ExitCode:1}]):'[]';
  });
  await runtime.start();assert.equal(runtime.state.ready,false);assert.match(runtime.state.detail,/技能准备未通过/);
  assert.equal(calls.some(c=>c.args.includes('status-all')),false);
});

test('recreated gateway is awaited across connection failures and non-ready HTTP responses',async t=>{
 const previous=global.fetch;t.after(()=>{global.fetch=previous;});let attempts=0;
 global.fetch=async()=>{attempts++;if(attempts===1)throw Error('connection refused');return{ok:attempts>2,text:async()=>'ok'};};
 await waitForGatewayHealth('http://127.0.0.1:18081',{timeoutMs:1000,intervalMs:1});
 assert.equal(attempts,3);
});

test('gateway readiness never accepts HTTP 200 with a wrong health body',async t=>{
 const previous=global.fetch;t.after(()=>{global.fetch=previous;});
 global.fetch=async()=>({ok:true,text:async()=>'still starting'});
 await assert.rejects(waitForGatewayHealth('http://127.0.0.1:18081',{timeoutMs:15,intervalMs:1}),/gateway_not_healthy/);
});
