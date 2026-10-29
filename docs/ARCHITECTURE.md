# 技术方案与架构设计

> ⚠️ **2026-10-29**：本文档中的早期阶段图、旧节点数和旧前端描述仅作历史参考；当前接口、运行时和验收边界以 [Agent 后端接口](API_BACKEND.md)、[前端接入契约](API_FRONTEND.md)、[当前状态](CURRENT_STATUS.md) 与架构 JSON/HTML 为准。

> **2026-10-29 当前接口运行时**：静态 `chat.html` 通过 `agent-client.js` 只依赖 `/api/v1/agent/*`；后端以 `CoreBackend` + `RuntimeBus` 选择本地或 Dify，模型管理能力由 `/api/v1/agent/info` 的能力矩阵声明。知识库、技能库、看板和 executor 的既有维护边界保持不变。旧 `/chat`、`/api/v1/chat` 和模型接口作为渐进弃用别名保留。当前架构事实图见 `diagrams/acouagent-runtime.architecture.html`，完整工作流见 `diagrams/acouagent-workflow-flow.mmd`。下文 DeepSeek、Dify 节点和旧 Gradio 段落仅作历史参考。

> 版本 v1.0 | 2026-08-08 | 关联: AGENTS.md, PRD.md

---

## 目录

1. [架构总览](#1-架构总览)
2. [数据流](#2-数据流)
3. [组件设计](#3-组件设计)
4. [接口规范](#4-接口规范)
5. [数据模型](#5-数据模型)
6. [安全模型](#6-安全模型)
7. [部署方案](#7-部署方案)
8. [演进路线](#8-演进路线)

---

## 1. 架构总览

### 1.1 系统架构图

当前运行时的主边界：

```text
用户 / 独立前端
        │  meta: /api/v1/agent
        ▼
静态 Chat UI (chat.html + chat.js)
        │  agent-client.js
        ▼
Agent API v1 (/api/v1/agent/*)
        │  ChatRequest / WorkflowEvent / capability envelope
        ▼
RuntimeBus ───── capability gate
        │
        ├── LocalCoreAdapter ── 模型 profile / BYOK / 预检
        │       ├── 参数提取 → problem_kind
        │       ├── 共享参数合同 / 技能与知识库检索
        │       ├── 代码生成 / 缓存纠错
        │       ├── jwave executor（隔离边界）
        │       └── 分析 / 确定性物理门 / 审查解释
        └── DifyCoreAdapter ── Dify workflow SSE → 事件归一化

旧接口兼容层：/chat、/api/v1/chat、/api/model-profiles、/api/byok/session
        └── 旧响应形状 + Deprecation: true + successor Link

独立维护旁路（本轮不重构）：/skills/api/v1、/dashboard/api/*
```

`Agent API v1` 的请求、事件、错误和能力矩阵见 [后端接口文档](API_BACKEND.md)；前端替换清单见 [前端接口文档](API_FRONTEND.md)。图中执行器仍只接受原有只读、降权、无外网和资源限制。

### 1.2 网络拓扑

```
                    ┌────────────────────────────┐
                    │      宿主机                 │
                    │  192.168.30.200             │
                    └────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ docker_default │         │ executor_int. │         │ docker_sandbox│
│ (external)     │         │ (internal)    │         │ (internal)    │
│               │         │               │         │               │
│ • nginx :80   │         │ • dify-mcp    │         │ • sandbox     │
│ • api :5001   │◄────────│   :8001       │────────►│   :5004       │
│ • worker      │         │               │         │               │
│ • postgres    │         │ • jwave-exec  │         │               │
│ • weaviate    │         │   :8010       │         │               │
│ • redis       │         │               │         │               │
└──────┬────────┘         └───────────────┘         └───────────────┘
       │
       ▼
  外部网络 (DeepSeek API)
```

**关键隔离**：
- jwave-executor **仅连** `executor_internal`（`internal: true`），不能访问外网和其他容器
- dify-mcp **双网卡**：连 `docker_default`（访问 Dify）和 `executor_internal`（访问 executor）
- dify-mcp 的 **read_only: true** + tmpfs /tmp，不可写文件系统

---

## 2. 数据流

### 2.1 一次完整仿真的数据流

```
时间线 →

T0: 用户输入 "仿真 5MHz 超声在含 3mm 囊肿的软组织中传播"
  │
T1: [需求分析] LLM (deepseek-v4-flash, temp 0.7)
  │  输入: user_query
  │  输出: 结构化需求描述 (str)
  │
T2: [知识检索] top_k=4 + qwen3-rerank
  │  输入: user_query + 需求描述
  │  输出: 4 个文档片段 (list[Document])
  │
T3: [模板转换]
  │  输入: 检索结果
  │  输出: 拼接的文本 (str)
  │
T4: [参数提取] LLM (deepseek-v4-flash, temp 0.3)
  │  输入: 用户需求 + 知识库文本
  │  输出: JSON {sound_speed, density, frequency, Nx, Ny, dx, ...}
  │
T5: [参数校验] MCP validate_simulation_params ← NEW (P1)
  │  输入: 参数 JSON
  │  输出: {valid: true/false, errors: [...], warnings: [...]}
  │  失败 → 回到 T4 + 修正建议
  │
T6: [代码生成] LLM (deepseek-coder, temp 0.1)
  │  输入: 参数 JSON + 知识库 API 参考
  │  输出: Python 代码 (纯文本, 无 markdown)
  │
T7: [沙箱执行] MCP run_jwave_code_with_retry
  │  输入: code, timeout=15s, max_retries=7
  │  流程:
  │    a. _clean_code(code) — 去除可能的 markdown 包裹
  │    b. httpx POST → jwave-executor:8010/execute
  │    c. executor: subprocess.Popen("/usr/local/bin/python -I main.py")
  │    d. 成功 → 返回 {exit_code:0, stdout, stderr, duration_ms}
  │    e. 失败 → _llm_fix_code() 调用 DeepSeek 纠错 → retry (≤7次)
  │  输出: {final_code, history: [{attempt, exit_code, ...}], stdout, stderr}
  │
T8: [结果分析] MCP analyze_simulation_result ← NEW (P1)
  │  输入: stdout, stderr, exit_code, params
  │  流程:
  │    a. 解析 stdout 中的压力场数据
  │    b. 计算 max_pressure, rms
  │    c. matplotlib 生成热力图 PNG → base64
  │    d. 合理性检查 (全零? 量级对?)
  │  输出: {has_signal, max_pressure, heatmap_base64, verdict, summary}
  │
T9: [审查决策] LLM (deepseek-chat, temp 0.2)
  │  输入: 用户需求 + 分析结果
  │  输出: JSON {verdict: "pass"|"retry"|"fail", suggestion/error}
  │  retry → 回到 T4 (iter < 3)   fail → 进入 T10 (错误解释)
  │
T10: [结果解释] LLM (deepseek-chat, temp 0.5) ← NEW (P1)
  │   输入: 用户需求 + 参数 + 分析摘要 + 审查结论
  │   输出: 自然语言解读 (含关键数值 + 物理意义 + 图片)
  │
T11: 返回用户 (聊天消息, 含图片)
  │
  │  更新 conversation variable: last_params, last_summary
  │  写入 SQLite: record_execution(...)
```

### 2.2 多轮对话数据流

```
第 1 轮: 用户 "5MHz 超声在软组织中"
  → 完整流程 T0-T11
  → 存入 conversation.last_params
  → 存入 conversation.last_summary

第 2 轮: 用户 "换成 3MHz"
  → T4 参数提取时注入: "上次参数: {last_params}。只修改 frequency=3e6，其余不变"
  → T4 输出: {..., frequency: 3e6, sound_speed: 1540, ...} (只改 frequency)
  → T5-T11 正常流程
```

---

## 3. 组件设计

### 3.1 MCP Gateway（薄壳 `app/server_safe.py` + 业务模块，2026-08-18 模块化拆分）

```
模块层次 (`app/server_safe.py` 为入口薄壳，装配 + 注册):

app/
├── server_safe.py   FastMCP("Dify JWave Tools") + auth + register_tools/portal/analysis + main
├── config.py        环境配置与校验 (token / deepseek / executor URL)
├── cache_store.py   纠错经验缓存 SQLite (error_cache.db)
├── execution.py     代码清理 / 沙箱执行 / Markdown 报告
├── llm.py           DeepSeek 自动纠错
├── tools.py         MCP 工具集 (register(mcp))
│   ├── list_installed_libraries()          → 环境信息
│   ├── jwave_environment()                 → executor 健康检查
│   ├── run_jwave_code(code, timeout)        → 直接执行
│   ├── run_jwave_code_with_retry(code, timeout, max_retries)
│   │   └── execution._execute_code() → httpx POST executor
│   │   └── llm._llm_fix_code()      → httpx POST deepseek
│   │   └── execution._clean_code()  → 去 markdown 包裹
│   └── validate_simulation_params(params_json)     → 物理规则校验
├── portal.py        Web 门户路由 (register(mcp))
│   ├── GET  /health                         → "ok"
│   ├── GET  /portal /dashboard /report /cache /demo  → HTML 页面
│   ├── POST /ask /demo/api/run              → Dify 工作流代理
│   └── GET/DELETE /dashboard/api/*          → 看板/缓存 JSON API
└── analysis.py      结果分析（T-007 ✅ 已实现）：analyze_simulation_result
    ├── 解析 stdout 场 JSON → max/rms/shape/has_signal
    ├── matplotlib 热力图/波形图 PNG → base64（无 matplotlib 降级 ASCII）
    └── verdict: normal / zero_field / abnormal（对标 VAL-1 基准）
```

> 说明：新功能按模块落位——仿真执行类工具进 `app/tools.py`，结果分析进 `app/analysis.py`，
> Web 页面进 `app/portal.py`，缓存进 `app/cache_store.py`；`app/server_safe.py` 保持薄壳，
> 各模块通过 `register(mcp)` 向同一 FastMCP 实例注册，避免循环导入。

**技能候选的当前信任边界（2026-09-28）**：`app/skill_store.py` 仅提供内部 SQLite 候选存储，尚无 MCP 写入工具、自动激活或生成前注入。`create_candidate` 现只接受 Q2 候选；调用方自报的 Q3/Q4 会被拒绝且不落库。`app/physics_gate.py` 当前只计算 Q0-Q2，待场景规则与来源运行证据的确定性校验完成后，才可设计 Q3/Q4 准入。`analysis.verdict=normal` 不能单独证明场景物理正确。

### 3.2 jwave Executor (`executor/executor.py`)

```
HTTPServer (BaseHTTPRequestHandler)
├── do_GET("/health")    → {status, python, libraries}
├── do_POST("/execute")
│   ├── 验证 X-Executor-Token (hmac.compare_digest)
│   ├── 解析 JSON body {code, timeout_seconds}
│   ├── execute(payload)
│   │   ├── 写入临时文件 main.py
│   │   ├── subprocess.Popen("/usr/local/bin/python -I main.py")
│   │   │   ├── start_new_session=True
│   │   │   ├── preexec_fn: resource.setrlimit (CPU/FSIZE/NOFILE/NPROC)
│   │   │   └── env: JAX_PLATFORMS=cpu, PYTHONUNBUFFERED=1
│   │   ├── process.wait(timeout)
│   │   ├── TimeoutExpired → os.killpg (整棵进程树)
│   │   ├── 读取 stdout/stderr (截断 ≤1MB)
│   │   └── 返回 {exit_code, timed_out, duration_ms, stdout, stderr}
│   └── _send(status, payload)
```

### 3.3 Dashboard (`app/dashboard.py`)

```
函数层级:
├── init_db()              → 创建 executions 表
├── record_execution(...)  → INSERT 一条记录
├── get_executions(limit, since_id) → SELECT (分页)
├── get_stats()            → 统计 (总数/成功/失败/超时)
└── DASHBOARD_HTML         → 内嵌单页 HTML + JS
    ├── 自动刷新 (3s polling)
    ├── 点击展开详情 (代码/stdout/stderr tabs)
    └── 统计卡片 (总/成功/失败/超时)
```

### 3.4 知识库

```
Dify Knowledge Base
├── Dataset: bc9aeb94-de93-4210-8e7d-5b932a731252
├── Documents: (多个 markdown 文件)
│   ├── jwave_api_reference.md     (已有, 需更新)
│   ├── simulation_templates.md    (P1 NEW)
│   ├── parameter_guide.md         (P1 NEW)
│   ├── physics_rules.md           (P1 NEW)
│   └── troubleshooting.md         (P1 NEW)
├── Indexing: Weaviate 向量嵌入
├── Retrieval: top_k=4 + qwen3-rerank
└── 编辑流程: [[kb-edit-process]]
```

### 3.5 Web 前端（当前实现）

```text
app/web/chat.html         静态聊天页面，声明 API base
app/web/chat.css          页面视觉样式
app/web/chat.js           会话、模型设置和事件展示
app/web/agent-client.js   无构建依赖的 Agent HTTP 客户端
                         ├── info()
                         ├── chat() 解析 JSON / NDJSON
                         ├── model profile / BYOK 方法
                         └── AgentApiError
app/ui_chrome.py          共享 design.css / design.js 注入
app/agent_api.py          /api/v1/agent/* + legacy aliases
```

聊天页启动时先读取 `/api/v1/agent/info`，再按能力展示或隐藏模型设置。历史仍保存在浏览器 localStorage，后端只接收 `requirement`、`session_id` 和可选 `model_profile`。技能维护和看板页面继续使用原有 API。

---

## 4. 接口规范

### 4.1 MCP 工具接口

所有 MCP 工具通过 JSON-RPC 2.0 over HTTP 暴露，端点: `POST http://192.168.30.200:8001/mcp`。

**认证**: `Authorization: Bearer <MCP_AUTH_TOKEN>`

#### 4.1.1 run_jwave_code

```
请求:
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "run_jwave_code",
    "arguments": {
      "code": "import jax.numpy as jnp; print(jnp.ones(3))",
      "timeout_seconds": 15
    }
  }
}

响应:
{
  "jsonrpc": "2.0",
  "result": {
    "exit_code": 0,
    "timed_out": false,
    "duration_ms": 234.5,
    "stdout": "[1. 1. 1.]\n",
    "stderr": ""
  }
}
```

#### 4.1.2 run_jwave_code_with_retry

```
请求: 同上 + "max_retries": 7

响应: 同上 + {
  "final_code": "...",
  "history": [
    {"attempt": 1, "exit_code": 1, "stderr_tail": "AttributeError: ..."},
    {"attempt": 2, "exit_code": 0, "stdout_tail": "max_pressure: 620.5"}
  ],
  "total_attempts": 2
}
```

#### 4.1.3 validate_simulation_params (P1 NEW)

```
请求:
{
  "name": "validate_simulation_params",
  "arguments": {
    "params_json": "{\"sound_speed\": 1540, \"source_frequency\": 5e6, ...}"
  }
}

响应:
{
  "valid": false,
  "errors": [
    {"field": "domain_dx", "message": "dx(0.001m) exceeds Nyquist limit for 5MHz", "suggestion": "dx ≤ 0.000077m"}
  ],
  "warnings": []
}
```

#### 4.1.4 analyze_simulation_result (P1 NEW)

```
请求:
{
  "name": "analyze_simulation_result",
  "arguments": {
    "stdout": "...",
    "stderr": "...",
    "exit_code": 0,
    "params_json": "{...}"
  }
}

响应:
{
  "has_signal": true,
  "max_pressure": 620.5,
  "rms_pressure": 89.2,
  "field_shape": [128, 128],
  "heatmap_base64": "iVBORw0KGgo...",
  "verdict": "normal",
  "summary": "峰值压力 620.5 Pa, RMS 89.2 Pa, 网格 128×128"
}
```

### 4.2 Executor HTTP 接口

```
GET /health
Headers: X-Executor-Token: <EXECUTOR_SHARED_TOKEN>
→ 200 {"status": "ok", "python": "/usr/local/bin/python", "libraries": [...]}

POST /execute
Headers: X-Executor-Token: <EXECUTOR_SHARED_TOKEN>, Content-Type: application/json
Body: {"code": "print(1+1)", "timeout_seconds": 15}
→ 200 {"exit_code": 0, "timed_out": false, "duration_ms": 12.3, "stdout": "2\n", "stderr": ""}
→ 401 {"error": "unauthorized"}
→ 400 {"error": "..."}
```

### 4.3 Dify Chat API

```
POST /v1/chat-messages
Headers: Authorization: Bearer <DIFY_APP_API_KEY>
Body: {
  "inputs": {"query": "仿真 5MHz 超声"},
  "query": "仿真 5MHz 超声",
  "response_mode": "streaming",
  "conversation_id": "xxx",   // 多轮对话
  "user": "user-1"
}

→ SSE 流式响应 (text/event-stream)
```

---

## 5. 数据模型

### 5.1 执行历史 (SQLite)

```sql
CREATE TABLE executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,              -- ISO 8601 UTC
    tool_name       TEXT NOT NULL,              -- "run_jwave_code" | "run_jwave_code_with_retry"
    code            TEXT NOT NULL,              -- 执行的 Python 代码
    exit_code       INTEGER,                   -- 进程退出码
    timed_out       INTEGER DEFAULT 0,          -- 是否超时 (0/1)
    duration_ms     REAL,                       -- 执行耗时
    stdout          TEXT DEFAULT '',
    stderr          TEXT DEFAULT '',
    attempt_count   INTEGER DEFAULT 1           -- 重试次数
);
```

### 5.2 仿真参数 (Dify Conversation Variable)

```json
{
  "last_params": {
    "sound_speed": 1540.0,
    "density": 1050.0,
    "source_frequency": 5000000.0,
    "source_type": "point_source",
    "source_position": [64, 16],
    "domain_N": [128, 128],
    "domain_dx": [0.0001, 0.0001],
    "t_end": 0.00005,
    "cfl": 0.3,
    "pml_size": 20
  },
  "last_summary": "5MHz 超声在软组织中传播，峰值压力 620 Pa，衰减 16.9 dB",
  "iteration_count": 1
}
```

### 5.3 工作流 (Dify PostgreSQL)

```sql
-- 表: workflows
-- 列: graph (JSON), app_id, version ('draft' | 'published')
-- 修改后需: docker restart docker-api-1 docker-worker-1
```

---

## 6. 安全模型

### 6.1 认证链

```
用户 / 独立前端 ──→ [同源 / 显式 CORS 白名单] ──→ Agent API v1
                                                   │
                                                   ▼ RuntimeBus / CoreBackend
                                        ┌──────────┴──────────┐
                                        ▼                     ▼
                                  LocalCoreAdapter       DifyCoreAdapter
                                        │                     │
                                        ▼                     ▼ Dify API Key
                                  MCP Gateway / executor    Dify workflow
                                        │
                                        ▼ HMAC compare_digest
                                  jwave-executor
```

### 6.2 隔离层级

| 层级 | 机制 | 作用 |
|------|------|------|
| 进程隔离 | `start_new_session` + `killpg` | 确保超时后所有子进程终止 |
| 容器隔离 | Docker `cap_drop: ALL` | 去除所有内核能力 |
| 权限 | `no-new-privileges: true` | 防止 setuid 提权 |
| 文件系统 | `read_only: true` (除 /tmp) | 不可写入系统文件 |
| 网络 | `executor_internal` (internal: true) | 无外网、无跨容器访问 |
| 资源 | cpu 2.0, mem 4g, pids 256 | 防资源耗尽 |
| 磁盘 | tmpfs /tmp, noexec/nosuid/nodev | 防恶意文件执行 |
| 文件大小 | RLIMIT_FSIZE = 1MB | 防磁盘写满 |
| CPU 时间 | RLIMIT_CPU = timeout+2 | 防 CPU 炸弹 |
| Token 比较 | `hmac.compare_digest` | 防时序攻击 |

### 6.3 威胁模型

| 威胁 | 缓解 | 残余风险 |
|------|------|----------|
| 恶意代码（rm -rf /） | 只读文件系统 | /tmp 可写但 noexec + tmpfs |
| 网络外连 | executor_internal: true | 无 |
| 资源耗尽（while True） | 30s 超时 + CPU rlimit | 最多消耗 30s CPU |
| Token 泄露 | .env 文件 600 权限 | 需物理访问 |
| LLM 注入（Prompt 中嵌入代码） | subprocess 隔离 | 代码在沙箱中执行，无法影响宿主机 |
| 依赖 CVE | jwave/JAX 锁定版本 | 无运行时更新 |

---

## 7. 部署方案

### 7.1 当前部署

```bash
cd <项目目录>
docker compose up -d

# 验证:
curl http://192.168.30.200:8001/health          # → "ok"
curl -o /dev/null -w "%{http_code}" http://192.168.30.200  # → 307 (Dify)
```

### 7.2 依赖顺序

```
1. docker_default network (由 Dify 创建)
2. jwave-executor 容器
3. dify-mcp 容器 (depends_on: jwave-executor)
4. [P1] frontend 容器 (depends_on: dify-mcp)
```

### 7.3 环境变量 (.env)

```bash
MCP_AUTH_TOKEN=<32+ 字符随机串>
EXECUTOR_SHARED_TOKEN=<32+ 字符随机串>
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
CODE_RETRY_MAX=7
```

### 7.4 P1 新增前端服务 (compose.yaml 增量)

```yaml
  frontend:
    build:
      context: ./frontend
    image: local/acouagent:frontend
    container_name: acouagent-frontend
    restart: unless-stopped
    environment:
      DIFY_API_KEY: ${DIFY_API_KEY}
      DIFY_API_URL: http://docker-api-1:5001/v1
      MCP_DASHBOARD_URL: http://dify-mcp:8001/dashboard/api/executions
    ports:
      - "0.0.0.0:7860:7860"
    networks:
      - dify_default
```

---

## 8. 演进路线

### 8.1 技术债务

| 项目 | 说明 | 计划 |
|------|------|------|
| Agent API 契约漂移 | 新前端与独立后端需要持续校验 OpenAPI、事件和能力矩阵 | 已建立契约测试；后续持续维护 |
| SQLite 替换为 PostgreSQL | 共用 Dify 的 PG | P2 |
| executor 单实例瓶颈 | 目前串行执行，需排队 | P2 (多 executor) |
| 无 CI/CD | 手动测试 + 手动部署 | P2 (GitHub Actions) |
| 日志分散 | Dify 日志 / MCP 无日志 / executor stdout | P2 (统一日志) |

### 8.2 扩展方向

| 方向 | 说明 | 优先级 |
|------|------|--------|
| GPU 加速执行器 | executor 容器挂载 GPU | 低（2D 不需 GPU） |
| 自定义介质导入 | 上传 CT/MRI 数据 | 低 |
| 仿真模板市场 | 用户分享参数模板 | 低 |
| 多人协作 | 共享仿真历史和结果 | 低 |


### 2026-10-02 执行记录来源关联切片

新 executions 记录保存实际最终执行代码的 SHA-256 和 gateway-execution.v1 标记；
历史记录新增列保持 NULL，不通过回填原始代码哈希冒充最终执行代码。
重试成功/耗尽写 final current_code；分析以唯一服务器记录核对 run_id、完整网关交付
stdout/stderr、exit_code 和代码哈希，并在 analysis_events 使用解析出的同一run_id。
重复run_id、歧义同输出、缺失、篡改或旧记录拒绝来源绑定；数据库不可用则返回未绑定。
输出核对使用网关压缩后的交付数据，不宣称完整executor原始场已保存。

execution_source.matched 只表示匹配服务器记录；数据库/网关写入属于当前信任边界，
不防有权限直接篡改数据库的操作者，也不能防沙箱程序打印伪造声学数据。
parameters_bound=false、physics_validated=false 明确保留，Q3/Q4准入和自动学习不开放。
来源、参数与确定性validator的完整绑定仍待实现，不能信任调用方回传的matched标志。
新部署会按既有init_db流程添加两个nullable列；本轮只在隔离临时数据库验证，未迁移生产。
记录中的timed_out会传入Q0-Q2质量评估，防止调用方仅给exit_code=0时把已超时运行升为Q1/Q2。

### 2026-10-02 执行器响应回执（来源前置，未部署）

执行器父进程在生成代码运行前采集自身源文件哈希、Python/科学库版本与机器类型；executor-receipt.v1把环境指纹、随机请求nonce、实际代码、解码后的输出、退出/超时和请求时限绑定。网关每次实际执行生成nonce，app/executor_evidence.py检查结构/类型/完整上下文，app/tools.py把最后一次尝试的证据写入SQLite新增nullable executor_evidence_json。旧行不回填，旧执行器缺回执仍可返回普通结果，但不能获得新的来源匹配。只在隔离临时DB验收，原服务/数据库未部署或迁移。

matched只代表内网服务响应关联；runtime_approved=false、trusted_run=false，不等于实际参数或物理认证。信任假设仍是内网端点、安装环境、网关及数据库写入；无签名、无包内容或镜像不可变性认证，不防特权改写，也不认证任意stdout声学数据。子进程不传共享令牌不等于证明所有同UID/proc攻击安全。支持环境合同和受控场景/validator接通仍未完成，Q3/Q4关闭。隔离镜像现场JAX=0.4.38，与Dockerfile的0.4.35不同，不能按文档声明自动批准。

代码/真实HTTP证据 auxiliary/reference/executor-receipt-2026-10-02/；Archify回执/四视口/深浅视觉证据 docs/diagrams/reviews/2026-10-02-heartbeat-0759/。架构图反映源代码能力，不表示现有生产服务已部署这些修改。

### 2026-10-02 固定平面波内部派发与隔离环境合同（未部署）

app/controlled_validation.py只允许无参固定平面波配方，经原_execute_code/HTTP执行器运行30s、服务器生成run_id、写execution记录，再从SQLite复读并检查固定validator与环境回执。网关独立保存gateway_request_nonce/gateway_timeout_seconds；检查器不相信返回的matched/可信等级标志，而是与服务器保存的完整代码/输出重新关联。Dockerfile准备复制scripts/validation资产，但未构建或部署新镜像。

plane-wave-isolated-runtime.v1仅指已经实跑验证的隔离环境指纹，明确含JAX0.4.38，与Dockerfile声明0.4.35不是同一批准合同。该指纹通过只使provenance_prerequisites_passed=true；runtime_approved=false/trusted_run=false/parameters_bound=false，无质量等级颁发、不接收调用方参数或代码、不注册公开MCP工具。生产环境证据批准与物理门/事件持久化仍待实现，Q3/Q4与学习不开放。真实隔离网关HTTP→executor→临时DB→校验器证据见auxiliary/reference/controlled-plane-wave-2026-10-02/；不能冒充原服务部署或Dify验收。

### 2026-10-02 离线批准注册表原型（未接通）

scripts/validation/runtime_registry.py在独立SQLite中验证包哈希、候选/批准/退役状态及事务审计。它不由网关调用，不修改生产SQLite模型；actor未鉴权、无固定生产使用路径，approved状态不能认证运行环境。所有runtime_approved返回false。注册表写入身份、使用重核及退役即时失效尚未实现。

### 2026-10-02 固定路径内部批准服务（未公开/未部署）

runtime_registry_service内部写入复用PORTAL_ADMIN_TOKEN，actor固定角色，数据库与配方包路径由安装代码选择。资格检查每次读取只读数据库和证据包，无缓存；批准哈希、production作用域、观察环境/镜像/配方/validator不符拒绝。contract_eligible尚未绑定可信实际运行观察，因此runtime_approved/trusted_run保持false。未注册公开路由或接通physics_gate，共享令牌不认证个人、拒绝审计和在途退役事务仍待续。

### 2026-10-02 宿主侧只读观察（未绑定运行）

executor_host_observation.py只在SSH宿主读取固定执行器的Docker生命周期/创建镜像ID；不向网关或沙箱提供Docker访问。实际生产与隔离镜像不同，lifecycle_stable不等于某个run使用该容器；run_bound/trusted_run/runtime_approved均false。尚需实际受控执行窗口和运行回执绑定，镜像ID也不认证可写层或挂载内容。

### 2026-10-07 门户流式工作流与延迟优化（已部署门户热补丁）

门户通过app/workflow_client.py接收Dify SSE，向浏览器发送NDJSON阶段进度；仅succeeded完成回执且报告非空时展示结果。总截止180s，空闲读取45s，错误脱敏并带请求编号；有task_id时异常请求停止，但不保证上游已终止，无自动重复提交。Dify移除输出未消费的分析调用，使用Flash代码生成，保留检索、参数提取和审查。分析绑定请求参数，解读不得将请求参数/理论值视作实测认证。executor限制与可信准入未变化。部署仅门户三个文件，其他未验收质量原型未部署。详见本日CHANGELOG和工作流前后备份。

代码生成与重试生成显式thinking=false/max_tokens=4096，依据已安装DeepSeek插件0.0.24 schema及转换实现；参数提取、结果审查和解释的配置保持。多轮33.28s成功与180.15s停止两种路径均有真实浏览器证据，不能承诺无上游故障。
