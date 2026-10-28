'use strict';
const {spawn} = require('node:child_process');
const {EventEmitter} = require('node:events');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const {streamBackup, streamRestore} = require('./backup.cjs');

const PROJECT = 'acouagent-desktop';
const OWNER_LABEL = 'io.acouagent.desktop.install_id';
const DEFAULT_VOLUME = PROJECT + '_desktop_data';
const DATA_VOLUME = /^(?:acouagent-desktop_desktop_data|acouagent-desktop-data-[a-f0-9]{32})$/;
const IMAGE = /^ghcr\.io\/mwxrookie\/acouagent-(?:gateway|executor)@sha256:[a-f0-9]{64}$/;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function redact(text, secrets = []) {
  let safe = String(text);
  for (const secret of secrets) if (secret) safe = safe.split(secret).join('[REDACTED]');
  return safe.replace(/(Bearer\s+)[^\s"']+/gi, '$1[REDACTED]')
    .replace(/((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,"']+/gi, '$1[REDACTED]');
}

function command(executable, args, {env, timeout = 30000, progress} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {env, shell: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
    let output = '', errorOutput = '', settled = false;
    const finish = (error, value) => {if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(value);};
    const timer = setTimeout(() => {child.kill(); finish(Object.assign(new Error('operation_timed_out'), {code: 'TIMEOUT'}));}, timeout);
    child.stdout.on('data', chunk => {output = (output + chunk.toString('utf8')).slice(-2_000_000); progress?.(chunk.toString('utf8'));});
    child.stderr.on('data', chunk => {errorOutput = (errorOutput + chunk.toString('utf8')).slice(-256_000); progress?.(chunk.toString('utf8'));});
    child.on('error', error => finish(error));
    child.on('close', code => code === 0 ? finish(null, output.trim()) : finish(Object.assign(new Error([errorOutput.slice(-64000), output.slice(-64000)].filter(Boolean).join('\n') || 'docker_command_failed'), {code: 'DOCKER_FAILED'})));
  });
}

async function atomicWrite(filename, data) {
  await fs.mkdir(path.dirname(filename), {recursive: true});
  const temporary = filename + '.' + crypto.randomUUID() + '.tmp';
  try {await fs.writeFile(temporary, data, {mode: 0o600, flag: 'wx'}); await fs.rename(temporary, filename);}
  finally {await fs.unlink(temporary).catch(() => {});}
}

async function portAvailable(port) {
  return new Promise(resolve => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.listen({port, host: '127.0.0.1', exclusive: true}, () => server.close(() => resolve(true)));
  });
}

function parseRecords(text) {
  if (!text.trim()) return [];
  try {const data = JSON.parse(text); return Array.isArray(data) ? data : [data];}
  catch {return text.split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));}
}

function dockerCandidates(platform, env = process.env) {
  if (platform !== 'win32') return ['docker'];
  // An app already running during Docker installation retains its old PATH.
  // Retry only official installation locations, never a shell or downloaded command.
  return [...new Set(['docker.exe',
    env.LOCALAPPDATA && path.win32.join(env.LOCALAPPDATA, 'Programs', 'DockerDesktop', 'resources', 'bin', 'docker.exe'),
    env.ProgramFiles && path.win32.join(env.ProgramFiles, 'Docker', 'Docker', 'resources', 'bin', 'docker.exe')
  ].filter(Boolean))];
}

async function waitForGatewayHealth(url, {timeoutMs = 90000, intervalMs = 1000} = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url + '/health', {signal: AbortSignal.timeout(Math.max(1, Math.min(4000, deadline - Date.now())))});
      if (response.ok && (await response.text()).trim() === 'ok') return;
    } catch { /* A starting/recreated gateway may not be listening yet. */ }
    await pause(Math.max(0, Math.min(intervalMs, deadline - Date.now())));
  }
  throw Error('gateway_not_healthy');
}

function parseSettings(value) {
  if (!value || !Number.isInteger(value.port) || value.port < 1024 || value.port > 65535 || typeof value.extensions !== 'boolean'
    || (value.runtimeSha256 !== undefined && typeof value.runtimeSha256 !== 'string')
    || (value.dataVolume !== undefined && typeof value.dataVolume !== 'string')
    || (value.learning !== undefined && typeof value.learning !== 'boolean')
    || (value.learning === true && !value.extensions)
    || (value.extensions && !/^[a-f0-9]{64}$/.test(value.runtimeSha256 || ''))
    || !/^(?:[a-f0-9]{64})?$/.test(value.runtimeSha256 || '') || !DATA_VOLUME.test(value.dataVolume || DEFAULT_VOLUME)) throw Error('invalid_desktop_settings');
  return {port: value.port, extensions: value.extensions, learning: value.learning ?? value.extensions,
    runtimeSha256: value.runtimeSha256 || '', dataVolume: value.dataVolume || DEFAULT_VOLUME};
}

class Runtime extends EventEmitter {
  constructor({resourceDir, dataDir, vault, runner = command, platform = process.platform, importer = streamRestore, exporter = streamBackup, referenceDownloader}) {
    super(); Object.assign(this, {resourceDir, dataDir, vault, runner, platform, importer, exporter, referenceDownloader});
    this.state = {phase: 'idle', title: '准备本地仿真环境', detail: '首次启动将检查运行环境并准备经过验证的计算镜像。', ready: false, busy: false, logs: []};
    this.settings = {port: 18081, extensions: false, learning: false, runtimeSha256: '', dataVolume: DEFAULT_VOLUME};
    this.context = null; this.secrets = null;
  }
  snapshot() {return {...this.state, port: this.settings.port, extensions: this.settings.extensions, learning: this.settings.learning, logs: [...this.state.logs]};}
  change(phase, title, detail, ready = false) {Object.assign(this.state, {phase, title, detail, ready}); this.emit('state', this.snapshot());}
  log(text) {
    const safe = redact(text, Object.values(this.secrets || {})).trim().slice(-1500);
    if (!safe) return;
    this.state.logs.push(safe); this.state.logs = this.state.logs.slice(-80); this.emit('state', this.snapshot());
  }
  async initialize() {
    this.images = JSON.parse(await fs.readFile(path.join(this.resourceDir, 'images.json'), 'utf8'));
    if (!IMAGE.test(this.images.gateway) || !IMAGE.test(this.images.executor) || this.images.platform !== 'linux/amd64') throw Error('invalid_bundled_image_manifest');
    const settingsFile = path.join(this.dataDir, 'settings.json');
    try {
      const value = JSON.parse(await fs.readFile(settingsFile, 'utf8'));
      this.settings = parseSettings(value);
    } catch (error) {if (error.code !== 'ENOENT') throw error;}
    this.secrets = await this.vault.load();
    for (const key of ['MCP_AUTH_TOKEN', 'EXECUTOR_SHARED_TOKEN', 'PORTAL_ADMIN_TOKEN', 'ACOUAGENT_INSTALL_ID']) {
      if (typeof this.secrets[key] !== 'string' || !/^[a-f0-9]{64}$/.test(this.secrets[key])) throw Error('invalid_desktop_credentials');
    }
    try {
      const journal = JSON.parse(await fs.readFile(path.join(this.dataDir, 'restore-journal.json'), 'utf8'));
      if (journal.schema !== 1 || journal.installation !== this.installationHash()) throw Error('invalid_restore_journal');
      this.settings = parseSettings(journal.previous);
      await atomicWrite(settingsFile, JSON.stringify(this.settings));
      this.pendingRestoreRecovery = true;
      this.change('idle', '上次恢复未完成，已选择恢复前的数据', '点击检查并启动，重新核对原服务和基础技能。两份数据均已保留。');
    } catch (error) {if (error.code !== 'ENOENT') throw error;}
  }
  installationHash() {return crypto.createHash('sha256').update(this.secrets.ACOUAGENT_INSTALL_ID).digest('hex');}
  environment(extra = {}) {
    const env = {...process.env};
    // Docker invokes its credential helper by name. A newly installed Desktop
    // must work even when this app inherited PATH before installation.
    if (this.platform === 'win32' && this.dockerExecutable && path.win32.isAbsolute(this.dockerExecutable)) {
      const pathKey = Object.keys(env).find(key => key.toUpperCase() === 'PATH') || 'Path';
      const directory = path.win32.dirname(this.dockerExecutable);
      env[pathKey] = directory + (env[pathKey] ? ';' + env[pathKey] : '');
    }
    // Never inherit a caller's remote Docker host or Compose overrides.
    for (const key of Object.keys(env)) if (/^(DOCKER_|COMPOSE_|ACOUAGENT_|MCP_AUTH_TOKEN$|EXECUTOR_SHARED_TOKEN$|PORTAL_ADMIN_TOKEN$)/.test(key)) delete env[key];
    return {...env, ...this.secrets, ACOUAGENT_GATEWAY_IMAGE: this.images.gateway, ACOUAGENT_EXECUTOR_IMAGE: this.images.executor,
      ACOUAGENT_DESKTOP_PORT: String(this.settings.port), ACOUAGENT_DATA_VOLUME: this.settings.dataVolume,
      ACOUAGENT_SKILL_EXTENSIONS: String(this.settings.extensions), ACOUAGENT_SKILL_LEARNING: String(this.settings.extensions && this.settings.learning),
      ACOUAGENT_RUNTIME_SHA256: this.settings.runtimeSha256, ...extra};
  }
  async docker(args, {timeout = 30000, progress = false, context = true, extraEnv = {}} = {}) {
    const candidates = [...new Set([this.dockerExecutable, ...dockerCandidates(this.platform)].filter(Boolean))];
    let missing;
    for (const executable of candidates) {
      try {
        const result = await this.runner(executable, [...(context && this.context ? ['--context', this.context] : []), ...args],
          {env: this.environment(extraEnv), timeout, progress: progress ? text => this.log(text) : undefined});
        this.dockerExecutable = executable;
        return result;
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        missing = error;
      }
    }
    this.dockerExecutable = null;
    throw missing;
  }
  compose(args, options) {return this.docker(['compose', '--project-name', PROJECT, '--project-directory', this.resourceDir, '--env-file', path.join(this.resourceDir, 'defaults.env'), '--file', path.join(this.resourceDir, 'compose.desktop.yaml'), ...args], options);}
  async inspectEnvironment() {
    try {await this.docker(['--version'], {context: false});}
    catch (error) {if (error.code === 'ENOENT') throw Error('docker_not_installed'); throw error;}
    const names = (await this.docker(['context', 'ls', '--format', '{{.Name}}'], {context: false})).split(/\r?\n/);
    this.context = this.platform === 'win32' && names.includes('desktop-linux') ? 'desktop-linux' : 'default';
    const endpoint = JSON.parse(await this.docker(['context', 'inspect', this.context, '--format', '{{json .Endpoints.docker.Host}}'], {context: false}));
    const pipe = endpoint.replaceAll('\\', '/').replace(/^npipe:\/+/, '');
    const local = this.platform === 'win32' ? /^\.\/pipe\/(?:docker_engine|dockerDesktopLinuxEngine)$/i.test(pipe) : /^unix:\/\//.test(endpoint);
    if (!local) throw Error('remote_docker_context_refused');
    const os = JSON.parse(await this.docker(['info', '--format', '{{json .OSType}}']));
    if (os !== 'linux') throw Error('linux_containers_required');
    const arch = JSON.parse(await this.docker(['info', '--format', '{{json .Architecture}}']));
    if (!['x86_64', 'amd64'].includes(arch)) throw Error('x64_runtime_required');
    await this.docker(['compose', 'version']);
  }
  async assertOwnership(volumeName = this.settings.dataVolume, required = false) {
    const ids = (await this.docker(['ps', '-a', '--filter', 'label=com.docker.compose.project=' + PROJECT, '--format', '{{.ID}}'])).split(/\r?\n/).filter(Boolean);
    for (const id of ids) {
      if (!/^[a-f0-9]{12,64}$/.test(id)) throw Error('invalid_container_identity');
      const labels = JSON.parse(await this.docker(['inspect', id, '--format', '{{json .Config.Labels}}']));
      if (labels?.[OWNER_LABEL] !== this.secrets.ACOUAGENT_INSTALL_ID) throw Error('foreign_desktop_resources_refused');
    }
    let volume;
    try {volume = JSON.parse(await this.docker(['volume', 'inspect', volumeName, '--format', '{{json .Labels}}']));}
    catch (error) {if (!/no such volume|not found/i.test(error.message)) throw error;}
    if (volume && volume[OWNER_LABEL] !== this.secrets.ACOUAGENT_INSTALL_ID) throw Error('foreign_desktop_volume_refused');
    if (!volume && required) throw Error('restore_volume_missing');
  }
  async exclusive(fn) {
    if (this.state.busy) return this.snapshot();
    this.state.busy = true; this.emit('state', this.snapshot());
    try {await fn();}
    catch (error) {this.change('error', '环境准备未完成', this.friendlyError(error)); this.log(error.message);}
    finally {this.state.busy = false; this.emit('state', this.snapshot());}
    return this.snapshot();
  }
  friendlyError(error) {
    if (error.message === 'restore_volume_missing') return '未发现原数据卷。请先检查并启动创建基础环境，再选择备份。';
    if (/reference_preparation_failed|reference_digest|reference_downloader/.test(error.message)) return '核验资料下载或校验未通过。准备前备份已保留，自动学习未开启；请确认本机网络后重新检查并启动。';
    if (/general_preparation_failed|skill_preparation_failed/.test(error.message)) return '手动技能准备未通过。准备前备份已保留，自动学习未开启；请重新检查并启动，或导出诊断。';
    if (error.message === 'skill_feature_update_failed') return '技能设置更新未完成，已保留原设置和数据。请重新检查并启动，确认当前状态。';
    if (/backup_installation_mismatch/.test(error.message)) return '备份不属于本安装，已拒绝恢复。原数据仍保留。';
    if (/backup_runtime_mismatch/.test(error.message)) return '备份的计算版本与当前程序不一致，已拒绝恢复。请使用与备份匹配的版本。';
    if (error.message === 'restore_failed_original_preserved') return '恢复未完成，已保留并选择恢复前的数据。请重新检查并启动，或导出诊断。';
    const known = {docker_not_installed: '请先安装并启动 Docker Desktop，使用 Linux 容器模式；完成后回到这里重试。', linux_containers_required: '请将 Docker Desktop 切换到 Linux 容器模式。', remote_docker_context_refused: '当前Docker连接不是允许的本机运行时，已停止操作。', x64_runtime_required: '首版计算运行时需要 Linux x64，请使用对应平台。', foreign_desktop_resources_refused: '发现不属于本安装的同名服务，已停止操作并保留原数据。', foreign_desktop_volume_refused: '发现不属于本安装的同名数据卷，已停止操作并保留原数据。', no_free_port: '本机端口暂不可用，请关闭占用或稍后重试。', bootstrap_failed: '基础技能准备未通过；数据已保留，可重试并查看诊断。', invalid_desktop_settings: '本地配置格式异常；请保留数据并导出诊断。'};
    if (known[error.message]) return known[error.message];
    if (/backup_|restore_|snapshot|BadZipFile/.test(error.message)) return '数据操作未完成，备份或数据库校验未通过。原数据仍保留，请重新检查并启动或导出诊断。';
    return '请确认 Docker Desktop 已启动、网络可下载镜像，再重试。原有数据不会被删除。';
  }
  async start() {return this.exclusive(() => this.startCore());}
  async startCore() {
    this.change('checking', '检查计算运行时', '检测本机Docker、Linux容器和数据所有权。');
    await this.inspectEnvironment(); await this.assertOwnership(this.settings.dataVolume, !!this.pendingRestoreRecovery);
    const existing = parseRecords(await this.compose(['ps', '--all', '--format', 'json']));
    if (!existing.some(item => item.Service === 'gateway' && item.State === 'running')) {
      let available = false;
      for (const port of [this.settings.port, ...Array.from({length: 20}, (_, i) => 18081 + i)]) {
        if (await portAvailable(port)) {this.settings.port = port; available = true; break;}
      }
      if (!available) throw Error('no_free_port');
    }
    await atomicWrite(path.join(this.dataDir, 'settings.json'), JSON.stringify(this.settings));
    await this.compose(['config', '--quiet']);
    this.change('downloading', '准备经过验证的计算镜像', '首次可能需要较长时间；已有完整镜像会复用。');
    let missingImage = false;
    for (const image of [this.images.gateway, this.images.executor]) {
      try {await this.docker(['image', 'inspect', image, '--format', '{{.Id}}']);}
      catch (error) {if (!/no such image|not found/i.test(error.message)) throw error; missingImage = true;}
    }
    if (missingImage) await this.compose(['pull'], {timeout: 20 * 60 * 1000, progress: true});
    this.change('starting', '启动本地服务', '服务只绑定本机，数据保存在本应用独立数据卷。');
    await this.compose(['up', '-d'], {timeout: 180000, progress: true});
    this.change('bootstrap', '准备基础技能', '正在核对17项固定技能及物理验证，完成后才能进入工作台。');
    const deadline = Date.now() + 15 * 60 * 1000;
    let bootstrapDone = false;
    while (Date.now() < deadline) {
      const records = parseRecords(await this.compose(['ps', '--all', '--format', 'json']));
      const bootstrap = records.find(item => item.Service === 'skill-bootstrap');
      if (bootstrap?.State === 'exited') {if (Number(bootstrap.ExitCode) !== 0) throw Error('bootstrap_failed'); bootstrapDone = true; break;}
      await pause(1500);
    }
    if (!bootstrapDone) throw Error('bootstrap_failed');
    await waitForGatewayHealth(this.baseURL());
    const skills = JSON.parse(await this.compose(['exec', '-T', 'gateway', 'python', '-m', 'scripts.skill_library.local_basic', 'status-all']));
    if (!skills.ready || skills.ready_count !== 17) throw Error('bootstrap_failed');
    if (this.pendingRestoreRecovery) {
      await fs.unlink(path.join(this.dataDir, 'restore-journal.json'));
      this.pendingRestoreRecovery = false;
    }
    this.change('ready', '本地工作台已就绪', '17项基础技能已准备。进入工作台后连接你的模型，即可开始仿真。', true);
  }
  baseURL() {return 'http://127.0.0.1:' + this.settings.port;}
  async pauseWriters() {
    await this.compose(['stop', 'gateway', 'skill-bootstrap'], {timeout: 90000});
    const writers = parseRecords(await this.compose(['ps', '--all', '--format', 'json']));
    if (writers.some(item => ['gateway', 'skill-bootstrap'].includes(item.Service) && !['exited', 'created', 'dead'].includes(item.State))) throw Error('backup_writers_still_running');
  }
  async prepareExtensions() {return this.exclusive(async () => {
    if (!this.state.ready || this.settings.extensions) throw Error('skill_preparation_requires_basic_ready');
    const previous = {...this.settings};
    const backups = path.join(this.dataDir, 'backups');
    await fs.mkdir(backups, {recursive: true});
    await this.backupCore(path.join(backups, 'before-skill-extensions-' + Date.now() + '-' + crypto.randomUUID() + '.zip'));
    this.change('preparing-skills', '准备手动技能', '准备前备份已保存，正在核对参考资料和本机计算环境；准备不会开启自动学习。');
    if(typeof this.referenceDownloader!=='function')throw Error('reference_downloader_unavailable');
    const directory=await fs.mkdtemp(path.join(this.dataDir,'reference-prep-'));
    let prepared;
    try{
      try{await this.referenceDownloader(directory);}catch(error){this.log(error.message);throw Error('reference_preparation_failed');}
      const source=await fs.readFile(path.join(this.resourceDir,'prepare_skills.py'),'utf8');
      prepared=JSON.parse(await this.compose(['run','--rm','--no-deps','-T','--entrypoint','python','skill-preparation','-c',source],{timeout:300000,
        extraEnv:{ACOUAGENT_SKILL_EXTENSIONS:'true',ACOUAGENT_SKILL_LEARNING:'false',ACOUAGENT_REFERENCE_DIRECTORY:directory}}));
    }finally{
      const root=await fs.realpath(this.dataDir),resolved=await fs.realpath(directory);
      if(path.relative(root,resolved)!==path.basename(resolved)||!path.basename(resolved).startsWith('reference-prep-'))throw Error('reference_cleanup_refused');
      await fs.rm(resolved,{recursive:true,force:true});
    }
    const runtime = prepared.runtime_sha256;
    const expected = ['b01', 'b02', 'b03'].map(name => 'external-helmholtz-' + name + '.v1.' + runtime).sort();
    if (prepared.prepared !== true || prepared.schema !== 18 || !/^[a-f0-9]{64}$/.test(runtime || '')
      || prepared.skills_activated !== 0 || prepared.learning_enabled_changed !== false
      || JSON.stringify([...(prepared.profiles || [])].sort()) !== JSON.stringify(expected)
      || !['skill_store.db', 'execution_history.db'].every(name => prepared.backed_up?.includes(name))) throw Error('skill_preparation_failed');
    this.settings = {...previous, extensions: true, learning: false, runtimeSha256: runtime};
    await atomicWrite(path.join(this.dataDir, 'settings.json'), JSON.stringify(this.settings));
    try {
      await this.startCore();
      const status = JSON.parse(await this.compose(['exec', '-T', 'gateway', 'python', '-m', 'scripts.skill_library.authoring', 'status', '--database', '/app/data/skill_store.db']));
      if (status.authoring_schema_ready !== true || status.schema !== 18) throw Error('skill_preparation_failed');
    } catch (error) {await this.rollbackCapabilities(previous, error);}
    this.change('ready', '手动技能已准备，自动学习未开启', '重新进入工作台后可添加技能。准备前的完整备份可从恢复数据备份中选择。自动学习需另行开启。', true);
  });}
  async setLearning(enabled) {return this.exclusive(async () => {
    if (typeof enabled !== 'boolean' || !this.state.ready || !this.settings.extensions) throw Error('skill_extensions_required');
    await this.inspectEnvironment(); await this.assertOwnership();
    const previous = {...this.settings};
    this.change('preparing-skills', enabled ? '开启自动学习' : '关闭自动学习', '正在更新本应用服务，已有技能和数据保留；完成后重新进入工作台并连接模型。');
    await this.pauseWriters();
    this.settings = {...previous, learning: enabled};
    await atomicWrite(path.join(this.dataDir, 'settings.json'), JSON.stringify(this.settings));
    try {await this.startCore();}
    catch (error) {await this.rollbackCapabilities(previous, error);}
    this.change('ready', enabled ? '自动学习已开启' : '自动学习已关闭', enabled ? '仅通过独立质量验证的结果可以学习。手动技能仍可使用。' : '停止新增自动学习，手动技能和已有数据仍保留。', true);
  });}
  async rollbackCapabilities(previous, error) {
    this.log(error.message);
    this.settings = previous;
    await atomicWrite(path.join(this.dataDir, 'settings.json'), JSON.stringify(previous));
    try {await this.pauseWriters();await this.startCore();} catch (rollbackError) {this.log(rollbackError.message);}
    throw Error('skill_feature_update_failed');
  }
  maintenanceArgs(container, source, metadata, restore = false) {
    return ['--context', this.context, 'compose', '--project-name', PROJECT, '--project-directory', this.resourceDir,
      '--env-file', path.join(this.resourceDir, 'defaults.env'), '--file', path.join(this.resourceDir, 'compose.desktop.yaml'),
      'run', '--rm', '--no-deps', '-T', ...(restore ? ['--interactive'] : []), '--name', container, '--entrypoint', 'python', 'data-maintenance', '-c', source,
      '--metadata', JSON.stringify(metadata), ...(restore ? ['--restore'] : [])];
  }
  async cleanMaintenance(container) {
    let labels;
    try {labels = JSON.parse(await this.docker(['inspect', container, '--format', '{{json .Config.Labels}}']));}
    catch (error) {if (!/no such|not found/i.test(error.message)) throw error;}
    if (labels) {
      if (labels[OWNER_LABEL] !== this.secrets.ACOUAGENT_INSTALL_ID) throw Error('foreign_desktop_resources_refused');
      await this.docker(['rm', '-f', container]);
    }
  }
  async restore(archive) {return this.exclusive(async () => {
    if (!['ready', 'stopped', 'error'].includes(this.state.phase)) throw Error('backup_requires_initialized_data');
    await this.inspectEnvironment(); await this.assertOwnership(this.settings.dataVolume, true);
    this.change('restoring', '验证备份并准备新的数据卷', '核对来源、文件哈希、数据库完整性和版本；通过后才切换，原数据卷始终保留。');
    const candidate = PROJECT + '-data-' + crypto.randomUUID().replaceAll('-', '');
    const container = PROJECT + '-restore-' + crypto.randomUUID();
    const source = await fs.readFile(path.join(this.resourceDir, 'data_backup.py'), 'utf8');
    let imported;
    try {
      imported = await this.importer(this.dockerExecutable, this.maintenanceArgs(container, source,
        {installation: this.installationHash(), images: this.images}, true),
        {env: this.environment({ACOUAGENT_DATA_VOLUME: candidate}), source: archive});
    } finally {await this.cleanMaintenance(container);}
    if (!imported.restored) throw Error('backup_import_failed');
    await this.assertOwnership(candidate, true);
    const previous = {...this.settings};
    const next = parseSettings({port: previous.port, extensions: imported.settings.extensions, learning: imported.settings.learning,
      runtimeSha256: imported.settings.runtimeSha256, dataVolume: candidate});
    await this.pauseWriters();
    const journalPath = path.join(this.dataDir, 'restore-journal.json');
    const journal = {schema: 1, installation: this.installationHash(), previous, next};
    await atomicWrite(journalPath, JSON.stringify(journal));
    try {
      this.settings = next;
      await atomicWrite(path.join(this.dataDir, 'settings.json'), JSON.stringify(next));
      await this.startCore();
      await atomicWrite(path.join(this.dataDir, 'last-restore.json'), JSON.stringify({...journal, completedAt: new Date().toISOString()}));
      await fs.unlink(journalPath);
      this.change('ready', '数据已恢复并重新就绪', '备份已导入独立数据卷，17项基础技能重新核对通过。恢复前的数据卷仍保留。', true);
    } catch (error) {
      this.log(error.message);
      this.settings = previous;
      await atomicWrite(path.join(this.dataDir, 'settings.json'), JSON.stringify(previous));
      this.pendingRestoreRecovery = true;
      try {await this.startCore();} catch (rollbackError) {this.log(rollbackError.message);}
      throw Error('restore_failed_original_preserved');
    }
  });}
  async backup(destination) {return this.exclusive(() => this.backupCore(destination));}
  async backupCore(destination) {
    if (!['ready', 'stopped'].includes(this.state.phase)) throw Error('backup_requires_initialized_data');
    await this.inspectEnvironment(); await this.assertOwnership();
    this.change('backing-up', '备份本地数据', '暂停本应用写入服务后导出一致快照；完成后可重新启动工作台。');
    await this.pauseWriters();
    const source = await fs.readFile(path.join(this.resourceDir, 'data_backup.py'), 'utf8');
    const metadata = {createdAt: new Date().toISOString(), images: this.images, settings: this.settings,
      installation: this.installationHash()};
    const container = PROJECT + '-backup-' + crypto.randomUUID();
    const args = this.maintenanceArgs(container, source, metadata);
    try {
      await this.exporter(this.dockerExecutable, args, {env: this.environment(), destination});
    } finally {
      // A killed CLI can leave its temporary reader running in the engine.
      await this.cleanMaintenance(container);
    }
    this.change('stopped', '数据备份已保存', '备份包含技能、执行历史、缓存及结果文件。服务已暂停，点击检查并启动可继续使用。');
  }
  async stop() {return this.exclusive(async () => {
    this.change('stopping', '停止本应用服务', '停止本应用容器，保留数据卷和其他项目。');
    await this.inspectEnvironment(); await this.assertOwnership();
    await this.compose(['stop'], {timeout: 90000, progress: true});
    this.change('stopped', '本地服务已停止', '数据已保留，下次启动可继续使用。');
  });}
}
module.exports = {Runtime, command, redact, atomicWrite, parseRecords, parseSettings, dockerCandidates, waitForGatewayHealth, portAvailable, PROJECT, OWNER_LABEL};
