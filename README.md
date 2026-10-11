# AcouAgent · 声学仿真与成功技能库

自然语言声学仿真服务：Dify 提取参数和生成代码，FastMCP 网关调用 jwave 沙箱执行，SQLite 保存执行历史与可复用技能。技能检索发生在参数提取之后、代码生成之前；缺少合格技能或证据时回退原上下文。

## 当前可用范围

截至 2026-10-11，本机试用支持两项**精确固定配置**：

| 技能 | 配方范围 | 已验证行为 |
| --- | --- | --- |
| 平面波初压 | 二维均匀介质、高斯初压、固定网格与传感器 | 接纳、检索、参考交付、实际执行与撤回 |
| 法向界面反射/透射 | 两均匀半空间、宽横向域、固定材料与参数 | 完整解析波形检查、接纳、检索、一次实际复用 |

这些配置由服务端目录固定，修改频率、材料或网格不会继承原认证。普通运行只有判定为 `normal` 且通过物理合理性门才可积累为候选；退出码 0 或模型自述不能证明物理正确。候选不能自动成为可复用技能。

本机基础使用不要求用户配置签名私钥。受控网关留证与物理检查负责本机固定技能；跨主机独立来源认证是单独的高级路径。自动学习默认关闭。当前部署的 Dify 技能接线仅更新草稿，尚未发布，也未完成真实模型采用、通用/派生场景和 30 项独立配置召回验收。详见 [验收矩阵](docs/SKILL_LIBRARY_ACCEPTANCE.md)。

## 架构与安全

```text
浏览器 / Dify
    → FastMCP 网关 :8001
        → SQLite：执行历史 / 纠错缓存 / 技能与只追加审计
        → jwave executor :8010（内部隔离网络）
```

技能代码仍只经过原 executor：只读文件系统、降权、cap-drop、no-new-privileges、网络隔离与资源上限保持。生成参考每次重核精确参数、版本、来源、物理证据、撤回与一次消费；公开检索只读，最多返回 3 条元数据。

## 新环境部署

需要 Docker Compose、现有 Dify 网络与应用配置。先设置自己的服务认证信息：

```bash
cp .env.example .env
# 编辑 .env：设置 MCP_AUTH_TOKEN、EXECUTOR_SHARED_TOKEN，
# 按需设置 DIFY_API_KEY、DEEPSEEK_API_KEY 和 PORTAL_ADMIN_TOKEN。
docker compose up -d --build
docker compose ps
```

上述构建命令用于新环境。已有生产 executor 的更新应使用经过验证的镜像和 **gateway-only** 方式，不重建 executor。模板见 [部署说明](deploy/README.md)。本机镜像、生产库及凭据不随仓库分发。

默认不开启技能库。要试用人工准备的固定基础技能，在 `.env` 显式设置：

```dotenv
SKILL_LIBRARY_ENABLED=true
SKILL_RETRIEVAL_ENABLED=true
SKILL_INJECTION_ENABLED=true
SKILL_MANUAL_INTAKE_ENABLED=true
SKILL_LEARNING_ENABLED=false
```

重建或更新网关使配置生效后，执行以下显式操作。首次 `init` 仅创建缺失库，不覆盖或迁移既有库：

```bash
docker exec dify-mcp python -m scripts.skill_library.local_basic init
docker exec dify-mcp python -m scripts.skill_library.local_basic seed
docker exec dify-mcp python -m scripts.skill_library.local_basic status
docker exec dify-mcp python -m scripts.skill_library.local_basic verify-reuse
```

第二项固定界面技能使用相同命令加：

```bash
--profile managed-local-interface-wide.v1
```

`seed` 与 `verify-reuse` 会真正调用受限 executor；不调用模型。重复 seed 不重复执行已合格技能，撤回后不自动恢复。现有库升级须先只读备份，再显式调用迁移；详见 [本机基础技能](docs/SKILL_LOCAL_BASIC.md) 与 [参考执行关联](docs/SKILL_LOCAL_REUSE_2026-10-11.md)。

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
