# 阶段七：产品化 v1.0

日期：2026-10-14

## 当前切片

阶段七从“干净安装持续验收门”开始。GitHub Actions 在全新 Ubuntu runner 上只使用仓库文件和公开 executor 镜像，执行根 Compose 配置解析、当前源码网关构建、完整栈启动、网关健康检查、17 项固定技能自举、技能库存核对、源码与容器文件哈希核对，以及 executor 内部网络和容器降权检查。任务无论成功或失败都删除容器、网络和数据卷。

该门不配置模型密钥，因此不调用付费模型，也不把无模型的健康与固定技能验证写成自然语言首题通过。阶段六已有真实 DeepSeek Chat 单题证据；阶段七仍需一键安装入口、新人按文档完成首次仿真、技能库审计报告和 v1.0.0 发布材料。

## F2 一键安装入口

`scripts/install.sh` 与 `scripts/install.ps1` 覆盖常见 Docker Compose 宿主。它们只在 `.env` 不存在时从模板创建配置，生成两个不同的 64 位十六进制本地 token，已有配置一律原样保留；随后解析 Compose、拉取 executor、从当前源码构建并启动网关、等待健康与 17 项自举并复核库存。`prepare-only` 模式允许用户在启动前填写服务端模型凭据。脚本不回显 token 或模型密钥。

## F3 新人首次仿真准备

`docs/FIRST_SIMULATION.md` 给出下载、prepare-only、服务端模型 profile、启动、标准首题和回查顺序。`scripts/validation/first_simulation_acceptance.py` 通过公开 profile 接口选择实际 profile，执行固定首题，再用 dashboard 核对同一 run id 的 exit code 和超时状态；回执排除报告、代码、提示词和凭据。真实新操作者 UAT 仍需在有模型凭据的独立环境执行，准备完成不计为出口通过。

## 出口条件

1. 干净 runner 使用 `docker compose up -d --build` 启动的网关源码与 Git head 一致。
2. `skill-bootstrap` 正常退出，17 项固定 profile 全部为可检索状态。
3. gateway 与 executor 保持只读根文件系统、drop all capabilities 和 no-new-privileges。
4. executor 网络保持 internal。
5. 失败时保留服务日志，结束时不遗留容器、网络或数据卷。

## 后续

- 在新的操作者环境完成 30 分钟内首次自然语言仿真 UAT。
- 生成技能库存与验证证据审计报告。
- 准备 v1.0.0 发布检查表、回滚说明和发行说明；实际打标签仍需发布授权。
