from __future__ import annotations

import os

from flask import Flask

from web_api.blueprints.health import health_blueprint
from web_api.blueprints.memory import memory_blueprint
from web_api.blueprints.playground import playground_blueprint
from web_api.blueprints.runtime import runtime_blueprint
from web_api.blueprints.sandbox import sandbox_blueprint
from web_api.blueprints.sessions import sessions_blueprint
from web_api.blueprints.story_packs import story_packs_blueprint
from web_api.blueprints.turns import turns_blueprint
from web_api.service import initialize_runtime


def create_app() -> Flask:
    """
    功能：创建 Flask 应用并按功能注册 Blueprint。
    入参：无。
    出参：Flask，可直接用于 `flask run` 或 `python app.py`。
    异常：运行时初始化失败时异常向上抛出；调用方应终止启动并修复依赖。
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
        static_url_path="/static",
    )
    initialize_runtime(app)
    app.register_blueprint(health_blueprint)
    app.register_blueprint(playground_blueprint)
    app.register_blueprint(sessions_blueprint)
    app.register_blueprint(story_packs_blueprint)
    app.register_blueprint(turns_blueprint)
    app.register_blueprint(memory_blueprint)
    app.register_blueprint(sandbox_blueprint)
    app.register_blueprint(runtime_blueprint)
    return app
