# 阶段1：格式与草稿存储

日期：2026-10-23。候选实现，默认关闭、未部署。本阶段完成内部基础，不代表自动学习或手动导入产品入口已经可用。

## 已实现

- `app/skill_spec.py`：严格JSON与字段校验，拒绝重复键、非有限数、过深嵌套、未知字段、自授可信、越界和重叠地图；限制原文64000字节、代码20000字节、参数4096字节。
- `normalizer_v2`：统一数值与网格步长，不重叠材料区域按规范JSON排序。物理身份包含完整地图、源、结果合同和版本；内容摘要与代码摘要独立。原17项指纹不重算。
- `DraftStore`：按服务端actor隔离，手动创建、读取、分页、编辑与归档；创建幂等，编辑/归档使用revision比较。不可变版本与幂等记录受SQL约束和触发器保护。
- 默认启动仍为schema17。只有显式迁移才追加18；旧读取仅接受经过定义核对的完整扩展。任务、产物、证据等表预留结构，本阶段不运行任务。

## 首版支持范围

仅二维频域点源、absorbing_pml边界和steady_complex_pressure结果。介质为均匀或不重叠rectangle/half_space区域；必须完整声明背景、材料、吸收、源系数、网格与采样。网格32–1024，每波长至少4点，源和采样点不得位于PML。结构Schema中的其他物理类型尚不支持，解析时明确拒绝。

## 开启与迁移

先停止所有写入服务，确认数据库路径与备份目录；在仓库根目录运行以下命令，将占位路径替换为实际位置：

```bash
python -m scripts.skill_library.authoring status --database <skill-db>
python -m scripts.skill_library.authoring migrate --database <skill-db> --backup-dir <backup-directory>
```

工具要求已有健康schema17，先完成SQLite备份及完整性检查，再事务迁移；迁移失败回滚，重复迁移不重复建表。备份失败不得继续迁移。保存成功输出的备份路径，勿把失败时留下的文件作为恢复凭据。

内部调用需显式设置 `SKILL_AUTHORING_ENABLED=true`。这不会开放HTTP、创建可信技能或触发执行。默认false。内部API见 `app/skill_drafts.py`；可运行样例见 `tests/unit/test_skill_drafts.py`，作者身份必须由未来鉴权层提供。

同当前版本相同内容不新增版本；提交历史旧内容返回 `historical_content_conflict`，不静默回退。创建请求重放返回原始版本内容，同时返回当前真实head与归档状态。并发编辑只有预期revision一致的请求成功。

回滚时继续停止写入，保留现库及其WAL/SHM供诊断，用成功核验的迁移前备份恢复数据库，再使用原版本启动。回滚会丢弃备份后产生的草稿，不能宣称在线无损回滚；不得只删除schema18版本标记。

## 边界与验证

无新HTTP/UI、后台worker、自动候选提取或新Q4接纳；草稿不执行、不进入可信检索。原executor隔离和物理门保持。后续依次实现持久验证/结果、受控验证与复用、自动学习、手动入口。

重点验证覆盖解析负例、物理身份、作者隔离、幂等/CAS并发、不可变历史、防替换、伪完整schema拒绝、事务失败回滚及真实备份。全量回归与架构检查结果见CHANGELOG。
