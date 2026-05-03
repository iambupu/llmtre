from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest

import tools.logs.stage_d_acceptance_check as stage_d


class _FakeResponse:
    """
    功能：模拟 Flask 响应对象，支持正常 JSON 与解析失败两类路径。
    入参：status_code（int）：HTTP 状态码；body（dict[str, Any] | None）：响应体；
        raise_json（bool）：是否在 get_json 时抛异常，默认 False。
    出参：测试辅助对象，暴露 status_code 与 get_json。
    异常：raise_json=True 时 get_json 抛 RuntimeError，用于验证解析降级。
    """

    def __init__(
        self,
        status_code: int,
        body: dict[str, Any] | None = None,
        *,
        raise_json: bool = False,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self._raise_json = raise_json

    def get_json(self) -> dict[str, Any] | None:
        """
        功能：返回预置 JSON 响应体，或模拟 JSON 解析失败。
        入参：无。
        出参：dict[str, Any] | None，预置响应体。
        异常：当 raise_json=True 时抛 RuntimeError。
        """
        if self._raise_json:
            raise RuntimeError("bad json")
        return self._body


class _FakeClient:
    """
    功能：模拟阶段 D 验收脚本依赖的最小 Flask test client。
    入参：session_id（str）：固定会话 ID；initial_turn（int）：初始回合游标。
    出参：测试辅助对象，记录请求并按路径返回确定性响应。
    异常：未知路径返回 404，由脚本断言负责暴露失败。
    """

    def __init__(self, session_id: str = "sess_stage_d01", initial_turn: int = 0) -> None:
        self.session_id = session_id
        self.turn_counter = initial_turn
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []

    def post(self, path: str, json: dict[str, Any]) -> _FakeResponse:
        """
        功能：按阶段 D POST 路径返回契约响应，并推进回合游标。
        入参：path（str）：请求路径；json（dict[str, Any]）：请求体。
        出参：_FakeResponse，包含状态码和响应体。
        异常：不主动抛出；未知路径通过 404 响应暴露。
        """
        self.posts.append((path, json))
        if path == "/api/sessions":
            return _FakeResponse(201, {"session_id": self.session_id})
        if path.endswith("/turns"):
            self.turn_counter += 1
            return _FakeResponse(200, {"session_turn_id": self.turn_counter})
        if path.endswith("/memory/refresh"):
            return _FakeResponse(
                200,
                {"covered_turn_range": {"start": 1, "end": self.turn_counter}},
            )
        if path.endswith("/sandbox/discard"):
            self.turn_counter += 1
            return _FakeResponse(200, {"session_turn_id": self.turn_counter})
        if path.endswith("/sandbox/commit"):
            self.turn_counter += 1
            return _FakeResponse(200, {"session_turn_id": self.turn_counter})
        if path.endswith("/reset"):
            self.turn_counter = 0
            return _FakeResponse(200, {"current_session_turn_id": self.turn_counter})
        return _FakeResponse(404, {"error": "unknown post"})

    def get(self, path: str) -> _FakeResponse:
        """
        功能：按阶段 D GET 路径返回会话、回合列表、回合详情与记忆摘要。
        入参：path（str）：请求路径。
        出参：_FakeResponse，包含状态码和响应体。
        异常：不主动抛出；未知路径通过 404 响应暴露。
        """
        self.gets.append(path)
        if path.endswith("/memory?format=summary"):
            return _FakeResponse(200, {"summary": "阶段 D 记忆摘要"})
        if "/turns?" in path:
            return _FakeResponse(200, {"total": max(self.turn_counter, 5)})
        turn_detail = re.search(r"/turns/(?P<turn_id>\d+)$", path)
        if turn_detail:
            return _FakeResponse(
                200,
                {"session_turn_id": int(turn_detail.group("turn_id"))},
            )
        if path == f"/api/sessions/{self.session_id}":
            return _FakeResponse(200, {"current_session_turn_id": self.turn_counter})
        return _FakeResponse(404, {"error": "unknown get"})


class _FakeApp:
    """
    功能：模拟 Flask app，只暴露 extensions 与 test_client。
    入参：client（_FakeClient）：固定返回的测试 client。
    出参：测试辅助对象。
    异常：无。
    """

    def __init__(self, client: _FakeClient) -> None:
        self._client = client
        self.extensions = {
            "tre_api_context": SimpleNamespace(
                main_loop=SimpleNamespace(gm_agent=SimpleNamespace(llm_enabled=True))
            )
        }

    def test_client(self) -> _FakeClient:
        """
        功能：返回预置 fake client，避免真实 Flask/DB 初始化。
        入参：无。
        出参：_FakeClient。
        异常：无。
        """
        return self._client


def test_request_helpers_generate_contract_id_and_fallback_empty_json() -> None:
    """
    功能：验证请求 ID 契约格式，以及响应 JSON 解析失败时降级为空字典。
    入参：无。
    出参：None。
    异常：断言失败表示请求辅助函数契约回归。
    """
    request_id = stage_d._new_request_id("reqtest")  # noqa: SLF001
    assert re.fullmatch(r"reqtest_[0-9a-f]{20}", request_id)

    client = SimpleNamespace(
        post=lambda path, json: _FakeResponse(202, raise_json=True),
        get=lambda path: _FakeResponse(204, raise_json=True),
    )

    assert stage_d._post_json(client, "/bad", {"x": 1}) == (202, {})  # noqa: SLF001
    assert stage_d._get_json(client, "/bad") == (204, {})  # noqa: SLF001


def test_disable_gm_llm_requires_runtime_and_turns_flag_off() -> None:
    """
    功能：验证阶段验收会显式关闭 GM LLM，且运行时缺失时给出可定位错误。
    入参：无。
    出参：None。
    异常：断言失败表示运行时保护或 LLM 关闭逻辑回归。
    """
    app = _FakeApp(_FakeClient())
    stage_d._disable_gm_llm(app)  # noqa: SLF001
    assert app.extensions["tre_api_context"].main_loop.gm_agent.llm_enabled is False

    broken_app = SimpleNamespace(extensions={})
    with pytest.raises(RuntimeError, match="tre_api_context 未初始化"):
        stage_d._disable_gm_llm(broken_app)  # noqa: SLF001


def test_assert_status_includes_step_and_body_on_failure() -> None:
    """
    功能：验证状态断言失败时输出步骤名与响应体，作为缺证据定位信息。
    入参：无。
    出参：None。
    异常：断言失败表示错误消息回归。
    """
    error_pattern = r"create_turn 失败：status=500, body=\{'error': 'boom'\}"
    with pytest.raises(AssertionError, match=error_pattern):
        stage_d._assert_status(500, 200, {"error": "boom"}, "create_turn")  # noqa: SLF001


def test_run_contract_and_e2e_returns_acceptance_report(monkeypatch) -> None:
    """
    功能：验证契约与 5 回合端到端验收成功时返回结构化证据。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示验收步骤编排或报告字段回归。
    """
    client = _FakeClient()
    app = _FakeApp(client)
    monkeypatch.setattr(stage_d, "create_app", lambda: app)

    report = stage_d._run_contract_and_e2e()  # noqa: SLF001

    assert report["contract"]["create_session"]["status"] == 201
    assert report["contract"]["list_turns"]["total"] >= 5
    assert report["contract"]["get_memory"]["summary_len"] > 0
    assert report["contract"]["reset"]["current_session_turn_id"] == 0
    assert report["e2e"]["five_turn_ids"] == [1, 2, 3, 4, 5]
    assert report["e2e"]["discard_turn_id"] == 6
    assert report["e2e"]["commit_turn_id"] == 7
    assert app.extensions["tre_api_context"].main_loop.gm_agent.llm_enabled is False


def test_run_restart_recovery_uses_new_app_and_continues_turns(monkeypatch) -> None:
    """
    功能：验证重启恢复验收会重建 app，并在同一会话继续推进回合。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示重启恢复检查回归。
    """
    before_client = _FakeClient(session_id="sess_restart")
    after_client = _FakeClient(session_id="sess_restart", initial_turn=1)
    apps = [_FakeApp(before_client), _FakeApp(after_client)]
    monkeypatch.setattr(stage_d, "create_app", lambda: apps.pop(0))

    report = stage_d._run_restart_recovery()  # noqa: SLF001

    assert report == {
        "session_id": "sess_restart",
        "turn_id_before_restart": 1,
        "loaded_after_restart": 1,
        "turn_id_after_restart_play": 2,
        "session_turn_cursor_after_restart": 2,
    }


def test_main_prints_ok_and_json_report(monkeypatch, capsys) -> None:
    """
    功能：验证 CLI 入口输出成功标记与 JSON 验收报告，并调整阶段验收超时配置。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 main 输出契约回归。
    """
    monkeypatch.setattr(stage_d, "_run_contract_and_e2e", lambda: {"contract": {"ok": True}})
    monkeypatch.setattr(stage_d, "_run_restart_recovery", lambda: {"session_id": "sess_restart"})
    monkeypatch.setattr(stage_d.web_service, "TURN_TIMEOUT_SECONDS", 1)

    stage_d.main()
    output = capsys.readouterr().out

    assert "STAGE_D_ACCEPTANCE_OK" in output
    payload = json.loads(output.split("STAGE_D_ACCEPTANCE_OK", 1)[1])
    assert payload == {"contract": {"ok": True}, "restart": {"session_id": "sess_restart"}}
    assert stage_d.web_service.TURN_TIMEOUT_SECONDS == 60
