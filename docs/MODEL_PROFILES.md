# BYOK 模型配置

AcouAgent 的本地编排统一调用 OpenAI-compatible `/chat/completions` 接口。项目不绑定具体模型厂商。下载者可以在门户为当前会话临时接入自己的模型，也可以在 `.env` 中配置可重复使用的模型 profile。

## 门户临时 BYOK

启动后打开 `/portal`，进入“模型设置”，填写：

- OpenAI 兼容 API 地址，例如 `https://models.example.com/v1`
- 服务商实际提供的模型 ID
- 用户自己的 API Key

浏览器把密钥提交给本机网关一次。网关只在进程内存中保存临时 profile，默认 12 小时后过期，重启也会立即失效；浏览器 `sessionStorage` 只保存不透明的临时 profile id。密钥不会写入 `localStorage`、SQLite、执行记录、模型审计或技能库。

填写或选择 profile 后可以点击“测试连接”。该操作会产生一次小型模型调用，
上限 160 token、30 秒，不执行模型返回的代码，也不形成技能候选。页面分别显示：

- 端点是否返回有效聊天响应；
- 是否能输出严格 JSON；
- 是否能输出受控的最小 Python 代码结构。

三项通过只代表“基础兼容”。完整仿真兼容仍需固定题、原 executor 和确定性
物理门验收。测试响应正文、提示词、endpoint 和密钥均不写入返回或持久化存储。

公网 BYOK 地址默认必须使用 HTTPS 443，并且 DNS 只能解析到公网地址。需要接入 Ollama、vLLM 或内网模型网关时，可由自部署操作者显式设置：

```dotenv
BYOK_ALLOW_PRIVATE_ENDPOINTS=true
```

此开关允许门户提供的 HTTP 和私网地址。只应在网关保持本机或可信网络访问时启用。临时 profile 生命周期可在 300 到 86400 秒之间调整：

```dotenv
BYOK_SESSION_TTL_SECONDS=43200
```

## 单一环境变量模型

无界面调用、自动化或长期自部署可直接配置：

```dotenv
ORCHESTRATION_BACKEND=local
MODEL_BASE_URL=https://models.example.com/v1
MODEL_NAME=acoustic-model
MODEL_API_KEY=replace-with-your-own-key
MODEL_LABEL=我的模型
```

如果模型网关使用可信内网 HTTP，再增加：

```dotenv
MODEL_ALLOW_INSECURE_HTTP=true
```

## 多 profile

`MODEL_PROFILES_JSON` 是单行 JSON 对象。密钥只通过 `api_key_env` 引用环境变量，不能直接写入 JSON：

```dotenv
DEFAULT_MODEL_PROFILE=cloud-a
CLOUD_A_KEY=replace-with-your-own-key
LOCAL_GATEWAY_KEY=local-placeholder-if-required
MODEL_PROFILES_JSON={"cloud-a":{"label":"Cloud A","provider":"openai-compatible","base_url":"https://models.example.com/v1","model":"model-a","api_key_env":"CLOUD_A_KEY","capabilities":["chat","json","code"]},"local-b":{"label":"Local B","provider":"openai-compatible","base_url":"http://host.docker.internal:11434/v1","model":"model-b","api_key_env":"LOCAL_GATEWAY_KEY","allow_insecure_http":true,"capabilities":["chat","json","code"]}}
```

`base_url` 可以包含 `/v1`，也可以直接以 `/chat/completions` 结尾；服务端会规范化请求地址。环境变量 profile 不受门户公网 URL 限制，因为它由部署者直接管理。

## 分任务参数

每个 profile 的 `tasks` 可以覆盖：

- `requirement_merge`
- `parameter_extraction`
- `code_generation`
- `result_review`
- `result_explanation`
- `retry`

每项支持 `temperature`、`max_tokens`、`thinking` 和 `timeout_seconds`：

```json
{
  "code_generation": {
    "temperature": 0.2,
    "max_tokens": 8192,
    "thinking": false,
    "timeout_seconds": 120
  }
}
```

范围限制：`temperature` 0..2，`max_tokens` 1..32768，超时 1..180 秒。模型响应超过 2 MiB 时终止，HTTP 重定向不会跟随。

## 能力、费用与失败语义

| 任务 | 必需能力 |
| --- | --- |
| 参数提取 | `chat`、`json` |
| 代码生成、重试 | `chat`、`code` |
| 需求合并、审查、解释 | `chat` |

未知 profile、过期 BYOK、缺少凭据或能力不足都会失败关闭。模型审查只能解释结果，不能覆盖 executor 状态和确定性物理门。

费用默认记录为 `0`，含义是未知。部署者可以按所用货币配置每百万 token 单价：`MODEL_PRICE_INPUT`、`MODEL_PRICE_INPUT_CACHE`、`MODEL_PRICE_OUTPUT`。

## Dify 兼容模式

完整 BYOK 只作用于 `ORCHESTRATION_BACKEND=local`。`dify` 模式保留给既有部署，其中模型由 Dify 工作流自行管理。

## 审计边界

每次调用记录 provider、model、任务、推理参数和 token usage，并与运行 id 关联。环境 profile 记录其 id；临时 BYOK 只记录不可重放的 SHA-256 摘要。审计不保存 endpoint、凭据变量名、临时 profile 令牌或密钥。
