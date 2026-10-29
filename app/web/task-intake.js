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
dialog.insertBefore(issues,table);
const evidence=el('details');evidence.append(el('summary','查看参数原文依据'));const sources=el('div');evidence.append(sources);dialog.append(evidence);
const assumptions=el('fieldset');assumptions.append(el('legend','确认计算约定'));const proposalList=el('div');assumptions.append(proposalList);dialog.append(assumptions);
const original=el('details');original.append(el('summary','修改原始问题并重新识别'));const label=el('label','原始问题');label.htmlFor='task-source';const source=el('textarea');source.id='task-source';source.rows=5;source.maxLength=65536;original.append(label,source);dialog.append(original);
const actions=el('div',undefined,'row task-actions');const cancel=el('button','取消'),refresh=el('button','重新识别'),confirm=el('button','确认参数并开始计算');[cancel,refresh,confirm].forEach(b=>b.type='button');confirm.className='primary';actions.append(cancel,refresh,confirm);dialog.append(actions);document.body.append(dialog);
const statusLabels={explicit:'已明确',converted:'单位已换算',derived:'按原文明示展开',missing:'未提供',conflict:'存在冲突',invalid:'数值无效',unit_ambiguous:'单位不明确',proposed:'约定待确认',model_proposed:'模型给出的值，待确认'};
const recovery=el('button','查询上次确认任务');recovery.type='button';recovery.className='task-recovery';recovery.hidden=true;byId('merge-hint').after(recovery);
const recoveryKey='acouagent-room-pending';let lastTask=null;
try{lastTask=JSON.parse(sessionStorage.getItem(recoveryKey)||'null');recovery.hidden=!lastTask}catch(e){}
async function taskStatus(){if(!lastTask)return null;const r=await fetch('/api/task-intake/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(lastTask)});if(!r.ok)throw Error('暂时无法查询，请保留会话并查看执行看板。');return r.json()}
recovery.onclick=async()=>{if(busy)return;setBusy(true);try{const result=await taskStatus();byId('merge-hint').textContent=result?.found?(result.report||'已查询上次计算状态。'):'暂未查到已登记结果；这不能证明任务没有执行，请勿连续重试。';if(result?.found&&['succeeded','rejected','error'].includes(result.state)){conversation.push({role:'assistant',content:result.report});renderChat();persistCurrent(true)}}catch(error){byId('merge-hint').textContent=error.message}finally{setBusy(false)}};
const valueLabels={rectangle:'矩形',rigid:'刚性（声硬边界）',periodic:'周期边界',unsupported:'暂不支持',absorbing_pml:'吸收层边界',time_domain:'时域传播',frequency_domain:'频域稳态',modal_initial_pressure:'周期模态初始压力',gaussian_initial_pressure:'高斯初始压力',continuous_sinusoid:'连续正弦',tone_burst:'有限周期脉冲',jwave_additive_pressure_coefficient:'jwave附加源项压力系数（非规定边界声压）',homogeneous_water:'均匀水介质',homogeneous:'均匀介质',zero:'零',air:'空气',point:'点声源',planar_x_uniform_y:'沿x方向平面、y方向均匀'};
function displayValue(f){
 if(f.value===null)return '—';if(f.name==='include_zero_mode')return f.value?'计入':'不计入';
 if(f.name==='dimensions')return f.value+'维';if(f.name==='mode_count')return f.value+'个';
 if(typeof f.value==='boolean')return f.value?'是':'否';
 if(Array.isArray(f.value))return f.value.map(x=>typeof x==='number'?Number(x.toPrecision(6)):Array.isArray(x)?x.join(','):'已声明区域').join(['grid_shape','domain_N','domain_dx','dx_m'].includes(f.name)?' × ':'；')+(f.unit?' '+f.unit:'');
 if(typeof f.value==='object')return f.name==='result_spec'?[f.value.kind==='steady_complex_pressure'?'稳态复压力':String(f.value.kind||'未明确结果量'),f.value.units==='numerical_source_normalization'?'数值源归一化':String(f.value.units||'未明确单位'),'轴序 '+(Array.isArray(f.value.axis_order)?f.value.axis_order:[]).join(' → '),'网格 '+(Array.isArray(f.value.grid_shape)?f.value.grid_shape:[]).join(' × '),f.value.full_complex===true?'保留完整复数':'未确认完整复数',f.value.downsample===1?'不降采样':'降采样 '+String(f.value.downsample)].join('；'):'结构化条件（在依据中展开完整内容）';
 return (valueLabels[f.value]||String(f.value))+(f.unit?' '+f.unit:'');
}
function setBusy(value){busy=value;dialog.setAttribute('aria-busy',String(value));refresh.disabled=value;cancel.disabled=value;source.disabled=value;proposalList.querySelectorAll('input').forEach(i=>i.disabled=value);byId('ask-btn').disabled=value;updateConfirm()}
function updateConfirm(){confirm.disabled=busy||dirty||!pending||pending.owner_session!==getSessionId()||pending.owner_profile!==(getModelProfile()||null)||!pending.execution_available||pending.contract.status!=='ready_for_confirmation'||[...proposalList.querySelectorAll('input')].some(i=>!i.checked)}
async function cancelPending(){if(pending?.task_id)await fetch('/api/task-intake/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:pending.task_id,session_id:pending.owner_session,model_profile:pending.owner_profile})}).catch(()=>{})}
function close(){if(busy)return;cancelPending();dialog.close();dirty=true;updateConfirm();if(previousFocus?.isConnected)previousFocus.focus()}
cancel.onclick=close;dialog.addEventListener('cancel',event=>{event.preventDefault();close()});
source.addEventListener('input',()=>{if(!dirty)cancelPending();dirty=true;state.textContent='问题已修改，请重新识别后再确认。';updateConfirm()});
dialog.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.tagName!=='TEXTAREA')event.preventDefault()});
async function requestPreview(message,requirement=''){
 await cancelPending();
 const owner_session=getSessionId(),owner_profile=getModelProfile()||null;
 const response=await fetch('/api/task-intake',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,requirement,session_id:owner_session,model_profile:owner_profile})});
 const result=await response.json();if(!response.ok){if(result.error==='task_model_setup_required'){openModelSettings();throw Error('请先连接模型，原始输入已保留。')}throw Error(result.error==='task_parameter_extraction_unavailable'?'参数识别暂未完成，请保留输入稍后重试；尚未执行。':'暂时无法识别任务，请保留输入并稍后重试。')}return Object.assign(result,{owner_session,owner_profile});
}
function show(value){
 pending=value;dirty=false;const contract=value.contract;rows.replaceChildren();sources.replaceChildren();issues.replaceChildren();proposalList.replaceChildren();source.value=contract.source_text;
 intro.textContent=({room_eigenmodes_2d:'房间模态频率分析',frequency_domain_field:'频域稳态声场',initial_pressure_propagation:'初始压力传播',driven_time_field:'声源驱动的时域声场',unknown_simulation:'待澄清的声学任务'})[contract.task_kind]+'。核对参数后再开始计算；模型识别的值需要你明确确认。';
 for(const f of contract.fields){
  if(contract.task_kind==='room_eigenmodes_2d'&&f.name==='density_kg_m3'&&f.status==='missing')continue;
  const tr=el('tr'),v=displayValue(f);
  tr.append(el('th',f.label),el('td',v),el('td',statusLabels[f.status]||'待核对'));rows.append(tr);
  if(f.sources.length){const p=el('p');p.append(el('strong',f.label+'：'),document.createTextNode(f.sources.map(s=>s.fragment).join('；')));sources.append(p)}
  if(f.value&&typeof f.value==='object'&&(f.name==='result_spec'||Array.isArray(f.value)&&f.value.some(x=>x&&typeof x==='object'&&!Array.isArray(x)))){const d=el('details');d.append(el('summary',f.label+'的完整识别结果'),el('pre',JSON.stringify(f.value,null,2)));sources.append(d)}
 }
 for(const item of contract.issues)issues.append(el('li',item.message));
 for(const name of contract.unsupported)issues.append(el('li',({dimensions:'目前仅支持二维房间',geometry:'目前仅支持空矩形房间',boundary_condition:'目前仅支持所有墙面为刚性',mode_count_over_20:'首批支持前20个以内的模态',numeric_range:'首批长度和声速数值范围为1e-6至1e6（SI单位）',nonuniform_medium:'目前仅支持均匀介质',workflow_task_kind:'请说明当前支持的声学传播、稳态声场或房间模态问题'})[name]||'所选条件尚不支持'));
 for(const item of contract.proposals){const l=el('label'),input=el('input');input.type='checkbox';input.value=item.id;input.addEventListener('change',updateConfirm);l.append(input,document.createTextNode(' '+item.label));proposalList.append(l)}
 assumptions.hidden=!contract.proposals.length;issues.hidden=!issues.childElementCount;original.open=contract.status!=='ready_for_confirmation';
 state.textContent=contract.status==='needs_input'?'还有条件需要补充或澄清，请修改原始问题后重新识别。':contract.status==='unsupported'?'当前条件暂不支持计算，请核对适用范围。':value.execution_available?'参数已识别，请核对数值和下方计算约定。':'参数已识别；当前版本尚未开放此任务的求解。';
 if(!dialog.open){previousFocus=document.activeElement;dialog.showModal();title.tabIndex=-1;title.focus()}
 else{const focus=contract.issues.length?issues:state;focus.tabIndex=-1;focus.focus()}
 updateConfirm();
}
refresh.onclick=async()=>{if(busy)return;setBusy(true);state.textContent='正在重新识别…';try{const value=await requestPreview(source.value);if(!value.handled)throw Error('修改后的问题暂不支持此确认入口，请取消后作为新任务提交。');show(value)}catch(error){state.textContent=error.message;dirty=true}finally{setBusy(false)}};
window.ask=async function(){
 if(busy)return;const input=byId('query-input'),message=input.value.trim();if(!message||byId('ask-btn').disabled)return;
 const prior=currentRequirement||'';setBusy(true);byId('merge-hint').textContent='正在识别任务与参数；尚未开始求解。';
 try{
  const value=await requestPreview(message,prior);
  if(input.value.trim()!==message)throw Error('输入已改变，请核对后重新提交。');
  if(value.handled){byId('merge-hint').textContent='';show(value);return}
  setBusy(false);busy=true;return await originalAsk();
 }catch(error){byId('merge-hint').textContent=error.message}finally{setBusy(false)}
};
confirm.onclick=async()=>{
 if(confirm.disabled)return;const contract=pending.contract;
 const body={task_id:pending.task_id,contract_sha256:contract.contract_sha256,message:source.value,session_id:getSessionId(),model_profile:getModelProfile()||null,accepted_proposals:[...proposalList.querySelectorAll('input:checked')].map(i=>i.value)};
 lastTask={task_id:body.task_id,session_id:body.session_id,model_profile:body.model_profile};try{sessionStorage.setItem(recoveryKey,JSON.stringify(lastTask))}catch(e){}recovery.hidden=false;
 setBusy(true);dialog.close();byId('query-input').value='';byId('merge-hint').textContent='已确认参数，正在计算，请勿重复提交。';
 conversation.push({role:'user',content:contract.source_text});const index=conversation.length;conversation.push({role:'assistant',content:'正在按已确认的条件计算…',loading:true});renderChat();
 let polling=false;const poll=setInterval(async()=>{if(polling)return;polling=true;try{const result=await taskStatus();if(busy&&pending?.task_id===body.task_id&&result?.workflow_status==='running')byId('merge-hint').textContent=result.report}catch(e){}finally{polling=false}},2500);
 try{
  const response=await fetch('/api/task-intake/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw Error('确认已过期或条件发生变化，请重新识别。');
  let report=result.report||'未收到结果，请查看执行记录。';const usage=Array.isArray(result.skill_usage)?result.skill_usage:[];
  window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:usage,final:true}}));
  if(usage.length)report+='\n\n技能库调用反馈：\n'+usage.map(s=>'- '+s.message+(s.version?'（版本'+s.version+'）':'')).join('\n');
  conversation[index]={role:'assistant',content:report};currentRequirement=contract.source_text;
 }catch(error){let status=null;try{status=await taskStatus()}catch(e){}conversation[index]={role:'assistant',content:status?.report||'本次请求未确认完成。请查询上次确认任务或查看执行看板后核对；系统不会自动重复提交整项任务。'};byId('query-input').value=contract.source_text;}
 finally{clearInterval(poll);pending=null;dirty=true;renderChat();persistCurrent(true);setBusy(false);byId('merge-hint').textContent='';}
};
})();
