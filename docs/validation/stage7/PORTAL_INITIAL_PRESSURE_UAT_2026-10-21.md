# Portal 初压合同与真实仿真候选验收

日期：2026-10-21。范围：修复初压任务被错误要求驱动频率，验证一次真实 BYOK Portal 固定参考执行。
本记录为候选验证，不代表正式服务已升级，也不关闭 v1.0 其余发布门。

## 基线与修复

GitHub main 为 `269d81f5d3f527ea7f48d31cb657c2109646437c`，正式网关和 executor 为 `052cee7`。
候选从 main 独立工作树构建，使用正式镜像的既有依赖，未宣称全新联网依赖构建。
生产库通过 SQLite 在线备份形成隔离副本，候选关闭自动学习；原正式网关保持运行。

原始请求 `605687bd-56a3-4651-9e76-22f0825dad8f` 在技能检索前因缺少 source_frequency 被拒绝。
共享预检现按激励合同检查：驱动源仍要求频率；周期初压检查波数分辨率、周期匹配、显式边界和零 PML；
高斯初压检查空间宽度。有限值、CFL、网格与源/探针几何继续受检查。
通用后验参数等级复用该预检，不能因此升级到 Q3/Q4。

提取提示词明确 simulation_params 必须保留完整初压合同。对于结尾为单个完整 JSON 的周期初压声明，
直接保留用户参数及缺项用于预检与技能查询，不从技能库补值；模型仍执行参数提取，
且场景不一致时拒绝继续。固定参考的执行后分析热力图传入报告副本，原执行记录保持原样。

## 最终真实运行

冻结题：stage1 exact-01，`managed-local-periodic-air-axial.v1 / initial_pressure_2d`。

| 证据 | 结果 |
| --- | --- |
| DeepSeek BYOK | deepseek-chat，3/3 兼容预检通过 |
| 技能 | 9ae39960-97f0-4b40-ba6d-cc9643e6a54f，v1，原记录 Q4 |
| 最终执行 run_id | eb53db88-7c76-4f4f-84d3-4a17bfee1b16 |
| 执行 | exit_code=0，timed_out=false，4364.5ms |
| 回执 | executor-receipt.v1，matched=true |
| 固定参考后验 | completion=verified；local_reference_execution_verified 事件 |
| 执行代码摘要 | f6ad4216b55f8ef985a7529b573ed8df60a34be7d306594268a98b34c23517c6 |
| 独立物理门 | periodic-eigenmode.v1，12/12，最大相对误差 1.90630387930657e-6，严格小于 0.01 |
| 普通场分析 | verdict=normal，Q2，64×64，峰值 1.000000596 Pa，RMS 0.707106753 Pa |
| 展示 | Portal 热力图、normal 与参考交付；看板技能证据入口及技能页验证事件 |

最后流程仅调用 parameter_extraction、result_review、result_explanation，固定参考由原 executor 重新执行，
未把固定配方运行称为模型生成或模型采用。跨主机/真人使用认证仍为 false；Q2 场统计与独立固定物理证据分开报告。
此前同固定参考运行 `d14ada0f-a87a-43b6-8b8c-c404d810b17f` 为 4229.6ms，物理门和后验同样通过，
但报告热力图未接入，不能将其作为最终带图验收。

## 库与安全边界

隔离候选中 17/17 固定 profile 可检索；未归档 Q4 active 17 条、Q2 candidate 2 条，
另保留一条归档旧 Q4。自动学习关闭，没有新增技能或覆盖生产库。
execution_history.db、skill_store.db、error_cache.db 的 quick_check 均为 ok。

原 executor 未改：只读根、10001:10001、2 CPU、4GiB、256 PID、drop ALL、no-new-privileges、
internal 网络与 30 秒墙钟。候选网关同样只读、降权、drop ALL、512MiB、128 PID。
密钥只经 Portal 注册为临时 DeepSeek BYOK；截图和公开证据不包含密钥。

## 验证和限制

相关单测最终 **238 passed，14 个既有依赖警告**；包括 17 项固定合同、缺项/冲突/非有限值、
驱动源频率保留、真实共享预检接入、显式用户参数优先、失败输出不升级及报告不修改原记录。
Python 语法和 diff 检查通过。架构 JSON/HTML 同步，Archify showcase 9/9、0 errors、0 warnings；
四档桌面视口与明暗截图检查通过。

保留的失败与发现：

- 首轮候选仍失败（请求 `0eb4ae31-c6fb-4c86-b145-132b726b4c7d`），由旧提取结构丢失激励合同触发，随后补充完整声明处理。
- 沿用旧会话会先做需求合并；改写后的参数可走普通生成 Q2，本记录不把该路径称为固定参考命中。
- 本阶段发现 JavaScript 数值表示改变可能导致 no_applicable_skill；后续全流程验收已修复规范化与场景元数据分离，浏览器直接复制请求成功。见同目录 PORTAL_BROWSER_FULL_FLOW_UAT_2026-10-21.md。
- 本轮是候选验证。正式部署、完整模型矩阵、全新操作者 UAT 与 VM 回滚演练未在此关闭。
