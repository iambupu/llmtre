from __future__ import annotations

from flask import Blueprint

health_blueprint = Blueprint("health", __name__)


@health_blueprint.get("/")
def healthcheck() -> str:
    """
    功能：返回服务存活探针。
    入参：无。
    出参：str，固定返回服务状态文本。
    异常：无显式异常。
    """
    return "TRE API running"
