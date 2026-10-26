# v1.0.0 发布检查表

> 2026-10-26：模型合同、main合并及正式/演示升降级交付子门已通过，见[交付报告](PRODUCTION_MODEL_COMPAT_2026-10-26.md)。以下v1.0检查表仍按最终正式候选独立签署，不能将本次0.1.0技术验收自动勾成完整发布。真人冷启动、独立Luna复核和Owner授权仍开放。


状态：**草案，禁止据此直接打标签**

更新：2026-10-15

实际 `v1.0.0` 标签和 GitHub Release 需要项目 Owner 单独授权。以下检查必须绑定同一个候选提交，失败项不得用其他提交的结果代替。

## 1. 冻结候选

- [ ] 候选提交已合并到 `main`，工作区无未提交文件。
- [ ] 记录候选完整 SHA、PR、合并提交和发布前回退分支。
- [ ] `Release container images` 与 `Clean install smoke` 在该 SHA 上均成功。
- [ ] 下载 Actions 产物 `release-manifest-<sha>`，运行：

  ```bash
  python3 scripts/validation/release_manifest.py --verify release-manifest.json
  ```

- [ ] 清单的 `release_files_ready=true`，无缺失文件或禁止跟踪的运行产物。
- [ ] 清单版本与拟发布版本一致。当前 `pyproject.toml` 仍为 `0.1.0`，因此此项尚未通过。

## 2. 用户与模型验收

- [ ] `python3 -m scripts.diagnostics.acouagent_doctor --mode runtime --json` 无失败项，脱敏报告已保存。
- [ ] 候选模型 profile 的门户“测试连接”三项基础检查通过。
- [ ] 新操作者从全新检出开始，30 分钟内按 `docs/FIRST_SIMULATION.md` 完成标准首题。
- [ ] 保存 `first_simulation_acceptance.py` 的脱敏回执和 run id。
- [ ] 第二个模型 profile 对固定题集完成关闭式验收。
- [ ] 本地编排与 Dify 的批量双跑完成，差异有解释且无合同回退。
  执行和脱敏证据合同见 [MODEL_MATRIX.md](MODEL_MATRIX.md)。
- [ ] 现有 Luna 会话在最终候选提交上完成全量测试，记录 collected/passed/skipped/warnings、耗时和退出码。

## 3. 数据备份

备份前先确认 `/app/data` 数据卷有足够空间。备份使用 SQLite online backup，不复制 WAL 中间态，也不覆盖既有文件：

```bash
docker compose exec -T dify-mcp python -c "from app.skill_store import SkillStore; SkillStore('/app/data/skill_store.db').backup_to('/app/data/backups/pre-v1.0.0.sqlite')"
docker compose exec -T dify-mcp python -m scripts.skill_library.audit_report --db /app/data/backups/pre-v1.0.0.sqlite --json /tmp/pre-v1-audit.json --markdown /tmp/pre-v1-audit.md
```

- [ ] 备份文件为新路径，原数据库未被替换。
- [ ] 备份审计 `healthy=true`，报告摘要已记录。
- [ ] `.env` 单独保存在受控位置，不进入 Git、Actions 产物或发布附件。

## 4. 升级演练

- [ ] 在可回退环境执行 `git pull --ff-only` 和 `docker compose up -d --build`。
- [ ] `/health`、门户、看板和技能库页面可达。
- [ ] 技能自举幂等，既有反馈、生命周期、版本与来源关系保持。
- [ ] executor 继续使用只读根、cap-drop ALL、no-new-privileges 和 internal 网络。
- [ ] 标准首题成功，技能库审计仍健康。

## 5. 回滚演练

回滚优先切回发布前 Git 分支并重建网关。只有 schema 或数据损坏时才从备份恢复数据库；恢复前停止网关以避免并发写入。

```bash
git switch <release-predecessor-or-rollback-branch>
docker compose up -d --build
```

- [ ] 回退提交可检出，Compose 可解析。
- [ ] 回退后 `/health` 正常，executor 安全边界未改变。
- [ ] 如演练数据库恢复，先 `docker compose stop dify-mcp skill-bootstrap`，保留故障库，再把备份恢复到数据卷，最后启动并审计。
- [ ] 演练记录包含开始/结束时间、提交 SHA、备份文件摘要和验证结果。

## 6. 发布与发布后观察

- [ ] Owner 已明确授权创建 `v1.0.0` 标签和 GitHub Release。
- [ ] 标签指向已验收的完整 SHA，没有在 Actions 后追加提交。
- [ ] Release 附带变更说明、安装入口、清单摘要、已知限制和回滚入口。
- [ ] 从 Release 页面重新下载源码，在独立目录复核安装与首题。
- [ ] 发布后观察健康、失败率、技能反馈和审计结果；异常时按已演练路径回滚。
