# 阶段七模型矩阵与本地/Dify 对照

状态：**运行器已实现，真实矩阵尚未执行**

本验收固定使用阶段一的 30 道题：15 个精确配置、5 个同义表达、5 个越界配置、
5 个缺参配置。不得改题、补跑后删除失败样本或降低确定性门。完整矩阵至少包含：

1. 当前首选 OpenAI-compatible profile；
2. 第二个不同模型 profile；
3. 当前发布 Dify 工作流。

每个 lane 各执行 30 次，共 90 次工作流运行。运行前应明确模型费用、候选 Git SHA、
Dify 图摘要和模型标识。不同 lane 可以使用不同模型，但报告必须保留各自声明的模型标识，
不能把编排差异误写成同模型严格 A/B。

## 记录边界

`model_matrix_acceptance.py` 只把以下内容写入 JSONL：lane、模型标识、候选或图摘要、
任务编号、提示词 SHA-256、运行 ID、耗时、质量门、执行状态、固定参考是否交付和稳定错误码。
它不会写入提示词正文、模型响应、生成代码、求解器输出、API endpoint、凭据或原始 SSE trace。

JSONL 是可恢复的追加记录。同一 lane/任务已存在时不会重跑；重复 key、任务摘要漂移、
模型标识或 revision 漂移会在离线评估时失败关闭。

## 执行

先确认本地网关使用候选源码，并为两个环境 profile 配置不同模型。每个本地 lane 在开始
30 题前会执行一次门户同款基础预检：

```bash
python3 -m scripts.validation.model_matrix_acceptance run \
  --backend local \
  --lane local-model-a \
  --profile model-a \
  --revision <candidate-git-sha> \
  --output <operator-evidence-dir>/records.jsonl

python3 -m scripts.validation.model_matrix_acceptance run \
  --backend local \
  --lane local-model-b \
  --profile model-b \
  --revision <candidate-git-sha> \
  --output <operator-evidence-dir>/records.jsonl
```

Dify lane 在能够访问发布工作流且已注入 `DIFY_API_KEY` 的受控环境执行。`revision`
使用规范化发布图 SHA-256，`model-label` 使用本次实际配置的模型名称：

```bash
python3 -m scripts.validation.model_matrix_acceptance run \
  --backend dify \
  --lane dify-published \
  --model-label <dify-model-name> \
  --revision <published-graph-sha256> \
  --dify-base-url http://nginx/v1 \
  --output <operator-evidence-dir>/records.jsonl
```

可以先用 `--dry-run`、`--limit` 或重复的 `--task-id` 检查调度；发布验收必须恢复并完成
全部 30 题，试跑不能单独冒充完整矩阵。

## 离线评估

```bash
python3 -m scripts.validation.model_matrix_acceptance evaluate \
  --records <operator-evidence-dir>/records.jsonl \
  --require-lane local-model-a \
  --require-lane local-model-b \
  --require-lane dify-published \
  --output <operator-evidence-dir>/report.json
```

机械门要求：每个 lane 30/30 完整；20 个正例至少 95% 同时通过确定性门并交付固定参考；
10 个负例不交付固定参考；5 个缺参题全部保持未完成且不猜测参考；跨 lane 的这些合同语义
没有差异。报告即使 `targets_met=true` 仍保持 `formal_acceptance=false`，必须再核对部署身份、
模型身份、Dify 发布图和运行来源后签字。
