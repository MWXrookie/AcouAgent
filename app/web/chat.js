function esc(s) { return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function renderMarkdown(md) {
  var blocks=[];
  var html=String(md||'').replace(/\r\n?/g,'\n');
  // Protect fenced output before applying inline Markdown.
  html=html.replace(/^\s*(\x60{3,}|~{3,})[^\n]*\n([\s\S]*?)^\s*\1\s*$/gm,function(_,mark,body){
    blocks.push('<details class="report-output"><summary>查看原始输出</summary><pre><code>'+esc(body.trimEnd())+'</code></pre></details>');
    return 'REPORTBLOCK'+(blocks.length-1)+'END';
  });
  html=esc(html.replace(/[\p{Extended_Pictographic}\uFE0F\u200D]/gu,''));
  html=html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,function(_,alt,url){
    if(!/^(data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+|https?:\/\/[^\s"'&<>]+|\/(?!\/)[^\s"'&<>]*)$/i.test(url))return '<p>图片地址无效</p>';
    blocks.push('<figure><img src="'+url+'" alt="'+alt.replace(/"/g,'&quot;')+'" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><figcaption hidden>图片加载失败，请在执行看板查看原始图像。</figcaption></figure>');
    return 'REPORTBLOCK'+(blocks.length-1)+'END';
  });
  html=html.replace(/^###\s+(.+)$/gm,'<h3>$1</h3>').replace(/^##\s+(.+)$/gm,'<h2>$1</h2>');
  html=html.replace(/\x60([^\x60\n]+)\x60/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  html=html.replace(/(\|[^\n]+\|\n)(\|[-:\s|]+\|\n)((?:\|[^\n]+\|\n?)*)/g,function(_,header,sep,rows){
    function cells(row,tag){return row.trim().replace(/^\||\|$/g,'').split('|').map(c=>'<'+tag+'>'+c.trim()+'</'+tag+'>').join('');}
    return '<div class="report-table"><table><thead><tr>'+cells(header,'th')+'</tr></thead><tbody>'+rows.trim().split('\n').map(r=>'<tr>'+cells(r,'td')+'</tr>').join('')+'</tbody></table></div>';
  });
  html=html.replace(/(?:^[-*]\s+.+(?:\n|$))+/gm,function(list){return '<ul>'+list.trim().split('\n').map(l=>'<li>'+l.replace(/^[-*]\s+/,'')+'</li>').join('')+'</ul>';});
  html=html.replace(/^---+\s*$/gm,'<hr>');
  html=html.split(/\n\s*\n/).map(function(part){
    part=part.trim();if(!part)return '';
    return /^<(h[1-6]|div|ul|hr)|^REPORTBLOCK/.test(part)?part:'<p>'+part.replace(/\n/g,'<br>')+'</p>';
  }).join('\n');
  return html.replace(/REPORTBLOCK(\d+)END/g,function(_,n){return blocks[n];});
}

var conversation = [];
var currentRequirement = '';
var byokConfigDirty = false;
var byokRegistrationConfirmed = false;
var portalModelState = null;
var modelDialogOpener = null;
var agentClient = window.AcouAgentApi.createAgentClient();
var agentCapabilities = null;

var BYOK_PRESETS = {
  deepseek: {
    baseUrl: 'https://api.deepseek.com',
    models: [
      ['deepseek-flash', 'DeepSeek Flash'],
      ['deepseek-v4-pro', 'DeepSeek V4 Pro']
    ]
  },
  qwen: {
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: [
      ['qwen-plus', 'Qwen Plus'],
      ['qwen-max', 'Qwen Max'],
      ['qwen-turbo', 'Qwen Turbo']
    ]
  }
};

function getModelProfile() {
  return sessionStorage.getItem('acouagent_byok_profile') || localStorage.getItem('acouagent_model_profile') || null;
}

function updateModelReadiness(data) {
  if (data) portalModelState = data;
  var box = document.getElementById('model-readiness');
  if (!box) return;
  var title = document.getElementById('model-readiness-title');
  var detail = document.getElementById('model-readiness-detail');
  var state = portalModelState;
  var handle = getModelProfile();
  if (!state) {
    box.dataset.state = 'error';
    title.textContent = '模型配置暂不可读取';
    detail.textContent = '打开模型设置重试；你的输入会保留。';
  } else if (state.orchestration_backend !== 'local') {
    box.dataset.state = 'configured';
    title.textContent = '使用平台工作流中的模型';
    detail.textContent = '模型由 Dify 配置；此处的自带模型配置不接管平台模型。';
  } else if (handle && handle.startsWith('byok-')) {
    box.dataset.state = 'configured';
    title.textContent = byokRegistrationConfirmed ? '本次会话模型已配置' : '已选择本次会话模型';
    detail.textContent = '配置到期或网关重启后需重新填写；已配置不代表连接测试通过。';
  } else {
    var id = handle || state.default_profile;
    var profile = state.profiles.find(function(p) { return p.id === id && p.configured; });
    box.dataset.state = profile ? 'configured' : 'missing';
    title.textContent = profile ? '已配置：' + profile.model : '开始前，先连接一个模型';
    detail.textContent = profile ? '凭据已配置；连接与兼容性可在模型设置中测试。' : '支持 DeepSeek、Qwen 和 OpenAI 兼容服务。输入内容会保留。';
  }
  box.querySelector('button').textContent = box.dataset.state === 'missing' ? '连接模型' : '模型设置';
  var submit = document.getElementById('ask-btn');
  if (submit && !submit.disabled) submit.textContent = box.dataset.state === 'missing' ? '连接模型并继续' : '开始仿真';
}

async function refreshModelReadiness() {
  try {
    var info = await agentClient.info();
    agentCapabilities = info.capabilities || {};
    if (!agentCapabilities.model_profiles || !agentCapabilities.byok) {
      portalModelState = {
        orchestration_backend: info.backend_id,
        profiles: [],
        default_profile: null,
        capabilities: agentCapabilities
      };
      updateModelReadiness(portalModelState);
      return;
    }
    var data = await agentClient.listModelProfiles();
    if (!Array.isArray(data.profiles)) throw new Error('model_profiles_unavailable');
    data.orchestration_backend = info.backend_id;
    data.capabilities = agentCapabilities;
    updateModelReadiness(data);
  } catch (_) { portalModelState = null; updateModelReadiness(); }
}

async function loadModelProfiles() {
  var select = document.getElementById('model-profile');
  var detail = document.getElementById('model-profile-detail');
  select.innerHTML = '';
  try {
    var info = await agentClient.info();
    agentCapabilities = info.capabilities || {};
    if (!agentCapabilities.model_profiles || !agentCapabilities.byok) {
      select.innerHTML = '<option>平台管理工作流模型</option>';
      detail.textContent = '当前后端由平台管理工作流模型，无需在门户填写模型凭据。';
      return;
    }
    var data = await agentClient.listModelProfiles();
    if (!Array.isArray(data.profiles)) throw new Error('无法读取模型配置');
    data.orchestration_backend = info.backend_id;
    data.capabilities = agentCapabilities;
    updateModelReadiness(data);
    var saved = getModelProfile() || data.default_profile;
    data.profiles.forEach(function(profile) {
      var option = document.createElement('option');
      option.value = profile.id;
      option.textContent = profile.label + ' · ' + profile.model + (profile.configured ? '' : '（未配置凭据）');
      option.disabled = !profile.configured;
      option.selected = profile.id === saved;
      select.appendChild(option);
    });
    var byok = document.createElement('option');
    byok.value = '__byok__';
    byok.textContent = sessionStorage.getItem('acouagent_byok_profile') ? '我的 BYOK 模型（本次会话）' : '使用自己的 API Key（BYOK）';
    byok.selected = !!sessionStorage.getItem('acouagent_byok_profile');
    select.appendChild(byok);
    if (!select.value && data.default_profile) select.value = data.default_profile;
    if (!select.value) select.value = '__byok__';
    detail.textContent = data.orchestration_backend === 'local'
      ? 'BYOK 会用于参数提取、代码生成、审查、解释和重试。公网端点默认要求 HTTPS；本地模型地址需由部署者显式开启。'
      : '当前是 Dify 兼容模式，BYOK 无法接管 Dify 内部模型。请把 ORCHESTRATION_BACKEND 设为 local。';
    toggleByokFields();
  } catch(e) {
    detail.textContent = '无法读取模型配置：' + String(e);
    portalModelState = null; updateModelReadiness();
  }
}

function toggleByokFields() {
  var selected = document.getElementById('model-profile').value;
  var fields = document.getElementById('byok-fields');
  fields.hidden = selected !== '__byok__';
  if (!fields.hidden && !document.getElementById('byok-model-preset').options.length) {
    if (sessionStorage.getItem('acouagent_byok_profile') && !byokConfigDirty) {
      showRestoredByokHandle();
    } else {
      applyByokProvider(true);
    }
  }
  if (selected === '__byok__' && sessionStorage.getItem('acouagent_byok_profile') && !byokConfigDirty) {
    if (byokRegistrationConfirmed) {
      setModelConnectionStatus('success', '本次会话模型已注册', '密钥已从输入框清除；可以再次测试连接或直接开始仿真。');
    } else {
      setModelConnectionStatus('warning', '发现本次浏览器会话配置', '请先测试连接；网关重启或配置到期后需要重新输入 API Key。');
    }
  } else {
    clearModelPreflightResult();
  }
}

function showRestoredByokHandle() {
  var providers = document.getElementById('byok-provider');
  var restored = providers.querySelector('option[value="session"]');
  if (!restored) {
    restored = document.createElement('option');
    restored.value = 'session';
    restored.textContent = '本次会话已注册配置（详情已隐藏）';
    providers.insertBefore(restored, providers.firstChild);
  }
  providers.value = 'session';
  var baseUrl = document.getElementById('byok-base-url');
  baseUrl.value = '';
  baseUrl.placeholder = '已注册端点（不回显）';
  var models = document.getElementById('byok-model-preset');
  models.innerHTML = '<option value="__session__">已注册模型（不回显）</option>';
  document.getElementById('byok-custom-model-wrap').hidden = true;
}

function applyByokProvider(initializing) {
  var provider = document.getElementById('byok-provider').value;
  var preset = BYOK_PRESETS[provider];
  var baseUrl = document.getElementById('byok-base-url');
  var models = document.getElementById('byok-model-preset');
  var restored = document.querySelector('#byok-provider option[value="session"]');
  if (restored) restored.remove();
  baseUrl.placeholder = 'https://api.example.com/v1';
  models.innerHTML = '';
  if (preset) {
    baseUrl.value = preset.baseUrl;
    preset.models.forEach(function(item) {
      var option = document.createElement('option');
      option.value = item[0];
      option.textContent = item[1] + ' · ' + item[0];
      models.appendChild(option);
    });
  } else {
    baseUrl.value = '';
  }
  var custom = document.createElement('option');
  custom.value = '__custom__';
  custom.textContent = '自定义模型 ID';
  models.appendChild(custom);
  if (!preset) models.value = '__custom__';
  toggleCustomByokModel(true);
  if (!initializing) markByokConfigDirty();
}

function toggleCustomByokModel(initializing) {
  var custom = document.getElementById('byok-model-preset').value === '__custom__';
  document.getElementById('byok-custom-model-wrap').hidden = !custom;
  if (!initializing) markByokConfigDirty();
}

function selectedByokModel() {
  var selected = document.getElementById('byok-model-preset').value;
  return selected === '__custom__'
    ? document.getElementById('byok-model-custom').value.trim()
    : selected;
}

function clearModelPreflightResult() {
  setModelConnectionStatus('idle', '等待测试连接', '输入 API Key 后测试；注册成功后 Key 会从输入框清除。');
}

function setModelConnectionStatus(state, title, detail) {
  document.getElementById('model-preflight-result').dataset.state = state;
  document.getElementById('model-preflight-title').textContent = title;
  document.getElementById('model-preflight-detail').textContent = detail;
}

function markByokConfigDirty() {
  byokConfigDirty = true;
  setModelConnectionStatus('idle', '配置已修改', '输入这组配置对应的 API Key，然后测试连接。');
}

function openModelSettings() {
  modelDialogOpener = document.activeElement;
  var modal = document.getElementById('model-modal');
  modal.classList.add('open');
  modal.tabIndex = -1; modal.focus();
  loadModelProfiles().then(function() { if (modal.classList.contains('open')) document.getElementById('model-profile').focus(); });
}

function closeModelSettings() {
  document.getElementById('model-modal').classList.remove('open');
  updateModelReadiness();
  if (modelDialogOpener && modelDialogOpener.isConnected) modelDialogOpener.focus();
}

async function revokeByokProfile() {
  var profile = sessionStorage.getItem('acouagent_byok_profile');
  if (!profile) return;
  sessionStorage.removeItem('acouagent_byok_profile');
  byokRegistrationConfirmed = false;
  try {
    await agentClient.deleteModelSession(profile);
  } catch (_) {}
}

async function registerByokForCurrentSession() {
  var baseUrl = document.getElementById('byok-base-url').value.trim();
  var model = selectedByokModel();
  var apiKey = document.getElementById('byok-api-key').value.trim();
  if (!baseUrl || !model || !apiKey) throw new Error('请填写 API 地址、模型名称和 API Key。');
  var data = await agentClient.createModelSession({base_url:baseUrl, model:model, api_key:apiKey});
  if (!data || !data.profile_id) throw new Error('BYOK 配置失败');
  await revokeByokProfile();
  sessionStorage.setItem('acouagent_byok_profile', data.profile_id);
  localStorage.removeItem('acouagent_model_profile');
  document.getElementById('byok-api-key').value = '';
  document.getElementById('byok-api-key').placeholder = '本次会话已安全注册';
  byokConfigDirty = false;
  byokRegistrationConfirmed = true;
  return data.profile_id;
}

async function testModelConnection() {
  var selected = document.getElementById('model-profile').value;
  var button = document.getElementById('model-test-btn');
  button.disabled = true;
  setModelConnectionStatus('pending', '正在测试连接', '只执行小型兼容性检查，不运行模型返回的代码。');
  try {
    var profile = selected;
    if (selected === '__byok__') {
      var hasNewCredential = document.getElementById('byok-api-key').value.trim();
      if (!hasNewCredential && byokConfigDirty) {
        throw new Error('配置已修改，请输入这组配置对应的 API Key。');
      }
      profile = hasNewCredential ? await registerByokForCurrentSession() : getModelProfile();
      if (!profile || !String(profile).startsWith('byok-')) {
        throw new Error('请填写并测试 BYOK 配置。');
      }
    }
    if (!profile) throw new Error('请选择一个已配置的模型。');
    var data = await agentClient.testModelProfile(profile);
    var checks = Array.isArray(data.checks) ? data.checks : [];
    var passed = checks.filter(function(item) { return item.passed; }).length;
    var detail = String(data.model || '') + ' · ' + String(data.latency_ms) + ' ms · '
      + String(passed) + '/' + String(checks.length) + ' 项兼容检查通过';
    var failedDetails = checks.filter(function(item) { return !item.passed && item.detail; })
      .map(function(item) { return String(item.detail); });
    if (failedDetails.length) detail += '；未通过：' + failedDetails.join('；');
    if (selected === '__byok__') byokRegistrationConfirmed = true;
    setModelConnectionStatus(data.ok ? 'success' : 'warning',
      data.ok ? '连接成功' : '端点已连接，兼容检查未通过', detail);
  } catch (e) {
    var message = String(e.message || e);
    if (selected === '__byok__' && message.indexOf('byok_session_missing_or_expired') !== -1) {
      sessionStorage.removeItem('acouagent_byok_profile');
      byokRegistrationConfirmed = false;
      byokConfigDirty = true;
      document.getElementById('byok-api-key').placeholder = '仅发送到这台自部署服务器';
      message = '本次会话配置已失效，请重新输入 API Key 后测试。';
    }
    setModelConnectionStatus('error', '连接失败', message);
  } finally {
    button.disabled = false;
  }
}

async function saveModelSettings() {
  var selected = document.getElementById('model-profile').value;
  if (selected === '__byok__') {
    var existing = sessionStorage.getItem('acouagent_byok_profile');
    if (existing && !document.getElementById('byok-api-key').value.trim() && !byokConfigDirty) {
      closeModelSettings();
      return;
    }
    setModelConnectionStatus('pending', '正在保存模型配置', '密钥只会写入当前网关进程的临时内存。');
    try {
      await registerByokForCurrentSession();
      setModelConnectionStatus('success', '本次会话模型已注册', '密钥已从输入框清除；关闭设置后即可开始仿真。');
      closeModelSettings();
    } catch (e) {
      setModelConnectionStatus('error', '保存失败', String(e.message || e));
    }
    return;
  }
  await revokeByokProfile();
  if (selected) localStorage.setItem('acouagent_model_profile', selected);
  else localStorage.removeItem('acouagent_model_profile');
  closeModelSettings();
}

// 会话隔离：浏览器 localStorage 持久化 session_id，随请求发给后端（Dify user 按会话区分）
var currentSessionId = null;

function loadSessions() {
  try { return JSON.parse(localStorage.getItem('acouagent_sessions') || '[]'); } catch(e) { return []; }
}
function saveSessions(list) {
  localStorage.setItem('acouagent_sessions', JSON.stringify(list));
}
function newSessionId() {
  return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}
function getSessionId() { return currentSessionId; }

function persistCurrent(updateTime) {
  var list = loadSessions();
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === currentSessionId) { cur = list[i]; break; } }
  if (!cur) return;
  cur.messages = conversation;
  cur.requirement = currentRequirement;
  if (updateTime) { cur.updatedAt = Date.now(); }
  if ((cur.title === '新对话' || !cur.title) && conversation.length > 0) {
    for (var j = 0; j < conversation.length; j++) {
      if (conversation[j].role === 'user') { cur.title = conversation[j].content.slice(0, 20); break; }
    }
  }
  saveSessions(list);
}

function sessionGroupLabel(ts) {
  var d = new Date(ts);
  var now = new Date();
  var days30 = 30 * 24 * 3600 * 1000;
  if (now - d < days30) return '30天内';
  var m = d.getMonth() + 1;
  return d.getFullYear() + '-' + (m < 10 ? '0' + m : m);
}

function renderSessionList() {
  var box = document.getElementById('session-list');
  if (!box) return;
  var list = loadSessions().slice().sort(function(a,b) { return b.updatedAt - a.updatedAt; });
  box.replaceChildren();
  var lastGroup = '';
  list.forEach(function(s) {
    var group = sessionGroupLabel(s.updatedAt);
    if (group !== lastGroup) {
      var label = document.createElement('div');label.className = 'session-group';label.textContent = group;
      box.appendChild(label);lastGroup = group;
    }
    var row = document.createElement('div');row.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
    var open = document.createElement('button');open.type = 'button';open.className = 'session-open';
    open.textContent = s.title || '新对话';open.title = open.textContent;
    if (s.id === currentSessionId) open.setAttribute('aria-current','true');
    open.addEventListener('click',function() { switchSession(s.id); });
    var remove = document.createElement('button');remove.type = 'button';remove.className = 'session-del';
    remove.textContent = '×';remove.setAttribute('aria-label','删除对话：' + (s.title || '新对话'));
    remove.addEventListener('click',function() { deleteSession(s.id); });
    row.append(open,remove);box.appendChild(row);
  });
}

function switchSession(id) {
  if (id === currentSessionId) return;
  persistCurrent(false);
  var list = loadSessions();
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === id) { cur = list[i]; break; } }
  if (!cur) return;
  localStorage.setItem('acouagent_current', id);
  currentSessionId = id;
  conversation = cur.messages || [];
  currentRequirement = cur.requirement || '';
  document.getElementById('merge-hint').textContent = '';
  renderChat();
  renderSessionList();
  document.getElementById('query-input').focus();
}

function deleteSession(id) {
  if (!confirm('删除这个对话？此操作不可撤销。')) return;
  var list = loadSessions().filter(function(s) { return s.id !== id; });
  if (id === currentSessionId) {
    if (list.length) {
      localStorage.setItem('acouagent_current', list[0].id);
      currentSessionId = list[0].id;
      conversation = list[0].messages || [];
      currentRequirement = list[0].requirement || '';
    } else {
      var fresh = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
      list = [fresh];
      localStorage.setItem('acouagent_current', fresh.id);
      currentSessionId = fresh.id;
      conversation = [];
      currentRequirement = '';
    }
  }
  saveSessions(list);
  renderChat();
  renderSessionList();
}

function renderChat() {
  var log = document.getElementById('chat-log');
  if (!conversation.length) {
    var suggestions = [
      '2 MHz 点声源，均匀介质，区域 1 cm × 1 cm，网格 200×200',
      '1 MHz 点声源在水中传播，区域 2 cm × 2 cm，网格 200×200',
      '800 kHz 点声源，声速 1500 m/s，区域 1 cm × 1 cm，记录中心点压力随时间变化'
    ];
    log.innerHTML = '<div class="chat-empty">'
      + '<div class="empty-title">开始一次声学仿真</div>'
      + '<div class="empty-sub">用自然语言描述你的问题，支持多轮修改参数</div>'
      + '<div class="suggestions">'
      + suggestions.map(function(s) { return '<button type="button" class="chip" onclick="fillQuery(this)">' + esc(s) + '</button>'; }).join('')
      + '</div></div>';
    return;
  }
  log.innerHTML = conversation.map(function(m) {
    var cls = m.role === 'user' ? 'user' : 'assistant';
    var body = m.loading ? '<span class="typing"><i></i><i></i><i></i></span>' : (m.role === 'user' ? esc(m.content) : renderMarkdown(m.content));
    return '<div class="msg ' + cls + '">' + body + '</div>';
  }).join('');
  log.scrollTop = log.scrollHeight;
}

function fillQuery(el) {
  var input = document.getElementById('query-input');
  input.value = el.textContent;
  input.focus();
}

async function ask() {
  var input = document.getElementById('query-input');
  var btn = document.getElementById('ask-btn');
  var hint = document.getElementById('merge-hint');
  var message = input.value.trim();
  if (!message || btn.disabled) return;
  await refreshModelReadiness();
  if (btn.disabled) return;
  if (input.value.trim() !== message) { hint.textContent = '输入已更新，请确认后再次提交。'; return; }
  var modelState = document.getElementById('model-readiness').dataset.state;
  if (modelState === 'missing' || modelState === 'error') {
    hint.textContent = '请先配置模型；本次输入已保留。';
    openModelSettings();
    return;
  }
  input.value = '';
  hint.textContent = '';
  var skillFeedback = document.getElementById('skill-usage-feedback');
  if (skillFeedback) skillFeedback.textContent = '技能库：本次尚未确认参考调用';
  window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:[],final:true}}));

  conversation.push({role: 'user', content: message});
  renderChat();
  btn.disabled = true;
  btn.textContent = '运行中';
  btn.classList.add('loading');
  window.dispatchEvent(new CustomEvent('acouagent-progress',{detail:'请求已提交 · 等待工作流返回进度'}));

  var loadingIdx = conversation.length;
  conversation.push({role: 'assistant', content: '', loading: true});
  renderChat();

  try {
    var data = await agentClient.chat({
      stream: true,
      message: message,
      requirement: currentRequirement,
      session_id: getSessionId(),
      model_profile: getModelProfile()
    }, {
      onProgress: function(event) {
        window.dispatchEvent(new CustomEvent('acouagent-progress',{detail:'当前步骤：'+event.stage}));
        conversation[loadingIdx] = {role: 'assistant', content: '当前步骤：' + event.stage + '。请稍候...'};
        renderChat();
      },
      onSkillUsage: function(event) {
        if (skillFeedback) skillFeedback.textContent = event.skill_usage.message + ' · v' + event.skill_usage.version;
        window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:[event.skill_usage],final:false}}));
        conversation[loadingIdx] = {role: 'assistant', content: event.skill_usage.message};
        renderChat();
      }
    });

    var report = data.report || '';
    if (skillFeedback) skillFeedback.textContent = Array.isArray(data.skill_usage) && data.skill_usage.length ? data.skill_usage.map(function(s) { return s.message + ' · v' + s.version; }).join('；') : '本次未确认交付经过核验的技能参考';
    window.dispatchEvent(new CustomEvent('acouagent-skill-usage',{detail:{items:Array.isArray(data.skill_usage)?data.skill_usage:[],final:true}}));
    if (Array.isArray(data.skill_usage) && data.skill_usage.length) {
      report += '\n\n**技能库调用反馈**\n' + data.skill_usage.map(function(s) { return '- ' + s.message + '（v' + s.version + '，编号 ' + s.skill_id + '）'; }).join('\n');
    }
    if (data.workflow_status === 'error' && data.request_id) report += '\n\n请求编号：' + data.request_id;
    currentRequirement = data.requirement || '';
    conversation[loadingIdx] = {role: 'assistant', content: report || '未获取到结果'};
    if (data.merge_used) {
      hint.textContent = '已根据历史需求合并本轮修改';
    }
  } catch(e) {
    conversation[loadingIdx] = {role: 'assistant', content: '请求失败：' + esc(String(e))};
  }
  renderChat();
  btn.disabled = false;
  btn.textContent = '开始仿真';
  btn.classList.remove('loading');
  window.dispatchEvent(new CustomEvent('acouagent-progress',{detail:'本次请求已结束 · 以报告中的成功或失败状态为准'}));
  persistCurrent(true);
  renderSessionList();
}

function resetChat() {
  persistCurrent(false);
  var fresh = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
  var list = loadSessions();
  list.unshift(fresh);
  saveSessions(list);
  localStorage.setItem('acouagent_current', fresh.id);
  currentSessionId = fresh.id;
  conversation = fresh.messages;
  currentRequirement = '';
  document.getElementById('merge-hint').textContent = '';
  var feedback = document.getElementById('skill-usage-feedback');
  if (feedback) feedback.textContent = '';
  renderChat();
  renderSessionList();
  document.getElementById('query-input').focus();
}

function initChat() {
  var list = loadSessions();
  var curId = localStorage.getItem('acouagent_current');
  var cur = null;
  for (var i = 0; i < list.length; i++) { if (list[i].id === curId) { cur = list[i]; break; } }
  if (!cur) {
    cur = { id: newSessionId(), title: '新对话', messages: [], requirement: '', updatedAt: Date.now() };
    list.unshift(cur);
    saveSessions(list);
    localStorage.setItem('acouagent_current', cur.id);
  }
  currentSessionId = cur.id;
  conversation = cur.messages || [];
  currentRequirement = cur.requirement || '';
  renderChat();
  renderSessionList();
}

document.getElementById('model-modal').addEventListener('keydown', function(event) {
  if (event.key === 'Escape') { event.preventDefault(); closeModelSettings(); return; }
  if (event.key !== 'Tab') return;
  var focusable = Array.from(this.querySelectorAll('button,input,select,a[href]')).filter(function(el) { return !el.disabled && el.getClientRects().length; });
  var first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
refreshModelReadiness();
window.initChat = initChat;
