# 核心模型配置所有权验证

日期：2026-10-30
范围：`local`、`jwave_flow`、`dify` 三类核心的模型配置所有权，以及 jwave_flow
managed/self 两种模式的请求边界。未修改外部 checkout，未重启生产容器。

## 契约

- `/api/v1/agent/info` 的 capabilities 新增 `model_config_mode`：
  - `managed`：核心消费网关统一模型 profile/BYOK。
  - `self`：核心在自身边界内管理模型；门户不得注入 profile/BYOK。
- `local` 固定 `managed`；
- `jwave_flow` 默认 `managed`，设置 `MODEL_CONFIG_MODE=self` 后切换为固定 bridge 模型；
- `dify` 固定 `self`，模型由 Dify 管理。
- self 模式多轮需求合并改为确定性文本拼接，不调用网关模型；
- managed 模式由网关解析 profile，校验 endpoint/凭据/能力后，以一次请求级
  `model_config` 传给 jwave_flow bridge；不写入 bridge 环境或持久存储。

## 验证

### Conda jw

```text
73 passed in 0.52s
```

覆盖 Agent 契约、API envelope、Dify self 限制、本地 managed、jwave_flow
managed/self、模型 profile/BYOK、bridge 请求解析、错误脱敏和前端静态契约。

额外检查：

```text
node --check app/web/chat.js
node --check app/web/agent-client.js
python -m py_compile ...
git diff --check
OpenAPI / architecture JSON parse
```

### Docker managed 模式

bridge 不配置 `JWAVE_FLOW_MODEL` / `JWAVE_FLOW_API_KEY` 启动：

```text
GET /health = 200
model_config_mode = managed
model_configuration = true
```

网关 `/info` 返回：

```text
backend_id = jwave_flow
model_config_mode = managed
model_profiles = true
byok = true
```

网关使用环境 profile 调用 jwave_flow，bridge 收到请求级模型配置后尝试模型调用；
故意使用不可达模型端点时返回脱敏的 `jwave_flow_failed`，没有返回
`jwave_flow_model_config_missing`，证明 managed profile 已传入 bridge。

另通过门户 `/api/v1/agent/model-sessions` 创建临时 BYOK profile，再以该
`model_profile` 发起 jwave_flow chat：

```text
byok-profile-created
managed-byok-chat-ok jwave_flow_failed ['progress', 'result']
```

结果不再是截图中的 `capability_not_supported`，也没有出现
`byok_session_missing_or_expired`；模型端点故意不可达，故业务 terminal result
为 `jwave_flow_failed`。

### Docker self 模式

bridge 使用固定 `JWAVE_FLOW_MODEL/API_KEY` 启动：

```text
GET /health = 200
model_config_mode = self
model_configuration = false
```

网关 `/info` 返回：

```text
model_config_mode = self
model_profiles = false
byok = false
```

请求只携带 `query`，返回的 terminal result 标记 `model_config_mode=self`；没有
网关模型 profile 被注入 bridge。

## 未完成门

- 本轮使用不可达模型端点验证配置传递和失败收口，没有真实模型科学矩阵。
- 当前环境没有 `archify`，架构 HTML/delivery/visual-check 未重跑。
- 未部署正式环境、未重启现有容器。
