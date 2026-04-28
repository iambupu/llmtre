from __future__ import annotations

from flask import Blueprint, render_template

playground_blueprint = Blueprint("playground", __name__)


@playground_blueprint.get("/play")
def playground_page() -> str:
    """
    功能：渲染最小可玩 Web 交互壳页面。
    入参：无。
    出参：str，HTML 模板渲染结果。
    异常：模板不存在或渲染失败时由 Flask 抛出异常。
    """
    return render_template("playground.html")
