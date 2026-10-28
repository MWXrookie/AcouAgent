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
module.exports={streamBackup};
