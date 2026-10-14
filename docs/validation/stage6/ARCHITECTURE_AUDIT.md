# 阶段六架构图与实际系统核对

日期：2026-10-14

## 结论

Archify 运行时图的核心执行和质量门链路与生产实际一致；发行冷启动部分存在缺失，现已同步修正。旧的 `docs/ARCHITECTURE.md` 明确标记为历史文档，仍包含 Gradio、旧工具数量和旧节点描述，不能作为当前发行事实。

## 逐项核对

| 图中事实 | 实际证据 | 结论 |
| --- | --- | --- |
| Dify 工作流 63 节点 / 70 边 | 生产 `apps.workflow_id` 指向的发布图实查为 63 / 70 | 一致 |
| FastMCP 网关 9 个工具 | 当前注册工具与 README 清单均为 9 个 | 一致 |
| executor 仅在内部网络 | 运行容器仅连接 `executor_internal`；只读、cap-drop、no-new-privileges、4 GiB、2 CPU、256 PID | 一致 |
| 生产技能库 Q4/Q2 分流 | 生产为 17 个 Q4 active 与 1 个 Q2 candidate，schema 1..17，quick_check=ok | 一致 |
| 生产自动学习已开启 | 五个技能相关开关均为 true | 一致 |
| Dify 与 DeepSeek 仍是依赖 | 门户主聊天仍调用 Dify；DeepSeek 仍用于需求合并和纠错 | 一致，属于阶段六未完成项 |
| GitHub 新环境能看到技能内容 | 原根 Compose 默认关闭技能开关、依赖外部 Dify 网络且空卷没有种子 | **不一致，已修复发行路径** |

## “拉取后没有技能库内容”的根因

1. 默认分支 `main` 仍停在 PR #6；前五阶段代码位于 PR #7 功能分支。
2. 即使拉取 PR #7，旧 `.env.example` 仍把技能库、检索、注入和人工接纳全部设为 false。
3. SQLite 生产数据卷不属于 Git 仓库；空数据卷不会自动获得生产中的 17 条记录。
4. 旧 README 只给出单 profile 的手工 seed 命令，不能让新成员一次得到完整目录。
5. 旧根 Compose 强制要求外部 `docker_default`，没有 Dify 的机器会在看到技能页面之前就启动失败。

## 修复后的发行契约

- 根 Compose 默认拉取公开 GHCR gateway/executor 镜像，并只要求内部 executor 网络。
- Dify 网络移入 `deploy/compose.dify.yaml` 可选覆盖。
- `.env.example` 默认开启技能库、检索、注入、人工固定配方接纳和 17 项自举；自动学习仍默认关闭。
- `skill-bootstrap` 使用空数据卷逐项调用现有 executor，经过原执行回执和确定性物理门后才写入 Q4。
- 自举幂等；已合格的 profile 不重复执行，撤回或归档记录不会被自动恢复。
- 没有复制生产数据库、候选记录、用户反馈、密钥或历史运行。

## 隔离验收

在独立 Docker volume 上完成 17 项冷启动：17/17 为 Q4 active，17 个版本，SQLite `quick_check=ok`。相关单测 41 passed。生产 volume、生产网关和生产 executor 配置均未修改。

## GHCR 空卷复验与网络修正

实际拉取 `522bc05` 的 gateway/executor 镜像后，在全新的隔离 volume 上再次完成 17/17 固定配方自举，得到 17 个 Q4 active、17 个版本，`skill_store.db` 的 `quick_check=ok`。随后发现根 Compose 若让 gateway 也只连接 `internal: true` 网络，Docker 会保留端口声明却不建立主机监听。现已改为 gateway 同时连接 `gateway_frontend` 与 `executor_internal`，executor 仍只连接内部网络。

修正后 `/health`、`/portal`、`/skills`、`/dashboard` 均返回 HTTP 200；`/skills/api?limit=20` 返回 17 项且全部为 Q4；`status-all` 返回 17/17 ready。该复验只使用 GHCR 镜像与空数据卷，不读取生产技能数据库。
