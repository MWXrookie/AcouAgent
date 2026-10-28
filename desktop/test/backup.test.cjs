'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
const {streamBackup}=require('../src/backup.cjs');
async function fixture(t){const root=await fs.mkdtemp(path.join(os.tmpdir(),'acouagent-backup-test-'));t.after(()=>fs.rm(root,{recursive:true,force:true}));return path.join(root,'data.zip');}
test('backup preserves exact binary bytes and commits only successful output',async t=>{
 const destination=await fixture(t);
 const value=await streamBackup(process.execPath,['-e','process.stdout.write(Buffer.from([0,255,1,10]))'],{destination});
 assert.equal(value.bytes,4);assert.deepEqual([...await fs.readFile(destination)],[0,255,1,10]);
});
test('export failure cannot overwrite the existing chosen backup',async t=>{
 const destination=await fixture(t);await fs.writeFile(destination,'keep');
 await assert.rejects(streamBackup(process.execPath,['-e',"process.stdout.write('partial');process.exitCode=1"],{destination}));
 assert.equal(await fs.readFile(destination,'utf8'),'keep');assert.deepEqual(await fs.readdir(path.dirname(destination)),['data.zip']);
});
test('bounded streaming aborts oversized and missing exporters without partial artifacts',async t=>{
 const destination=await fixture(t);
 await assert.rejects(streamBackup(process.execPath,['-e','process.stdout.write(Buffer.alloc(1024))'],{destination,maxBytes:16}),/backup_size_limit/);
 await assert.rejects(streamBackup(path.join(path.dirname(destination),'missing-executable'),[],{destination}));
 assert.deepEqual(await fs.readdir(path.dirname(destination)),[]);
});
