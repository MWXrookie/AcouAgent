# 尚未解决的产品化问题

状态：阶段六部分关闭。本地编排和服务端模型 profile 已进入候选验收；第二模型、Dify 同题双跑和全新 GitHub 环境终验仍开放。

## 2026-10-14 当前进展

- PR #7 已合并到 `main`，合并提交为 `96b4e0b3336e746100f87f7f4552f0874828308b`；合并前主线保存在远端 `rollback/pre-pr7-main-20261014`。
- 根 Compose 与 GHCR 空卷 17 项 Q4 自举已经通过上一切片验收。
- `ORCHESTRATION_BACKEND=local` 已实现参数提取、生成前技能检索、固定参考直达、代码生成、原 executor 执行、确定性物理门、最多两次修正、审查和解释。
- 浏览器只提交 profile id；endpoint、model、凭据环境变量和各任务参数由服务端 profile 管理。响应流式限长且不跟随重定向。
- 同机候选容器使用新数据卷完成真实 DeepSeek + 原 executor 运行：HTTP 200、`workflow_status=succeeded`、`verdict=normal`、`Q2`，执行、分析和四次模型调用均按 run id 留证。未知 profile 返回 `model_profile_unknown`，未调用模型。
- 单题本地/Dify 双跑已完成：两边均为正常 Q2、64×64 场，峰值约相差 3.6%；批量关键产物与失败语义对照仍未完成。
- 第二 profile `deepseek-reasoner` 已验证可选择、可审计、失败关闭；两次完整运行分别结束于 `model_response_empty` 和 `model_transport_failed`，所以第二模型固定集仍未通过。
- 仍需：第二模型适配和固定集；批量本地/Dify 双跑；从合并后 GitHub `main` 在全新环境完成下载、启动、失败回退、技能复用和升级回滚。

下面的“GitHub 发行快照实测”记录阶段六开始时的历史基线，其中“main 未合并 PR #7”和“聊天固定依赖 Dify”已经被后续切片改变。

## 2026-10-14 GitHub 发行快照实测

阶段五远端分支 SHA `b00013519623ca4ab5b2568f061357643c2efb00` 的 425 个跟踪文件包含网关、executor、锁文件、Compose、测试和验收材料。隔离 archive 可构建网关，使用空数据卷在只读降权容器中 `/health=ok`，相关 68 项测试通过并完成资源清理。

完整产品仍不能从该快照直接启动：根 Compose 要求外部 `docker_default` 网络，聊天固定调用 Dify；默认分支尚未包含 PR #7；全新 executor 构建在下载约 93.0/101.8 MB 的 jaxlib wheel 后触发 120 秒 ReadTimeout。浏览器 BYOK 目前只影响多轮需求合并，没有控制 Dify 主工作流。详见 `docs/validation/stage6/PORTABILITY_BASELINE.md`。

阶段六首个切片增加可重复的 release snapshot smoke 和网关/executor GHCR 构建工作流。工作流提交后仍需等待 GitHub Actions 实际成功并回拉镜像验证，不能仅凭 YAML 存在宣称预构建分发已经可用。

## GAP-PLAT-001：运行时强依赖 Dify

**现状**：本地编排候选已经覆盖完整聊天主链路，发行示例默认 `local`；Dify 保留为兼容后端。当前尚未在合并后的 GitHub `main` 和完全全新的机器上完成最终下载即用验收，也未完成与已发布 Dify 图的同题双跑。

**影响**：无法真正做到 GitHub 下载后直接运行；部署容器多，工作流版本、插件和模型凭据还存在额外配置面。

**拟议方向**：延续 `DEVELOPMENT_PLAN.md` 的 Phase E，将编排迁入本项目内的可测试运行时。迁移实现可以采用 LangGraph，但必须继续调用现有 FastMCP 工具和 `jwave-executor`，不得把技能代码放到网关进程执行，也不得削弱 executor 的网络、权限、资源和回执限制。

**关闭条件**：

1. 门户在未安装 Dify 的干净机器上可完成完整仿真、重试、分析、报告和技能复用。
2. 与当前已发布 Dify 工作流按同题双跑，关键产物和失败语义一致。
3. 预构建镜像与一键启动材料完成，首次运行不依赖本仓库之外的私有文件。
4. executor 安全合同和 VAL-1 物理门完整回归通过。

## GAP-LLM-001：工作流模型写死为 DeepSeek

**现状**：本地编排已使用统一服务端 profile，覆盖需求合并、参数提取、代码生成、结果审查、解释和重试。浏览器只选择 profile id；每次调用记录无密钥参数快照，任务能力不匹配会在传输前失败关闭。Dify 兼容图内部模型仍由 Dify 配置；当前只完成 DeepSeek 一个 profile 的真实运行。

**影响**：用户无法按本地部署、成本、速度、上下文长度或推理能力更换生成内核；切换模型需要直接修改工作流节点，并且容易造成不同节点配置漂移。

**拟议方向**：在迁出 Dify 时同时引入统一的模型配置层。按参数提取、代码生成、结果审查、解释和重试五类职责声明模型 profile；profile 至少包含 OpenAI 兼容 endpoint、model、鉴权引用、temperature、thinking、max_tokens、超时及能力约束。默认可继续使用 DeepSeek，并允许指向其他云模型或 Ollama/vLLM。本地配置只保存凭据引用，不把密钥写入技能库、日志或仓库。

**关闭条件**：

1. 用户可通过配置或受控界面选择模型 profile，无需修改工作流源码。
2. 每个运行记录实际 provider、model 与参数快照，但不记录密钥。
3. 不兼容的模型能力在运行前失败关闭，不静默降级或换模型。
4. 至少用两个不同的 OpenAI 兼容模型完成同一固定验收集，并分别报告物理通过率、耗时、重试和调用量。

## 与当前阶段的关系

阶段四的冻结 A/B 已完成，阶段五自动学习已通过。阶段六按“发行镜像与干净环境门 → 移除 Dify 运行时依赖 → 模型 profile → 双跑和下载即用终验”的顺序关闭这些问题；迁移期间所有生成代码仍只能进入现有 jwave executor。
