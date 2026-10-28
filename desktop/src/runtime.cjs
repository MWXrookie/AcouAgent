'use strict';
const {spawn} = require('node:child_process');
const {EventEmitter} = require('node:events');
const fs = require('node:fs/promises');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');

const PROJECT = 'acouagent-desktop';
const OWNER_LABEL = 'io.acouagent.desktop.install_id';
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
    child.on('close', code => code === 0 ? finish(null, output.trim()) : finish(Object.assign(new Error(errorOutput || 'docker_command_failed'), {code: 'DOCKER_FAILED'})));
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

class Runtime extends EventEmitter {
  constructor({resourceDir, dataDir, vault, runner = command, platform = process.platform}) {
    super(); Object.assign(this, {resourceDir, dataDir, vault, runner, platform});
    this.state = {phase: 'idle', title: '准备本地仿真环境', detail: '首次启动将检查运行环境并准备经过验证的计算镜像。', ready: false, busy: false, logs: []};
    this.settings = {port: 18081, extensions: false, runtimeSha256: ''};
    this.context = null; this.secrets = null;
  }
  snapshot() {return {...this.state, port: this.settings.port, extensions: this.settings.extensions, logs: [...this.state.logs]};}
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
      if (!Number.isInteger(value.port) || value.port < 1024 || value.port > 65535 || typeof value.extensions !== 'boolean' || !/^(?:[a-f0-9]{64})?$/.test(value.runtimeSha256 || '')) throw Error('invalid_desktop_settings');
      this.settings = {port: value.port, extensions: value.extensions, runtimeSha256: value.runtimeSha256 || ''};
    } catch (error) {if (error.code !== 'ENOENT') throw error;}
    this.secrets = await this.vault.load();
    for (const key of ['MCP_AUTH_TOKEN', 'EXECUTOR_SHARED_TOKEN', 'PORTAL_ADMIN_TOKEN', 'ACOUAGENT_INSTALL_ID']) {
      if (typeof this.secrets[key] !== 'string' || !/^[a-f0-9]{64}$/.test(this.secrets[key])) throw Error('invalid_desktop_credentials');
    }
  }
  environment(extra = {}) {
    const env = {...process.env};
    // Never inherit a caller's remote Docker host or Compose overrides.
    for (const key of Object.keys(env)) if (/^(DOCKER_|COMPOSE_|ACOUAGENT_|MCP_AUTH_TOKEN$|EXECUTOR_SHARED_TOKEN$|PORTAL_ADMIN_TOKEN$)/.test(key)) delete env[key];
    return {...env, ...this.secrets, ACOUAGENT_GATEWAY_IMAGE: this.images.gateway, ACOUAGENT_EXECUTOR_IMAGE: this.images.executor,
      ACOUAGENT_DESKTOP_PORT: String(this.settings.port), ACOUAGENT_SKILL_EXTENSIONS: String(this.settings.extensions), ACOUAGENT_RUNTIME_SHA256: this.settings.runtimeSha256, ...extra};
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
  async assertOwnership() {
    const ids = (await this.docker(['ps', '-a', '--filter', 'label=com.docker.compose.project=' + PROJECT, '--format', '{{.ID}}'])).split(/\r?\n/).filter(Boolean);
    for (const id of ids) {
      if (!/^[a-f0-9]{12,64}$/.test(id)) throw Error('invalid_container_identity');
      const labels = JSON.parse(await this.docker(['inspect', id, '--format', '{{json .Config.Labels}}']));
      if (labels?.[OWNER_LABEL] !== this.secrets.ACOUAGENT_INSTALL_ID) throw Error('foreign_desktop_resources_refused');
    }
    let volume;
    try {volume = JSON.parse(await this.docker(['volume', 'inspect', PROJECT + '_desktop_data', '--format', '{{json .Labels}}']));}
    catch (error) {if (!/no such volume|not found/i.test(error.message)) throw error;}
    if (volume && volume[OWNER_LABEL] !== this.secrets.ACOUAGENT_INSTALL_ID) throw Error('foreign_desktop_volume_refused');
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
    const known = {docker_not_installed: '请先安装并启动 Docker Desktop，使用 Linux 容器模式；完成后回到这里重试。', linux_containers_required: '请将 Docker Desktop 切换到 Linux 容器模式。', remote_docker_context_refused: '当前Docker连接不是允许的本机运行时，已停止操作。', x64_runtime_required: '首版计算运行时需要 Linux x64，请使用对应平台。', foreign_desktop_resources_refused: '发现不属于本安装的同名服务，已停止操作并保留原数据。', foreign_desktop_volume_refused: '发现不属于本安装的同名数据卷，已停止操作并保留原数据。', no_free_port: '本机端口暂不可用，请关闭占用或稍后重试。', bootstrap_failed: '基础技能准备未通过；数据已保留，可重试并查看诊断。', invalid_desktop_settings: '本地配置格式异常；请保留数据并导出诊断。'};
    return known[error.message] || '请确认 Docker Desktop 已启动、网络可下载镜像，再重试。原有数据不会被删除。';
  }
  async start() {return this.exclusive(async () => {
    this.change('checking', '检查计算运行时', '检测本机Docker、Linux容器和数据所有权。');
    await this.inspectEnvironment(); await this.assertOwnership();
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
    const response = await fetch(this.baseURL() + '/health', {signal: AbortSignal.timeout(4000)});
    if (!response.ok || (await response.text()).trim() !== 'ok') throw Error('gateway_not_healthy');
    const skills = JSON.parse(await this.compose(['exec', '-T', 'gateway', 'python', '-m', 'scripts.skill_library.local_basic', 'status-all']));
    if (!skills.ready || skills.ready_count !== 17) throw Error('bootstrap_failed');
    this.change('ready', '本地工作台已就绪', '17项基础技能已准备。进入工作台后连接你的模型，即可开始仿真。', true);
  });}
  baseURL() {return 'http://127.0.0.1:' + this.settings.port;}
  async stop() {return this.exclusive(async () => {
    this.change('stopping', '停止本应用服务', '停止本应用容器，保留数据卷和其他项目。');
    await this.inspectEnvironment(); await this.assertOwnership();
    await this.compose(['stop'], {timeout: 90000, progress: true});
    this.change('stopped', '本地服务已停止', '数据已保留，下次启动可继续使用。');
  });}
}
module.exports = {Runtime, command, redact, atomicWrite, parseRecords, dockerCandidates, portAvailable, PROJECT, OWNER_LABEL};
