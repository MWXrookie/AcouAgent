# 服务端模型 profile 配置

阶段六本地编排使用 OpenAI-compatible `/chat/completions` 接口。浏览器只保存并发送 profile id；URL、模型名、凭据环境变量和推理参数全部由服务端管理员配置。

## 最小配置

`.env` 中的 `MODEL_PROFILES_JSON` 必须是单行 JSON 对象，键为 profile id：

```dotenv
ORCHESTRATION_BACKEND=local
DEFAULT_MODEL_PROFILE=deepseek-default
DEEPSEEK_API_KEY=replace-with-server-secret
MODEL_PROFILES_JSON={"deepseek-default":{"label":"DeepSeek","provider":"openai-compatible","base_url":"https://api.deepseek.com","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY","capabilities":["chat","json","code"]}}
```

`base_url` 可以包含 `/v1`，也可以直接以 `/chat/completions` 结尾；服务端会规范化为唯一请求地址。HTTPS 是默认要求。受信的内部 HTTP 模型网关必须显式设置 `"allow_insecure_http":true`，并由操作者保证网络边界。

## 多 profile 示例

```dotenv
DEFAULT_MODEL_PROFILE=deepseek-default
DEEPSEEK_API_KEY=replace-with-server-secret
SECOND_MODEL_API_KEY=replace-with-another-server-secret
MODEL_PROFILES_JSON={"deepseek-default":{"label":"DeepSeek","provider":"openai-compatible","base_url":"https://api.deepseek.com","model":"deepseek-chat","api_key_env":"DEEPSEEK_API_KEY","capabilities":["chat","json","code"]},"second-cloud":{"label":"Second cloud model","provider":"openai-compatible","base_url":"https://models.example.com/v1","model":"acoustic-model","api_key_env":"SECOND_MODEL_API_KEY","capabilities":["chat","json","code"]}}
```

重启网关后，门户的“选择模型配置”只显示 profile 标签、模型名、能力与凭据是否已配置，不返回 URL、凭据变量名或密钥。

## 分任务参数

可在每个 profile 的 `tasks` 中覆盖以下职责：

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

范围限制：`temperature` 0..2，`max_tokens` 1..32768，超时 1..180 秒。模型响应流式读取，超过 2 MiB 时立即终止。

## 能力与失败语义

| 任务 | 必需能力 |
| --- | --- |
| 参数提取 | `chat`、`json` |
| 代码生成、重试 | `chat`、`code` |
| 需求合并、审查、解释 | `chat` |

未知 profile、缺少凭据或能力不足都会失败关闭，不会静默改用其他模型。模型审查只解释结果，不能覆盖 executor 状态和确定性物理门。

## Dify 兼容模式

`ORCHESTRATION_BACKEND=dify` 时，profile 只用于门户多轮需求合并；Dify 工作流内部节点仍由 Dify 管理。需要整条链路按 profile 切换时使用 `local`。

## 审计边界

每次调用记录 provider、model、profile id、任务、temperature、max tokens、thinking、timeout 和 token usage，并与运行 id 关联。记录中不保存 endpoint、凭据变量名或密钥。
