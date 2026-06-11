"""
功能：覆盖 SSE A2-Plus 触发器与任务阶段事件的回归测试。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask

from state.tools.runtime_schema import ensure_runtime_tables
from web_api.blueprints.turns import turns_blueprint
from web_api.service import ApiRuntimeContext
from web_api.session_store import WebSessionStore


class _SSETestRuntimeContext(ApiRuntimeContext):
    """
    功能：提供 SSE 路由测试用运行时上下文。
    入参：db_path（str）：测试 SQLite 数据库路径。
    出参：_SSETestRuntimeContext 实例，携带 session_store 与会话锁。
    异常：初始化失败时向上抛出，交由 pytest 报告。
    """

    def __init__(self, db_path: str) -> None:
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        super().__init__()
        self.main_loop = object()
        self.session_store = WebSessionStore(db_path)
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：提供 get session lock 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _make_case_root(name: str) -> Path:
    """
    功能：提供 make case root 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _init_runtime_db(db_path: Path) -> None:
    """
    功能：提供 init runtime db 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


def _client(case_root: Path, monkeypatch: Any, trigger_count: int = 0, quest_count: int = 2) -> Any:
    """
    功能：构造带伪造 run_turn 的 Flask 测试客户端。
    入参：case_root（Path）：测试临时目录；monkeypatch（Any）：pytest monkeypatch；
        trigger_count（int）：伪造触发器事件数量；quest_count（int）：伪造任务更新数量。
    出参：Any，Flask test_client，用于请求 SSE 回合路由。
    异常：数据库初始化或 Flask app 注册失败时向上抛出，交由 pytest 报告。
    """
    db_path = case_root / "runtime.db"
    _init_runtime_db(db_path)
    context = _SSETestRuntimeContext(str(db_path))
    context.session_store.create_session(
        session_id="sess_sse_a2p01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-11T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
        pack_metadata={
            "pack_id": "demo_a2_core",
            "scenario_id": "default",
            "pack_version": "0.1.0",
            "compiled_artifact_hash": "hash_a2_demo",
        },
    )

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["tre_api_context"] = context
    app.register_blueprint(turns_blueprint)

    def fake_run_turn(
        session,
        user_input,
        character_id,
        sandbox_mode,
        narrative_stream_callback=None,
        trace_id=None,
        request_id="",
    ) -> dict[str, Any]:
        """
        功能：提供 fake run turn 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        if narrative_stream_callback is not None:
            narrative_stream_callback("Test narrative.")

        trigger_events = [
            {
                "trigger_id": f"trg_{i}",
                "type": "observe",
                "label": f"Trigger {i}",
                "description": f"Test trigger event {i}",
                "effects": ["narrative"],
                "timestamp": "2026-05-11T00:00:00Z",
            }
            for i in range(trigger_count)
        ]
        quest_updates = [
            {
                "quest_id": f"qst_{i}",
                "status": "active",
                "current_stage_id": "stage_01",
                "data": {},
                "started_at": "2026-05-11T00:00:00Z",
                "updated_at": "2026-05-11T00:00:00Z",
            }
            for i in range(quest_count)
        ]

        return {
            "session_id": session["session_id"],
            "runtime_turn_id": 1,
            "trace_id": trace_id or "trc_sse",
            "request_id": request_id,
            "is_valid": True,
            "action_intent": {"type": "observe", "target_id": "", "parameters": {}},
            "physics_diff": {},
            "final_response": f"SSE response:{user_input}",
            "quick_actions": [],
            "affordances": [],
            "is_sandbox_mode": bool(sandbox_mode),
            "active_character": {"id": character_id, "inventory": []},
            "scene_snapshot": {
                "schema_version": "scene_snapshot.v2",
                "current_location": {"id": "forest_edge", "name": "Test Forest"},
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
            "outcome": "valid_action",
            "clarification_question": "",
            "failure_reason": "",
            "suggested_next_step": "Look around",
            "should_advance_turn": True,
            "should_write_story_memory": True,
            "debug_trace": [],
            "errors": [],
            "trigger_events": trigger_events,
            "quest_updates": quest_updates,
        }

    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr("web_api.blueprints.turns.run_turn", fake_run_turn)
    monkeypatch.setattr("web_api.blueprints.turns.new_trace_id", lambda: "trc_sse_fixed")
    return app.test_client()


def _parse_sse_events(raw: str) -> list[dict[str, Any]]:
    """
    功能：将原始 SSE 文本解析为事件字典列表。
    入参：raw（str）：Flask 测试响应中的 text/event-stream 文本。
    出参：list[dict[str, Any]]，每项包含 event 与 data 字段。
    异常：单个 data 不是 JSON 时降级保留原始字符串，不向外抛出。
    """
    events: list[dict[str, Any]] = []
    for frame in raw.split("\n\n"):
        if not frame.strip():
            continue
        event_type = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    data = line[6:]
        events.append({"event": event_type, "data": data})
    return events


def test_sse_stream_contains_trigger_evaluation_events(monkeypatch: Any) -> None:
    """
    功能：验证 SSE 流包含 trigger_evaluation started/done 事件和正确计数。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换角色校验与 run_turn。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；临时目录在 finally 中清理。
    """
    case_root = _make_case_root("sse_trigger")
    try:
        client = _client(case_root, monkeypatch, trigger_count=3, quest_count=0)
        resp = client.post(
            "/api/sessions/sess_sse_a2p01/turns/stream",
            json={"request_id": "req_sse_t1", "user_input": "test trigger"},
        )
        raw = resp.data.decode("utf-8")
        events = _parse_sse_events(raw)

        assert resp.status_code == 200
        assert "event: done" in raw

        trigger_started = [
            e
            for e in events
            if e["event"] == "trigger_evaluation"
            and isinstance(e["data"], dict)
            and e["data"].get("status") == "started"
        ]
        trigger_done = [
            e
            for e in events
            if e["event"] == "trigger_evaluation"
            and isinstance(e["data"], dict)
            and e["data"].get("status") == "done"
        ]

        assert len(trigger_started) == 1, (
            f"Expected 1 trigger_evaluation started, " f"got {len(trigger_started)}"
        )
        assert len(trigger_done) == 1, (
            f"Expected 1 trigger_evaluation done, " f"got {len(trigger_done)}"
        )
        (f"Expected triggers_fired=3, " f"got {trigger_done[0]['data'].get('triggers_fired')}")
        assert trigger_done[0]["data"]["triggers_fired"] == 3
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_sse_stream_contains_quest_resolution_events(monkeypatch: Any) -> None:
    """
    功能：验证 SSE 流包含 quest_resolution started/done 事件和正确计数。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换角色校验与 run_turn。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；临时目录在 finally 中清理。
    """
    case_root = _make_case_root("sse_quest")
    try:
        client = _client(case_root, monkeypatch, trigger_count=0, quest_count=5)
        resp = client.post(
            "/api/sessions/sess_sse_a2p01/turns/stream",
            json={"request_id": "req_sse_q1", "user_input": "test quest"},
        )
        raw = resp.data.decode("utf-8")
        events = _parse_sse_events(raw)

        assert resp.status_code == 200

        quest_started = [
            e
            for e in events
            if e["event"] == "quest_resolution"
            and isinstance(e["data"], dict)
            and e["data"].get("status") == "started"
        ]
        quest_done = [
            e
            for e in events
            if e["event"] == "quest_resolution"
            and isinstance(e["data"], dict)
            and e["data"].get("status") == "done"
        ]

        assert len(quest_started) == 1, (
            f"Expected 1 quest_resolution started, " f"got {len(quest_started)}"
        )
        assert len(quest_done) == 1, f"Expected 1 quest_resolution done, " f"got {len(quest_done)}"
        (f"Expected quests_updated=5, " f"got {quest_done[0]['data'].get('quests_updated')}")
        assert quest_done[0]["data"]["quests_updated"] == 5
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_sse_stream_zero_counts_when_no_pack_data(monkeypatch: Any) -> None:
    """
    功能：验证没有触发器或任务数据时 SSE 阶段事件仍返回 0 计数。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换角色校验与 run_turn。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；临时目录在 finally 中清理。
    """
    case_root = _make_case_root("sse_zero")
    try:
        client = _client(case_root, monkeypatch, trigger_count=0, quest_count=0)
        resp = client.post(
            "/api/sessions/sess_sse_a2p01/turns/stream",
            json={"request_id": "req_sse_z1", "user_input": "test zero"},
        )
        raw = resp.data.decode("utf-8")
        events = _parse_sse_events(raw)

        assert resp.status_code == 200

        trigger_done = [
            e
            for e in events
            if e["event"] == "trigger_evaluation"
            and isinstance(e["data"], dict)
            and e["data"].get("status") == "done"
        ]
        quest_done = [
            e
            for e in events
            if e["event"] == "quest_resolution"
            and isinstance(e["data"], dict)
            and e["data"].get("status") == "done"
        ]

        assert len(trigger_done) == 1
        assert trigger_done[0]["data"]["triggers_fired"] == 0
        assert len(quest_done) == 1
        assert quest_done[0]["data"]["quests_updated"] == 0
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_sse_stage_events_retain_pack_metadata(monkeypatch: Any) -> None:
    """
    功能：验证新增 SSE 阶段事件不会破坏剧本包会话元数据。
    入参：monkeypatch（Any）：pytest monkeypatch，用于替换角色校验与 run_turn。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；临时目录在 finally 中清理。
    """
    case_root = _make_case_root("sse_packmeta")
    try:
        client = _client(case_root, monkeypatch, trigger_count=1, quest_count=2)
        resp = client.post(
            "/api/sessions/sess_sse_a2p01/turns/stream",
            json={"request_id": "req_sse_m1", "user_input": "test pack meta"},
        )
        raw = resp.data.decode("utf-8")
        context = client.application.extensions["tre_api_context"]
        session = context.session_store.get_session("sess_sse_a2p01")

        assert resp.status_code == 200
        assert "event: done" in raw
        assert session is not None
        assert session["pack_id"] == "demo_a2_core"
        assert session["scenario_id"] == "default"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
