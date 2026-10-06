"""漫画工作台 FastAPI 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import assets, panels, projects
from .db import init_db
from .pipeline import worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker.start_worker()
    yield
    worker.stop_worker()


app = FastAPI(title="Comic Workbench 漫画工作台", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(panels.router)
app.include_router(assets.router)

# 项目文件（图片）托管 — 请求时解析目录，便于测试注入
from fastapi import HTTPException


@app.get("/files/{file_path:path}")
def serve_project_file(file_path: str):
    root = config.PROJECTS_DIR.resolve()
    p = (config.PROJECTS_DIR / file_path).resolve()
    if not str(p).startswith(str(root)) or not p.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "comic-workbench"}


# 前端构建产物托管（生产模式：一条命令起整个工作台）
_dist = config.FRONTEND_DIST
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(_dist / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        candidate = _dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
