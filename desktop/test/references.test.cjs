'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const {createReferenceDownloader,bounded}=require('../src/references.cjs');
async function fixture(t){
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'acouagent-reference-test-'));t.after(()=>fs.rm(root,{recursive:true,force:true}));
 const output=path.join(root,'references');await fs.mkdir(output);const data={};const manifest={commit:'c'.repeat(40),cases:{}};
 for(const id of ['B01','B02','B03']){data[id]=Buffer.from('fixed test bytes '+id);manifest.cases[id]={name:id.toLowerCase(),sha256:crypto.createHash('sha256').update(data[id]).digest('hex')};}
 await fs.writeFile(path.join(root,'references.json'),JSON.stringify(manifest));return{root,output,data};
}
test('fixed reference downloads are bounded, redirect-free and hash-checked before writing',async t=>{
 const {root,output,data}=await fixture(t),requests=[];
 const download=createReferenceDownloader(root,async(url,options)=>{requests.push({url,options});return new Response(data[/\/(b0[123])\.mat/.exec(url)[1].toUpperCase()]);});
 await download(output);assert.equal(requests.length,3);assert.ok(requests.every(r=>r.url.startsWith('https://raw.githubusercontent.com/ucl-bug/jwave-data/')&&r.options.redirect==='error'));
 for(const id of Object.keys(data))assert.deepEqual(await fs.readFile(path.join(output,id+'.mat')),data[id]);
});
test('content mismatch is refused instead of silently replacing the answer through another source',async t=>{
 const {root,output}=await fixture(t);let calls=0;
 await assert.rejects(createReferenceDownloader(root,async()=>{calls++;return new Response('wrong data');})(output),/reference_digest_mismatch/);
 assert.equal(calls,1);assert.deepEqual(await fs.readdir(output),[]);
});
test('network fallback uses the same fixed commit and validates decoded bytes',async t=>{
 const {root,output,data}=await fixture(t);
 await createReferenceDownloader(root,async url=>{
  if(url.includes('raw.githubusercontent.com'))throw Error('network unavailable');
  assert.ok(url.startsWith('https://api.github.com/repos/ucl-bug/jwave-data/contents/'));assert.ok(url.endsWith('?ref='+'c'.repeat(40)));
  return Response.json({encoding:'base64',content:data[/\/(b0[123])\.mat/.exec(url)[1].toUpperCase()].toString('base64')});
 })(output);
 assert.equal((await fs.readdir(output)).length,3);
 await assert.rejects(bounded(new Response(Buffer.alloc(9)),8),/reference_size_limit/);
});
