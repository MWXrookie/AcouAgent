# Agent Frontend Contract

本文档定义可替换前端需要遵守的最小接口。前端不直接依赖本地编排、Dify 或 executor 模块；所有 Agent 操作通过 `agent-client.js` 或等价的 HTTP 客户端完成。

## 1. 静态前端组成

```text
app/web/chat.html        聊天页面和 API base meta
app/web/chat.css         聊天视觉样式
app/web/chat.js          会话、模型设置、Markdown 和事件展示
app/web/agent-client.js  无构建依赖的 Agent HTTP 客户端
```

默认 API base：

```html
<meta name="acouagent-api-base" content="/api/v1/agent">
```

独立前端可以使用自己的地址；服务端必须通过 `PORTAL_ALLOWED_ORIGINS` 放行。前端不得把 API base 写死为 `/chat`。

## 2. AgentApiClient

浏览器加载后可使用：

```html
<script src="/assets/agent-client.js"></script>
<script>
  const client = AcouAgentApi.createAgentClient({
    baseUrl: document.querySelector('meta[name="acouagent-api-base"]').content
  });
</script>
```

方法：

| 方法 | 说明 |
|---|---|
| `info()` | 获取后端身份、版本和能力 |
| `chat(body, handlers)` | 发送聊天并解析 JSON/NDJSON |
| `listModelProfiles()` | 获取公开模型 profile |
| `createModelSession(body)` | 创建临时 BYOK 会话 |
| `testModelProfile(profileId)` | 执行模型预检 |
| `deleteModelSession(profileId)` | 撤销临时 BYOK 会话 |

### chat handlers

```js
const result = await client.chat(
  {
    message: '仿真 2 MHz 点声源',
    requirement: '',
    session_id: 's1',
    model_profile: null,
    stream: true
  },
  {
    onProgress(event) {},
    onSkillUsage(event) {},
    onResult(event) {}
  }
);
```

`chat()` 返回 terminal `result`。如果没有收到 `result`，抛出：

```text
AgentApiError.code = workflow_incomplete
```

### 错误对象

客户端统一抛出 `AgentApiError`：

```js
{
  name: 'AgentApiError',
  code: 'capability_not_supported',
  message: '当前后端不支持模型配置。',
  status: 501,
  requestId: 'uuid',
  details: undefined
}
```

前端应优先展示 `message`，不要拼接服务端 traceback 或密钥。

## 3. 能力降级

初始化时先调用 `info()`：

```js
const info = await client.info();
const canManageModels = info.capabilities.model_profiles && info.capabilities.byok;
```

- `model_profiles=false` 或 `byok=false`：隐藏模型设置按钮，显示“模型由当前平台工作流管理”。
- `chat` 始终必须为 true；否则显示后端不可用。
- 不应根据 `backend_id` 写死前端分支；只使用 capabilities。

## 4. 会话与历史

当前前端继续使用浏览器 localStorage 保存会话列表和当前需求：

```text
acouagent_sessions
acouagent_current
acouagent_model_profile
acouagent_byok_profile (sessionStorage)
```

每个聊天请求携带：

- `session_id`：当前对话 ID；
- `requirement`：上一轮完整需求；
- `model_profile`：当前选择的服务端 profile id，可为空。

后端不新增聊天历史数据库。替换前端时需要自行保持或丢弃浏览器历史，但要继续发送相同的 `ChatRequest` 字段。

## 5. UI 事件处理

流式事件处理建议：

```js
await client.chat(request, {
  onProgress: event => showProgress(event.stage),
  onSkillUsage: event => showSkill(event.skill_usage),
  onResult: event => renderReport(event.report, event.workflow_status)
});
```

- `progress.stage` 用于进度提示。
- `skill_usage.skill_usage` 用于展示本次参考技能；没有该事件不代表请求失败。
- `result.report` 是最终 Markdown 报告。
- `workflow_status` 的 `error` 是业务失败，不代表 HTTP 失败。
- `request_id` 和 `workflow_run_id` 用于审计与故障排查。

## 6. 替换检查清单

- [ ] 只调用 `/api/v1/agent/*`，不直接调用旧 `/chat` 或 `/api/model-profiles`。
- [ ] 启动时读取 `info()`。
- [ ] 处理 `AgentApiError.code`、`status` 和 `requestId`。
- [ ] 支持 NDJSON 分片，不假设一个 chunk 一个事件。
- [ ] 模型能力缺失时隐藏配置入口。
- [ ] API base 可配置，不写死部署域名。
- [ ] 保留技能维护页面的 `/skills/api` 和 `/skills/api/v1` 兼容边界。
