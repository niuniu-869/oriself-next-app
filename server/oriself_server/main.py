"""
FastAPI 入口。

启动：
    uvicorn oriself_server.main:app --reload

环境变量：
    ORISELF_PROVIDER    默认 provider（qwen / deepseek / kimi / openai / mock）
    ORISELF_DB_PATH     sqlite 文件路径（默认 ./data/oriself.db）
    ORISELF_{provider}_API_KEY   各 provider 的 API key
    ORISELF_CORS_ORIGINS  逗号分隔的允许 origin（生产环境收紧）
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .database import init_db
from .routes.feedback import router as feedback_router
from .routes.issues import router as issues_router
from .routes.letters import router as letters_router


def _parse_cors_origins() -> list[str]:
    raw = os.environ.get("ORISELF_CORS_ORIGINS", "")
    if not raw:
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app() -> FastAPI:
    app = FastAPI(
        title="OriSelf Next · API",
        description="产品即 skill 的对话式人格测试 · 后端 API",
        version=__version__,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(letters_router)
    app.include_router(issues_router)
    app.include_router(feedback_router)

    @app.on_event("startup")
    def _startup():
        init_db()

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
