from __future__ import annotations

from pathlib import Path

from flask import Blueprint, render_template, send_from_directory
from werkzeug.exceptions import NotFound

playground_blueprint = Blueprint("playground", __name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"


@playground_blueprint.get("/play")
def playground_page() -> str:
    """
    功能：渲染最小可玩 Web 交互壳页面。
    入参：无。
    出参：str，HTML 模板渲染结果。
    异常：模板不存在或渲染失败时由 Flask 抛出异常。
    """
    return render_template("playground.html")


@playground_blueprint.get("/app")
def app_page():
    """
    功能：提供 React 前端同源入口，优先返回构建产物，缺失时返回降级引导页。
    入参：无。
    出参：Response，`frontend/dist/index.html` 或模板降级页。
    异常：文件读取失败时由 Flask 抛出异常；降级页路径缺失时返回 404。
    """
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return send_from_directory(FRONTEND_DIST_DIR, "index.html")
    return render_template("app_bootstrap.html")


@playground_blueprint.get("/app/<path:asset_path>")
def app_assets(asset_path: str):
    """
    功能：同源托管 React 构建产物的静态资源与 SPA 子路由。
    入参：asset_path（str）：`/app` 之后的路径片段。
    出参：Response，命中静态文件时返回文件；未命中时回退到 `index.html`。
    异常：dist 不存在时抛出 NotFound；文件读取失败由 Flask 抛出异常。
    """
    if not FRONTEND_DIST_DIR.exists():
        raise NotFound("frontend dist not found")
    candidate_file = FRONTEND_DIST_DIR / asset_path
    if candidate_file.exists() and candidate_file.is_file():
        return send_from_directory(FRONTEND_DIST_DIR, asset_path)
    index_file = FRONTEND_DIST_DIR / "index.html"
    if not index_file.exists():
        raise NotFound("frontend index not found")
    return send_from_directory(FRONTEND_DIST_DIR, "index.html")
