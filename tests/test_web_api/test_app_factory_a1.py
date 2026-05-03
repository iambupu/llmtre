from __future__ import annotations

from pathlib import Path

import pytest

from web_api import create_app


def test_create_app_initializes_runtime_and_registers_all_blueprints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 Flask app factory 会挂载运行时上下文并注册所有契约 API blueprint。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 app 装配入口或 blueprint 注册回归。
    """
    initialized = {"called": False}

    def _fake_initialize_runtime(app) -> None:  # noqa: ANN001
        """
        功能：替代真实 runtime 初始化，避免测试触发 DB/RAG 构建。
        入参：app（Flask）：待初始化应用。
        出参：None。
        异常：无。
        """
        initialized["called"] = True
        app.extensions["tre_api_context"] = "runtime-ready"

    monkeypatch.setattr("web_api.initialize_runtime", _fake_initialize_runtime)

    app = create_app()

    assert initialized["called"] is True
    assert app.extensions["tre_api_context"] == "runtime-ready"
    assert app.static_url_path == "/static"
    assert Path(app.template_folder).name == "templates"
    assert Path(app.static_folder).name == "static"
    assert {
        "health.healthcheck",
        "playground.playground_page",
        "sessions.create_session",
        "sessions.get_session_detail",
        "turns.create_turn",
        "turns.create_turn_stream",
        "turns.list_turns",
        "turns.get_turn",
        "memory.get_memory",
        "memory.refresh_memory",
        "sandbox.discard_sandbox",
        "sandbox.commit_sandbox",
        "runtime.reset_session",
    }.issubset(app.view_functions)


def test_create_app_propagates_runtime_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证运行时初始化失败会向上抛出，避免返回半初始化 Flask app。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 app factory 错误处理语义回归。
    """

    def _raise_initialize_error(app) -> None:  # noqa: ANN001, ARG001
        """
        功能：模拟 runtime 初始化失败。
        入参：app（Flask）：待初始化应用。
        出参：None。
        异常：始终抛 RuntimeError。
        """
        raise RuntimeError("runtime failed")

    monkeypatch.setattr("web_api.initialize_runtime", _raise_initialize_error)

    with pytest.raises(RuntimeError, match="runtime failed"):
        create_app()
