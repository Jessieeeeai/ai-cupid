"""入口:同一进程跑 REST API(网页/TG 用)+ MCP streamable HTTP(AI 用)。

- 网页入口:    http://<host>/
- REST API:    http://<host>/api/...
- MCP 地址:    http://<host>/mcp   ← 用户在 Claude/ChatGPT 里填这个
"""
import contextlib

from fastapi import FastAPI

from app.api import router
from app.db import init_db
from app.mcp_server import build_mcp


def create_app() -> FastAPI:
    mcp = build_mcp()
    mcp.settings.streamable_http_path = "/mcp"  # 精确匹配 /mcp,无重定向(部分客户端不跟随307)
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="hongniang", lifespan=lifespan)
    app.include_router(router)
    # 挂在根:已注册的 API 路由优先匹配,未命中的落到 mcp_app(它只响应 /mcp)
    app.mount("/", mcp_app)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
