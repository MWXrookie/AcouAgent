'use strict';
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const fsp=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const {pipeline}=require('node:stream/promises');
const {Transform}=require('node:stream');

/** Commit the chosen destination only after a complete, successful export. */
async function streamBackup(executable,args,{env,destination,timeout=600000,maxBytes=9*1024**3}={}){
 const temporary=path.join(path.dirname(destination),'.'+path.basename(destination)+'.'+crypto.randomUUID()+'.partial');
 let child,timer,errorOutput='',bytes=0;
 try{
  const target=fs.createWriteStream(temporary,{flags:'wx',mode:0o600});
  child=spawn(executable,args,{env,shell:false,windowsHide:true,stdio:['ignore','pipe','pipe']});
  const exit=new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',code=>code===0?resolve():reject(Error(errorOutput||'backup_export_failed')));});
  child.stderr.on('data',chunk=>{errorOutput=(errorOutput+chunk.toString('utf8')).slice(-16000);});
  timer=setTimeout(()=>child.kill(),timeout);
  const limit=new Transform({transform(chunk,_encoding,done){bytes+=chunk.length;done(bytes>maxBytes?Error('backup_size_limit'):null,chunk);}});
  const outcomes=await Promise.allSettled([pipeline(child.stdout,limit,target).catch(error=>{child.kill();throw error;}),exit]);
  for(const outcome of outcomes)if(outcome.status==='rejected')throw outcome.reason;
  if(bytes===0)throw Error('empty_backup_export');
  await fsp.rename(temporary,destination);
  return {bytes};
 }finally{clearTimeout(timer);await fsp.unlink(temporary).catch(error=>{if(error.code!=='ENOENT')throw error;});}
}
/** Feed a chosen archive to the fixed offline importer without buffering it. */
async function streamRestore(executable,args,{env,source,timeout=600000,maxBytes=9*1024**3}={}){
 const stats=await fsp.stat(source);
 if(!stats.isFile()||stats.size===0||stats.size>maxBytes)throw Error('invalid_backup_input');
 const child=spawn(executable,args,{env,shell:false,windowsHide:true,stdio:['pipe','pipe','pipe']});
 let output='',errors='',bytes=0;
 const timer=setTimeout(()=>child.kill(),timeout);
 const exit=new Promise((resolve,reject)=>{child.once('error',reject);child.once('close',code=>code===0?resolve():reject(Error(errors||'backup_import_failed')));});
 child.stdout.on('data',chunk=>{output=(output+chunk.toString('utf8')).slice(-2000000);});
 child.stderr.on('data',chunk=>{errors=(errors+chunk.toString('utf8')).slice(-16000);});
 const limit=new Transform({transform(chunk,_encoding,done){bytes+=chunk.length;done(bytes>maxBytes?Error('backup_size_limit'):null,chunk);}});
 try{
  const outcomes=await Promise.allSettled([pipeline(fs.createReadStream(source),limit,child.stdin).catch(error=>{child.kill();throw error;}),exit]);
  for(const outcome of outcomes)if(outcome.status==='rejected')throw outcome.reason;
  return JSON.parse(output);
 }finally{clearTimeout(timer);}
}
module.exports={streamBackup,streamRestore};
