# Dify 工作流备份

> 自动导出自 Dify PostgreSQL `workflows` 表（App `0592503e-2eba-458f-bf19-128229441427`「声学仿真」）
> 导出日期：2026-08-17

## 文件

| 文件 | 说明 |
|------|------|
| `stage2_daily_reuse_published_20261013.json` | 阶段二最终发布图：63 节点/70 边，SHA-256 `d4a133a6e971816e1ded7f81f3f734ba8ccb254e9e86eb49055ef8ab1cf69925`；精确固定配方经物理数值门后直接生成确定性报告，其他输入回退原模型路径 |
| `app_0592503e_workflows.json` | 该 App 全部 4 个版本（published / draft / 2 个时间戳版本），含 `graph`、`features`、环境变量、会话变量 |

## 版本说明

| 版本 | 节点数 | 说明 |
|------|--------|------|
| `published` | 8 | 线上实际运行的版本（2026-08-06） |
| `draft` | 9 | 草稿版，含 `validate_simulation_params` 校验节点（2026-08-07，未发布） |
| `2026-08-09 12:23:55` | 9 | 08-09 中间版本 |
| `2026-08-09 12:37:46` | 9 | 08-09 最新版本（含缓存页面前端优化相关） |

> ⚠️ 注意：README/开发文档中"9 节点工作流"描述的是 **draft** 版本；**线上运行的是 published（8 节点）**。若需让校验节点生效，需在 Dify 中发布 draft。

## 如何重新导出

```bash
docker exec docker-db_postgres-1 psql -U postgres -d dify -t -A -c "
SELECT jsonb_pretty(jsonb_agg(jsonb_build_object(
  'workflow_id', id, 'type', type, 'version', version,
  'marked_name', marked_name, 'created_at', created_at, 'updated_at', updated_at,
  'graph', graph::jsonb, 'features', features::jsonb,
  'environment_variables', environment_variables::jsonb,
  'conversation_variables', conversation_variables::jsonb
) ORDER BY created_at))
FROM workflows WHERE app_id='0592503e-2eba-458f-bf19-128229441427';
" > app_0592503e_workflows.json
```

## 如何恢复（灾难场景）

1. 将 JSON 导入临时表或用 psql 逐条 `UPDATE workflows SET graph='<json>' WHERE id='...'`
2. 或直接在 Dify 管理后台用 graph 内容重建工作流（推荐人工核对后再操作）

## 安全

导出文件已做密钥检查：不含 `sk-` 开头的 API key、不含 64 位 hex token、无 Bearer 明文（工作流中密钥均通过环境变量引用，安全）。
