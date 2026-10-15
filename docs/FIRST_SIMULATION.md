# 30 分钟完成第一次自然语言仿真

本指南面向一台只安装了 Git 和 Docker Compose 的新机器。目标是得到一个可回查的 `run_id`，并确认本地编排、模型 profile、jwave executor 和确定性质量门都参与了这次运行。

## 1. 下载并准备配置

```bash
git clone https://github.com/MWXrookie/dify-mcp-server.git
cd dify-mcp-server
bash scripts/install.sh --prepare-only
```

Windows PowerShell 使用：

```powershell
git clone https://github.com/MWXrookie/dify-mcp-server.git
Set-Location dify-mcp-server
powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -PrepareOnly
```

安装器只在 `.env` 不存在时生成两个不同的本地 token，不会覆盖已有配置，也不会打印 token。

## 2. 选择 BYOK 方式

最简单的方式是先启动，然后在门户“模型设置”中填写 OpenAI 兼容 API 地址、模型名和自己的 API Key。该配置只在网关内存中存在，默认 12 小时过期。

需要运行命令行验收器或让配置在重启后仍可用时，在 `.env` 填写：

```dotenv
MODEL_BASE_URL=<你的 OpenAI 兼容 API 地址>
MODEL_NAME=<模型 ID>
MODEL_API_KEY=<你的 API Key>
```

多 profile、本地 Ollama/vLLM 和临时密钥生命周期见 [BYOK 模型配置](MODEL_PROFILES.md)。不要把密钥直接写进 `MODEL_PROFILES_JSON`。

## 3. 启动

Linux、macOS 或 WSL：

```bash
bash scripts/install.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

安装器会等待 `/health=ok` 和 17 项固定技能全部可检索。完成后打开 [http://127.0.0.1:8001/portal](http://127.0.0.1:8001/portal)。

## 4. 执行标准首题并保存回执

门户临时 BYOK 用户直接输入“仿真 2 MHz 超声在水中传播”，完成后从报告或执行看板保存 `run_id`。

下面的无界面验收器需要第 2 步的环境变量 profile。它不保存报告、代码、提示词或密钥，只保存模型标识、质量等级、run id 和执行状态：

```bash
docker exec dify-mcp python /app/scripts/validation/first_simulation_acceptance.py \
  --base-url http://127.0.0.1:8001 \
  --output /app/data/first-simulation-acceptance.json
docker cp dify-mcp:/app/data/first-simulation-acceptance.json .
```

PowerShell 可使用同样两条 `docker` 命令。成功回执必须同时满足：

- `accepted=true`
- `workflow_status=succeeded`
- `orchestration_backend=local`
- `quality_level` 为 Q1–Q4
- `run_id` 是有效 UUID
- 对应执行记录 `exit_code=0` 且 `timed_out=false`

Q1/Q2 只表示当前确定性门通过，不等于完整物理真值证明。固定参考在精确匹配并重新执行通过时可得到更高等级。

## 5. 回查

- 门户报告：`http://127.0.0.1:8001/portal`
- 执行记录：`http://127.0.0.1:8001/dashboard`
- 技能库：`http://127.0.0.1:8001/skills`

如果验收器失败，它只输出稳定错误码。先运行 `docker compose ps -a`，再按需查看 `docker compose logs dify-mcp` 或 `docker compose logs skill-bootstrap`。不要把 `.env`、完整日志或模型响应提交到 Git。
