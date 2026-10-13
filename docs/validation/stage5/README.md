# 阶段五验收：受质量门控的自动学习闭环

日期：2026-10-13
结论：**通过**

阶段五把既有执行记录、物理验证、技能候选和用户反馈接成了可运行的自动学习闭环。生产网关已经开启学习；executor 镜像、容器与安全限制未改。

## 本阶段实现

- `run_jwave_code` 与 `run_jwave_code_with_retry` 的全部结束路径统一进入学习终结器。
- 服务端固定配方先走确定性物理门。只有精确匹配并通过验证的结果才可作为 `Q4 active` 接纳；同一运行不会再写一条较低等级候选。
- 其他生成代码的正常执行最高只写入 `Q2 candidate`。候选保留版本和执行来源，但不能被检索、注入或直接复用。
- 自动学习可在首次合格固定配方运行时初始化缺失的 SQLite 技能库；人工接纳模式仍要求先显式初始化。
- “结果不对”按 `run_id` 绑定到实际技能版本，重复提交幂等，失败计数不会重复增加。
- 保留原 jwave executor、网络隔离、只读文件系统、降权、能力删除、资源上限及执行超时。

## 验收结果

| 范围 | 结果 | 结论 |
| --- | --- | --- |
| 学习终结器与接纳单测 | 68 passed，15 warnings，3.31s | 通过 |
| Luna 独立全量技能回归 | 772 collected / 772 passed，16 warnings，73.30s | 通过 |
| 真实 Dify 固定技能 | 工作流 `b873e836-682c-4220-9ec8-1c3b5c9b2fa5`；参考已交付，生成与执行代码摘要一致，12/12 物理检查通过，最大误差约 `1.9064e-6` | 通过 |
| 真实 Dify 通用请求 | 工作流 `4580692c-8f35-4b93-8b2f-2414b63b8274`；形成一个 `Q2 candidate`、2 个不可变版本 | 通过 |
| 候选隔离 | 对候选规范化参数直接检索返回 0 项，`fallback=true`、`reason=no_applicable_skill` | 通过 |
| 用户负反馈 | 首次记录后 `failure_count=1`；重复请求返回 `duplicate=true`，计数仍为 1 | 通过 |
| 数据完整性 | schema 17；17 个既有技能和版本逐项不变；新增 1 个候选；`PRAGMA integrity_check=ok` | 通过 |
| 运行状态 | `/health`、`/portal`、`/skills`、`/dashboard` 均为 200；活动 Dify 工作流为 0 | 通过 |

Luna 使用既有本地会话 `01a12fed-a22d-7560-8989-ac8f88a636fb` 执行重复且明确的全量回归，并确认新测试文件被收集。其测试容器已退出，无遗留运行容器。

## 生产状态冻结

- 学习、技能库、检索、注入和人工接纳五个开关均为 `true`。
- 生产技能库由 17 个 `active` 技能变为 17 个 `active` 加 1 个 `candidate`。
- 新候选 `985595c9-b417-4762-b6b3-4c3f4e6dfd23` 为 `point_source_2d / Q2`，`success_count=2`、`failure_count=1`、`archived=0`。
- 网关运行镜像 ID 为 `sha256:7554c3487e29065decfacf95ce9edab06e42861553549ab2d5b19da8149d61b5`，同时保留阶段五标签和 Compose 兼容标签。
- executor 容器 ID 为 `168bb601c55f0f416b90c2c6e86fd0dea51af10a979a53b29c74ce520347d3a0`，镜像 ID 为 `sha256:66cc20f558b6cf4d6b9f23e5aec1af6d968f8675078c921a99b43bdb1aa9a5c8`；部署前后相同。

## 证据摘要

原始日志、数据库前后快照和容器检查保存在操作者私有目录 `auxiliary/reference/stage5-auto-learning-20261013/`，不提交运行数据、凭据或原始 SSE。

| 证据 | SHA-256 |
| --- | --- |
| 固定技能真实运行记录 | `6ddd9e55c9b25a0a158c093eb52d236a3afc9382cdbf0be1d76fed7c467cd260` |
| 通用请求学习摘要 | `e2e973ccbda3be36d7a725f13231a65c2a958405b6b1d8b6f969f03bf3b24065` |
| 首次负反馈响应 | `0a700a7547011caa70be85254e5f8e7e0cf5f9f57069a15bf2fc71313033dffb` |
| 重复负反馈响应 | `6e7a140068fa49c2591e01dfb6f38cb29db8c7fc4b5006fd84d47b7bbd3892ab` |
| 学习前技能库 | `02e2ab215c034f08dbec5baff5732bc63ffbdf45b1be8371417364fbe1a19559` |
| 学习后技能库 | `14845278d3fc856c22fbb5b8ae4dbca934ef881e1eb0a8e7f0fc5775ec797417` |

固定技能验收的第一次本地包装器尝试因缺少 `PYTHONPATH` 在发出 Dify 请求前退出，未产生数据库变化，不计入成功样本。修正包装器环境后完成上述真实运行。

## 架构资产

同步更新 `docs/diagrams/acouagent-runtime.architecture.json` 和生成的 HTML。Archify `validate --quality showcase` 为 9/9、0 error、0 warning；`deliver` 通过；1440×900、1600×1000、1920×1080、2048×1320 明暗主题视觉检查均通过，无溢出。

## 当前边界

- 确定性固定目录仍是自动进入 `Q4 active` 的唯一入口；通用成功运行只形成不可复用的 `Q2 candidate`。
- 仓库发行示例继续默认关闭学习，让新环境先完成本机验收；本次生产部署已显式开启。
- Dify 运行时依赖和 DeepSeek 内核固定仍是未解决问题，记录于 `docs/KNOWN_GAPS.md`，后续阶段处理。
