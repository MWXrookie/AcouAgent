'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),crypto=require('node:crypto');
async function bounded(response,limit){
 if(!response.ok)throw Error('reference_http_failed');
 const reader=response.body.getReader(),chunks=[];let size=0;
 try{while(true){const item=await reader.read();if(item.done)break;size+=item.value.length;if(size>limit)throw Error('reference_size_limit');chunks.push(Buffer.from(item.value));}}
 finally{await reader.cancel().catch(()=>{});}
 return Buffer.concat(chunks);
}
function createReferenceDownloader(resourceDir,fetcher){return async directory=>{
 const manifest=JSON.parse(await fs.readFile(path.join(resourceDir,'references.json'),'utf8'));
 if(!/^[a-f0-9]{40}$/.test(manifest.commit)||Object.keys(manifest.cases).sort().join(',')!=='B01,B02,B03')throw Error('invalid_reference_manifest');
 for(const [id,entry] of Object.entries(manifest.cases)){
  if(!/^[a-z0-9_]+$/.test(entry.name)||!/^[a-f0-9]{64}$/.test(entry.sha256))throw Error('invalid_reference_manifest');
  const relative='tests/kwave_data/'+entry.name+'.mat';let raw;
  try{raw=await bounded(await fetcher('https://raw.githubusercontent.com/ucl-bug/jwave-data/'+manifest.commit+'/'+relative,{redirect:'error',signal:AbortSignal.timeout(20000)}),1048576);}
  catch{
   const response=await fetcher('https://api.github.com/repos/ucl-bug/jwave-data/contents/'+relative+'?ref='+manifest.commit,{redirect:'error',signal:AbortSignal.timeout(20000),headers:{Accept:'application/vnd.github+json'}});
   const value=JSON.parse((await bounded(response,2097152)).toString('utf8'));
   if(value.encoding!=='base64'||typeof value.content!=='string')throw Error('reference_payload_invalid');
   const content=value.content.replace(/\s/g,'');raw=Buffer.from(content,'base64');
   if(raw.toString('base64')!==content||raw.length>1048576)throw Error('reference_payload_invalid');
  }
  if(crypto.createHash('sha256').update(raw).digest('hex')!==entry.sha256)throw Error('reference_digest_mismatch');
  await fs.writeFile(path.join(directory,id+'.mat'),raw,{flag:'wx',mode:0o644});
 }
};}
module.exports={createReferenceDownloader,bounded};
