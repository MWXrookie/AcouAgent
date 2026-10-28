# Windows桌面候选阶段验收

日期：2026-10-28。候选版本：0.2.0-alpha.1。**安装与独立窗口通过；完整桌面仿真与发布仍未通过。**

| 验证 | 当前证据与结论 |
|---|---|
| Windows实际安装 | NSIS当前用户安装、安装后真实窗口、重装保持凭据文件哈希、卸载保持凭据和标记：4项通过 |
| 独立桌面窗口 | 源码、打包EXE、实际安装EXE各10项：无renderer Node权限、有限预加载接口、真实隔离配置、缺Docker阻断、系统加密凭据、无JS异常 |
| 运行管理回归 | 17项通过，覆盖外部Docker拒绝、异主容器/卷拒绝、只停止自有服务、并发防重、脱敏、破损配置/凭据保留、17技能就绪门及失败阻断、Docker默认安装目录发现 |
| GitHub干净环境 | 552bc82的桌面CI成功：Windows构建、实际安装/窗口/重装/卸载通过；Linux实际固定镜像17技能准备、沙箱/网络/端口检查、停止再启动与数据保留8项通过。发行镜像CI也成功 |
| 供应链 | Electron官方SHA256核验，Docker安装器官方SHA256与Docker Inc有效签名核验；npm审计0已知漏洞。候选安装包未签名 |
| 运行时身份 | 网关固定d3ab537发行镜像digest，执行器固定digest；不从main下载可变脚本；队友之后的main尚未纳入 |
| 架构 | 后端和桌面JSON/HTML已同步，showcase验证、交付及四种视口检查通过；人工查看桌面浅色与后端深色代表图，无遮挡 |
| 未完成 | Windows实际容器运行、17技能自举和数值求解、技能扩展向导、备份/恢复、跨版本数据升降级、新操作者首题、最终独立复核及GitHub发布 |

安装测试使用专属临时程序目录；确认无原有AcouAgent安装与默认用户数据后执行。卸载后的测试用户数据移入本机验收输出目录，不触碰原Web数据库和虚拟机服务。测试证明同版本重装保留桌面凭据，**尚不能证明后端数据库跨版本升级正确**。

本机证据位于仓库忽略的 `outputs/desktop-release-20261028/`，包括安装器报告与真实截图；源码窗口最新证据在 `outputs/desktop-smoke/`。这些包含本机路径与加密测试配置的产物不进入源码或发布包。

首次远端运行的失败记录保留：Electron运行文件未显式安装、tmpfs数组解析错误均已修复，再跑552bc82通过。本机Docker随后安装并确认Linux amd64；新安装时凭据助手未进入旧PATH的问题，已限定在应用子进程环境修复并通过回归，Windows真实运行继续验收。

远端证据：[桌面CI](https://github.com/MWXrookie/AcouAgent/actions/runs/40698343996)、[发行CI](https://github.com/MWXrookie/AcouAgent/actions/runs/40698344186)。Linux上的物理技能准备不替代Windows自然语言端到端验收。

可重复检查：`cd desktop` 后执行 `npm ci`、`npm test`、`npm run dist`；在无现有安装的Windows测试账号执行 `scripts/desktop/installer_smoke.ps1`。缺运行时窗口检查仅用于Docker尚未可用的测试机。Linux实际运行脚本仅在专属CI环境执行，不操作已有桌面项目。
