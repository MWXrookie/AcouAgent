# 队友从 main 安装与更新

2026-10-26：PR37已合入main，模型兼容版已完成正式/演示部署、升级回退及真实浏览器复验。当前是`0.1.0`测试版，可按下列步骤使用；验收范围见[交付记录](validation/stage7/PRODUCTION_MODEL_COMPAT_2026-10-26.md)。

这份说明适用于本地自部署。需要 Git、已启动的 Docker Compose，以及自己的
OpenAI-compatible 模型 API 地址、模型名和 API Key。Windows 使用 Docker Desktop
的 Linux 容器模式；公开科学计算镜像当前面向 linux/amd64。无需部署 Dify。

## 第一次使用：Windows

打开 PowerShell，依次运行：

```powershell
git clone https://github.com/MWXrookie/AcouAgent.git
Set-Location AcouAgent
git switch main
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

Linux / WSL（amd64）在仓库目录运行 `bash scripts/install.sh`。安装器会自动生成
两个独立随机 token，拉取 executor、从当前源码构建网关，并等待空数据卷上的
17 项固定技能执行、自举和物理门完成。首次需要联网下载镜像与依赖，可能耗时数分钟；
看到 `AcouAgent is ready` 后再打开门户。不要把 `skill-bootstrap Exited (0)` 当作故障。

打开 <http://127.0.0.1:8001/portal>，在“模型设置”填写自己的 API 地址、模型名和
API Key，点击“测试连接”。临时 BYOK 默认 12 小时过期，网关重启后需要重新填写。
需要长期配置时按 [模型配置](MODEL_PROFILES.md) 设置本地 `.env`，不要提交密钥。

## 已经安装过：更新到 main

先检查未提交改动；有改动时自行保存后再切换分支，不要使用强制覆盖。
有需要保留的业务数据时，先按 [部署说明](../deploy/README.md) 的要求备份持久库。

```powershell
git status --short
git remote set-url origin https://github.com/MWXrookie/AcouAgent.git
git switch main
git pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

Linux/WSL 最后一行改为 `bash scripts/install.sh`。`git pull` 本身不会更新正在运行的
容器；安装器执行构建和启动后才运行新代码。已有 `.env` 和持久数据卷继续保留。
已知旧发行 executor 默认值会先备份再迁移；自定义镜像不被覆盖。如果曾经关闭技能开关，
按 [已有克隆升级](../deploy/README.md#已有克隆升级) 恢复开关并显式自举。
不要运行 `docker compose down -v`，它会删除本地数据卷。

## 开始使用前核对

```powershell
docker compose ps -a
docker exec dify-mcp python -m scripts.skill_library.local_basic status-all
```

应看到网关和 executor 健康，技能状态 `ready=true`、`ready_count=17`。
浏览器依次打开 `/portal`、`/skills`、`/cache`、`/dashboard`；这是本机独立数据，
不会包含其他人的生产执行历史和纠错缓存。

填写模型后按 [第一次自然语言仿真](FIRST_SIMULATION.md) 跑首题并保存 `run_id`，
核对报告、执行状态和看板记录。固定技能成功自举不等于任意自然语言问题都已通过。
模型请求会使用操作者自己的配额。

安装失败时查看 `docker compose logs skill-bootstrap` 和 `docker compose logs dify-mcp`，
或运行 `python -m scripts.diagnostics.acouagent_doctor --mode auto --json`。
未启动 Docker、镜像下载网络失败、8001 端口被占用，以及模型鉴权失败分别排查；
不要公开粘贴 `.env`、完整日志或凭据。当前开放发布门仍以 [当前状态](CURRENT_STATUS.md) 为准。

## 仓库已更名为 AcouAgent

自动学习和用户手动添加属于可选扩展，默认关闭。基础安装完成后，按 [启用指南](skills-next/ENABLE_SKILLS.md) 备份并准备三项独立参考；无需自行搭建签名服务或向量数据库。只更新代码不会自动迁移扩展数据或开启学习。

GitHub 地址为 `https://github.com/MWXrookie/AcouAgent`。已有克隆可继续在原目录更新，
并按上方命令修改 origin；不要为了改目录名而移动或重建持久数据卷。
镜像、Docker 服务名、容器名及内部兼容标识继续使用原值，避免影响已有部署。
