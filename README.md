# AcouAgent

### 用自然语言完成声学仿真，让结果有据可查

AcouAgent 是一款面向声学学习、科研原型验证与教学演示的仿真智能体。
在浏览器中描述仿真需求，系统会提取参数、检索适用技能、生成并运行 jwave 代码，
展示声场热力图、数值摘要和结果解读。你可以继续对话修改参数，也可以回到看板查看代码与执行记录。

**本地部署 · 自带模型 · 技能复用 · 物理校验 · 执行可追溯**

[快速开始](#快速开始) · [队友安装与更新](docs/TEAM_QUICKSTART.md) · [首次仿真](docs/FIRST_SIMULATION.md) · [当前状态](docs/CURRENT_STATUS.md)

## 可以用它做什么

| 功能 | 你能完成的操作 |
| --- | --- |
| 自然语言仿真 | 描述介质、声源和计算条件，由智能体组织仿真；在同一会话继续调整参数 |
| 声场与结果展示 | 查看声场热力图、峰值、空间 RMS、执行状态和分析报告 |
| 成功技能库 | 浏览经过验证的固定配置，查看适用参数与证据，在新任务中检索和复用 |
| 自动纠错与经验缓存 | 追踪执行报错和修正过程，在后续适用任务中使用已有纠错经验 |
| 执行看板 | 搜索历史任务，查看代码与报告，对比、导出记录，并在授权后重新执行 |
| 自带模型 | 在门户接入自己的 OpenAI-compatible 模型，或用环境变量配置长期模型 profile |

默认部署使用项目内的本地编排，无需安装 Dify；已有 Dify 的团队也可以启用兼容工作流。
仿真在本机 Docker 沙箱内执行，模型请求发往你配置的模型服务。

## 从问题到结果

1. **描述问题**：输入介质、声源、频率、网格和结束时间等条件；缺少必要参数时补充说明。
2. **检索与求解**：系统先检查参数并查找技能，再生成代码或交付适用的固定参考，送入隔离执行器。
3. **检查结果**：执行反馈和确定性质量门共同检查结果，失败时在限额内修正；报告展示实际输出与检查结论。
4. **继续探索**：在对话中调整参数，并用看板中的 `run_id` 回查本次运行。

退出码为 0 不等于物理正确。技能的认证只对其精确配置生效，改变材料、频率或网格后需要重新验证。

## 技能库覆盖

当前内置 **17 项精确配置**，首次安装会在本机真实执行并验证后建立技能记录：

| 类型 | 覆盖内容 |
| --- | --- |
| 平面波初压 | 均匀介质中的高斯初压传播 |
| 界面反射与透射 | 两均匀半空间的法向入射 |
| 周期模态（15 项） | 水、空气、软组织参数下的轴向、斜向、正交、谐波与拍频配置 |

这 17 项是已验证的固定技能范围。其他声学任务由模型生成后尝试求解，其支持情况和精度需要按具体问题核验。
普通成功运行最多积累为候选，不能自动当作可信技能；发行配置默认关闭自动学习。

## 快速开始

需要 **Git、已启动的 Docker Compose**，以及自然语言仿真使用的模型 API 地址、模型名和 API Key。
Windows 使用 Docker Desktop 的 **Linux 容器模式**；预构建求解器镜像当前面向 **linux/amd64**。
首次安装需联网下载镜像和依赖。

### Windows PowerShell

```powershell
git clone https://github.com/MWXrookie/AcouAgent.git
Set-Location AcouAgent
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

### Linux / WSL（amd64）

```bash
git clone https://github.com/MWXrookie/AcouAgent.git
cd AcouAgent
bash scripts/install.sh
```

安装器生成本地凭据、构建网关，并等待 17 项固定技能自举完成。看到 `AcouAgent is ready` 后：

1. 打开 **[http://127.0.0.1:8001/portal](http://127.0.0.1:8001/portal)**。
2. 在“模型设置”填写自己的 API 地址、模型名和 API Key，点击“测试连接”。
3. 按 [第一次自然语言仿真](docs/FIRST_SIMULATION.md) 完成首题并保存 `run_id`。

临时模型配置默认 12 小时过期，重启网关后需要重新填写。
长期配置、安装排错和数据备份见 [队友安装与更新](docs/TEAM_QUICKSTART.md) 与 [部署详解](deploy/README.md)。

### 已有安装更新到 main

先保存自己的未提交改动；需要保留的数据请先备份。仓库改名前已有的本地目录可以继续使用。

```powershell
git remote set-url origin https://github.com/MWXrookie/AcouAgent.git
git switch main
git pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

Linux / WSL 将最后一行换成 `bash scripts/install.sh`。
**拉取代码后还要运行安装器重建容器**，运行中的旧容器不会随 `git pull` 自动更新。
已有 `.env` 和持久数据卷会保留；不要通过删除数据卷来更新版本。

## 页面入口

| 页面 | 地址 |
| --- | --- |
| 仿真对话 | `/portal` |
| 技能库 | `/skills` |
| 执行看板 | `/dashboard` |
| 纠错缓存 | `/cache` |

新安装使用自己的本地数据库，不会包含其他人的执行历史或纠错缓存。

## 当前版本

当前为 **0.1.0 测试版**，适合团队试用、课程演示与有参考答案的算例验证。
已建立自动化回归、干净安装检查、固定技能物理校验，以及门户技能复用、纠错缓存和看板的真实交互验收记录。

正式 v1.0 仍需完成多模型固定题集、本地与 Dify 同题对照、新操作者真实 BYOK 首题及完整升级回滚验收。
复杂三维模型、论文算例和任意自然语言需求不保证一次求解成功；请结合原始参数、数值答案和误差标准验证。
最新证据和未关闭事项统一见 [当前状态](docs/CURRENT_STATUS.md)。

## 进一步了解

- [队友安装与更新](docs/TEAM_QUICKSTART.md)：首次部署、更新 main、环境检查与常见问题
- [模型配置](docs/MODEL_PROFILES.md)：临时 BYOK、长期 profile 和模型服务设置
- [技术与部署详解](docs/TECHNICAL_GUIDE.md)：安全边界、MCP 工具、存储、技能质量与 Dify 接线
- [变更日志](docs/CHANGELOG.md)：功能演进与验证记录
- [开发与提交规范](AGENTS.md)：面向项目维护者的协作入口

AcouAgent 基于 jwave、FastMCP、SQLite 和可选 Dify 构建，借鉴技能检索与反馈学习方法，
不宣称原创声学求解算法。
