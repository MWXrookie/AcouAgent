# 阶段2：持久试运行与完整结果

日期：2026-10-23。内部候选实现，默认关闭、尚未部署到生产。依赖阶段1及显式schema18，不新增数据库迁移。

## 已实现范围

- SQLite持久队列、创建幂等、作者隔离、版本冻结、短事务领取、owner/fence/revision保护和5秒心跳。并发1、排队100、租约120秒、任务从创建起总预算180秒；executor每次仍30秒。
- 派发前持久保存独立attempt/run/nonce及代码、请求、参数、运行时摘要。执行调用与文件解析在写事务外；只执行一次，无付费模型调用。
- 过期prepared有明确未派发事实，允许保留旧终态后创建新attempt；dispatching或unknown进入needs_reconciliation并继续占并发，绝不因租约过期盲目重跑。
- queued取消直接终结；running取消保留名额，直到确认未派发或可信执行终态。取消与完成串行竞争，取消后的结果不接纳。新worker可读取原唯一历史并只补结果。
- 历史只读核对唯一run与nonce，重算回执、请求、实际代码、参数、stdout/stderr和预先登记的runtime摘要；忽略持久JSON自述的matched。重复、篡改、过大扫描或缺失均保持不确定。
- 完整complex64/complex128二维NPY产物及绑定manifest按内容寻址保存；读回重核摘要、合同和来源。幅值数组、峰值、RMS取自同一完整复数场，单位为numerical_source_normalization，不冒称Pa。
- 限制stdout1MiB、单资产4MiB、总资产256MiB（含暂存孤儿）、目录/索引扫描4096项；拒绝pickle、压缩炸弹、非有限数、错轴/形状、符号链接和越界路径。文件先写临时并fsync、原子改名，再登记索引；失败不会登记半文件成功。显式审计缺失/损坏/孤儿，显式清理只处理24小时以上未索引普通文件，保留全部已索引资产。

## 状态含义

当前只开放内部 `intent=trial`。服务端固定trial_only政策按运行时摘要注册，用户不能选择验证器、参考或阈值。真实试运行成功的状态为 `rejected / trial_completed_ungraded`：执行和结果导出已完成，科学认证没有实施。失败为failed，取消为cancelled，unknown为needs_reconciliation。这些状态不能转换成可信技能、validated或admitted。

尚未实现HTTP/UI、自动后台启动、独立物理比较、跨库接纳outbox、自动学习事件及BYOK模型作业。时域/全时最大值等输出仍不支持，不把它们解释为稳态复压力。BYOK凭据恢复留给未来实际调用模型的阶段；本阶段不保存密钥或模型profile。

## 运维入口

`python -m scripts.skill_library.validation --help` 列出内部操作。操作员须先完成阶段1备份迁移，并在该Python进程环境显式设置 `SKILL_AUTHORING_ENABLED=true` 和 `SKILL_VALIDATION_ENABLED=true`；不开启原学习或接纳开关。运行时摘要必须由操作员核对executor来源及版本后提供，不能从用户stdout学习。

```bash
python -m scripts.skill_library.validation install-profile --database <skill-db> --assets <dedicated-root> --runtime-sha256 <reviewed-runtime-sha>
python -m scripts.skill_library.validation enqueue --database <skill-db> --assets <dedicated-root> --runtime-sha256 <reviewed-runtime-sha> --draft-id <uuid> --expected-revision <revision> --idempotency-key <key> --actor <server-actor>
python -m scripts.skill_library.validation run-once --database <skill-db> --assets <dedicated-root> --runtime-sha256 <reviewed-runtime-sha>
```

run-once一次只处理一个任务，不自动循环启动。status/cancel/reconcile/result使用同一参数和job-id；cancel还需expected-revision。recover仅修复过期状态，reconcile只读原历史，不派发。result导出同源幅值和指标；CLI摘要不打印草稿代码。正式环境沿现有dashboard.DB_PATH历史库运行，测试使用隔离历史库。

unknown或取消中任务可能长期阻塞唯一名额：缺少可信终态时这是有意保守行为，不能手改为queued。profile退役或队头运行时与worker不符时不自动跳过，操作员须取消或由匹配worker处理。结果磁盘失败可留下未索引文件，先audit-assets，再按24小时宽限cleanup-orphans；不删除DB索引伪装修复。

## 实跑证据

B01/B02/B03通过当前worker、真实原executor、临时history和schema18库完成链路，各一次派发，无参考资产挂入求解代码，无生产三库写入、无付费模型。原沙箱2CPU/4GiB、只读、10001、cap-drop ALL、no-new-privileges和隔离网络保持。

|实例|耗时ms|stdout字节|完整shape|峰值|RMS|
|---|---:|---:|---|---:|---:|
|B01|5832.4|490181|128×128|0.6314084842|0.0583491011|
|B02|5774.0|514364|128×128|0.6250008881|0.0590513585|
|B03|5878.8|510716|128×128|0.4762814536|0.0572079674|

上述值是本次完整输出派生的显示量，未在本阶段重新作论文误差认证。运行时SHA256为fbf71341fa128e39deade7f61dec6b37ba63d0309697a083aa132a93831ae0e8。临时测试库/数值文件随测试容器清除；摘要日志作为本地验收产物保留，不能作为未来技能认证来源。恢复负例和正常集成覆盖实际持久库与history，dispatcher替身用于故障注入。
