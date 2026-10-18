# AGENTS.md — AcouAgent 项目指令手册

> 2026-10-16 当前状态：发行默认使用本地编排，门户和环境变量均支持用户自带 OpenAI-compatible 模型；模型调用统一通过 `app/model_client.py` 的脱敏受限传输，自动纠错不再维护平行 HTTP 实现。阶段七 Dify 固定集已完成 30/30，两个本地 BYOK lane、全新机器自然语言首题和 VM 回滚终验仍未关闭；不得把部分矩阵写成完整 v1.0 发布。权威状态入口为 `docs/CURRENT_STATUS.md`。

> 本文档面向项目中的所有 AI Agent。Agent 在执行任何任务之前，必须先阅读本文档以获取项目上下文、当前状态和可执行任务。

> 👥 **新成员/新 Agent 上手第一步**：先读 `docs/CHANGELOG.md`（按时间倒序的完整工作记录：动机/做法/验证/变更文件），再回来看本文件的任务队列，即可快速接上最新进度；想了解系统设计再看 `docs/ARCHITECTURE.md`。

> 📏 **每次提交前必读**：`docs/COMMIT_CONVENTION.md`（提交信息格式 + 检查清单：密钥红线/可移植性/验证要求）。不遵守规范 = 提交被拒。

> 2026-10-11 当前代码范围：schema17、9工具、两项精确本机基础技能；人工接纳/实际参考执行可用，学习默认关闭、Dify草稿未发布、整体合同未通过。最新使用以根 README 和验收矩阵为准。

---

> ⚠️ **2026-10-07 起**：开发规划以 **`docs/DEVELOPMENT_PLAN.md` v3.0** 为准（终点＝"下载即用"的本地技能库系统），验收依据见 `docs/SKILL_LIBRARY_ACCEPTANCE.md`。
> 本文档第 2 节「当前状态」与第 3 节「任务队列」是 **2026-08 的历史快照，已过期**（如"34 次测试 76%""8 节点 draft""5 个 MCP 工具"），仅作历史参考；v2.1 规划全文已归档于 `docs/archive/DEVELOPMENT_PLAN_v2.1.md`。

## 0. 项目记忆

### 0.1 项目记忆 store

本项目的 `global_memory` 项目专用 store 固定为：

```text
project:dify-mcp-server
```

不要把本项目记忆混入其他项目 store，也不要复用到 `MMG_VisualizeMM` 等其他项目。

### 0.2 会话启动读取步骤

1. 先读本项目根目录或 `docs/AGENTS.md`。
2. 再使用 `global_memory.memory_search`，以当前任务为 query，并传入：

```text
store="project:dify-mcp-server"
```

3. 需要完整历史时使用 `global_memory.memory_list`，同样指定本项目 store。

### 0.3 重要信息回写

以下内容应使用 `global_memory.memory_store` 写回 `project:dify-mcp-server`：

- 重要架构决策；
- 错误修复和固定错误模式；
- 用户纠正；
- 已验证的技术约束；
- 阶段完成总结；
- 本轮确认的优化方向。

每条记忆至少带两个有意义的标签，例如：

```text
acouagent,dify-mcp-server,architecture,optimization
```

### 0.4 架构变更前置检查

任何涉及架构内容的新增、删除、重命名或行为变动，包括 Dify 工作流、FastMCP 网关与 MCP 工具、jwave executor/沙箱、执行与纠错闭环、结果分析、SQLite 数据模型、网络与信任边界、部署拓扑等，必须遵守以下顺序：

1. 修改代码或架构文档前，先阅读 `docs/diagrams/acouagent-runtime.architecture.json`，以它作为当前架构事实基线进行检查。
2. 变更实施后，同步更新该 JSON，不能只改代码而让架构资产漂移。
3. 重新生成 `docs/diagrams/acouagent-runtime.architecture.html`。
4. 依次运行 `archify validate --quality showcase`、`deliver` 和 `visual-check`，保留最新回执。
5. 若真实代码与 JSON 冲突，以真实代码为准，但必须同步修正 JSON，并说明冲突原因。

用户级 Codex `PostToolUse` hook 位于 `~/.codex/hooks.json`，由 `~/.codex/hooks/archify-after-pr.mjs` 执行。该 hook 对所有项目生效：当成功的 `gh pr create` 命令结束后，它会扫描当前项目中的 `*.architecture.json`；找到架构文件时，把“检查并同步 Archify 架构”的强制上下文追加给当前 Agent，找不到时静默跳过。该 hook 需要由用户在 Codex `/hooks` 界面完成信任审核后才会执行。

### 0.5 降级行为

如果 `global_memory` 不可用：

- 不得因此中断当前任务；
- 优先以当前代码和用户最新要求为准；
- 可在 `docs/AGENTS.md` 或 `docs/CHANGELOG.md` 中记录关键结论；
- 恢复 MCP 后再补写项目 store。

### 0.6 当前优化方向

完整分析文档由项目 Owner 在仓库外维护；仓库内不记录本机绝对路径。

核心方向：把 `error_fix_cache` 升级为“成功技能库 + 生成前检索 + 物理质量门控”。第一阶段新增 `skill_store.py` 和 `search_simulation_skill` MCP 工具，在 Dify 参数提取后、代码生成前检索技能。

### 0.7 临时脚本处理偏好

- 临时脚本默认用完后删除，不留在项目根目录或 `docs/` 里。
- 只有当脚本明确会重复使用时，才保留并放进 `scripts/` 对应子目录。
- 单次生成、一次性迁移、调试排查类脚本不属于重复使用脚本，应直接删除。

---

## 1. 项目速览

### 1.1 这是什么

**AcouAgent** = 自然语言驱动的声学仿真智能体。用户在 Web 聊天界面输入"仿真 5MHz 超声在软组织中传播"，系统自动完成代码生成 → 沙箱执行 → 纠错 → 可视化 → 解读。

### 1.2 项目路径

```
<项目目录>/        （按部署环境，例如 VM: ~/dify-mcp-server）
```

### 1.3 关键文件清单

| 文件 | 行数 | 职责 | 修改频率 |
|------|------|------|----------|
| `app/server_safe.py` | 薄壳 | FastMCP 网关入口：装配配置 + 注册模块 | 低 |
| `app/config.py` | ~30 | 环境配置与校验 | 低 |
| `app/cache_store.py` | ~200 | 纠错经验缓存 SQLite | 中 |
| `app/execution.py` | ~150 | 代码清理/沙箱执行/报告生成 | 中 |
| `app/llm.py` | ~130 | BYOK profile 自动纠错 | 低 |
| `app/tools.py` | ~330 | MCP 工具集（6 个） | 中（新增工具时） |
| `app/portal.py` | ~170 | Web 门户路由 | 中 |
| `app/analysis.py` | 占位 | 结果分析（T-007 落点） | 中（T-007 后） |
| `executor/executor.py` | 123 | Docker 沙箱，单请求代码执行 | 低（稳定） |
| `compose.yaml` | ~80 | Docker Compose 双容器编排 | 低 |
| `app/dashboard.py` | 347 | SQLite 执行历史 + Web 看板 HTML | 低 |
| `Dockerfile` | ~22 | dify-mcp 镜像（COPY 全部模块） | 低 |
| `executor/Dockerfile` | ~25 | jwave-executor 镜像 | 低 |
| `.env` | 7 | 环境变量（Token/Key） | 低 |

> ⚠️ **模块化约定（2026-08-18）**：新功能按职责落位——仿真执行类工具进 `app/tools.py`，
> 结果分析进 `app/analysis.py`，Web 页面进 `app/portal.py`，缓存进 `app/cache_store.py`；
> `app/server_safe.py` 只做装配，**不要在薄壳里堆业务代码**。

### 1.4 怎么启动

```bash
cd <项目目录>
docker compose up -d
```

### 1.5 怎么验证

```bash
# MCP 网关健康检查
curl http://192.168.30.200:8001/health
# 预期: "ok"

# 执行看板
curl -s -o /dev/null -w "%{http_code}" http://192.168.30.200:8001/dashboard
# 预期: 200

# 查看容器状态
docker ps --filter "name=dify-mcp" --filter "name=jwave-executor"
# 预期: 两个容器均为 Up
```

### 1.6 怎么测试仿真

```bash
# 在 Dify 平台 http://192.168.30.200 中打开"仿真" App
# 输入: "仿真 2MHz 超声在水中传播"
# 或通过 MCP 工具直接调用（需 Bearer Token）
```

---

## 2. 当前状态

### 2.1 已完成的

| # | 事项 | 状态 |
|----|------|------|
| 1 | FastMCP 网关 + 5 个 MCP 工具 | ✅ 运行中 |
| 2 | jwave-executor Docker 沙箱 | ✅ 运行中 |
| 3 | Dify 工作流（8 节点，draft 版本） | ✅ 已部署 |
| 4 | LLM 自动纠错循环（≤7 次重试） | ✅ 已实现 |
| 5 | 知识库（1 个 md 文件，jwave API 参考） | ✅ 已索引 |
| 6 | SQLite 执行看板 | ✅ 可用 |
| 7 | 34 次端到端测试，76% 成功率 | ✅ 数据已有 |
| 8 | PRD v3.0 | ✅ 完成 |

### 2.2 已知缺陷

| # | 缺陷 | 严重度 | 影响 |
|----|------|--------|------|
| BUG-1 | Sources 点源方式压力场输出全零 | P0 | 最常用激励方式不可用 |
| BUG-2 | LLM 编造不存在的 jwave API | P0 | 24% 执行失败根因 |
| BUG-3 | 代码输出偶尔被 markdown 包裹 | P1 | 导致首次执行 SyntaxError |
| BUG-4 | DeepSeek 纠错对 jwave API 错误盲区大 | P1 | 反复编造不同错误 API |

### 2.3 未开始的

| # | 事项 | 优先级 | 阻塞条件 |
|----|------|--------|----------|
| FEAT-1 | Gradio Web 聊天前端 | P1 | 无 |
| FEAT-2 | validate_simulation_params MCP 工具 | P1 | 无 |
| FEAT-3 | analyze_simulation_result MCP 工具 | P1 | 需确认 executor 有 matplotlib |
| FEAT-4 | Dify 工作流迭代循环（审查→重试） | P1 | 依赖 FEAT-2, FEAT-3 |
| FEAT-5 | 多轮对话上下文记忆 | P1 | 依赖 FEAT-1 |
| FEAT-6 | 知识库扩展（5 个文档） | P1 | 无 |
| FEAT-7 | Python SDK | P2 | 依赖 FEAT-1 |

### 2.4 关键数值

```
container: dify-mcp      port: 192.168.30.200:8001
container: jwave-executor port: 8010 (internal only)
Dify App ID:  0592503e-2eba-458f-bf19-128229441427
Dataset ID:   bc9aeb94-de93-4210-8e7d-5b932a731252
Document ID:  1280a56c-2da8-44e0-ba18-01e5d2b21197
Upload File:  c6d0dd10-b1d8-4252-84af-6ff0799003b2
MCP Bearer:   见 VM `~/dify-mcp-server/.env` 的 `MCP_AUTH_TOKEN`（2026-08-18 轮换，仓库不再存放明文 token）
DB:           docker exec docker-db_postgres-1 psql -U postgres -d dify
```

---

## 3. 任务队列

Agent 应**按编号顺序**领取任务。每完成一个任务，在 `docs/CHANGELOG.md` 中追加一条记录，并将本文件中对应任务标记为 `[DONE]`。

> ⚠️ **编号说明（2026-08-17 校准）**：本文件的任务编号（T-001~T-010）与 `docs/DEVELOPMENT_PLAN.md` 的阶段化编号（T-001~T-017）**从 T-004 起错位**。统一以 **DEVELOPMENT_PLAN.md 为准**，映射关系：
>
> | AGENTS.md | PLAN.md | 状态 |
> |-----------|---------|------|
> | T-001 Sources 排查 | T-001 | ✅ [DONE] |
> | T-002 Prompt 强化 | T-002 | ✅ [DONE] |
> | T-003 参数校验工具 | T-003 | ✅ [DONE] |
> | —（验证基准集） | VAL-1 | ✅ [DONE]（2026-08-18，5/5 PASS 含 3D 球面波 1/r + 频域衰减，`docs/VALIDATION_BASELINE.md`） |
> | T-004 结果分析工具 | T-007 | ✅ [DONE]（2026-08-18，`analysis.py`，VM 端到端验证 verdict/热力图） |
> | —（审查+解释节点） | T-008 | ✅ [DONE]（2026-08-18，工作流 13 节点已发布，真实运行含物理解读） |
> | T-005 迭代循环 | T-009 | ✅ [DONE]（2026-08-18，if-else 分支 + 预展开重试，异常结果自动重试 2 次终止） |
> | T-006 Gradio 前端 | T-010 | ✅ [DONE]（2026-08-18，自研 Web 门户替代 Gradio：门户/多轮对话/看板/Demo/报告/缓存 7 页面全 200） |
> | T-007 多轮记忆 | T-011 | ✅ [DONE]（2026-08-18，session 隔离 `portal-<session>` + DeepSeek 需求合并，实测 95%） |
> | T-008 知识库扩展 | T-013 | ⬜ TODO（阶段三） |
> | T-009 回归测试套件 | （PLAN 无对应，阶段三 T-016 前置） | ⬜ TODO |
> | T-010 技术文档完善 | T-017 | ⬜ TODO（阶段三） |

---

### T-001: [DONE] 修复 Sources 点源压力场全零

| 字段 | 值 |
|------|-----|
| 优先级 | P0 |
| 依赖 | 无 |
| 文件 | `executor/executor.py` (不改), 仅排查 |
| 验收 | 使用标准点源模板代码执行，max_pressure > 0 |

**执行步骤**：
1. 在 executor 容器中直接执行 jwave 源码检查：`docker exec jwave-executor python -c "from jwave.geometry import Sources; help(Sources.__init__)"`
2. 读 jwave 源码中 `Sources.on_grid()` 的实现，确认 source term 如何注入 time stepping
3. 写一个最小复现脚本，对比 Sources 方式 vs p0 方式的差异
4. 如果 Sources 本身有问题，输出 workaround 方案；如果是用法问题，更新知识库
5. 在 `docs/CHANGELOG.md` 记录根因和解决方案

**输出物**：根因报告 + 修复代码或 workaround

---

### T-002: [DONE] 代码生成 Prompt 强化 + 知识库更新

| 字段 | 值 |
|------|------|
| 优先级 | P0 |
| 依赖 | 无 |
| 文件 | Dify 工作流 draft → `app/llm.py` 纠错 Prompt |
| 验收 | 50 次端到端测试成功率 ≥ 90% |

**执行步骤**：
1. 从 Dify 数据库读取当前 draft 工作流的 graph JSON
2. 修改代码生成 LLM 节点的 system prompt：
   - 嵌入 ✅ 正确 API（TimeAxis/FourierSeries/Sources/Medium）
   - 嵌入 ❌ 禁止 API 黑名单（p0.shape / time_axis.t / from_array 等）
   - 嵌入 2 个最小完整代码示例
3. 修改纠错 Prompt（`app/llm.py` 的 `_llm_fix_code` 函数）：
   - 注入 jwave 常见错误模式速查表
4. 更新知识库 markdown：
   - 替换文件 → 更新 upload_files.size → 重置 indexing_status → 删除旧 segments → 触发重索引
   - 流程参照 [[kb-edit-process]]
5. 写回工作流 graph → 重启 Dify → 在 Dify 中测试

**输出物**：更新后的 Prompt 文本 + 更新后的知识库文档 + 测试结果

---

### T-003: [DONE] validate_simulation_params MCP 工具

| 字段 | 值 |
|------|------|
| 优先级 | P1 |
| 依赖 | 无 |
| 文件 | `app/tools.py` |
| 验收 | 5 种边界条件全部正确拦截/放行 |

**功能规格**：
```
工具名: validate_simulation_params
输入: JSON 参数对象 {sound_speed, density, source_frequency, domain_N, domain_dx, t_end, cfl, pml_size}
输出: {valid: bool, errors: [{field, message, suggestion}], warnings: [{field, message}]}
```

**校验规则（硬编码，不调用 LLM）**：
1. λ = sound_speed / source_frequency
2. Nyquist: dx ≤ λ / 4（每波长 4 点），不满足 → error
3. CFL: cfl ≤ 0.3，不满足 → error
4. PML: pml_size ≥ 10，不满足 → warning
5. 网格: Nx, Ny ∈ [32, 1024]，越界 → error
6. 时间: t_end * sound_speed / dx / max(Nx,Ny) 大致匹配传播距离，偏差过大 → warning
7. 频率-分辨率: 对 MHz 级超声，若 dx > 0.5mm → error

**验证方法**：
```bash
# 在 Dify 工作流中插入此工具节点，或在本地直接测试 MCP 调用
```

---

### T-004: analyze_simulation_result MCP 工具

| 字段 | 值 |
|------|------|
| 优先级 | P1 |
| 依赖 | 需确认 executor 中有 matplotlib |
| 文件 | `app/tools.py` / `app/analysis.py`（新增工具）, `executor/Dockerfile`（如需） |
| 验收 | 执行标准仿真后，自动返回热力图 PNG + 物理量摘要 |

**功能规格**：
```
工具名: analyze_simulation_result
输入: stdout_text (str), stderr_text (str), exit_code (int), params_json (str)
输出: {
  has_signal: bool,
  max_pressure: float,
  rms_pressure: float,
  field_shape: [int, int],
  heatmap_png_base64: str (or null),
  waveform_png_base64: str (or null),
  verdict: "normal" | "zero_field" | "abnormal",
  summary: str
}
```

**实现要点**：
1. 代码生成节点额外输出一段 Python 将压力场数据序列化为 JSON，写入 stdout
2. 结果分析工具解析该 JSON → 生成 matplotlib 热力图 PNG → base64 编码返回
3. 如果 matplotlib 不可用，生成 ASCII 热力图作为降级

**关联**：此工具的输出将作为结果审查 LLM 节点的输入

---

### T-005: Dify 工作流改造（迭代循环）

| 字段 | 值 |
|------|------|
| 优先级 | P1 |
| 依赖 | T-003, T-004 |
| 文件 | Dify 工作流 graph |
| 验收 | 注入"全零结果"时，系统自动检测并重试（≤3 次迭代） |

**新增节点**：
1. `validate_params`（MCP 工具节点，调用 T-003）
2. `analyze_result`（MCP 工具节点，调用 T-004）
3. `review_result`（LLM 节点，判断 pass/retry/fail）
4. 条件分支：pass → 结果解释节点；retry → 回到参数提取（最多 3 次）；fail → 错误输出

**工作流改造前后对比**：
```
[改造前] Start → 需求分析 → 知识检索 → 模板转换 → 参数提取 → 代码生成 → MCP retry → End
[改造后] Start → 需求分析 → 知识检索 → 模板转换 → 参数提取 → 参数校验(NEW) → 代码生成
         → MCP retry → 结果分析(NEW) → 审查决策(NEW) → [pass] 结果解释(NEW) → End
                                                      → [retry] → 参数提取 (loop ≤3)
                                                      → [fail] → End(fail)
```

---

### T-006: Gradio Web 前端

| 字段 | 值 |
|------|------|
| 优先级 | P1 |
| 依赖 | T-005 |
| 文件 | 新建 `frontend/app.py`，更新 `compose.yaml` |
| 验收 | 浏览器打开后可对话，可看到可视化图和自然语言解读 |

**实现方案**：
```python
# frontend/app.py 核心结构
import gradio as gr
import httpx

DIFY_API_URL = "http://docker-api-1:5001/v1"
DIFY_APP_ID = "0592503e-2eba-458f-bf19-128229441427"

def chat(message, history):
    # 调用 Dify Chat API，返回生成器（流式）
    ...
    yield updated_history

gr.ChatInterface(
    fn=chat,
    title="AcouAgent - 声学仿真智能体",
).launch(server_name="0.0.0.0", server_port=7860)
```

**注意**：前端仅负责 UI，所有智能体逻辑在 Dify 工作流中。前端通过 Dify Chat API 对话。

---

### T-007: 多轮对话上下文记忆

| 字段 | 值 |
|------|------|
| 优先级 | P1 |
| 依赖 | T-005 |
| 文件 | Dify 工作流 conversation variables |
| 验收 | 用户说"把频率改成 3MHz"时，智能体能复用上轮参数仅改 frequency |

**实现**：
1. 在 Dify 工作流中设置 conversation variable `last_params`（JSON 字符串）
2. 参数提取节点的 Prompt 增加上下文注入：
   ```
   上次仿真参数（如有）：{{#conversation.last_params#}}
   如果用户只要求修改某个参数，基于上次参数仅修改该值，其余保持不变。
   ```
3. 每次成功仿真后，用变量赋值节点更新 `last_params`

---

### T-008: 知识库扩展

| 字段 | 值 |
|------|------|
| 优先级 | P1 |
| 依赖 | T-002 |
| 文件 | 知识库 Markdown 文件 |
| 验收 | 知识检索 top_k=4 中至少 3 条与当前仿真需求相关 |

**需要创建的文档**：
1. `jwave_api_reference.md` — 已有，更新补充禁止 API 黑名单
2. `simulation_templates.md` — 6 个可执行代码模板
3. `parameter_guide.md` — 场景-参数对照表
4. `physics_rules.md` — Nyquist/CFL/PML 规则
5. `troubleshooting.md` — 常见错误和修复

编辑流程参照 [[kb-edit-process]]。

---

### T-009: 回归测试套件

| 字段 | 值 |
|------|------|
| 优先级 | P2 |
| 依赖 | T-001 ~ T-008 |
| 文件 | 新建 `tests/` 目录 |
| 验收 | `python -m pytest tests/` 全部通过 |

**测试文件**：
```
tests/
├── test_jwave_api.py       # 10 个基础 API 正确性
├── test_validation.py      # 参数校验 5 种边界条件
├── test_execution.py       # 10 种标准场景端到端
├── test_retry.py           # 10 种错误注入恢复率
├── test_multi_turn.py      # 5 种多轮对话场景
└── conftest.py             # 共享 fixtures
```

---

### T-010: 技术文档完善

| 字段 | 值 |
|------|------|
| 优先级 | P2 |
| 依赖 | T-001 ~ T-009 |
| 文件 | `README.md`, `docs/` |
| 验收 | 新开发者能根据 README 在 30 分钟内完成部署和首次仿真 |

---

## 4. 开发约束

### 4.1 不能改的

| 约束 | 原因 |
|------|------|
| jwave 版本锁定 0.2.1 | 更高的版本 API 可能不兼容，且未经测试 |
| executor 网络不能连外网 | 安全要求（Docker compose 中 executor_internal 为 internal: true） |
| MCP Auth Token 不能硬编码 | 必须从环境变量读取 |
| 代码执行超时上限 30s | jwave executor 硬限制，改需要评估资源影响 |
| 不能信任 LLM 生成的安全敏感代码 | executor 沙箱是最后防线，不能削弱 |

### 4.2 应该遵循的

| 约定 | 说明 |
|------|------|
| Prompt 中的 API 参考必须与 jwave 0.2.1 源码一致 | 用 `inspect.signature()` 验证，不能用 LLM 记忆 |
| 所有文件路径使用绝对路径 | 否则 Agent 在不同工作目录下会出错 |
| Docker 容器名固定 | dify-mcp, jwave-executor，脚本依赖这些名字 |
| 每次修改知识库必须走完整重索引流程 | 参照 [[kb-edit-process]]，不能只替换文件 |
| 修改 Dify 工作流后必须重启 api 和 worker | `docker restart docker-api-1 docker-worker-1` |

### 4.3 不要做的

| 禁止 | 原因 |
|------|------|
| 不要生成 markdown 包裹的代码 | 这是 BUG-3 的根因 |
| 不要在 Prompt 中编造 API | 用已验证的正确 API |
| 不要在 executor 中安装非必要包 | 增加镜像体积（已有 280MB）和攻击面 |
| 不要修改 compose.yaml 中的安全限制 | cap_drop/read_only/pids_limit 不可削弱 |

---

## 5. 参考索引

| 资源 | 路径 |
|------|------|
| PRD (人类版) | `docs/PRD.md` |
| 技术方案与架构 | `docs/ARCHITECTURE.md` |
| 变更日志 | `docs/CHANGELOG.md` |
| 项目记忆 | `~/.claude/projects/<项目路径>/memory/`（Owner 本地，新成员按自己的路径） |
| Dify 知识库编辑流程 | 记忆: [[kb-edit-process]] |
| jwave API 正确用法 | 记忆: [[jwave-api-gotchas]] |
| 测试结果 (34 次) | 记忆: [[test-results-2025-08-07]] |
| 提示词优化历史 | 记忆: [[prompt-and-workflow-optimization]] |
| Dify 工作流详情 | 记忆: [[dify-workflow-jwave]] |

---

## 6. 会话启动检查清单

每个新 Agent 会话启动时应检查：

- [ ] `docker ps` — dify-mcp 和 jwave-executor 是否运行
- [ ] `curl http://192.168.30.200:8001/health` — MCP 网关节可用
- [ ] `curl http://192.168.30.200` — Dify 平台可访问
- [ ] 查阅 `docs/CHANGELOG.md` 了解上次会话做了什么
- [ ] 查阅本文档第 3 节找到下一个 `[TODO]` 任务
