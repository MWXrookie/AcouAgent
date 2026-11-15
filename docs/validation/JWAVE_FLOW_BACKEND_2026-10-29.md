# jwave_flow 受限后端接入验证

日期：2026-10-29
> 2026-10-30 更新：模型所有权已扩展为 `MODEL_CONFIG_MODE=managed|self`；当前行为与
> 73 项回归证据见 [核心模型配置所有权验证](MODEL_CONFIG_OWNERSHIP_2026-10-30.md)。
> 本文其余内容保留为 2026-10-29 接入阶段的记录。

范围：只修改 `AcouAgent`；核心已迁移到本仓库，运行和 Docker 构建不依赖任何
外部项目路径。未修改或重启现有 `dify-mcp` 与 `jwave-executor` 生产服务。

现场 `dependency jwave-flow-core failed to start` 日志同时显示 bridge 因缺少
`JWAVE_FLOW_MODEL` / `JWAVE_FLOW_API_KEY` 退出。迁移后这些变量和
`JWAVE_FLOW_BRIDGE_TOKEN` 改为 Compose 必填插值，缺失时在启动前给出明确错误，
不再表现为含混的依赖失败。

## 目标与边界

- 新增 `ORCHESTRATION_BACKEND=jwave_flow`，实现 `CoreBackend.info()` 和
  `CoreBackend.events(ChatRequest)`，注册到 `WorkflowAdapter._backends`。
- 只保留 jwave_flow 的智能体工作流：需求分析、知识检索、参数预检、代码生成、
  静态检查、执行、语义门、需求完成度评审和纠错。
- 不复制普通 `jwave_frontend`、TUI、`history.py`、`memory.py`、
  `session_store.py`、`panels.py` 或任何知识库/模型管理入口。
- 模型、只读 KB 路径和 executor URL 只在 bridge 启动时由专用环境变量固定；
  请求只接受 `query`，没有运行中改模型、改知识库、历史、记忆或会话接口。
- 生成代码送本项目既有 `jwave-executor`，不启动 jwave_flow 的本地
  subprocess/jupyter/mcp executor。
- 适配器不声明 `model_profiles`、`byok`、`model_preflight` 或 `skill_usage`；
  门户传入 `model_profile` 时返回 `capability_not_supported` terminal result。

## 实现文件

- `app/adapters.py`：核心注册表，不承载具体核心实现。
- `cores/local/adapter.py`、`cores/dify/adapter.py`：本地与 Dify 核心适配器。
- `cores/jwave_flow/adapter.py`：`JwaveFlowCoreAdapter`。
- `cores/jwave_flow/client.py`：受限 bridge 健康校验、共享令牌、NDJSON 解析、
  大小/期限约束、错误脱敏与 `progress/result` 归一化。
- `cores/jwave_flow/bridge/server.py`：只有 `GET /health` 和 `POST /v1/run`，
  严格拒绝管理字段；将执行转发到本项目 executor。
- `cores/jwave_flow/workflow/`：迁移后的工作流节点；移除了 `.env`、环境选模和
  本地 executor 实现。
- 原 `auxiliary/reference/jwave-dify-langchain/` 参考副本已删除，避免与
  `cores/jwave_flow/` 形成重复核心。
- `deploy/jwave-flow-core.Dockerfile`：只从当前仓库复制 `cores/jwave_flow/` 的
  allowlist 工作流模块和只读 KB，不使用 external build context。
- `deploy/compose.jwave-flow.yaml`：可选隔离 bridge overlay，不向 bridge 注入
  不需要的 AcouAgent 环境变量。

## 验证

### Conda jw 定向回归

```text
49 passed in 0.32s
```

覆盖 Agent 合同、canonical API、旧接口兼容、Dify transport、jwave bridge
请求边界、健康能力拒绝、错误脱敏、模型 profile 拒绝和输入/输出状态隔离。

### Docker bridge

构建镜像：

```text
local/acouagent-jwave-flow-core:source built
```

镜像内容检查：

```text
cores/jwave_flow/workflow = __init__.py, config.py, executor.py, graph.py,
knowledge.py, llm.py, physics.py, prompts.py, semantic.py, state.py,
static_check.py
jwave_frontend = False
session_store.py = False
memory.py = False
history.py = False
graph-import = ok
```

临时 bridge 容器：

```text
GET /health = 200
service = jwave-flow-agent-bridge
knowledge_base_management = false
model_configuration = false
history = false
memory = false
sessions = false
GET /api/sessions = 404
```

临时网关容器通过 FastMCP 实际路由验证：

- `/api/v1/agent/info` 返回 `backend_id=jwave_flow`，四项模型/技能能力均为 false。
- 带 `model_profile` 的 chat 返回唯一的 terminal `result`，代码为
  `capability_not_supported`，未调用 bridge 工作流。
- bridge 上游模型故意不可达时，聊天只返回脱敏的 `jwave_flow_failed` terminal
  result；没有转发上游 traceback、模型 key 或管理数据。

### 未完成门

- 没有使用真实模型凭据跑完整科学题；本轮只验证接口、隔离和失败合同，不能据此
  宣称 jwave_flow 后端已完成科学矩阵验收。
- `tests/unit` 全量运行在当前环境长时间无输出后由人工中止，未把该次运行计入
  通过结果；上述 49 项为明确的定向回归证据。
- 当前环境没有 `archify`，架构 HTML/delivery/visual-check 未重跑；架构 JSON 与
  Mermaid 源已同步。
- 当前 `jw` 环境没有 `ruff`，因此以 Python 编译检查、pytest 和 `git diff --check`
  作为本轮静态检查证据。
- 未替换正式部署、未重启现有容器；bridge Dockerfile 不引用其他项目目录。
