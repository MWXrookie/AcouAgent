"""FastMCP 网关入口（薄壳）：装配配置、实例化 MCP、注册各业务模块。

模块职责（拆分后）：
- config.py        环境配置与校验
- cache_store.py   纠错经验缓存（SQLite）
- execution.py     代码清理/沙箱执行/报告生成
- llm.py           BYOK 模型自动纠错
- tools.py         MCP 工具集（6 个）
- portal.py        Web 门户路由
- analysis.py      结果分析（T-007 占位，落点在此）

入口保持 `python /app/server_safe.py`（Dockerfile APP_FILE 不变）。
"""

import asyncio
from contextlib import asynccontextmanager
from fastmcp import FastMCP

try:
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
except ImportError:
    from fastmcp.server.auth import StaticTokenVerifier

from app import config
from app.analysis import register as register_analysis
from app.dashboard import init_db
from app.portal import register as register_portal
from app.tools import register as register_tools

config.validate()

auth = StaticTokenVerifier(
    tokens={config.MCP_AUTH_TOKEN: {"client_id": "dify", "scopes": ["mcp:tools"]}},
    required_scopes=["mcp:tools"],
)
@asynccontextmanager
async def _learning_lifespan(_server):
    from app.skill_auto_worker import start_worker
    from app.skill_manual_worker import start_worker as start_manual_worker
    worker = start_worker()
    manual_worker = start_manual_worker()
    try:
        yield {}
    finally:
        if worker is not None:
            await asyncio.to_thread(worker.stop)
        if manual_worker is not None:
            await asyncio.to_thread(manual_worker.stop)


mcp = FastMCP("Dify JWave Tools", auth=auth, lifespan=_learning_lifespan)

register_tools(mcp)
register_portal(mcp)
register_analysis(mcp)


if __name__ == "__main__":
    init_db()
    mcp.run(transport="http", host="0.0.0.0", port=8001, path="/mcp", stateless_http=True)
