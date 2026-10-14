# 阶段六：本地编排与模型 profile 候选验收

日期：2026-10-14

## 结论

本切片通过候选验收。门户在 `ORCHESTRATION_BACKEND=local` 时不调用 Dify，能够完成参数提取、确定性参数校验、生成前技能检索、固定参考直达或代码生成、原 executor 沙箱执行、分析审计、物理质量门、最多两次修正、模型审查和结果解释。

这不是阶段六最终关闭：仍需第二模型固定集、Dify/本地同题双跑，以及合并后 GitHub `main` 的全新机器终验。

## 实现边界

| 能力 | 实现 |
| --- | --- |
| 后端选择 | `ORCHESTRATION_BACKEND=local|dify`；既有环境缺省保持 `dify`，发行示例为 `local` |
| 模型选择 | 浏览器只发送 profile id；服务端保存 endpoint、model、凭据引用和分任务参数 |
| 能力门 | 参数提取要求 `chat+json`，代码生成和重试要求 `chat+code`；不满足时传输前拒绝 |
| 传输边界 | OpenAI-compatible `/chat/completions`，关闭重定向，响应流式读取且上限 2 MiB |
| 执行边界 | 复用 `run_jwave_code` 和现有 executor；网关不执行生成代码 |
| 成功判定 | exit code、超时、`verdict` 和质量等级共同决定；模型审查不能覆盖 |
| 审计 | 执行、分析、模型调用按 run id 关联；模型记录包含无密钥参数快照 |
| 技能复用 | 代码生成前检索；精确固定参考由服务端重核后直达，失效参考回退普通生成并保留重试 |

## 验收结果

| 检查 | 结果 |
| --- | --- |
| 针对性回归 | 54 passed，14 warnings，2.06s |
| Archify | showcase 9/9，0 error，0 warning |
| 视觉包含 | 1440×900、1600×1000、1920×1080、2048×1320 均通过；亮暗截图人工检查通过 |
| 候选健康 | `/health=ok`；`/api/model-profiles` 返回 local 和已配置 profile，不返回 endpoint/密钥引用 |
| 真实本地运行 | HTTP 200，13.13s，`workflow_status=succeeded`，`verdict=normal`，`Q2`，run id 存在 |
| executor 证据 | 1 条执行记录与 1 条分析记录；生成代码仍由原 executor 执行 |
| 模型审计 | 4 次：参数提取、代码生成、结果审查、结果解释；实际模型 `openai-compatible:deepseek-chat` |
| 密钥边界 | 四条审计元数据均不含 `api_key` 或 `base_url` |
| 错误 profile | `missing-profile` 返回 `workflow_status=error / model_profile_unknown`，未静默换模型 |
| Dify 同题对照 | 本地与已发布 Dify 均为 `exit_code=0`、未超时、`verdict=normal`、`Q2`、`64×64`；峰值约相差 3.6% |
| 第二模型探测 | `deepseek-reasoner` 可选择并留下实际模型审计，但两次完整工作流分别以 `model_response_empty` 和 `model_transport_failed` 失败，未计为通过 |
| Luna 全量回归 | 1475 passed，0 failed，1 skipped，15 warnings，10 subtests，92.91s |



同题对照中，本地编排峰值约 `0.2139 Pa`，已发布 Dify 路径峰值约 `0.2215 Pa`；两者都通过当前确定性门，但约 3.6% 的差异说明模型生成代码仍会影响数值产物，后续批量双跑不能只比较成功状态。

第二 profile 使用相同 OpenAI-compatible 接口的 `deepseek-reasoner`。通用默认上限的一次运行在 74.10s 后返回 `model_response_empty`；扩大该 profile 的 token/超时后，第二次在 244.69s 后返回 `model_transport_failed`。第二次内部三次 executor 结果为两次 abnormal、一次 normal（峰值约 `0.1897 Pa`），但结果解释调用未完成，因此整个工作流正确保持 error。该证据证明 profile 路由和失败关闭有效，不能据此把第二模型验收标为通过。

真实请求使用二维水中点源：`64×64`、`dx=0.5 mm`、`100 kHz`、`20 us`、`CFL=0.3`、`PML=10`。结果的峰值和场数据经过确定性分析；Q2 只表示已执行且参数/场检查通过，不表示完整物理真值证明。

## 回退与兼容

- 合并前主线固定在远端 `rollback/pre-pr7-main-20261014`；PR #7 的合并提交为 `96b4e0b3336e746100f87f7f4552f0874828308b`。
- 回退该合并应使用 `git revert -m 1 96b4e0b`，避免改写共享分支历史。
- 当前切片位于 `feat/stage6-local-orchestration-20261014`，尚未合并；现有生产容器、生产数据卷和 Dify 发布图未修改。
- `ORCHESTRATION_BACKEND=dify` 保留既有兼容行为；该模式的门户 profile 只负责多轮需求合并。

## 尚未完成

1. 用第二个 OpenAI-compatible profile 跑同一固定验收集，并比较物理通过率、耗时、重试和调用量。
2. 本地编排与当前已发布 Dify 工作流做同题双跑，核对产物、重试和失败语义。
3. 从合并后的 GitHub `main` 在无既有卷、无 Dify 的全新机器完成下载、配置、启动、固定题、失败回退、技能复用和升级回滚。
4. 为第二模型建立适配参数和固定集，避免只证明 profile 可选而未证明工作流可完成。
