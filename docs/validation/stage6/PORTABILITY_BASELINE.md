# 阶段六起点：GitHub 发行可移植性基线

日期：2026-10-14

## 结论

阶段五提交 `b00013519623ca4ab5b2568f061357643c2efb00` 的远端跟踪快照包含网关、executor、锁文件、Compose、测试和阶段五验收材料。该快照可独立构建网关镜像，网关在空数据卷上可启动，相关 68 项测试通过。

仓库目前仍未达到“下载即用的完整产品”：根 Compose 强制引用外部 `docker_default` 网络，聊天主路径仍调用 Dify；executor 在弱网全新构建时可能因 101.8 MB 的 jaxlib wheel 下载超时。默认分支 `main` 仍停在 PR #6，阶段一至五位于尚未合并的 PR #7 分支。

## 本轮独立检查

| 检查 | 结果 |
| --- | --- |
| 远端分支 SHA | `b00013519623ca4ab5b2568f061357643c2efb00` |
| 远端跟踪快照文件数 | 425 |
| 必需源码、锁文件、Compose、Dockerfile、阶段五证据 | 完整 |
| `.env.example` 生成无秘密测试配置 | 通过 |
| `docker compose config` | 通过 |
| 网关从远端跟踪快照构建 | 通过 |
| 空数据卷、只读、降权网关健康检查 | `/health=ok` |
| 阶段五相关隔离回归 | 68 passed，15 warnings，3.17s |
| 测试容器、镜像、卷、临时目录清理 | 完成，无遗留 |
| executor 全新构建 | 阻断：下载 jaxlib 至约 93/101.8 MB 后 120s ReadTimeout |

第一次 HTTPS 克隆和第二次 SSH 克隆分别因连接重置和 `early EOF` 失败。为区分网络传输问题与仓库内容问题，后续检查使用已执行 `git fetch` 且 SHA 与 GitHub 插件回读一致的 `origin/feat/acouagent-evidence-gates-20261007`，通过 `git archive` 生成无未提交文件、无 `.env`、无生产数据的隔离快照。

## 已确认的发行缺口

1. **默认分支尚未包含阶段一至五**：直接下载默认分支得不到本轮能力。PR #7 已更新且当前可合并，合并仍需仓库 Owner 决定。
2. **外部 Dify 网络是启动前置条件**：根 Compose 声明 `dify_default.external=true` 且固定为 `docker_default`。没有 Dify 的机器即使网关和 executor 源码完整，也不能按根 Compose 启动完整栈。
3. **聊天仍依赖 Dify**：门户 `/chat` 调用 `http://nginx/v1/workflows/run`。单独启动网关只能使用健康、MCP、技能库和看板等本地能力，不能完成完整自然语言仿真。
4. **模型选择没有贯穿主流程**：门户保存的 BYOK 配置只用于多轮需求合并；Dify 内部节点仍固定模型，页面当前容易让用户误以为整条工作流已经切换。
5. **弱网构建风险真实存在**：executor 科学栈体积大，重试参数不能保证首次下载成功。需要 GHCR 预构建镜像和源码构建回退，不能只写“运行 docker compose build”。

## 阶段六推进顺序

1. 建立 GitHub Actions 网关/executor 镜像构建与 GHCR 发布，提供可固定摘要的预构建路径。
2. 把 Dify 网络连接移到兼容覆盖文件，根 Compose 在无 Dify 的干净机器上可启动。
3. 将编排迁入本项目，所有生成代码继续只交给现有 executor；对同题做 Dify/本地编排双跑。
4. 引入服务端模型 profile，覆盖参数提取、生成、审查、解释和重试；前端只选择 profile，不传输或保存服务端密钥。
5. 在全新机器执行下载、配置、启动、固定题、失败回退、技能复用和升级回滚终验。

## 本阶段新增发行门

`scripts/validation/release_snapshot_smoke.sh` 只测试选定 Git ref 中真实存在的文件，并在退出时清理自己的容器、卷、镜像与临时目录。默认验证网关构建、空数据卷健康检查和阶段五相关测试；加 `--with-executor` 时再构建 executor 并运行 VAL-1。其失败不得用当前工作区或生产镜像替代为“通过”。
