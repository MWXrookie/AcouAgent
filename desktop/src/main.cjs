'use strict';
const {app, BrowserWindow, protocol, net, ipcMain, safeStorage, dialog, shell, clipboard, Menu} = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const {pathToFileURL} = require('node:url');
const {Runtime} = require('./runtime.cjs');
const {Vault} = require('./vault.cjs');

protocol.registerSchemesAsPrivileged([{scheme: 'acouagent', privileges: {standard: true, secure: true, supportFetchAPI: true}}]);
const localData = process.env.LOCALAPPDATA || app.getPath('appData');
app.setPath('userData', app.commandLine.getSwitchValue('acouagent-data-dir') ? path.resolve(app.commandLine.getSwitchValue('acouagent-data-dir')) : path.join(localData, 'AcouAgent', 'Desktop'));
let manager, workbench, runtime, quitting = false;
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();
else {
  app.on('second-instance', () => {if (manager) {if (manager.isMinimized()) manager.restore(); manager.show(); manager.focus();}});
  app.whenReady().then(initialize).catch(async error => {await dialog.showMessageBox({type: 'error', title: 'AcouAgent', message: '桌面环境初始化失败', detail: String(error.message).replace(/[^a-z0-9_]/gi, ' ').slice(0, 140)}); app.quit();});
  app.on('window-all-closed', () => app.quit());
}

function hardened(win, allowed) {
  const documentation = new Set(['https://github.com/MWXrookie/AcouAgent/blob/main/docs/skills-next/ENABLE_SKILLS.md']);
  win.webContents.setWindowOpenHandler(({url}) => {if (documentation.has(url)) shell.openExternal(url); return {action: 'deny'};});
  win.webContents.on('will-navigate', (event, url) => {if (!allowed(url)) event.preventDefault();});
  win.webContents.on('will-redirect', (event, url) => {if (!allowed(url)) event.preventDefault();});
  win.webContents.on('will-attach-webview', event => event.preventDefault());
  win.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  win.webContents.session.setPermissionCheckHandler(() => false);
}

async function initialize() {
  await fs.mkdir(app.getPath('userData'), {recursive: true});
  const uiRoot = path.join(app.getAppPath(), 'ui');
  protocol.handle('acouagent', request => {
    const url = new URL(request.url);
    const names = new Set(['/index.html', '/manager.css', '/manager.js']);
    if (url.host !== 'desktop' || !names.has(url.pathname)) return new Response('Not found', {status: 404});
    return net.fetch(pathToFileURL(path.join(uiRoot, url.pathname.slice(1))).href);
  });
  runtime = new Runtime({resourceDir: app.isPackaged ? path.join(process.resourcesPath, 'runtime') : path.join(app.getAppPath(), 'runtime'), dataDir: app.getPath('userData'), vault: new Vault(app.getPath('userData'), safeStorage)});
  await runtime.initialize();
  manager = new BrowserWindow({width: 1080, height: 790, minWidth: 780, minHeight: 620, title: 'AcouAgent · 本地运行管理', backgroundColor: '#f5f7fb', show: false,
    webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, spellcheck: false}});
  hardened(manager, url => url === 'acouagent://desktop/index.html');
  runtime.on('state', state => {if (manager && !manager.isDestroyed()) manager.webContents.send('desktop:state', {...state, appVersion: app.getVersion()});});
  for (const [channel, fn] of Object.entries({
    status: () => ({...runtime.snapshot(), appVersion: app.getVersion()}),
    start: () => runtime.start(),
    stop: async () => {const result = await dialog.showMessageBox(manager, {type: 'warning', buttons: ['取消', '停止本应用服务'], defaultId: 0, cancelId: 0, message: '停止会中断尚未完成的任务。', detail: '只停止AcouAgent桌面版的服务，保留数据和其他Docker项目。'}); return result.response === 1 ? runtime.stop() : runtime.snapshot();},
    open: () => openWorkbench(),
    'docker-guide': () => shell.openExternal('https://docs.docker.com/desktop/setup/install/windows-install/'),
    'copy-admin': async () => {clipboard.writeText(runtime.secrets.PORTAL_ADMIN_TOKEN); return {copied: true};},
    diagnostics: () => exportDiagnostics(),
    backup: () => exportBackup()
  })) ipcMain.handle('desktop:' + channel, (event) => {
    if (event.sender !== manager.webContents || event.senderFrame !== manager.webContents.mainFrame || event.senderFrame.url !== 'acouagent://desktop/index.html') throw Error('untrusted_ipc_sender');
    return fn();
  });
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label: 'AcouAgent', submenu: [{label: '运行管理', click: () => manager.show()}, {label: '打开工作台', click: () => openWorkbench()}, {type: 'separator'}, {label: '退出', click: () => manager.close()}]},
    {label: '编辑', submenu: [{role: 'undo'}, {role: 'redo'}, {type: 'separator'}, {role: 'cut'}, {role: 'copy'}, {role: 'paste'}, {role: 'selectAll'}]},
    {label: '显示', submenu: [{role: 'resetZoom'}, {role: 'zoomIn'}, {role: 'zoomOut'}, {role: 'togglefullscreen'}]}
  ]));
  manager.on('close', event => {if (!quitting) {event.preventDefault(); closeApplication();}});
  manager.once('ready-to-show', () => manager.show());
  await manager.loadURL('acouagent://desktop/index.html');
}

async function openWorkbench() {
  if (!runtime.state.ready) {manager.show(); return {opened: false};}
  if (workbench && !workbench.isDestroyed()) {workbench.show(); workbench.focus(); return {opened: true};}
  const origin = runtime.baseURL();
  workbench = new BrowserWindow({width: 1440, height: 960, minWidth: 780, minHeight: 620, title: 'AcouAgent · 声学仿真', backgroundColor: '#f5f7fb',
    webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, spellcheck: false, partition: 'persist:acouagent-workbench'}});
  hardened(workbench, url => {try {return new URL(url).origin === origin;} catch {return false;}});
  await workbench.loadURL(origin + '/portal');
  return {opened: true};
}

async function exportDiagnostics() {
  const chosen = await dialog.showSaveDialog(manager, {title: '导出脱敏诊断', defaultPath: 'AcouAgent-diagnostics.json', filters: [{name: 'JSON', extensions: ['json']}]});
  if (chosen.canceled) return {saved: false};
  const report = {schema: 1, appVersion: app.getVersion(), createdAt: new Date().toISOString(), platform: process.platform, architecture: process.arch,
    state: runtime.snapshot(), images: runtime.images, notice: '不包含API Key、内部令牌、仿真输入或数据库。'};
  await fs.writeFile(chosen.filePath, JSON.stringify(report, null, 2), {mode: 0o600});
  return {saved: true};
}

async function exportBackup() {
  if (runtime.state.busy) return runtime.snapshot();
  const confirmation = await dialog.showMessageBox(manager, {type: 'question', buttons: ['取消', '选择备份位置'], defaultId: 0, cancelId: 0,
    message: '备份会暂停本应用的仿真服务。', detail: '请先等待任务完成。备份包含技能、执行历史、缓存和结果，可能含私人仿真内容，请妥善保存；不包含内部令牌和模型密钥。完成后可重新启动。'});
  if (confirmation.response !== 1) return runtime.snapshot();
  const chosen = await dialog.showSaveDialog(manager, {title: '保存数据备份', defaultPath: 'AcouAgent-data-' + new Date().toISOString().slice(0,10) + '.zip', filters: [{name: 'AcouAgent数据备份', extensions: ['zip']}]});
  if (chosen.canceled) return runtime.snapshot();
  return runtime.backup(chosen.filePath);
}

async function closeApplication() {
  if (runtime.state.busy) {await dialog.showMessageBox(manager, {message: '正在准备或停止服务，请等待当前操作完成后退出。'}); return;}
  const running = runtime.state.ready;
  const choice = await dialog.showMessageBox(manager, {type: 'question', buttons: running ? ['取消', '停止服务并退出', '保留后台服务并退出'] : ['取消', '退出'], defaultId: 0, cancelId: 0,
    message: running ? '如何退出AcouAgent？' : '退出AcouAgent？', detail: running ? '停止服务可能中断未完成任务。两种方式均保留数据。' : '已有数据会保留。'});
  if (choice.response === 0) return;
  if (running && choice.response === 1) {await runtime.stop(); if (runtime.state.phase === 'error') return;}
  quitting = true; if (workbench && !workbench.isDestroyed()) workbench.destroy(); manager.destroy(); app.quit();
}
