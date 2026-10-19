# 固定平面波正式运行时验收

日期：2026-10-19
范围：只关闭固定平面波配方在既有 30 秒安全墙钟内稳定执行的发行门。

## 来源绑定

- PR：[#24](https://github.com/MWXrookie/dify-mcp-server/pull/24)
- 合并提交：`052cee73e22cccf05a47cd6484cd2c94e06a90b0`
- 最终审查提交：`01fd87a2fea191310540d740cc369a1db387837b`
- 正式部署：使用绑定该合并提交的独立 detached 工作树
- 主分支发布流水线：`39251681214`，全部成功
- 主分支精确 SHA 干净安装：`39254584073`

正式网关与 executor 镜像的 `org.opencontainers.image.revision`、工作树 HEAD 均为合并
提交。网关内的 v2/v3 配方、验证器、运行绑定与技能引导脚本逐项和该工作树 SHA-256
一致。executor 的运行文件与上一已验收镜像字节相同；正式镜像中的
`/app/executor.py` 与合并提交摘要均为
`207dfbc8fb6ff290b37b9091deed82908468423af5aae2b9259394e5acf9981d`。

## 回退与数据保护

- 切换前保留 `local/dify-mcp:rollback-pre-052cee7`。
- 切换前保留 `local/acouagent-executor:rollback-pre-052cee7`。
- 对 `skill_store.db`、`execution_history.db`、`error_cache.db` 执行 SQLite 在线备份。
- 切换前后三个数据库的 `PRAGMA quick_check` 均为 `ok`。
- 新正式工作树保留切换前 `.env` 副本；未输出或提交凭据。

## 技能升级

一次性升级使用原数据卷和真实 executor：

| 项目 | 结果 |
| --- | --- |
| 固定 profile | 17 |
| 可检索 | 17 |
| 本次真实执行 | 1 |
| 精确归档旧配方 | 1 |
| 新平面波质量等级 | Q4 |

旧配方仅在参数指纹与代码摘要同时精确匹配时归档。数据库中旧摘要
`4561ef66...d6b2b` 为 `archived=1`，新摘要 `7f7331ee...c9e2` 为唯一未归档 Q4 active
记录。其他固定技能和两条既有 candidate 未被重写。

## 正式复用结果

| 配方 | 耗时 | 退出 | 超时 | executor 回执 | 物理门 | 使用标识 |
| --- | ---: | ---: | --- | --- | --- | --- |
| `managed-local-plane-wave.v1` | 24167.3ms | 0 | false | matched | pass | `recipe_execution_verified` |
| `managed-local-periodic-water-axial.v1` | 8586.8ms | 0 | false | matched | pass | `recipe_execution_verified` |

两次运行的执行器运行时均为 CPython 3.11.17、jwave 0.2.1、jax/jaxlib 0.4.38、
jaxdf 0.2.8、numpy 1.26.4。回执相关性与确定性物理门通过；记录仍明确
`actual_use_authenticated=false` 与 `model_adoption_authenticated=false`，因此不把固定
技能执行写成模型采用证明。

## v3 真实诊断

`val1_plane_wave_v3.py` 在 2 CPU、4GiB、30 秒、只读根、无网络、drop-all capabilities、
`no-new-privileges` 和 256 PID 限制下真实运行：

- `duration_ms=25995.5`
- `exit_code=0`
- `timed_out=false`
- `numeric_pass=true`
- `quality_level=Q2`
- 五项误差百分比：`0.00012175`、`0.00003576`、`0.00019025`、`0.00001788`、
  `0.00005364`

该运行是数值诊断，保持 `trusted_run=false`、`runtime_approved=false`、`run_bound=false`，
不借此提升技能等级。

## 服务与隔离边界

- `/health`、`/portal`、`/dashboard`、`/skills`、`/cache`、`/api/model-profiles` 均为 HTTP 200。
- gateway 与 executor 均为 healthy，切换后 restart count 为 0。
- executor：2 CPU、4GiB、只读根、drop `ALL`、`no-new-privileges`、256 PID。
- executor 仅连接 `dify-mcp-server_executor_internal`，该网络 `internal=true`。
- executor 服务仍以容器 PID 1 运行。
- 恶意派生进程调用 `setsid()` 并尝试向 PID 1 发送 `SIGKILL` 后，请求回执仍匹配、
  服务保持健康且延迟 marker 未生成。

## 不在本记录关闭的门禁

- 新操作者使用真实 BYOK 在全新环境完成首题。
- 第二个不同模型完成同一固定验收集。
- 本地编排与 Dify 发布图的批量同题双跑。
- 实际执行一次完整的 VM 回滚再前滚演练。
- `v1.0.0` 版本、标签和 GitHub Release。
