/* Review server-derived parameters before a controlled task is submitted. */
(()=>{'use strict';
if(!document.getElementById('query-input')||typeof window.ask!=='function')return;
const originalAsk=window.ask,byId=id=>document.getElementById(id);
let busy=false,pending=null,previousFocus=null,dirty=false;
const dialog=document.createElement('dialog');dialog.id='task-review';dialog.className='task-review';dialog.setAttribute('aria-labelledby','task-review-title');
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
const title=el('h2','核对本次任务');title.id='task-review-title';dialog.append(title);
const intro=el('p','已识别为房间模态频率分析。核对参数后再开始计算。','muted');dialog.append(intro);
const state=el('p');state.setAttribute('role','status');state.setAttribute('aria-live','polite');dialog.append(state);
const table=el('table');table.className='task-parameters';const head=el('tr');['参数','识别值','状态'].forEach(t=>head.append(el('th',t)));const thead=el('thead');thead.append(head);table.append(thead);const rows=el('tbody');table.append(rows);dialog.append(table);
const issues=el('ul');issues.className='task-issues';issues.setAttribute('role','alert');dialog.append(issues);
const evidence=el('details');evidence.append(el('summary','查看参数原文依据'));const sources=el('div');evidence.append(sources);dialog.append(evidence);
const assumptions=el('fieldset');assumptions.append(el('legend','确认计算约定'));const proposalList=el('div');assumptions.append(proposalList);dialog.append(assumptions);
const original=el('details');original.append(el('summary','修改原始问题并重新识别'));const label=el('label','原始问题');label.htmlFor='task-source';const source=el('textarea');source.id='task-source';source.rows=5;source.maxLength=65536;original.append(label,source);dialog.append(original);
const actions=el('div',undefined,'row task-actions');const cancel=el('button','取消'),refresh=el('button','重新识别'),confirm=el('button','确认参数并开始计算');[cancel,refresh,confirm].forEach(b=>b.type='button');confirm.className='primary';actions.append(cancel,refresh,confirm);dialog.append(actions);document.body.append(dialog);
const statusLabels={explicit:'已明确',converted:'单位已换算',missing:'未提供',conflict:'存在冲突',invalid:'数值无效',unit_ambiguous:'单位不明确',proposed:'约定待确认'};
const valueLabels={rectangle:'矩形',rigid:'刚性（声硬边界）',periodic:'周期边界',unsupported:'暂不支持'};
function setBusy(value){busy=value;dialog.setAttribute('aria-busy',String(value));refresh.disabled=value;cancel.disabled=value;source.disabled=value;proposalList.querySelectorAll('input').forEach(i=>i.disabled=value);byId('ask-btn').disabled=value;updateConfirm()}
function updateConfirm(){confirm.disabled=busy||dirty||!pending||!pending.execution_available||pending.contract.status!=='ready_for_confirmation'||[...proposalList.querySelectorAll('input')].some(i=>!i.checked)}
async function cancelPending(){if(pending?.task_id)await fetch('/api/task-intake/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:pending.task_id,session_id:getSessionId(),model_profile:getModelProfile()||null})}).catch(()=>{})}
function close(){if(busy)return;cancelPending();dialog.close();dirty=true;updateConfirm();if(previousFocus?.isConnected)previousFocus.focus()}
cancel.onclick=close;dialog.addEventListener('cancel',event=>{event.preventDefault();close()});
source.addEventListener('input',()=>{if(!dirty)cancelPending();dirty=true;state.textContent='问题已修改，请重新识别后再确认。';updateConfirm()});
dialog.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.tagName!=='TEXTAREA')event.preventDefault()});
async function requestPreview(message,requirement=''){
 const response=await fetch('/api/task-intake',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,requirement,session_id:getSessionId(),model_profile:getModelProfile()||null,previous_task_id:pending?.task_id||null})});
 if(!response.ok)throw Error('暂时无法识别任务，请保留输入并稍后重试。');return response.json();
}
function show(value){
 pending=value;dirty=false;const contract=value.contract;rows.replaceChildren();sources.replaceChildren();issues.replaceChildren();proposalList.replaceChildren();source.value=contract.source_text;
 for(const f of contract.fields){
  if(f.name==='density_kg_m3'&&f.status==='missing')continue;
  const tr=el('tr'),v=f.value===null?'—':f.name==='include_zero_mode'?(f.value?'计入':'不计入'):f.name==='dimensions'?(f.value===2?'二维':'三维'):f.name==='mode_count'?f.value+'个':(valueLabels[f.value]||String(f.value))+(f.unit?' '+f.unit:'');
  tr.append(el('th',f.label),el('td',v),el('td',statusLabels[f.status]||'待核对'));rows.append(tr);
  if(f.sources.length){const p=el('p');p.append(el('strong',f.label+'：'),document.createTextNode(f.sources.map(s=>s.fragment).join('；')));sources.append(p)}
 }
 for(const item of contract.issues)issues.append(el('li',item.message));
 for(const name of contract.unsupported)issues.append(el('li',({dimensions:'目前仅支持二维房间',geometry:'目前仅支持空矩形房间',boundary_condition:'目前仅支持所有墙面为刚性',mode_count_over_20:'首批支持前20个以内的模态',nonuniform_medium:'目前仅支持均匀介质'})[name]||'所选条件尚不支持'));
 for(const item of contract.proposals){const l=el('label'),input=el('input');input.type='checkbox';input.value=item.id;input.addEventListener('change',updateConfirm);l.append(input,document.createTextNode(' '+item.label));proposalList.append(l)}
 assumptions.hidden=!contract.proposals.length;issues.hidden=!issues.childElementCount;original.open=contract.status!=='ready_for_confirmation';
 state.textContent=contract.status==='needs_input'?'还有条件需要补充或澄清，请修改原始问题后重新识别。':contract.status==='unsupported'?'当前条件暂不支持计算，请核对适用范围。':value.execution_available?'参数已识别，请核对数值和下方计算约定。':'参数已识别；当前版本尚未开放此任务的求解。';
 if(!dialog.open){previousFocus=document.activeElement;dialog.showModal();title.tabIndex=-1;title.focus()}
 else{const focus=contract.issues.length?issues:state;focus.tabIndex=-1;focus.focus()}
 updateConfirm();
}
refresh.onclick=async()=>{if(busy)return;setBusy(true);state.textContent='正在重新识别…';try{const value=await requestPreview(source.value);if(!value.handled)throw Error('修改后的问题未识别为房间模态任务，请取消后作为新任务提交。');show(value)}catch(error){state.textContent=error.message;dirty=true}finally{setBusy(false)}};
window.ask=async function(){
 if(busy)return;const input=byId('query-input'),message=input.value.trim();if(!message||byId('ask-btn').disabled)return;
 const prior=currentRequirement||'';setBusy(true);
 try{
  const value=await requestPreview(message,prior);
  if(input.value.trim()!==message)throw Error('输入已改变，请核对后重新提交。');
  if(value.handled){show(value);return}
  setBusy(false);busy=true;return await originalAsk();
 }catch(error){byId('merge-hint').textContent=error.message}finally{setBusy(false)}
};
confirm.onclick=async()=>{
 if(confirm.disabled)return;const contract=pending.contract;
 const body={task_id:pending.task_id,contract_sha256:contract.contract_sha256,message:source.value,session_id:getSessionId(),model_profile:getModelProfile()||null,accepted_proposals:[...proposalList.querySelectorAll('input:checked')].map(i=>i.value)};
 setBusy(true);dialog.close();byId('query-input').value='';byId('merge-hint').textContent='已确认参数，正在计算，请勿重复提交。';
 conversation.push({role:'user',content:contract.source_text});const index=conversation.length;conversation.push({role:'assistant',content:'正在按已确认的条件计算…',loading:true});renderChat();
 try{
  const response=await fetch('/api/task-intake/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw Error('确认已过期或条件发生变化，请重新识别。');
  conversation[index]={role:'assistant',content:result.report||'未收到结果，请查看执行记录。'};currentRequirement=contract.source_text;
 }catch(error){conversation[index]={role:'assistant',content:'本次请求未确认完成。请先查看执行看板，再决定是否重新提交；系统不会自动重试。'};byId('query-input').value=contract.source_text;}
 finally{pending=null;dirty=true;renderChat();persistCurrent(true);setBusy(false);byId('merge-hint').textContent='';}
};
})();
