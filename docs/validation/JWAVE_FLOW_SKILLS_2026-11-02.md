# jwave_flow 技能库增强验证

日期：2026-11-02

## 范围

本次只修改本地候选和本地 Docker 运行态，不部署正式/演示环境，不重启现有
`jwave-executor`。实现目标是让 `jwave_flow` 通过网关内部 SkillPort 使用现有
技能库，同时保持无技能、异常和固定参考失败时的安全回退。

## 实现摘要

- 参数提取增加可选 `skill_query`，保留旧 `params_json` 兼容。
- `param_check` 后增加 `skill_resolve` 和 `skill_execute`：
  - 精确 `local_fixed_reference_delivery` / `general_validated_reference_delivery`
    由网关执行新 run 并返回独立核验结果；
  - `guided` 只注入同场景白名单参数约束；
  - 无命中或技能服务异常回退原有生成链路。
- `jwave-flow-core` 只通过内部 token 调用 SkillPort，不挂载技能数据库。
- 生成路径成功且学习开关满足时，网关只排队既有 Q2 学习流程，不直接授
  Q3/Q4。
- 默认 `JWAVE_FLOW_SKILL_MODE=full`；`off` 已取消，不再作为可选模式。未配置
  独立 SkillPort token 时，本地内部调用复用 `JWAVE_FLOW_BRIDGE_TOKEN`。

## 代码与单元验证

`conda jw` 定向回归：

```text
308 passed
```

覆盖 jwave_flow backend/bridge、skill client/graph/skill port、技能检索、
automatic source、local/general reference、local workflow 和物理门。另执行
`python -m compileall -q app cores scripts`，通过。

全量执行时 Python 3.14 环境中 `starlette.testclient.TestClient` 会在进入 fixture
时卡住，因此没有把这两个文件计入通过：

```text
tests/unit/test_skill_api.py
tests/unit/test_skill_authoring_api.py
```

在此基础上运行其余 2148 项：

```text
2081 passed, 1 skipped, 63 errors, 3 failed
```

63 个 error 均为沙箱禁止 `socket.socket()`/本地 TCP 监听造成的
`PermissionError`。3 个 failed 中，1 个是同类 loopback 失败，2 个是未修改的
Dify 图基准 `test_model_availability_graph.py` 的既有 guard fixture 漂移。
这些失败没有作为通过记录。

## Docker 验证

本地构建成功：

```text
local/acouagent-jwave-flow-core:source
local/acouagent-gateway:source
```

本轮只重建/启动 `jwave-flow-core` 和 `dify-mcp`，命令均使用
`--no-deps` 且未包含 `jwave-executor`。最终检查时 executor 的
`RestartCount=0`；其容器在本次验证窗口内由外部/先前运行态重新创建，当前
`StartedAt=2026-11-02T13:02:30.289229997Z`，因此不能继续沿用更早的
“启动时间未变”声明。

### 技能关闭

- bridge `/health`：`skill_port=false`、`skill_guidance=false`。
- gateway `/api/v1/agent/info`：`skill_usage=false`。

### 技能开启

- bridge `/health`：`skill_port=true`、`skill_guidance=true`。
- gateway `/api/v1/agent/info`：`skill_usage=true`。
- 内部 SkillPort 的非法 token 拒绝逻辑和 fallback 路径由单元测试覆盖。

### 真实固定参考

使用本地技能库现有 active Q4 `initial_pressure_2d` 参考：

```text
resolve.mode = reference
resolve.reason = local_fixed_reference_delivery
execute.execution_reuse_verified = true
execute.reason = local_reference_execution_verified
execute.run_id = da1ffa67-5384-4a9b-9346-18fcff7a57ad
stdout_length = 369147
```

该路径由网关重新执行固定参考，不是重放旧运行；参考代码未出现在 bridge
响应中。最终本地容器保留默认 `JWAVE_FLOW_SKILL_MODE=full`，SkillPort
能力可用。

## 复杂 3D 输入修复复测

截图中的复杂球层介质请求暴露了两个与技能复用无关的参数合同缺陷：

1. jwave_flow 模型返回结构包含 `materials.water.sound_speed_m_s` 等显式 SI
   字段，而原 `physics.extract_scalars` 只识别旧的 `sound_speed`、`density`、
   `source_frequency` 名称，错误报告缺声速和频率。
2. local 的 `physics_gate` 未先做显式 SI 别名归一化，导致参数实际存在时仍把
   `parameters_complete` 标为 false；同时两个核心都可能在参数不完整时仅凭执行
   成功返回成功状态。

修复后：

- jwave_flow 支持顶层和嵌套材料中的 `sound_speed_m_s`、`density_kg_m3`、
  `frequency_hz`、`grid_shape_xy`、`step_m`；
- local 物理门先执行 `parameter_contract.canonicalize_parameters`；
- local 的 `_passed` 和 jwave_flow 的 `finalize` 都要求参数合同通过。

同一复杂 3D 输入的复测结果：

```text
local.workflow_status = succeeded
local.quality_level = Q2
local.run_id = 9f1c60e7-e124-42bb-9f74-6fd89d40e7b4

jwave_flow.param_check_ok = true
jwave_flow.success = true
jwave_flow.verified = true
```

模型在 jwave_flow 复测中仍发生一次参数提取重试，但第二次参数检查通过；这属于
模型输出波动，不再被误报为缺声速/缺频率。

## 综合报告与状态提示

- `jwave_flow` 客户端复用 local 的 `execution._build_report`，统一生成“执行状态、
  指标表、关键数据、程序输出、错误/警告、图像”结构。
- 其后追加确定性物理门、技能模式/技能使用、模型审查和物理解读段落。
- 输出层复用 local 的 `execution._build_report`，并清晰展示执行状态、确定性
  物理门、技能模式、模型审查和物理解读。
- 完成度评审逻辑不修改；报告只优化状态提示和缺失项展示。

## 未完成检查

- 真实模型端到端 smoke 请求在 180 秒内未返回，日志显示上游模型多次
  `HTTP 400` 后继续重试；本次不把它计为通过，也不宣称完成真实模型矩阵。
- 当前环境没有 `archify` 和 `ruff`，因此没有执行
  `archify validate --quality showcase`、`deliver`、`visual-check` 或 ruff 检查。
  `docs/diagrams/acouagent-runtime.architecture.json` 已更新并通过 JSON 解析；
  HTML 未重新生成，不能宣称架构资产全部同步。

## 结论

默认 `full` 路径、能力协商、内部 SkillPort、真实 Q4 固定参考新 run 和独立核验
均已在本地 Docker 中验证。正式网络、真实模型矩阵、架构 HTML 生成和 ruff 仍需
在具备对应工具/网络的环境补跑。
