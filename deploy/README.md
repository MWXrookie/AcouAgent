# 网关部署

新环境使用根目录 compose.yaml。默认技能开关关闭；人工基础试用配置见根 README。

已有生产环境仅更新网关：
```bash
DIFY_MCP_IMAGE=<经过验证的镜像> docker compose   -f compose.yaml -f deploy/compose.gateway.yaml   up -d --no-deps --no-build dify-mcp
```

此覆盖文件不修改 executor，也不提供本机镜像或私有网络配置。更新之前记录原网关镜像，备份持久库，更新后检查 health、技能状态及 executor Id/Image/StartedAt；失败使用原镜像回滚。数据库迁移独立审批与执行，不随部署脚本隐式迁移。

本机 compose.deploy.yaml、compose.local-basic.yaml 和验收日志不提交。历史自动部署辅助曾按旧 6 工具计数，并读取本机 env，现已归档，不作为当前部署入口。现有部署的开关配置应继续使用其完整覆盖文件，不能用通用覆盖替代。
