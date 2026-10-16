# AcouAgent · 声学仿真与成功技能库

> 当前实现、开放发布门和文档优先级统一见
> [当前状态](docs/CURRENT_STATUS.md)。带日期的旧计划和验收记录用于追溯，
> 不覆盖该状态页。

> **2026-10-15 BYOK 运行时**：发行示例默认 `local`，自然语言聊天可以脱离 Dify 完成参数提取、技能检索、代码生成、原 executor 执行、物理门、审查解释和最多两次修正。下载者可在门户临时接入自己的 OpenAI-compatible 模型，也可用环境变量配置可复用 profile；模型密钥不进入浏览器持久存储、SQLite、日志或技能库。

> **2026-10-13 阶段五自动学习闭环已通过**：主执行与重试路径统一经过质量门；精确固定配方可确定性接纳为 `Q4 active`，普通成功运行最高只形成不可复用的 `Q2 candidate`；真实 Dify 学习、候选隔离、run-bound 幂等负反馈和 Luna 772 项回归均通过。当前生产已显式开启学习，executor 未变。详见 [阶段五验收](docs/validation/stage5/README.md)。

自然语言声学仿真服务：服务内本地编排器或可选 Dify 兼容工作流负责参数提取和代码生成，FastMCP 网关调用 jwave 沙箱执行，SQLite 保存执行历史、模型调用审计与可复用技能。技能检索发生在参数提取之后、代码生成之前；缺少合格技能或证据时回退原上下文。

## 当前可用范围

截至 2026-10-12，本机固定技能目录支持 17 项**精确配置**：

| 技能 | 配方范围 | 已验证行为 |
| --- | --- | --- |
| 平面波初压 | 二维均匀介质、高斯初压、固定网格与传感器 | 接纳、检索、参考交付、实际执行与撤回 |
| 法向界面反射/透射 | 两均匀半空间、宽横向域、固定材料与参数 | 完整解析波形检查、接纳、检索、一次实际复用 |
| 周期模态（15 项） | 水、空气、软组织无损参数 × 轴向/斜向/正交/谐波/拍频 | 3 个完整探针与 3 幅完整场解析比对、精确参数接纳与撤回回归 |

这些配置由服务端目录固定，修改频率、材料或网格不会继承原认证。普通运行只有判定为 `normal` 且通过物理合理性门才可积累为候选；退出码 0 或模型自述不能证明物理正确。候选不能自动成为可复用技能。

本机基础使用不要求用户配置签名私钥。受控网关留证与物理检查负责本机固定技能；跨主机独立来源认证是单独的高级路径。仓库发行示例继续默认关闭自动学习，当前生产环境已在阶段五验收后显式开启。固定目录的精确合格结果可进入 `Q4 active`；普通成功运行最高写入 `Q2 candidate`，不能参与检索或注入。本地编排已统一使用 OpenAI-compatible BYOK profile；剩余产品化门包括第二模型固定集验收、Dify/本地同题双跑和全新机器自然语言首题终验。详见 [验收矩阵](docs/SKILL_LIBRARY_ACCEPTANCE.md) 与 [已知缺口](docs/KNOWN_GAPS.md)。

## 阶段一验收基线

阶段一已确认完成：17 项清单、30 道冻结题（15 独立正向配置、5 同义、5 越界、5 缺参），独立 Luna 验证 68 项相关测试通过。完整场和探针误差须各自严格小于 1%，负例不交付固定参考。后续 180 次 A/B 仅为计划，实际模型采用和收益尚未验证。公开清单及可重复检查见 [阶段一基线](docs/validation/stage1/README.md)。下载仓库不会继承生产数据库、用户反馈或运行历史；阶段六冷启动会在新机器重新执行固定配方并建立本机 Q4 记录。

## 架构与安全

```text
浏览器 → Web 门户 / 本地编排器 → 用户 BYOK / 环境变量模型 profile
                         ↓
                 FastMCP 共享校验与执行入口 :8001
                         → SQLite：执行 / 分析 / 模型 / 技能审计
                         → jwave executor :8010（内部隔离网络）

可选 Dify 兼容工作流 ───────────────→ FastMCP MCP 工具
```

技能代码仍只经过原 executor：只读文件系统、降权、cap-drop、no-new-privileges、网络隔离与资源上限保持。生成参考每次重核精确参数、版本、来源、物理证据、撤回与一次消费；公开检索只读，最多返回 3 条元数据。

## 新环境部署

基础栈只需要 Docker Compose，不要求已有 Dify：

Linux、macOS 或 WSL：

```bash
bash scripts/install.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

安装前或运行异常时，如主机已有 Python 3.11，可执行脱敏 Doctor。Doctor
不输出 token、模型密钥、endpoint 或模型响应正文：

```bash
python3 -m scripts.diagnostics.acouagent_doctor --mode auto
python3 -m scripts.diagnostics.acouagent_doctor --mode auto --json
```

Windows 可将 `python3` 换成 `py -3.11` 或实际的 Python 3.11 命令。

Doctor 的 `prepare` 模式检查主机、`.env`、Docker、Compose 和端口；
`runtime` 模式还检查网关、模型 profile 接口、17 项技能、SQLite 审计、
在线备份入口、源码身份及 executor 隔离。它是诊断工具，安装器仍只要求
Docker Compose。

安装器仅在 `.env` 不存在时创建它并生成两个不同的本地 token；已有 `.env` 不会被覆盖。未配置环境变量模型时，健康检查、技能库和固定技能自举仍可用；启动后可直接在门户“模型设置”中填写自己的 API 地址、模型名和密钥。需要先编辑配置时使用 `--prepare-only` 或 PowerShell 的 `-PrepareOnly`。

对应的手工命令为：

```bash
cp .env.example .env
# 为 MCP_AUTH_TOKEN 和 EXECUTOR_SHARED_TOKEN 设置两个不同的 32+ 字符随机值
# 可选：设置 MODEL_BASE_URL、MODEL_NAME、MODEL_API_KEY；也可启动后在门户使用 BYOK
docker compose pull jwave-executor
docker compose up -d --build
docker compose ps -a
```

多 profile、分任务参数、内部模型网关和失败语义见 [模型 profile 配置](docs/MODEL_PROFILES.md)。
发布前的第二模型固定题与本地/Dify 同题对照见
[阶段七模型矩阵](docs/validation/stage7/MODEL_MATRIX.md)；运行器不会保存提示词、
模型响应、生成代码、求解器输出或凭据。
新操作者从下载到保存首个 `run_id` 的完整步骤见 [30 分钟首次仿真](docs/FIRST_SIMULATION.md)。

首次启动的一次性 `skill-bootstrap` 会逐项执行 17 个服务端固定配方；只有通过原 executor 回执与确定性物理门的结果才进入 Q4。正常完成后它显示 `Exited (0)`：

```bash
docker compose logs skill-bootstrap
docker exec dify-mcp python -m scripts.skill_library.local_basic status-all
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/skills
```

生成脱敏的技能库存、版本、质量、反馈、来源摘要和一致性报告：

```bash
docker exec dify-mcp python -m scripts.skill_library.audit_report
docker cp dify-mcp:/app/data/audit/skill-library-audit.json .
docker cp dify-mcp:/app/data/audit/skill-library-audit.md .
docker exec dify-mcp python -m scripts.skill_library.audit_report \
  --verify /app/data/audit/skill-library-audit.json
```

报告用哈希后的来源 run id 和代码哈希，不导出代码、物理证据正文、反馈理由或凭据；`healthy=true` 只证明库存与审计结构一致，不证明任意场景物理正确。

自举幂等，不复制生产数据库，不恢复已撤回记录；自动学习仍默认关闭。源码构建回退和已有生产环境升级见 [部署说明](deploy/README.md)。

已有克隆的 `.env` 不会被 `git pull` 覆盖。若升级后技能页仍为空，请按 [部署说明](deploy/README.md#已有克隆升级) 开启固定技能开关并显式运行一次自举。

若需要继续使用既有 Dify 工作流，可启用兼容覆盖：

```bash
docker compose -f compose.yaml -f deploy/compose.dify.yaml up -d --build
```

通过 `DIFY_NETWORK_NAME` 与 `DIFY_BASE_URL` 指向现有 Dify，并把 `ORCHESTRATION_BACKEND=dify`。此模式中门户 profile 只负责多轮需求合并，Dify 内部模型仍由 Dify 配置。

## 页面与工具

默认门户：`http://127.0.0.1:8001/portal`（绑定地址按 `DIFY_MCP_BIND_IP` 配置）。

| 路由 | 功能 |
| --- | --- |
| `/`、`/portal`、`/chat` | 自然语言多轮仿真，侧栏包含技能库入口 |
| `/skills` | 技能列表、适用参数、版本、证据与反馈 |
| `/dashboard` | 执行历史、代码与报告 |
| `/cache` | 纠错经验缓存 |
| `/health` | 网关存活检查 |

网关注册 9 个 MCP 工具：

- 执行：`run_jwave_code`、`run_jwave_code_with_retry`
- 环境：`jwave_environment`、`list_installed_libraries`
- 校验/分析：`validate_simulation_params`、`analyze_simulation_result`
- 技能：`search_simulation_skill`、`build_simulation_generation_context`、`read_simulation_generation_context`

MCP 地址为 `http://<gateway>:8001/mcp`，使用 `Authorization: Bearer <MCP_AUTH_TOKEN>`；HTTP 客户端须接受 `application/json, text/event-stream`。管理员反馈需要 `PORTAL_ADMIN_TOKEN`，令牌不写入浏览器持久存储。构建/读取上下文工具不接受调用者代码、可信布尔或自行编造的证明。

## Dify 接线

首次和重试分支分别绑定自己的参数：

```text
参数提取 → 参数适配 → 只读技能检索
         → 服务端生成决定 → 提取编号 → 服务端重新验证
         → 参考格式归一 → 代码生成
```

关闭开关、检索失败、缺少认证或参考撤回都回退原上下文。JSON 格式检查不是授权器；Dify 工具响应使用实际安装版本的 JSON 数组输出契约。离线图工具在 `scripts/workflow/`，部署步骤见 [接线说明](docs/SKILL_STAGE1_WIRING.md)。导出图不能替代完整可导入 DSL，更新草稿不等于发布。

## 存储与目录

SQLite 数据卷持久化以下文件：`execution_history.db`、`error_cache.db`、`skill_store.db`。技能库当前 schema17，迁移原子执行，审计和执行关联只追加。

| 目录 | 内容 |
| --- | --- |
| `app/` | 网关、物理门、技能接纳/检索/执行关联 |
| `executor/` | 固定 jwave 0.2.1 沙箱 |
| `scripts/validation/` | 运行时物理验证、固定配方与来源契约 |
| `scripts/skill_library/` | 人工基础技能准备与复用 CLI |
| `scripts/workflow/` | 可重复使用的离线工作流工具 |
| `scripts/archive/` | 历史一次性工作流补丁，保留供追溯 |
| `host_observer/` | 可选跨主机观察与签名辅助 |
| `tests/` | 隔离单测、集成测试及固定测试夹具 |
| `docs/` | 当前计划、验收、历史交接与架构事实 |

运行日志、生产快照、临时数据库、密钥、本机镜像和本机部署覆盖文件保留在操作者环境，不提交。清理规则见 [脚本说明](scripts/README.md)。

## 验证与开发

`scripts/tests/run_unit_tests.sh` 可构建独立测试镜像；批量 Dify 测试会使用模型并产生费用，运行前确认范围。技能验证应使用临时 SQLite，不写生产库；固定配方检查不允许放宽 1% 误差或删除失败样本。

最近技能执行关联相关回归 343 项通过；不是全库或最终产品验收。此次 GitHub 整理对 87 个单测文件完整分批验证：1334 项及 10 个子测试通过，依赖警告按批记录；见 CHANGELOG。原窄界面算例的完整波形失败记录保留，加宽算例是独立新配置。

项目借鉴技能库/检索/反馈学习方向，结合事务、来源留证、声学解析基准及 jwave 源码进行工程实现，不宣称原创求解算法，不引入 Open Interpreter 或向量数据库。开发约束见 [AGENTS](AGENTS.md)、[提交规范](docs/COMMIT_CONVENTION.md)。
