# AcouAgent 部署入口

## 新环境

根 Compose 使用公开 GHCR 预构建镜像，不要求本机已有 Dify 网络：

```bash
cp .env.example .env
# 为 MCP_AUTH_TOKEN 和 EXECUTOR_SHARED_TOKEN 生成两个不同的 32+ 字符随机值
docker compose pull
docker compose up -d
docker compose ps -a
```

首次启动包含一次性 `skill-bootstrap`。它会在新数据卷中通过现有 executor 逐项执行 17 个固定配方，并且只有通过物理门的结果才写入 Q4。确认：

```bash
docker compose logs skill-bootstrap
docker exec dify-mcp python -m scripts.skill_library.local_basic status-all
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/skills
```

`skill-bootstrap` 正常完成后状态为 `Exited (0)`。它是幂等任务，不需要常驻。自动学习仍由 `SKILL_LEARNING_ENABLED` 独立控制，发行默认关闭。

## 已有克隆升级

`git pull` 不会覆盖现有 `.env`。如果旧环境曾关闭技能开关，请把以下值写入自己的 `.env`：

```dotenv
SKILL_LIBRARY_ENABLED=true
SKILL_RETRIEVAL_ENABLED=true
SKILL_INJECTION_ENABLED=true
SKILL_MANUAL_INTAKE_ENABLED=true
SKILL_BOOTSTRAP_ALL_FIXED=true
SKILL_LEARNING_ENABLED=false
```

然后拉取新镜像、重建网关配置并显式运行一次自举：

```bash
docker compose pull
docker compose up -d --force-recreate jwave-executor dify-mcp
docker compose run --rm skill-bootstrap
docker exec dify-mcp python -m scripts.skill_library.local_basic status-all
```

最后一条只有 17 项全部可检索时才返回 0。

## 连接已有 Dify

```bash
docker compose -f compose.yaml -f deploy/compose.dify.yaml up -d
```

通过 `DIFY_NETWORK_NAME` 指定外部网络，通过 `DIFY_BASE_URL` 指定 API 根地址。当前门户完整自然语言聊天仍需此兼容层；健康、MCP、技能库和看板可在基础栈独立运行。

## 源码构建回退

```bash
docker compose -f compose.yaml -f deploy/compose.build.yaml up -d --build
```

executor 科学栈较大，弱网环境优先使用 GHCR 镜像。

## 既有生产环境

只更新网关时使用：

```bash
DIFY_MCP_IMAGE=<经过验证的镜像> docker compose \
  -f compose.yaml -f deploy/compose.gateway.yaml \
  up -d --no-deps --no-build dify-mcp
```

更新前记录原网关镜像并备份持久库；更新后检查 health、技能状态和 executor Id/Image/StartedAt。数据库迁移独立执行，不随网关更新隐式迁移。
