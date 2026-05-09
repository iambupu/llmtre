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


class _A2TurnRuntimeContext(ApiRuntimeContext):
    """
    功能：为 A2 turns 测试提供真实 session_store 与会话锁。
    入参：db_path（str）：SQLite 路径。
    出参：_A2TurnRuntimeContext。
    异常：无额外异常。
    """

    def __init__(self, db_path: str) -> None:
        """
        功能：初始化 turns 测试运行时。
        入参：db_path（str）：SQLite 文件路径。
        出参：None。
        异常：父类初始化失败时向上抛出。
        """
        super().__init__()
        self.main_loop = object()
        self.session_store = WebSessionStore(db_path)
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：返回会话级锁，保持普通/SSE 路由串行语义。
        入参：session_id（str）：会话 ID。
        出参：threading.Lock。
        异常：无。
        """
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _make_case_root(name: str) -> Path:
    """
    功能：创建 turns A2 测试自管目录，避开 Windows tmp_path 权限噪声。
    入参：name（str）：用例名前缀。
    出参：Path，已创建目录。
    异常：目录创建失败时向上抛出。
    """
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _init_runtime_db(db_path: Path) -> None:
    """
    功能：初始化 Web runtime SQLite schema。
    入参：db_path（Path）：SQLite 文件路径。
    出参：None。
    异常：SQL 执行失败时向上抛出。
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


def _client(case_root: Path, monkeypatch: Any) -> Any:
    """
    功能：构造带 pack 绑定 session 的 turns 测试客户端。
    入参：case_root（Path）：测试目录；monkeypatch（Any）：pytest monkeypatch。
    出参：FlaskClient。
    异常：Flask 或 SQLite 初始化失败时向上抛出。
    """
    db_path = case_root / "runtime.db"
    _init_runtime_db(db_path)
    context = _A2TurnRuntimeContext(str(db_path))
    context.session_store.create_session(
        session_id="sess_a2pack01",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-07T00:00:00Z",
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
        session: dict[str, Any],
        user_input: str,
        character_id: str,
        sandbox_mode: bool,
        narrative_stream_callback=None,
        trace_id: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        """
        功能：替代主循环，返回满足 TurnResult 的稳定 A2 pack 回合结果。
        入参：保持生产 run_turn 参数形状。
        出参：dict[str, Any]，最小有效回合结果。
        异常：不抛异常。
        """
        if narrative_stream_callback is not None:
            narrative_stream_callback("雾气涌动。")
        return {
            "session_id": session["session_id"],
            "runtime_turn_id": 7,
            "trace_id": trace_id or "trc_a2",
            "request_id": request_id,
            "is_valid": True,
            "action_intent": {"type": "observe", "target_id": "", "parameters": {}},
            "physics_diff": {},
            "final_response": f"响应:{user_input}",
            "quick_actions": ["检查路标"],
            "affordances": [],
            "is_sandbox_mode": bool(sandbox_mode),
            "active_character": {"id": character_id, "inventory": []},
            "scene_snapshot": {
                "schema_version": "scene_snapshot.v2",
                "current_location": {"id": "forest_edge", "name": "雾林边缘"},
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
            "suggested_next_step": "检查路标",
            "should_advance_turn": True,
            "should_write_story_memory": True,
            "debug_trace": [],
            "errors": [],
        }

    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr("web_api.blueprints.turns.run_turn", fake_run_turn)
    monkeypatch.setattr("web_api.blueprints.turns.new_trace_id", lambda: "trc_a2_fixed")
    return app.test_client()


def test_create_turn_and_stream_preserve_pack_session_metadata(monkeypatch: Any) -> None:
    """
    功能：验证普通与 SSE 回合都不会丢失 session 上的 A2 pack 绑定元数据。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示普通/SSE 路由持久化时破坏 pack/session 元数据。
    """
    case_root = _make_case_root("turns_pack_metadata")
    try:
        client = _client(case_root, monkeypatch)
        normal = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a2_turn_normal_01", "user_input": "观察路标"},
        )
        stream = client.post(
            "/api/sessions/sess_a2pack01/turns/stream",
            json={"request_id": "req_a2_turn_stream_01", "user_input": "继续观察"},
        )
        raw_stream = stream.data.decode("utf-8")
        session = client.application.extensions["tre_api_context"].session_store.get_session(  # type: ignore[attr-defined]
            "sess_a2pack01"
        )

        assert normal.status_code == 200
        assert stream.status_code == 200
        assert "event: done" in raw_stream
        assert session is not None
        assert session["pack_id"] == "demo_a2_core"
        assert session["scenario_id"] == "default"
        assert session["pack_version"] == "0.1.0"
        assert session["compiled_artifact_hash"] == "hash_a2_demo"
        frames = [frame for frame in raw_stream.split("\n\n") if frame.strip()]
        done_frame = next(frame for frame in frames if "event: done" in frame)
        payload_line = next(line for line in done_frame.splitlines() if line.startswith("data: "))
        done_payload = json.loads(payload_line.replace("data: ", "", 1))
        assert done_payload["session_turn_id"] == 2
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
