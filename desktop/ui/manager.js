'use strict';
const $ = id => document.getElementById(id);
const phases = {idle:'尚未启动',checking:'检查环境',downloading:'准备镜像',starting:'启动服务',bootstrap:'准备技能',ready:'已就绪',stopping:'停止中',stopped:'已停止','backing-up':'备份中',restoring:'恢复中','preparing-skills':'准备技能扩展',error:'需要处理'};
function render(state) {
  document.body.dataset.phase = state.phase;
  document.body.dataset.busy = String(state.busy);
  $('state-title').textContent = state.title;
  $('state-detail').textContent = state.detail;
  $('status-badge').textContent = phases[state.phase] || state.phase;
  if (state.appVersion) $('version').textContent = '桌面版 ' + state.appVersion;
  $('start').hidden = state.ready;
  $('start').textContent = state.busy ? '正在准备…' : state.phase === 'error' ? '重新检查并启动' : '检查并启动';
  $('start').disabled = state.busy;
  $('open').hidden = !state.ready;
  $('open').disabled = state.busy;
  $('stop').hidden = !state.ready;
  $('stop').disabled = state.busy;
  $('admin').disabled = !state.ready || state.busy;
  $('backup').disabled = state.busy || !['ready','stopped'].includes(state.phase);
  $('restore').disabled = state.busy || !['ready','stopped','error'].includes(state.phase);
  $('prepare-skills').disabled = !state.ready || state.busy || state.extensions;
  $('learning').disabled = !state.ready || state.busy || !state.extensions;
  $('learning').textContent = state.learning ? '关闭自动学习' : '开启自动学习';
  $('skill-status').textContent = state.extensions ? '手动技能已准备。自动学习' + (state.learning ? '已开启' : '未开启') + '；仅独立质量验证通过的结果可以学习。' : '默认提供17项基础技能。准备扩展后可以手动添加技能；准备前会保存完整备份，自动学习需单独开启。';
  $('logs').textContent = state.logs.length ? state.logs.join('\n\n') : '尚无操作记录。';
  const current = {checking:0,downloading:1,starting:1,bootstrap:2,ready:3}[state.phase];
  document.querySelectorAll('.steps li').forEach((el, index) => {el.classList.toggle('complete', current !== undefined && index < current);if(index===current)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');});
}
async function action(fn, success) {
  $('action-feedback').textContent = '';
  try {const result = await fn();if(result?.phase)render(result);if(success)$('action-feedback').textContent = success;}
  catch { $('action-feedback').textContent = '操作未完成，请重试或导出脱敏诊断。'; }
}
$('start').addEventListener('click', () => action(window.acouagent.start));
$('stop').addEventListener('click', () => action(window.acouagent.stop));
$('open').addEventListener('click', () => action(window.acouagent.open));
$('docker-guide').addEventListener('click', () => action(window.acouagent.dockerGuide));
$('admin').addEventListener('click', () => action(window.acouagent.copyAdminToken, '令牌已复制。仅在本机技能管理页面使用，请勿发到群聊。'));
$('diagnostics').addEventListener('click', () => action(window.acouagent.diagnostics));
$('backup').addEventListener('click', () => action(window.acouagent.backup));
$('restore').addEventListener('click', () => action(window.acouagent.restore));
$('prepare-skills').addEventListener('click', () => action(window.acouagent.prepareSkills));
$('learning').addEventListener('click', () => action(window.acouagent.toggleLearning));
window.acouagent.onState(render);
action(window.acouagent.status);
