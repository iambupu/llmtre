from __future__ import annotations

import pytest
from flask import Flask
from jinja2 import TemplateNotFound

from web_api.blueprints.health import health_blueprint
from web_api.blueprints.playground import playground_blueprint


def test_healthcheck_returns_static_probe_text() -> None:
    """
    功能：验证健康检查返回固定存活文本。
    入参：无。
    出参：None。
    异常：断言失败表示健康检查响应契约回归。
    """
    app = Flask(__name__)
    app.register_blueprint(health_blueprint)

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "TRE API running"


def test_playground_page_renders_template(tmp_path) -> None:
    """
    功能：验证 playground blueprint 会渲染 playground.html 模板。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示 playground 页面响应回归。
    """
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "playground.html").write_text("<main>TRE Playground</main>", encoding="utf-8")
    app = Flask(__name__, template_folder=str(template_dir))
    app.register_blueprint(playground_blueprint)

    response = app.test_client().get("/play")

    assert response.status_code == 200
    assert "TRE Playground" in response.get_data(as_text=True)


def test_playground_page_propagates_missing_template(tmp_path) -> None:
    """
    功能：验证 playground 模板缺失时异常向上抛出，避免返回伪成功页面。
    入参：tmp_path。
    出参：None。
    异常：断言失败表示缺模板错误处理语义回归。
    """
    app = Flask(__name__, template_folder=str(tmp_path / "missing_templates"))
    app.config["TESTING"] = True
    app.register_blueprint(playground_blueprint)

    with pytest.raises(TemplateNotFound, match="playground.html"):
        app.test_client().get("/play")
