from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from state.tools.runtime_schema import ensure_runtime_tables
from web_api.blueprints.turns import (
    _append_trace_stage,
    _build_post_run_error_payload,
    turns_blueprint,
)
from web_api.service import ApiRuntimeContext, TurnExecutionError
from web_api.session_store import WebSessionStore


class _FakeRuntimeContext(ApiRuntimeContext):
    """
    功能：为 turns 蓝图测试提供最小运行时上下文，复用真实会话存储与会话锁语义。
    入参：db_path（str）：临时 SQLite 路径。
    出参：_FakeRuntimeContext，可注入 Flask `app.extensions`。
    异常：数据库初始化失败时向上抛出 sqlite3.Error。
    """

    def __init__(self, db_path: str) -> None:
        """
        功能：初始化测试用会话存储与会话锁容器。
        入参：db_path（str）：SQLite 文件路径。
        出参：None。
        异常：底层文件系统或 sqlite 初始化失败时向上抛出。
        """
        super().__init__()
        self.main_loop = object()
        self.session_store = WebSessionStore(db_path)
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：返回会话级锁对象，保持与生产代码一致的串行语义。
        入参：session_id（str）：会话标识。
        出参：object，锁实例。
        异常：无显式异常；内存不足等系统异常向上抛出。
        """
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _init_runtime_db(db_path: str) -> None:
    """
    功能：初始化 tests 所需的运行时表结构（session/turn/idempotency）。
    入参：db_path（str）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行异常向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


def _now_iso() -> str:
    """
    功能：生成测试内统一 UTC 时间字符串。
    入参：无。
    出参：str，ISO8601 UTC 文本。
    异常：时间系统异常向上抛出。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@pytest.fixture
def turns_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Generator[Flask]:
    """
    功能：构建仅注册 turns 蓝图的最小 Flask 测试客户端，并注入假运行时上下文。
    入参：tmp_path（pytest fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：Generator[Flask, None, None]，yield Flask test client。
    异常：运行时表初始化失败时向上抛出，测试直接失败。
    """
    db_path = str(tmp_path / "runtime.db")
    _init_runtime_db(db_path)
    context = _FakeRuntimeContext(db_path)
    context.session_store.create_session(
        session_id="sess_a1demo01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso=_now_iso(),
        memory_policy={"mode": "auto", "max_turns": 20},
    )

    app = Flask(__name__)
    app.register_blueprint(turns_blueprint)
    app.extensions["tre_api_context"] = context
    app.config["TESTING"] = True

    call_count = {"run_turn": 0}

    def fake_run_turn(
        session: dict[str, Any],
        user_input: str,
        character_id: str,
        sandbox_mode: bool,
        narrative_stream_callback=None,
        trace_id: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        """
        功能：替代主循环执行，返回稳定回合结果并在流式场景推送 GM 片段。
        入参：保持与生产 `run_turn` 一致，便于蓝图路径直连验证。
        出参：dict[str, Any]，满足 TurnResult 契约最小字段集合。
        异常：不抛异常；用于验证蓝图逻辑而非主循环异常分支。
        """
        call_count["run_turn"] += 1
        if narrative_stream_callback is not None:
            narrative_stream_callback("片段一")
            narrative_stream_callback("片段二")
        return {
            "session_id": session["session_id"],
            "runtime_turn_id": 99,
            "trace_id": trace_id or "trc_test",
            "request_id": request_id,
            "is_valid": True,
            "action_intent": {"type": "observe", "target_id": "", "parameters": {}},
            "physics_diff": {"hp_delta": 0, "mp_delta": 0},
            "final_response": f"响应:{user_input}",
            "quick_actions": ["继续观察", "等待片刻"],
            "affordances": [],
            "is_sandbox_mode": bool(sandbox_mode),
            "active_character": {"id": character_id, "inventory": []},
            "scene_snapshot": {
                "schema_version": "scene_snapshot.v2",
                "current_location": {"id": "loc_a", "name": "测试地点", "description": "desc"},
                "visible_npcs": [],
                "visible_items": [],
                "active_quests": [],
                "recent_memory": "",
                "suggested_actions": [],
                "scene_objects": [
                    {
                        "object_id": "obj_1",
                        "object_type": "location",
                        "label": "石碑",
                        "description": "可观察的古老石碑",
                        "state_tags": [],
                        "source_ref": {"id": "obj_1"},
                        "priority": 10,
                    }
                ],
                "exits": [],
                "interaction_slots": [
                    {
                        "slot_id": "s1",
                        "object_id": "obj_1",
                        "action_type": "observe",
                        "label": "观察",
                        "enabled": True,
                        "disabled_reason": "",
                        "default_input": "观察石碑",
                        "required_params": [],
                    }
                ],
                "affordances": [],
                "available_actions": ["observe"],
                "ui_hints": {},
            },
            "outcome": "valid_action",
            "clarification_question": "",
            "failure_reason": "",
            "suggested_next_step": "继续观察",
            "should_advance_turn": True,
            "should_write_story_memory": True,
            "debug_trace": [],
            "errors": [],
        }

    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr("web_api.blueprints.turns.run_turn", fake_run_turn)
    monkeypatch.setattr("web_api.blueprints.turns.new_trace_id", lambda: "trc_fixed_001")

    client = app.test_client()
    client.call_count = call_count  # type: ignore[attr-defined]
    yield client


def test_create_turn_request_id_idempotent_no_duplicate_advance(turns_client) -> None:
    """
    功能：验证重复 request_id 命中幂等缓存，不重复推进回合也不重复调用 run_turn。
    入参：turns_client（fixture）：最小 Flask 客户端。
    出参：None，通过断言表达验收结果。
    异常：断言失败表示 A1 幂等契约破损。
    """
    payload = {
        "request_id": "req_a1idem_01",
        "user_input": "观察四周",
        "character_id": "player_01",
    }
    first = turns_client.post("/api/sessions/sess_a1demo01/turns", json=payload)
    second = turns_client.post("/api/sessions/sess_a1demo01/turns", json=payload)
    first_body = first.get_json()
    second_body = second.get_json()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first_body["session_turn_id"] == 1
    assert second_body["session_turn_id"] == 1
    assert first_body["request_id"] == second_body["request_id"]
    assert first_body["session_turn_id"] == second_body["session_turn_id"]
    assert first_body["runtime_turn_id"] == second_body["runtime_turn_id"]
    assert turns_client.call_count["run_turn"] == 1


def test_create_turn_stream_event_order_fixed(turns_client) -> None:
    """
    功能：验证 SSE 阶段事件顺序固定，且最终以 done 事件收敛。
    入参：turns_client（fixture）：最小 Flask 客户端。
    出参：None，通过断言表达验收结果。
    异常：断言失败表示 A1 流式阶段协议顺序不稳定。
    """
    payload = {
        "request_id": "req_a1sse_001",
        "user_input": "观察场景",
        "character_id": "player_01",
    }
    response = turns_client.post("/api/sessions/sess_a1demo01/turns/stream", json=payload)
    assert response.status_code == 200
    raw_text = response.data.decode("utf-8")
    frames = [frame for frame in raw_text.split("\n\n") if frame.strip()]
    events: list[str] = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("event: "):
                events.append(line.replace("event: ", "", 1))
    assert events[0] == "received"
    assert events[1:6] == [
        "loading_scene",
        "parsing_nlu",
        "validating_action",
        "resolving_action",
        "rendering_gm",
    ]
    assert "gm_delta" in events
    assert events[-1] == "done"

    done_frame = next(frame for frame in frames if "event: done" in frame)
    done_payload_line = next(
        line for line in done_frame.splitlines() if line.startswith("data: ")
    )
    done_payload = json.loads(done_payload_line.replace("data: ", "", 1))
    assert done_payload["session_turn_id"] == 1
    assert done_payload["runtime_turn_id"] == 99


def test_create_turn_and_stream_done_payload_isomorphic(turns_client) -> None:
    """
    功能：验证普通回合响应与 SSE done 负载在核心字段上同构。
    入参：turns_client（fixture）：最小 Flask 客户端。
    出参：None，通过断言表达验收结果。
    异常：断言失败表示 turns 与 turns/stream 契约字段可能漂移。
    """
    normal_resp = turns_client.post(
        "/api/sessions/sess_a1demo01/turns",
        json={
            "request_id": "req_a1_iso_normal_01",
            "user_input": "观察周围",
            "character_id": "player_01",
        },
    )
    normal_body = normal_resp.get_json()
    stream_resp = turns_client.post(
        "/api/sessions/sess_a1demo01/turns/stream",
        json={
            "request_id": "req_a1_iso_stream_01",
            "user_input": "观察周围",
            "character_id": "player_01",
        },
    )
    frames = [frame for frame in stream_resp.data.decode("utf-8").split("\n\n") if frame.strip()]
    done_frame = next(frame for frame in frames if "event: done" in frame)
    payload_line = next(line for line in done_frame.splitlines() if line.startswith("data: "))
    done_body = json.loads(payload_line.replace("data: ", "", 1))
    assert normal_resp.status_code == 200
    assert stream_resp.status_code == 200

    core_keys = {
        "session_id",
        "session_turn_id",
        "runtime_turn_id",
        "trace_id",
        "request_id",
        "outcome",
        "is_valid",
        "action_intent",
        "physics_diff",
        "final_response",
        "quick_actions",
        "affordances",
        "memory_summary",
        "active_character",
        "scene_snapshot",
        "clarification_question",
        "failure_reason",
        "suggested_next_step",
        "should_advance_turn",
        "should_write_story_memory",
        "debug_trace",
        "errors",
        "trace",
    }
    assert core_keys.issubset(normal_body.keys())
    assert core_keys.issubset(done_body.keys())
    assert normal_body["outcome"] == done_body["outcome"] == "valid_action"
    assert normal_body["runtime_turn_id"] == done_body["runtime_turn_id"] == 99


def test_create_turn_error_keeps_run_turn_trace_id(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 run_turn 失败时普通回合错误响应复用同一 trace_id 与 trace 结构。
    入参：turns_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示失败链路 trace 串联退化。
    """

    def failing_run_turn(
        session: dict[str, Any],
        user_input: str,
        character_id: str,
        sandbox_mode: bool,
        narrative_stream_callback=None,
        trace_id: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        raise TurnExecutionError(
            message="boom",
            trace_id=trace_id or "trc_fallback",
            trace={
                "trace_id": trace_id or "trc_fallback",
                "stages": [{"stage": "run_turn", "status": "failed", "at": "now", "detail": {}}],
                "errors": [{"stage": "run_turn", "error": "boom"}],
            },
        )

    monkeypatch.setattr("web_api.blueprints.turns.run_turn", failing_run_turn)
    resp = turns_client.post(
        "/api/sessions/sess_a1demo01/turns",
        json={"request_id": "req_a1err_01", "user_input": "观察", "character_id": "player_01"},
    )
    body = resp.get_json()
    assert resp.status_code == 500
    assert body["trace_id"] == "trc_fixed_001"
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["trace"]["trace_id"] == "trc_fixed_001"


def test_trace_helpers_handle_missing_or_malformed_trace_structures() -> None:
    """
    功能：验证 turns trace helper 在 trace 缺失或结构损坏时能降级为可回传的最小 trace。
    入参：无，使用内联 payload。
    出参：None。
    异常：断言失败表示 post-run trace 兜底链路回归。
    """
    missing_trace_id, missing_trace = _build_post_run_error_payload(
        None,
        "api.persisted",
        RuntimeError("persist failed"),
    )
    payload = {
        "trace_id": "trc_bad_001",
        "trace": {"trace_id": "old", "stages": "bad", "errors": "bad"},
    }
    trace_id, trace = _build_post_run_error_payload(
        payload,
        "api.response_built",
        RuntimeError("contract failed"),
    )
    _append_trace_stage({"trace": {"stages": "bad"}}, "ignored", "failed")

    assert missing_trace_id.startswith("trc_")
    assert missing_trace["stages"][0]["stage"] == "api.persisted"
    assert trace_id == "trc_bad_001"
    assert trace["trace_id"] == "trc_bad_001"
    assert trace["stages"][0]["stage"] == "api.response_built"
    assert trace["errors"][0]["error"] == "contract failed"


def test_create_turn_rejects_preflight_argument_boundaries(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证普通回合入口拒绝 session/request/user/character/memory 的高风险非法参数。
    入参：turns_client；monkeypatch。
    出参：None。
    异常：断言失败表示 create_turn 前置校验边界回归。
    """
    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: False)
    cases = [
        (
            "/api/sessions/sess_missing01/turns",
            {"request_id": "req_missing_sess01", "user_input": "观察"},
            404,
        ),
        ("/api/sessions/sess_a1demo01/turns", {"user_input": "观察"}, 400),
        (
            "/api/sessions/sess_a1demo01/turns",
            {"request_id": "req_blank_input01", "user_input": "   "},
            400,
        ),
        (
            "/api/sessions/sess_a1demo01/turns",
            {"request_id": "req_bad_char01", "user_input": "观察", "character_id": "x"},
            400,
        ),
        (
            "/api/sessions/sess_a1demo01/turns",
            {"request_id": "req_conflict01", "user_input": "观察", "character_id": "player_02"},
            409,
        ),
        (
            "/api/sessions/sess_a1demo01/turns",
            {"request_id": "req_char_missing01", "user_input": "观察"},
            404,
        ),
    ]

    for path, body, status in cases:
        response = turns_client.post(path, json=body)
        assert response.status_code == status
        assert response.get_json()["ok"] is False

    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    memory_cases = [
        {"request_id": "req_bad_memory01", "user_input": "观察", "memory": {"mode": "manual"}},
        {
            "request_id": "req_bad_memory02",
            "user_input": "观察",
            "memory": {"mode": "auto", "max_turns": 101},
        },
    ]
    for body in memory_cases:
        response = turns_client.post("/api/sessions/sess_a1demo01/turns", json=body)
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "INVALID_ARGUMENT"


def test_create_turn_updates_memory_policy_before_persist(turns_client) -> None:
    """
    功能：验证合法 memory.max_turns 会更新会话策略，并参与本回合记忆摘要窗口。
    入参：turns_client。
    出参：None。
    异常：断言失败表示 memory 策略更新链路回归。
    """
    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns",
        json={
            "request_id": "req_memory_policy01",
            "user_input": "观察",
            "memory": {"mode": "auto", "max_turns": 5},
        },
    )
    session = turns_client.application.extensions["tre_api_context"].session_store.get_session(  # type: ignore[attr-defined]
        "sess_a1demo01"
    )

    assert response.status_code == 200
    assert session["memory_policy"]["max_turns"] == 5


def test_create_turn_stream_error_keeps_run_turn_trace_id(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 run_turn 失败时 SSE error 事件包含 trace_id/trace，便于前后端日志串联。
    入参：turns_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示流式失败链路缺失 trace 关键字段。
    """

    def failing_run_turn(
        session: dict[str, Any],
        user_input: str,
        character_id: str,
        sandbox_mode: bool,
        narrative_stream_callback=None,
        trace_id: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        raise TurnExecutionError(
            message="回合执行超时",
            trace_id=trace_id or "trc_fallback",
            trace={
                "trace_id": trace_id or "trc_fallback",
                "stages": [{"stage": "run_turn", "status": "failed", "at": "now", "detail": {}}],
                "errors": [{"stage": "run_turn", "error": "timeout"}],
            },
            error_code="TURN_TIMEOUT",
            status_code=504,
        )

    monkeypatch.setattr("web_api.blueprints.turns.run_turn", failing_run_turn)
    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns/stream",
        json={"request_id": "req_a1errsse01", "user_input": "观察", "character_id": "player_01"},
    )
    frames = [frame for frame in response.data.decode("utf-8").split("\n\n") if frame.strip()]
    error_frame = next(frame for frame in frames if "event: error" in frame)
    payload_line = next(line for line in error_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.replace("data: ", "", 1))
    assert payload["code"] == "TURN_TIMEOUT"
    assert payload["trace_id"] == "trc_fixed_001"
    assert payload["trace"]["trace_id"] == "trc_fixed_001"


def test_create_turn_stream_rejects_preflight_argument_boundaries(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 SSE 回合入口在启动 worker 前拒绝非法参数并返回普通 JSON 错误。
    入参：turns_client；monkeypatch。
    出参：None。
    异常：断言失败表示流式入口前置校验边界回归。
    """
    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: False)
    cases = [
        (
            "/api/sessions/bad/turns/stream",
            {"request_id": "req_sse_bad01", "user_input": "观察"},
            400,
        ),
        (
            "/api/sessions/sess_missing01/turns/stream",
            {"request_id": "req_sse_missing01", "user_input": "观察"},
            404,
        ),
        ("/api/sessions/sess_a1demo01/turns/stream", {"user_input": "观察"}, 400),
        (
            "/api/sessions/sess_a1demo01/turns/stream",
            {"request_id": "req_sse_blank01", "user_input": ""},
            400,
        ),
        (
            "/api/sessions/sess_a1demo01/turns/stream",
            {"request_id": "req_sse_bad_char01", "user_input": "观察", "character_id": "x"},
            400,
        ),
        (
            "/api/sessions/sess_a1demo01/turns/stream",
            {"request_id": "req_sse_conflict01", "user_input": "观察", "character_id": "player_02"},
            409,
        ),
        (
            "/api/sessions/sess_a1demo01/turns/stream",
            {"request_id": "req_sse_char_missing01", "user_input": "观察"},
            404,
        ),
    ]

    for path, body, status in cases:
        response = turns_client.post(path, json=body)
        assert response.status_code == status
        assert response.get_json()["ok"] is False


def test_create_turn_stream_idempotent_done_does_not_rerun(turns_client) -> None:
    """
    功能：验证 SSE 命中幂等缓存时直接输出 done，不重复调用 run_turn。
    入参：turns_client。
    出参：None。
    异常：断言失败表示流式幂等短路回归。
    """
    payload = {"request_id": "req_sse_idem01", "user_input": "观察", "character_id": "player_01"}
    first = turns_client.post("/api/sessions/sess_a1demo01/turns", json=payload)
    second = turns_client.post("/api/sessions/sess_a1demo01/turns/stream", json=payload)
    frames = [frame for frame in second.data.decode("utf-8").split("\n\n") if frame.strip()]
    events = [
        line.replace("event: ", "", 1)
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("event: ")
    ]

    assert first.status_code == 200
    assert second.status_code == 200
    assert events == ["received", "done"]
    assert turns_client.call_count["run_turn"] == 1


def test_create_turn_post_run_failure_returns_trace(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证普通回合 post-run（持久化/契约）失败时返回 INTERNAL_ERROR 且复用 run_turn trace。
    入参：turns_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示 post-run 错误链路仍未可观测。
    """

    def failing_persist(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(
        turns_client.application.extensions["tre_api_context"].session_store,  # type: ignore[attr-defined]
        "persist_turn_result_with_idempotency",
        failing_persist,
    )
    resp = turns_client.post(
        "/api/sessions/sess_a1demo01/turns",
        json={"request_id": "req_a1post_01", "user_input": "观察", "character_id": "player_01"},
    )
    body = resp.get_json()
    assert resp.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["trace_id"] == "trc_fixed_001"
    assert body["trace"]["trace_id"] == "trc_fixed_001"
    assert body["trace"]["stages"][-1]["stage"] == "api.persisted"
    assert body["trace"]["stages"][-1]["status"] == "failed"


def test_create_turn_stream_post_run_failure_emits_error_event(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 SSE post-run 失败不静默中断，而是输出 error 事件并携带 trace。
    入参：turns_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示流式协议仍存在静默中断风险。
    """

    def failing_persist(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(
        turns_client.application.extensions["tre_api_context"].session_store,  # type: ignore[attr-defined]
        "persist_turn_result_with_idempotency",
        failing_persist,
    )
    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns/stream",
        json={"request_id": "req_a1post_sse01", "user_input": "观察", "character_id": "player_01"},
    )
    frames = [frame for frame in response.data.decode("utf-8").split("\n\n") if frame.strip()]
    assert any("event: error" in frame for frame in frames)
    error_frame = next(frame for frame in frames if "event: error" in frame)
    payload_line = next(line for line in error_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.replace("data: ", "", 1))
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["trace_id"] == "trc_fixed_001"
    assert payload["trace"]["trace_id"] == "trc_fixed_001"
    assert payload["trace"]["stages"][-1]["stage"] == "api.persisted"
    assert payload["trace"]["stages"][-1]["status"] == "failed"


def test_create_turn_stream_worker_app_context_failure_emits_error(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 SSE worker 无法进入 Flask app context 时会输出 api.worker error 事件。
    入参：turns_client；monkeypatch。
    出参：None。
    异常：断言失败表示 worker 外层兜底异常链路回归。
    """

    class _BrokenAppContext:
        def __enter__(self) -> None:
            raise RuntimeError("app context failed")

        def __exit__(self, exc_type, exc, traceback) -> bool:  # noqa: ANN001
            return False

    class _BrokenApp:
        def app_context(self) -> _BrokenAppContext:
            return _BrokenAppContext()

    class _BrokenCurrentApp:
        def _get_current_object(self) -> _BrokenApp:
            return _BrokenApp()

    monkeypatch.setattr(
        "web_api.blueprints.turns.cast",
        lambda _type, _value: _BrokenCurrentApp(),
    )

    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns/stream",
        json={"request_id": "req_worker_ctx01", "user_input": "观察", "character_id": "player_01"},
    )
    frames = [frame for frame in response.data.decode("utf-8").split("\n\n") if frame.strip()]
    error_frame = next(frame for frame in frames if "event: error" in frame)
    payload_line = next(line for line in error_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.replace("data: ", "", 1))

    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["trace"]["stages"][0]["stage"] == "api.worker"


def test_create_turn_stream_worker_exit_without_terminal_event_emits_error(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 SSE worker 未产生 done/error 即退出时，生成器会补发 api.worker error。
    入参：turns_client；monkeypatch。
    出参：None。
    异常：断言失败表示流式终止事件兜底回归。
    """

    class _FakeThread:
        def __init__(self, target: Any, daemon: bool = False) -> None:  # noqa: ARG002
            self._started = False

        def start(self) -> None:
            self._started = True

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr("web_api.blueprints.turns.threading.Thread", _FakeThread)

    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns/stream",
        json={"request_id": "req_worker_exit01", "user_input": "观察", "character_id": "player_01"},
    )
    frames = [frame for frame in response.data.decode("utf-8").split("\n\n") if frame.strip()]
    error_frame = next(frame for frame in frames if "event: error" in frame)
    payload_line = next(line for line in error_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.replace("data: ", "", 1))

    assert payload["trace"]["stages"][0]["stage"] == "api.worker"
    assert "worker exited without terminal event" in payload["message"]


def test_create_turn_stream_pre_run_exception_emits_error_event(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 run_turn 前链路抛异常时，SSE 仍会输出 error 事件而非静默断流。
    入参：turns_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示 worker 全链路兜底缺失。
    """

    def failing_get_idempotent_response(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("idem read failed")

    monkeypatch.setattr(
        turns_client.application.extensions["tre_api_context"].session_store,  # type: ignore[attr-defined]
        "get_idempotent_response",
        failing_get_idempotent_response,
    )
    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns/stream",
        json={"request_id": "req_a1pre_sse01", "user_input": "观察", "character_id": "player_01"},
    )
    frames = [frame for frame in response.data.decode("utf-8").split("\n\n") if frame.strip()]
    error_frame = next(frame for frame in frames if "event: error" in frame)
    payload_line = next(line for line in error_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.replace("data: ", "", 1))
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["trace_id"] == "trc_fixed_001"
    assert payload["trace"]["trace_id"] == "trc_fixed_001"
    assert payload["trace"]["stages"][-1]["status"] == "failed"


def test_openapi_declares_create_turn_504_response() -> None:
    """
    功能：校验 OpenAPI 已声明 createTurn 的 504 超时响应，避免真实返回码与契约漂移。
    入参：无。
    出参：None。
    异常：断言失败表示路径级响应码仍不完整。
    """
    spec_path = Path("config/api/openapi.yaml")
    content = spec_path.read_text(encoding="utf-8")
    assert "/api/sessions/{session_id}/turns:" in content
    assert '"504":' in content
    assert "回合执行超时" in content


def test_create_turn_rejects_invalid_outcome_contract(
    turns_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 TurnResult 契约会拦截非法 outcome，防止回合结果语义漂移。
    入参：turns_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示 outcome 枚举约束失效。
    """

    def invalid_outcome_run_turn(
        session: dict[str, Any],
        user_input: str,
        character_id: str,
        sandbox_mode: bool,
        narrative_stream_callback=None,
        trace_id: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        payload = {
            "session_id": session["session_id"],
            "runtime_turn_id": 99,
            "trace_id": trace_id or "trc_test",
            "request_id": request_id,
            "is_valid": True,
            "action_intent": {"type": "observe", "target_id": "", "parameters": {}},
            "physics_diff": {"hp_delta": 0, "mp_delta": 0},
            "final_response": f"响应:{user_input}",
            "quick_actions": ["继续观察"],
            "affordances": [],
            "is_sandbox_mode": bool(sandbox_mode),
            "active_character": {"id": character_id, "inventory": []},
            "scene_snapshot": {
                "schema_version": "scene_snapshot.v2",
                "current_location": {"id": "loc_a", "name": "测试地点", "description": "desc"},
                "visible_npcs": [],
                "visible_items": [],
                "active_quests": [],
                "recent_memory": "",
                "suggested_actions": [],
                "scene_objects": [],
                "exits": [],
                "interaction_slots": [],
                "affordances": [],
                "available_actions": ["observe"],
                "ui_hints": {},
            },
            "outcome": "success",
            "clarification_question": "",
            "failure_reason": "",
            "suggested_next_step": "继续观察",
            "should_advance_turn": True,
            "should_write_story_memory": True,
            "debug_trace": [],
            "errors": [],
            "trace": {"trace_id": trace_id or "trc_test", "stages": [], "errors": []},
        }
        return payload

    monkeypatch.setattr("web_api.blueprints.turns.run_turn", invalid_outcome_run_turn)
    response = turns_client.post(
        "/api/sessions/sess_a1demo01/turns",
        json={
            "request_id": "req_a1_invalid_outcome_01",
            "user_input": "观察周围",
            "character_id": "player_01",
        },
    )
    body = response.get_json()
    assert response.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["trace"]["stages"][-1]["stage"] == "api.response_built"


def test_list_turns_rejects_invalid_page_and_page_size(turns_client) -> None:
    """
    功能：验证 list_turns 会拒绝非法分页参数，避免越界查询和脏分页输入。
    入参：turns_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示 turns 分页参数校验回归。
    """
    not_int = turns_client.get("/api/sessions/sess_a1demo01/turns?page=x&page_size=20")
    out_of_range = turns_client.get("/api/sessions/sess_a1demo01/turns?page=0&page_size=101")
    assert not_int.status_code == 400
    assert out_of_range.status_code == 400
    assert not_int.get_json()["error"]["code"] == "INVALID_ARGUMENT"
    assert out_of_range.get_json()["error"]["code"] == "INVALID_ARGUMENT"


def test_list_turns_and_get_turn_reject_invalid_or_missing_session(turns_client) -> None:
    """
    功能：验证 list/get 读接口在 session_id 非法或会话缺失时返回明确错误。
    入参：turns_client。
    出参：None。
    异常：断言失败表示 turns 读接口会话边界回归。
    """
    list_bad = turns_client.get("/api/sessions/bad/turns")
    list_missing = turns_client.get("/api/sessions/sess_missing01/turns")
    get_bad = turns_client.get("/api/sessions/bad/turns/1")
    get_missing = turns_client.get("/api/sessions/sess_missing01/turns/1")

    assert list_bad.status_code == 400
    assert list_missing.status_code == 404
    assert get_bad.status_code == 400
    assert get_missing.status_code == 404


def test_list_turns_and_get_turn_success(turns_client) -> None:
    """
    功能：验证先创建回合后可通过 list/get 查询到持久化结果，覆盖 turns 读接口主路径。
    入参：turns_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示回合读接口契约回归。
    """
    create_resp = turns_client.post(
        "/api/sessions/sess_a1demo01/turns",
        json={
            "request_id": "req_a1_listget_01",
            "user_input": "观察周围",
            "character_id": "player_01",
        },
    )
    created = create_resp.get_json()
    list_resp = turns_client.get("/api/sessions/sess_a1demo01/turns?page=1&page_size=20")
    list_body = list_resp.get_json()
    get_resp = turns_client.get(
        f"/api/sessions/sess_a1demo01/turns/{created['session_turn_id']}",
    )
    get_body = get_resp.get_json()
    assert create_resp.status_code == 200
    assert list_resp.status_code == 200
    assert get_resp.status_code == 200
    assert list_body["total"] >= 1
    assert len(list_body["items"]) >= 1
    assert get_body["session_turn_id"] == created["session_turn_id"]
    assert get_body["user_input"] == "观察周围"


def test_get_turn_returns_not_found_for_missing_session_turn_id(turns_client) -> None:
    """
    功能：验证 get_turn 对不存在 session_turn_id 返回 TURN_NOT_FOUND。
    入参：turns_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示 turns 详情缺失分支回归。
    """
    response = turns_client.get("/api/sessions/sess_a1demo01/turns/999")
    body = response.get_json()
    assert response.status_code == 404
    assert body["error"]["code"] == "TURN_NOT_FOUND"
