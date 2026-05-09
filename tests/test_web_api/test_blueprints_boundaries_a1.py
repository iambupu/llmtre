from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from web_api.blueprints import memory, runtime, sessions


def _client_for(*blueprints):
    """
    功能：构造只注册指定 blueprint 的 Flask 测试客户端。
    入参：blueprints：Flask Blueprint 对象列表。
    出参：FlaskClient。
    异常：Flask 初始化或 blueprint 注册失败时向上抛出。
    """
    app = Flask(__name__)
    app.config["TESTING"] = True
    for blueprint in blueprints:
        app.register_blueprint(blueprint)
    return app.test_client()


class _MemoryStore:
    """
    功能：为 memory blueprint 提供最小 session_store 替身。
    入参：turns（list[dict[str, Any]]）：记忆查询返回的回合列表。
    出参：测试辅助对象，记录更新和幂等写入。
    异常：无。
    """

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = turns
        self.saved_payload: dict[str, Any] | None = None
        self.memory_policy: dict[str, Any] | None = None
        self.summary: str | None = None

    def get_recent_story_turns_for_memory(
        self,
        session_id: str,
        max_turns: int,
    ) -> list[dict[str, Any]]:
        """
        功能：返回预置回合列表，并模拟 max_turns 截断。
        入参：session_id（str）：会话 ID；max_turns（int）：记忆窗口。
        出参：list[dict[str, Any]]。
        异常：无。
        """
        return self.turns[-max_turns:]

    def get_idempotent_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        """
        功能：memory refresh 测试中默认不命中幂等缓存。
        入参：scope；session_id；request_id。
        出参：None。
        异常：无。
        """
        return None

    def update_memory_policy(
        self,
        session_id: str,
        memory_policy: dict[str, Any],
        now_iso: str,
    ) -> None:
        """
        功能：记录刷新后的记忆策略。
        入参：session_id；memory_policy；now_iso。
        出参：None。
        异常：无。
        """
        self.memory_policy = memory_policy

    def update_memory_summary(self, session_id: str, memory_summary: str, now_iso: str) -> None:
        """
        功能：记录刷新后的摘要文本。
        入参：session_id；memory_summary；now_iso。
        出参：None。
        异常：无。
        """
        self.summary = memory_summary

    def save_idempotent_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
        response_payload: dict[str, Any],
    ) -> None:
        """
        功能：记录 refresh_memory 写入的幂等响应。
        入参：scope；session_id；request_id；response_payload。
        出参：None。
        异常：无。
        """
        self.saved_payload = response_payload


class _RuntimeStore:
    """
    功能：为 reset_session 提供最小 session_store 替身。
    入参：clear_ok（bool）：clear_session_turns_and_reset 返回值。
    出参：测试辅助对象。
    异常：无。
    """

    def __init__(self, clear_ok: bool) -> None:
        self.clear_ok = clear_ok
        self.saved_payload: dict[str, Any] | None = None

    def get_idempotent_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
    ) -> dict[str, Any] | None:
        """
        功能：reset 测试中默认不命中幂等缓存。
        入参：scope；session_id；request_id。
        出参：None。
        异常：无。
        """
        return None

    def clear_session_turns_and_reset(
        self,
        session_id: str,
        keep_character: bool,
        now_iso: str,
    ) -> bool:
        """
        功能：返回预置清理结果。
        入参：session_id；keep_character；now_iso。
        出参：bool。
        异常：无。
        """
        return self.clear_ok

    def save_idempotent_response(
        self,
        scope: str,
        session_id: str,
        request_id: str,
        response_payload: dict[str, Any],
    ) -> None:
        """
        功能：记录 reset 写入的幂等响应。
        入参：scope；session_id；request_id；response_payload。
        出参：None。
        异常：无。
        """
        self.saved_payload = response_payload


def _lock_context(store: object) -> SimpleNamespace:
    """
    功能：构造带 session_store 和 get_session_lock 的运行时上下文。
    入参：store（object）：session_store 替身。
    出参：SimpleNamespace。
    异常：无。
    """
    return SimpleNamespace(session_store=store, get_session_lock=lambda _sid: threading.Lock())


def test_memory_get_rejects_invalid_missing_and_bad_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证 get_memory 对非法 session_id、缺失会话、非法 format 返回明确错误。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 memory 查询参数边界回归。
    """
    client = _client_for(memory.memory_blueprint)

    invalid = client.get("/api/sessions/bad space/memory")
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == "INVALID_ARGUMENT"

    monkeypatch.setattr(memory, "get_session", lambda _sid: None)
    missing = client.get("/api/sessions/sess_boundary01/memory")
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "SESSION_NOT_FOUND"

    monkeypatch.setattr(
        memory,
        "get_session",
        lambda _sid: {"memory_policy": {"max_turns": 20}},
    )
    bad_format = client.get("/api/sessions/sess_boundary01/memory?format=xml")
    assert bad_format.status_code == 400
    assert bad_format.get_json()["error"]["message"] == "format 仅支持 summary/raw"


def test_memory_get_raw_and_refresh_empty_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证 raw 记忆输出拼接回合文本，refresh 空窗口返回 0..0 覆盖范围。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 memory 主输出或空窗口边界回归。
    """
    client = _client_for(memory.memory_blueprint)
    turns = [
        {"session_turn_id": 1, "user_input": "看", "final_response": "看到门"},
        {"session_turn_id": 2, "user_input": "开门", "final_response": "门开了"},
    ]
    store = _MemoryStore(turns)
    monkeypatch.setattr(
        memory,
        "get_session",
        lambda _sid: {"memory_policy": {"max_turns": 20}},
    )
    monkeypatch.setattr(memory, "get_runtime_context", lambda: _lock_context(store))

    raw = client.get("/api/sessions/sess_boundary01/memory?format=raw")
    raw_body = raw.get_json()
    assert raw.status_code == 200
    assert "第1回合" in raw_body["summary"]
    assert "第2回合" in raw_body["summary"]
    assert raw_body["token_estimate"] > 0

    empty_store = _MemoryStore([])
    monkeypatch.setattr(memory, "get_runtime_context", lambda: _lock_context(empty_store))
    refreshed = client.post(
        "/api/sessions/sess_boundary01/memory/refresh",
        json={"request_id": "req_mem_empty01", "max_turns": 5},
    )
    refreshed_body = refreshed.get_json()
    assert refreshed.status_code == 200
    assert refreshed_body["covered_turn_range"] == {
        "from_session_turn_id": 0,
        "to_session_turn_id": 0,
    }
    assert empty_store.memory_policy == {"mode": "auto", "max_turns": 5}


def test_memory_refresh_rejects_bad_request_and_max_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 refresh_memory 对 request_id 缺失和 max_turns 越界返回 INVALID_ARGUMENT。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 refresh 参数边界回归。
    """
    client = _client_for(memory.memory_blueprint)
    monkeypatch.setattr(
        memory,
        "get_session",
        lambda _sid: {"memory_policy": {"max_turns": 20}},
    )

    missing_request = client.post("/api/sessions/sess_boundary01/memory/refresh", json={})
    assert missing_request.status_code == 400
    assert missing_request.get_json()["error"]["code"] == "INVALID_ARGUMENT"

    bad_turns = client.post(
        "/api/sessions/sess_boundary01/memory/refresh",
        json={"request_id": "req_mem_bad001", "max_turns": 101},
    )
    assert bad_turns.status_code == 400
    assert bad_turns.get_json()["error"]["message"] == "max_turns 需在 5..100"


def test_sessions_create_rejects_invalid_request_character_and_missing_character(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 create_session 对 request_id、character_id 和角色存在性错误返回稳定错误。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 sessions 创建边界回归。
    """
    client = _client_for(sessions.sessions_blueprint)

    invalid_request = client.post("/api/sessions", json={})
    assert invalid_request.status_code == 400
    assert invalid_request.get_json()["error"]["message"] == "request_id 缺失或格式非法"

    monkeypatch.setattr(
        sessions,
        "get_runtime_context",
        lambda: SimpleNamespace(
            session_store=SimpleNamespace(get_idempotent_response=lambda *args, **kwargs: None)
        ),
    )
    invalid_character = client.post(
        "/api/sessions",
        json={"request_id": "req_sess_bad01", "character_id": "x"},
    )
    assert invalid_character.status_code == 400
    assert invalid_character.get_json()["error"]["message"] == "character_id 格式非法"

    monkeypatch.setattr(sessions, "ensure_character_available", lambda _cid: False)
    missing_character = client.post(
        "/api/sessions",
        json={"request_id": "req_sess_missing01", "character_id": "player_01"},
    )
    assert missing_character.status_code == 404
    assert missing_character.get_json()["error"]["code"] == "CHARACTER_NOT_FOUND"


def test_sessions_get_detail_rejects_invalid_and_missing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 get_session_detail 对非法 session_id 和缺失会话返回稳定错误。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 sessions 查询边界回归。
    """
    client = _client_for(sessions.sessions_blueprint)

    invalid = client.get("/api/sessions/bad space")
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == "INVALID_ARGUMENT"

    monkeypatch.setattr(sessions, "get_session", lambda _sid: None)
    missing = client.get("/api/sessions/sess_boundary01")
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_runtime_reset_rejects_invalid_missing_request_and_failed_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 reset_session 对非法路径、缺失会话、request_id 错误和清理失败的响应。
    入参：monkeypatch。
    出参：None。
    异常：断言失败表示 runtime reset 边界回归。
    """
    client = _client_for(runtime.runtime_blueprint)

    invalid = client.post("/api/sessions/bad space/reset", json={})
    assert invalid.status_code == 400
    assert invalid.get_json()["error"]["code"] == "INVALID_ARGUMENT"

    monkeypatch.setattr(runtime, "get_session", lambda _sid: None)
    missing = client.post("/api/sessions/sess_boundary01/reset", json={})
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "SESSION_NOT_FOUND"

    monkeypatch.setattr(
        runtime,
        "get_session",
        lambda _sid: {"session_id": "sess_boundary01"},
    )
    bad_request = client.post("/api/sessions/sess_boundary01/reset", json={})
    assert bad_request.status_code == 400
    assert bad_request.get_json()["error"]["message"] == "request_id 缺失或格式非法"

    store = _RuntimeStore(clear_ok=False)
    monkeypatch.setattr(runtime, "get_runtime_context", lambda: _lock_context(store))
    failed_clear = client.post(
        "/api/sessions/sess_boundary01/reset",
        json={"request_id": "req_reset_fail01", "keep_character": False},
    )
    assert failed_clear.status_code == 404
    assert failed_clear.get_json()["error"]["code"] == "SESSION_NOT_FOUND"
