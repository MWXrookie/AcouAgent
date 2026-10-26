# 启用手动技能与受控自动学习

适用：已完成17项固定技能安装的版本。扩展能力需按下列步骤单独启用，不随代码更新自动开启。

扩展默认关闭。准备只增加schema18及三个独立参考profile，不接纳任何技能。执行器仍用原镜像/内部网络/资源限制，不接收参考数组。

## 准备

先按队友安装说明更新容器，确认 `local_basic status-all` 的17项ready。保留本地 `.env` 副本，暂停网关业务，再执行一次显式准备：

```sh
docker compose stop dify-mcp
docker compose run --rm --no-deps -e SKILL_AUTHORING_ENABLED=true -e SKILL_VALIDATION_ENABLED=true -e SKILL_GENERAL_ADMISSION_ENABLED=true dify-mcp python -m scripts.skill_library.prepare_general --backup-dir /app/data/skill-backups --download-references
```

同一命令可在PowerShell运行。它在线备份已存在的技能、执行和纠错三库并核对完整性；不存在的可选缓存库不创建。来源数据全部校验通过、实际运行时身份匹配后才迁移/安装profile。

下载限定作者固定提交 `ucl-bug/jwave-data@600e50c8b4ac71f0bc344098320a1e30b80ade00` 的三个MAT文件，先校验冻结SHA，再有界转换128×128完整复数场，并再次核对既定NPY SHA。原始内容CDN网络失败时只回退一次到同提交的GitHub Contents API，不跟随重定向，不换参考或摘要。不安装新Python依赖，也不在本仓库重新分发作者数据。来源、精确参数和阈值见 [VALIDATION_PROFILES.md](VALIDATION_PROFILES.md)。有已审阅离线NPY目录时可替换为 `--reference-directory <容器内已挂载目录>`，同样必须逐字节匹配。

命令成功输出 `prepared=true`、`schema=18`、三个profile和实际 `runtime_sha256`。没有成功输出就不要启用；保留备份并排查网络、磁盘、参考或运行时问题。准备命令不修改 `.env` 或启动后台学习。

## 开启

替换本地 `.env` 中对应行，不要追加重复键：

```dotenv
ORCHESTRATION_BACKEND=local
PORTAL_ADMIN_TOKEN=<独立随机管理员令牌，至少32字符>
SKILL_AUTHORING_ENABLED=true
SKILL_VALIDATION_ENABLED=true
SKILL_GENERAL_ADMISSION_ENABLED=true
SKILL_GENERAL_RUNTIME_SHA256=<准备命令的runtime_sha256>
SKILL_GENERAL_ASSET_ROOT=/app/data/skill_assets
```

手动编辑/验证不要求学习开启；要自动学习再显式设置 `SKILL_LEARNING_ENABLED=true`。保留已有LIBRARY/RETRIEVAL/INJECTION开关为true。无需永久保留临时参考下载目录：受审参考已进入受控数据卷，执行器仍无法读取。

管理员令牌与MCP/执行器令牌分开；未配置时不能保存草稿或管理技能。自动学习链在local编排中验收，Dify兼容工作流保留原有行为，不会自动接管这条新链路。

```sh
docker compose up -d --no-deps --force-recreate dify-mcp
docker compose exec dify-mcp python -m scripts.skill_library.authoring status --database /app/data/skill_store.db
```

打开 `/skills/authoring`，使用自己的管理员令牌添加/导入。完整流程：保存→预览并确认代码差异→验证→查看本次结果→显式接纳；新自然语言任务必须重新实际执行。三项独立profile只认证精确条件下的稳态幅值，不认证相位、其他参数范围或任意程序。

## 反馈、停用与回退

对真实关联运行提交负反馈后，通用技能立即停止交付并显示待复核。自动学习不能恢复它。管理员在技能详情请求重新验证，再提交请求之后的新手动验证任务；只有新独立证据通过才可重新接纳。历史反馈保留，旧job/旧证明不能恢复。归档恢复同样不直接恢复信任。

关闭LEARNING只停止新增学习；关闭AUTHORING/VALIDATION/GENERAL可停止扩展能力，但不会将18变回17。回退旧代码须停止网关，先另行保全新增数据，再使用准备时同一时间点的三库备份和旧镜像恢复；不能删除迁移版本行伪装降级。不要执行 `docker compose down -v`。

持久卷、重启与旧镜像恢复的验收见 [STAGE6_IMPLEMENTATION.md](STAGE6_IMPLEMENTATION.md)；完整v1.0开放门仍见 [CURRENT_STATUS.md](../CURRENT_STATUS.md)。
