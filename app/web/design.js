(()=>{'use strict';
const root=document.documentElement;
function apply(t){root.dataset.theme=t;document.querySelectorAll('.design-theme').forEach(b=>{b.textContent=t==='dark'?'切换浅色':'切换深色';b.setAttribute('aria-label',b.textContent+'主题');b.setAttribute('aria-pressed',String(t==='light'))})}
let saved;try{saved=localStorage.getItem('acouagent-theme')}catch(e){}
apply(saved==='dark'?'dark':'light');
const nav=[['/portal','声学仿真'],['/dashboard','执行看板'],['/skills','技能库'],['/cache','纠错缓存']];
const chat=!!document.getElementById('chat-log');document.body.classList.add(chat?'design-chat':'design-page');
let side=document.querySelector('.app-sidebar');if(!side){side=document.createElement('aside');side.className='design-sidebar';side.setAttribute('aria-label','主导航');document.body.prepend(side)}
side.setAttribute('aria-label','主导航');
side.replaceChildren();const brand=document.createElement('div');brand.className='brand';brand.textContent='AcouAgent';const sub=document.createElement('small');sub.textContent='声学仿真工作台';brand.append(sub);side.append(brand);
for(const [href,label]of nav){const a=document.createElement('a');a.href=href;a.className='side-link';a.textContent=label;if(location.pathname===href||(href==='/portal'&&['/','/chat'].includes(location.pathname))||(href==='/skills'&&location.pathname.startsWith('/skills/'))){a.classList.add('active');a.setAttribute('aria-current','page')}side.append(a)}
const foot=document.createElement('div');foot.className='design-sidebar-footer';foot.textContent='受控执行 · 证据重核';side.append(foot);
const toggle=document.createElement('button');toggle.type='button';toggle.className='design-theme';toggle.addEventListener('click',()=>{const t=root.dataset.theme==='light'?'dark':'light';apply(t);try{localStorage.setItem('acouagent-theme',t)}catch(e){}});(document.querySelector('.header')||document.body).append(toggle);apply(root.dataset.theme);
function referenceName(item){
 if(item.presentation?.title)return item.presentation.title;
 const text=typeof item.state_text==='string'?item.state_text:'',parts=text.split('；');
 const names={initial_pressure_2d:'二维初始压力',plane_wave_2d:'二维平面波',point_source_2d:'二维点声源',point_source_3d:'三维点声源',interface_2d:'介质界面',heterogeneous_2d:'二维异质介质',attenuation_helmholtz_2d:'频域衰减'};
 if(parts.length>1&&parts[0].includes('受控执行器')&&parts.slice(1).join('；').trim())return parts.slice(1).join('；').trim();
 if(text&&!text.includes('受控执行器')&&!text.includes('未认证候选')&&text!=='Automatic controlled source')return text;
 const inputs=(item.display_parameters||[]).map(f=>f.label+' '+(Array.isArray(f.value)?f.value.map(n=>Number(n.toPrecision(6))).join('×'):Number(f.value.toPrecision(6)))+(f.unit?' '+f.unit:'')).join('；');
 return (names[item.scenario_type]||item.scenario_type||'声学仿真')+' · '+(item.status==='candidate'?'待验证案例':'仿真方案')+(inputs?' · '+inputs:'');
}
if(chat){
 const sess=document.createElement('div');sess.className='session-section';sess.innerHTML='<div class="session-head">历史会话</div><div class="session-list" id="session-list"></div>';side.insertBefore(sess,foot);
 if(window.initChat)window.initChat();
 const history=document.createElement('button');history.type='button';history.className='history-toggle';history.textContent='历史会话';history.setAttribute('aria-controls','session-list');history.setAttribute('aria-expanded','false');
 function closeHistory(){document.body.classList.remove('show-sessions');history.setAttribute('aria-expanded','false')}
 history.addEventListener('click',()=>{const open=document.body.classList.toggle('show-sessions');history.setAttribute('aria-expanded',String(open))});
 document.querySelector('.header').append(history);
 sess.addEventListener('click',e=>{if(e.target.closest('.session-open'))closeHistory()});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('show-sessions')){closeHistory();history.focus()}});
 document.querySelector('.reset-btn')?.addEventListener('click',closeHistory);


 const btn=document.getElementById('ask-btn');btn.textContent='开始仿真';const observe=new MutationObserver(()=>{root.classList.toggle('design-busy',btn.disabled);btn.setAttribute('aria-busy',String(btn.disabled));if(!btn.disabled&&btn.textContent==='发送')btn.textContent='开始仿真'});observe.observe(btn,{attributes:true,attributeFilter:['disabled'],childList:true});
 const hint=document.getElementById('merge-hint');if(hint&&!hint.textContent)hint.textContent='Enter 换行 · Ctrl / ⌘ + Enter 发送';
 const rail=document.createElement('aside');rail.className='design-reference';rail.setAttribute('aria-label','技能参考');const h=document.createElement('h2');h.textContent='技能参考';const note=document.createElement('p');note.textContent='查看固定配方与适用范围。列表展示不代表本次仿真已调用。';rail.append(h,note);const loading=document.createElement('p');loading.textContent='正在读取技能…';loading.setAttribute('role','status');rail.append(loading);const all=document.createElement('a');all.href='/skills';all.textContent='查看技能库 →';rail.append(all);document.body.append(rail);
 fetch('/skills/api?limit=3&archived=false',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json()}).then(data=>{loading.remove();if(!data.available||!data.items.length){note.textContent='当前没有可读取的技能。仿真仍可按原流程运行。';return}for(const item of data.items){const a=document.createElement('a');a.className='design-reference-row';a.href='/skills?skill_id='+encodeURIComponent(item.skill_id);a.textContent=referenceName(item);a.title=item.skill_id;const s=document.createElement('small');s.textContent=(item.status==='active'?'可用 · 调用时重核证据':'候选 · 尚不可作为可信参考')+' · '+item.quality_level;a.append(s);rail.insertBefore(a,all)}}).catch(()=>{loading.textContent='技能列表暂不可读取，原仿真流程不受影响。';loading.setAttribute('role','alert')});
}
const list=document.getElementById('list');if(list){const search=document.getElementById('skill-search'),scenario=document.getElementById('scenario-filter'),status=document.getElementById('status-filter'),state=document.getElementById('list-state');function filter(){const q=(search?.value||'').trim().toLowerCase();let visible=0;list.querySelectorAll('button.skill').forEach(b=>{const hide=!b.textContent.toLowerCase().includes(q)||(scenario?.value&&b.dataset.scenario!==scenario.value)||(status?.value&&b.dataset.status!==status.value);b.hidden=hide;if(!hide)visible++});list.querySelectorAll('h3').forEach(h=>{let n=h.nextElementSibling,shown=false;while(n&&n.tagName!=='H3'){if(n.matches('button.skill')&&!n.hidden)shown=true;n=n.nextElementSibling}h.hidden=!shown});if(state&&list.children.length){state.hidden=visible>0;state.dataset.kind='empty';if(!visible){state.querySelector('strong').textContent='暂无匹配技能';state.querySelector('span').textContent='调整搜索、场景或生命周期筛选。'}}}for(const el of [search,scenario,status])el?.addEventListener(el===search?'input':'change',filter);window.addEventListener('acouagent-skill-filter',filter);new MutationObserver(filter).observe(list,{childList:true});list.addEventListener('click',e=>{const b=e.target.closest('button.skill');if(b)list.querySelectorAll('button.skill').forEach(x=>x.setAttribute('aria-pressed',String(x===b)))})}
document.querySelectorAll('.modal input').forEach(i=>{const l=i.previousElementSibling;if(l&&l.tagName==='LABEL')l.htmlFor=i.id});
})();
