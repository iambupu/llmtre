from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from flask import Flask

from state.tools.runtime_schema import ensure_runtime_tables
from web_api.blueprints.sandbox import sandbox_blueprint
from web_api.service import ApiRuntimeContext, TurnExecutionError
from web_api.session_store import WebSessionStore


class _FakeDBUpdater:
    """
    功能：提供可控的 Shadow 状态探针，供 sandbox 接口前置校验测试复用。
    入参：has_shadow（bool，默认 True）：是否存在可用 Shadow 快照。
    出参：_FakeDBUpdater，暴露 `has_shadow_state` 接口。
    异常：无显式异常；调用方可直接修改 has_shadow 模拟状态切换。
    """

    def __init__(self, has_shadow: bool = True) -> None:
        """
        功能：初始化 Shadow 状态开关。
        入参：has_shadow（bool，默认 True）：Shadow 快照是否存在。
        出参：None。
        异常：无。
        """
        self.has_shadow = has_shadow
        self.owner_session_id: str | None = "sess_a1demo01"

    def has_shadow_state(self) -> bool:
        """
        功能：返回当前 Shadow 快照可用性。
        入参：无。
        出参：bool，True 表示存在可用 Shadow 快照。
        异常：无。
        """
        return self.has_shadow

    def is_sandbox_owner(self, session_id: str) -> bool:
        """
        功能：返回当前会话是否持有沙盒租约。
        入参：session_id（str）：会话 ID。
        出参：bool，匹配 owner 时返回 True。
        异常：无。
        """
        return self.owner_session_id == session_id


class _FakeMainLoop:
    """
    功能：提供最小 main_loop 外观，满足 sandbox 蓝图读取 `db_updater` 的依赖。
    入参：db_updater（_FakeDBUpdater）：可控 Shadow 状态探针。
    出参：_FakeMainLoop。
    异常：无。
    """

    def __init__(self, db_updater: _FakeDBUpdater) -> None:
        """
        功能：保存 db_updater 供蓝图校验阶段访问。
        入参：db_updater（_FakeDBUpdater）：Shadow 状态探针。
        出参：None。
        异常：无。
        """
        self.db_updater = db_updater


class _FakeRuntimeContext(ApiRuntimeContext):
    """
    功能：为 sandbox 蓝图测试提供最小运行时上下文，复用真实会话存储与会话锁语义。
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
        self.shadow_probe = _FakeDBUpdater(has_shadow=True)
        self.main_loop = _FakeMainLoop(db_updater=self.shadow_probe)
        self.session_store = WebSessionStore(db_path)
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：返回会话级锁对象，保持与生产代码一致的串行语义。
        入参：session_id（str）：会话标识。
        出参：threading.Lock，会话锁实例。
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
def sandbox_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Generator[Flask]:
    """
    功能：构建仅注册 sandbox 蓝图的最小 Flask 测试客户端，并注入假运行时上下文。
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
        sandbox_mode=True,
        now_iso=_now_iso(),
        memory_policy={"mode": "auto", "max_turns": 20},
    )

    app = Flask(__name__)
    app.register_blueprint(sandbox_blueprint)
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
        功能：替代主循环执行，返回稳定沙盒动作结果，便于验证幂等与事务行为。
        入参：保持与生产 `run_turn` 一致。
        出参：dict[str, Any]，满足沙盒响应构造所需字段。
        异常：不抛异常；异常路径由测试通过 monkeypatch 单独构造。
        """
        call_count["run_turn"] += 1
        return {
            "session_id": session["session_id"],
            "runtime_turn_id": 7,
            "trace_id": trace_id or "trc_test",
            "request_id": request_id,
            "is_valid": True,
            "action_intent": {"type": "commit_sandbox", "target_id": "", "parameters": {}},
            "physics_diff": {"hp_delta": 0, "mp_delta": 0},
            "final_response": f"响应:{user_input}",
            "quick_actions": [],
            "affordances": [],
            "is_sandbox_mode": False,
            "active_character": {"id": character_id, "inventory": []},
            "scene_snapshot": {},
            "outcome": "valid_action",
            "clarification_question": "",
            "failure_reason": "",
            "suggested_next_step": "继续",
            "should_advance_turn": True,
            "should_write_story_memory": True,
            "debug_trace": [],
            "errors": [],
            "trace": {"trace_id": trace_id or "trc_test", "stages": [], "errors": []},
        }

    monkeypatch.setattr("web_api.blueprints.sandbox.run_turn", fake_run_turn)
    monkeypatch.setattr("web_api.blueprints.sandbox.new_trace_id", lambda: "trc_fixed_001")

    client = app.test_client()
    client.call_count = call_count  # type: ignore[attr-defined]
    yield client


def test_sandbox_commit_idempotent_replay_hits_cache(sandbox_client) -> None:
    """
    功能：验证 sandbox commit 同一 request_id 重试命中幂等缓存，不重复执行 run_turn。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示沙盒路径幂等语义退化。
    """
    payload = {"request_id": "req_a1sbx_001"}
    first = sandbox_client.post("/api/sessions/sess_a1demo01/sandbox/commit", json=payload)
    second = sandbox_client.post("/api/sessions/sess_a1demo01/sandbox/commit", json=payload)
    first_body = first.get_json()
    second_body = second.get_json()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first_body["session_turn_id"] == 1
    assert second_body["session_turn_id"] == 1
    assert sandbox_client.call_count["run_turn"] == 1


def test_sandbox_commit_post_run_persist_failure_reuses_trace(
    sandbox_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证沙盒 post-run 持久化异常时返回 trace_id/trace，且失败阶段可验收。
    入参：sandbox_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示沙盒失败链路可观测性不足。
    """

    def failing_persist(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("persist failed")

    monkeypatch.setattr(
        sandbox_client.application.extensions["tre_api_context"].session_store,  # type: ignore[attr-defined]
        "persist_turn_result_with_idempotency",
        failing_persist,
    )
    resp = sandbox_client.post(
        "/api/sessions/sess_a1demo01/sandbox/commit",
        json={"request_id": "req_a1sbx_fail01"},
    )
    body = resp.get_json()
    assert resp.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["trace_id"] == "trc_fixed_001"
    assert body["trace"]["trace_id"] == "trc_fixed_001"
    assert body["trace"]["stages"][-1]["stage"] == "api.persisted"
    assert body["trace"]["stages"][-1]["status"] == "failed"


def test_sandbox_discard_idempotent_replay_hits_cache(sandbox_client) -> None:
    """
    功能：验证 sandbox discard 同一 request_id 重试命中幂等缓存，不重复执行 run_turn。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示 discard 链路幂等语义退化。
    """
    payload = {"request_id": "req_a1sbx_discard_001"}
    first = sandbox_client.post("/api/sessions/sess_a1demo01/sandbox/discard", json=payload)
    second = sandbox_client.post("/api/sessions/sess_a1demo01/sandbox/discard", json=payload)
    first_body = first.get_json()
    second_body = second.get_json()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first_body["discarded"] is True
    assert second_body["discarded"] is True
    assert first_body["session_turn_id"] == second_body["session_turn_id"] == 1
    assert sandbox_client.call_count["run_turn"] == 1


def test_sandbox_commit_rejects_invalid_session_id(sandbox_client) -> None:
    """
    功能：验证 commit 接口在 session_id 格式非法时返回 INVALID_ARGUMENT。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示 path 参数校验分支退化。
    """
    resp = sandbox_client.post(
        "/api/sessions/invalid!/sandbox/commit",
        json={"request_id": "req_a1sbx_invalid_session"},
    )
    body = resp.get_json()
    assert resp.status_code == 400
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_sandbox_discard_rejects_invalid_request_id(sandbox_client) -> None:
    """
    功能：验证 discard 接口在 request_id 缺失时返回 INVALID_ARGUMENT。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示 request 参数校验分支退化。
    """
    resp = sandbox_client.post("/api/sessions/sess_a1demo01/sandbox/discard", json={})
    body = resp.get_json()
    assert resp.status_code == 400
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_sandbox_commit_returns_session_not_found(sandbox_client) -> None:
    """
    功能：验证 commit 在会话不存在时返回 SESSION_NOT_FOUND。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示会话存在性校验分支退化。
    """
    resp = sandbox_client.post(
        "/api/sessions/sess_missing/sandbox/commit",
        json={"request_id": "req_missing_session"},
    )
    body = resp.get_json()
    assert resp.status_code == 404
    assert body["error"]["code"] == "SESSION_NOT_FOUND"


def test_sandbox_discard_returns_turn_execution_error_payload(
    sandbox_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 run_turn 抛 TurnExecutionError 时接口透传其错误码、状态码和 trace_id。
    入参：sandbox_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示执行异常映射分支退化。
    """

    def failing_run_turn(*args: Any, **kwargs: Any) -> Any:
        raise TurnExecutionError(
            message="conflict",
            error_code="EVENT_CONFLICT",
            status_code=409,
            trace_id="trace_err_001",
            trace={"trace_id": "trace_err_001", "stages": [], "errors": []},
        )

    monkeypatch.setattr("web_api.blueprints.sandbox.run_turn", failing_run_turn)
    resp = sandbox_client.post(
        "/api/sessions/sess_a1demo01/sandbox/discard",
        json={"request_id": "req_turn_err_001"},
    )
    body = resp.get_json()
    assert resp.status_code == 409
    assert body["error"]["code"] == "EVENT_CONFLICT"
    assert body["trace_id"] == "trace_err_001"


def test_sandbox_commit_returns_internal_error_on_unexpected_run_turn_exception(
    sandbox_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 run_turn 抛通用异常时返回 INTERNAL_ERROR，避免异常细节泄漏。
    入参：sandbox_client（fixture）；monkeypatch（pytest.MonkeyPatch）。
    出参：None。
    异常：断言失败表示通用异常降级分支退化。
    """

    def boom_run_turn(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("web_api.blueprints.sandbox.run_turn", boom_run_turn)
    resp = sandbox_client.post(
        "/api/sessions/sess_a1demo01/sandbox/commit",
        json={"request_id": "req_run_turn_boom_001"},
    )
    body = resp.get_json()
    assert resp.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"


def test_sandbox_commit_rejects_when_session_not_in_sandbox_mode(sandbox_client) -> None:
    """
    功能：验证 commit 在会话已离开沙盒模式时直接拒绝，避免 API 层强行透传 sandbox_mode=True。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示“会话模式守卫”分支退化。
    """
    runtime = sandbox_client.application.extensions["tre_api_context"]  # type: ignore[attr-defined]
    runtime.session_store.update_memory_summary(  # 仅借助现有存储更新接口触发一次会话写入路径
        session_id="sess_a1demo01",
        memory_summary="keep",
        now_iso=_now_iso(),
    )
    with sqlite3.connect(runtime.session_store.db_path) as connection:
        connection.execute(
            "UPDATE web_sessions SET sandbox_mode = 0 WHERE session_id = ?",
            ("sess_a1demo01",),
        )
        connection.commit()
    resp = sandbox_client.post(
        "/api/sessions/sess_a1demo01/sandbox/commit",
        json={"request_id": "req_guard_not_sandbox_001"},
    )
    body = resp.get_json()
    assert resp.status_code == 409
    assert body["error"]["code"] == "SANDBOX_STATE_INVALID"
    assert sandbox_client.call_count["run_turn"] == 0


def test_sandbox_commit_rejects_when_shadow_state_missing(sandbox_client) -> None:
    """
    功能：验证 commit 在 Shadow 快照不存在时拒绝执行，阻断 merge 空表清空 Active 的风险链路。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示“Shadow 存在性守卫”分支退化。
    """
    runtime = sandbox_client.application.extensions["tre_api_context"]  # type: ignore[attr-defined]
    runtime.shadow_probe.has_shadow = False
    resp = sandbox_client.post(
        "/api/sessions/sess_a1demo01/sandbox/commit",
        json={"request_id": "req_guard_no_shadow_001"},
    )
    body = resp.get_json()
    assert resp.status_code == 409
    assert body["error"]["code"] == "SHADOW_STATE_NOT_FOUND"
    assert sandbox_client.call_count["run_turn"] == 0


def test_sandbox_commit_rejects_when_owner_mismatch(sandbox_client) -> None:
    """
    功能：验证 commit 在会话未持有沙盒租约时拒绝执行，防止跨会话提交他人 Shadow。
    入参：sandbox_client（fixture）：最小 Flask 客户端。
    出参：None。
    异常：断言失败表示 owner 互斥守卫分支退化。
    """
    runtime = sandbox_client.application.extensions["tre_api_context"]  # type: ignore[attr-defined]
    runtime.shadow_probe.owner_session_id = "sess_other_owner"
    resp = sandbox_client.post(
        "/api/sessions/sess_a1demo01/sandbox/commit",
        json={"request_id": "req_guard_owner_mismatch_001"},
    )
    body = resp.get_json()
    assert resp.status_code == 409
    assert body["error"]["code"] == "SANDBOX_OWNER_MISMATCH"
    assert sandbox_client.call_count["run_turn"] == 0
