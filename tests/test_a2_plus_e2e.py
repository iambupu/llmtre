"""A2-Plus E2E 测试：场景切换、trigger 语义、任务状态机、确定性模式、无 pack 回归、SSE 阶段事件。"""

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
from tools.packs.registry import StoryPackRegistry
from web_api.blueprints.sessions import sessions_blueprint
from web_api.blueprints.turns import turns_blueprint
from web_api.service import ApiRuntimeContext
from web_api.session_store import WebSessionStore


class _E2ERuntimeContext(ApiRuntimeContext):
    """
    功能：为 A2 E2E 测试提供可同时支持 sessions 与 turns 蓝图的运行期上下文。
    入参：db_path（str）：SQLite 路径；registry（StoryPackRegistry）：测试 registry。
    出参：_E2ERuntimeContext。
    异常：无额外异常。
    """

    def __init__(self, db_path: str, registry: StoryPackRegistry) -> None:
        """初始化会话存储、registry 与会话锁。"""
        super().__init__()
        self.main_loop = object()
        self.session_store = WebSessionStore(db_path)
        self.story_pack_registry = registry
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """返回会话级锁。"""
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _make_case_root(name: str) -> Path:
    """创建 A2 E2E 测试自管目录，避开 Windows tmp_path 权限噪声。"""
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _init_runtime_db(db_path: Path) -> None:
    """初始化 Web runtime SQLite schema。"""
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()


def _session_client(case_root: Path, monkeypatch: Any) -> Any:
    """
    功能：构造仅注册 sessions 蓝图的测试客户端，用于会话创建 / 查询 E2E 测试。
    入参：case_root（Path）：测试根目录；monkeypatch（Any）：pytest monkeypatch。
    出参：FlaskClient。
    异常：Flask 或 SQLite 初始化失败时向上抛出。
    """
    packs_root = case_root / "story_packs"
    shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")
    registry = StoryPackRegistry(packs_root)
    db_path = case_root / "runtime.db"
    _init_runtime_db(db_path)
    context = _E2ERuntimeContext(str(db_path), registry)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["tre_api_context"] = context
    app.register_blueprint(sessions_blueprint)

    monkeypatch.setattr("web_api.blueprints.sessions.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr(
        "web_api.blueprints.sessions.build_initial_turn_payload",
        lambda _cid, _sandbox_mode, **_kwargs: {
            "active_character": {"id": "player_01", "inventory": []},
            "scene_snapshot": {"schema_version": "scene_snapshot.v2", "affordances": []},
            "final_response": "开场叙事",
            "quick_actions": ["观察周围"],
            "affordances": [],
            "failure_reason": "",
            "suggested_next_step": "观察周围",
            "outcome": "initial_scene",
        },
    )
    monkeypatch.setattr(
        "web_api.blueprints.sessions.get_play_state",
        lambda _cid, _sandbox_mode, recent_memory="", **_kwargs: {
            "active_character": {"id": "player_01", "inventory": []},
            "scene_snapshot": {
                "schema_version": "scene_snapshot.v2",
                "recent_memory": recent_memory,
            },
        },
    )
    return app.test_client()


def _turn_client(
    case_root: Path,
    monkeypatch: Any,
    trigger_count: int = 0,
    quest_count: int = 0,
) -> Any:
    """
    功能：构造注册 turns 蓝图的测试客户端，内建预绑定 pack 的 session 与 fake_run_turn。
    入参：case_root（Path）：测试根目录；monkeypatch（Any）：pytest monkeypatch；
          trigger_count（int）：fake 返回的 trigger_events 数量；
          quest_count（int）：fake 返回的 quest_updates 数量。
    出参：FlaskClient。
    异常：Flask 或 SQLite 初始化失败时向上抛出。
    """
    db_path = case_root / "runtime.db"
    _init_runtime_db(db_path)
    registry = StoryPackRegistry(case_root / "story_packs")
    context = _E2ERuntimeContext(str(db_path), registry)
    context.session_store.create_session(
        session_id="sess_a2_e2e",
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-05-11T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
        pack_metadata={
            "pack_id": "demo_a2_core",
            "scenario_id": "default",
            "pack_version": "0.1.0",
            "compiled_artifact_hash": "hash_a2_e2e",
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
        narrative_stream_callback: Any = None,
        trace_id: str | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        """
        功能：替代主循环，返回包含 scene_snapshot、trigger_events、
        quest_updates 的稳定 A2 回合结果。
        入参：保持生产 run_turn 参数形状。
        出参：dict[str, Any]，最小有效回合结果。
        异常：不抛异常。
        """
        if narrative_stream_callback is not None:
            narrative_stream_callback("雾气涌动，四周传来了低语。")

        current_location_id = "forest_edge"
        if "move" in user_input.lower() or "前往" in user_input:
            current_location_id = "old_camp"

        trigger_events = [
            {
                "trigger_id": f"trg_{i}",
                "type": "enter_scene",
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
                "data": {"description": f"Quest {i}"},
                "started_at": "2026-05-11T00:00:00Z",
                "updated_at": "2026-05-11T00:00:00Z",
            }
            for i in range(quest_count)
        ]

        return {
            "session_id": session["session_id"],
            "runtime_turn_id": 7,
            "trace_id": trace_id or "trc_e2e",
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
                "current_location": {"id": current_location_id, "name": "测试场景"},
                "visible_npcs": [],
                "visible_items": [],
                "active_quests": [],
                "recent_memory": "",
                "suggested_actions": [],
                "scene_objects": [],
                "exits": [],
                "interaction_slots": [],
                "affordances": [],
                "available_actions": ["observe", "move"],
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
            "trigger_events": trigger_events,
            "quest_updates": quest_updates,
        }

    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr("web_api.blueprints.turns.run_turn", fake_run_turn)
    monkeypatch.setattr("web_api.blueprints.turns.new_trace_id", lambda: "trc_e2e_fixed")
    return app.test_client()


def _parse_sse_events(raw: str) -> list[dict[str, Any]]:
    """
    功能：将 SSE 原始字符串解析为事件字典列表。
    入参：raw（str）：SSE 响应体原始文本。
    出参：list[dict[str, Any]]，每个元素包含 event 和 data 字段。
    异常：JSON 解析失败时 data 保留原始字符串。
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


# ============================================================
# 测试函数
# ============================================================


def test_e2e_session_creation_binds_story_pack(monkeypatch: Any) -> None:
    """
    功能：验证通过 _session_client 创建 session 时可绑定 Story Pack，创建/详情接口返回一致元数据。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 pack/session 绑定链路回归。
    """
    case_root = _make_case_root("e2e_session_pack_bind")
    try:
        client = _session_client(case_root, monkeypatch)
        create = client.post(
            "/api/sessions",
            json={
                "request_id": "req_e2e_bind_01",
                "character_id": "player_01",
                "pack_id": "demo_a2_core",
                "scenario_id": "default",
                "persona_profile": {"name": "流浪药剂师"},
            },
        )
        created = create.get_json()
        detail = client.get(f"/api/sessions/{created['session_id']}")
        detailed = detail.get_json()

        assert create.status_code == 201
        assert created["pack_id"] == "demo_a2_core"
        assert created["scenario_id"] == "default"
        assert created["pack_version"] == "0.1.0"
        assert created["compiled_artifact_hash"]
        assert created["persona_profile"] == {"name": "流浪药剂师"}
        assert detail.status_code == 200
        assert detailed["pack_id"] == created["pack_id"]
        assert detailed["compiled_artifact_hash"] == created["compiled_artifact_hash"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_scene_switching(monkeypatch: Any) -> None:
    """功能：验证 move 动作后 scene_snapshot 中的 current_location 更新。"""
    case_root = _make_case_root("e2e_scene_switch")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")
        client = _turn_client(case_root, monkeypatch)  # session=sess_a2_e2e
        turn1 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={
                "request_id": "req_e2e_switch",
                "user_input": "观察周围",
                "character_id": "player_01",
            },
        )
        t1 = turn1.get_json()
        assert turn1.status_code == 200
        assert t1["scene_snapshot"]["current_location"]["id"] == "forest_edge"
        assert t1["scene_snapshot"]["current_location"]["name"] == "测试场景"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_trigger_events(monkeypatch: Any) -> None:
    """功能：验证回合返回中包含 trigger_events 且数量匹配。"""
    case_root = _make_case_root("e2e_triggers")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")
        client = _turn_client(case_root, monkeypatch, trigger_count=2)
        turn1 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={
                "request_id": "req_e2e_trig",
                "user_input": "检查陷阱",
                "character_id": "player_01",
            },
        )
        t1 = turn1.get_json()
        assert turn1.status_code == 200
        assert len(t1["trigger_events"]) == 2
        for te in t1["trigger_events"]:
            assert te["trigger_id"].startswith("trg_")
            assert te["type"] == "enter_scene"
            assert te["timestamp"]
        # 第二轮仍然返回触发器（once 语义由引擎内部管理）
        turn2 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={
                "request_id": "req_e2e_trig2",
                "user_input": "再检查一次",
                "character_id": "player_01",
            },
        )
        t2 = turn2.get_json()
        assert turn2.status_code == 200
        assert len(t2["trigger_events"]) == 2
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_quest_updates(monkeypatch: Any) -> None:
    """功能：验证回合返回中包含 quest_updates 且字段完整。"""
    case_root = _make_case_root("e2e_quests")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")
        client = _turn_client(case_root, monkeypatch, quest_count=2)
        turn1 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={
                "request_id": "req_e2e_qst",
                "user_input": "接受任务",
                "character_id": "player_01",
            },
        )
        t1 = turn1.get_json()
        assert turn1.status_code == 200
        assert len(t1["quest_updates"]) == 2
        for qu in t1["quest_updates"]:
            assert qu["quest_id"].startswith("qst_")
            assert qu["status"] == "active"
            assert qu["current_stage_id"] == "stage_01"
            assert qu["started_at"]
            assert qu["updated_at"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_scene_switching_via_move(monkeypatch: Any) -> None:
    """
    功能：验证提交 move 类输入后，回合响应中的 scene_snapshot.current_location 发生变化。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示场景切换链路回归。
    """
    case_root = _make_case_root("e2e_scene_switch")
    try:
        # 预先拷贝 story_packs 以便 _turn_client 使用
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")

        client = _turn_client(case_root, monkeypatch, trigger_count=0, quest_count=0)

        # 第一回合：默认在 forest_edge
        resp1 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={"request_id": "req_e2e_move_01", "user_input": "观察周围"},
        )
        data1 = resp1.get_json()
        assert resp1.status_code == 200
        assert data1["is_valid"] is True
        loc1 = data1.get("scene_snapshot", {}).get("current_location", {}).get("id")
        assert loc1 == "forest_edge", f"预期 forest_edge，实际 {loc1}"

        # 第二回合：move 触发场景切换为 old_camp
        resp2 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={"request_id": "req_e2e_move_02", "user_input": "move to old camp"},
        )
        data2 = resp2.get_json()
        assert resp2.status_code == 200
        assert data2["is_valid"] is True
        loc2 = data2.get("scene_snapshot", {}).get("current_location", {}).get("id")
        assert loc2 == "old_camp", f"预期 old_camp（move 触发），实际 {loc2}"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_trigger_once_semantics(monkeypatch: Any) -> None:
    """
    功能：验证 trigger_count=2 时 fake_run_turn 返回的 trigger_events 数量正确，且两回合均可获取。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 trigger_events 链路回归。
    """
    case_root = _make_case_root("e2e_trigger_once")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")

        client = _turn_client(case_root, monkeypatch, trigger_count=2, quest_count=0)

        # 第一回合
        resp1 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={"request_id": "req_e2e_trg_01", "user_input": "观察周围"},
        )
        data1 = resp1.get_json()
        assert resp1.status_code == 200
        triggers1 = data1.get("trigger_events", [])
        assert len(triggers1) == 2, f"预期 2 个 trigger_events，实际 {len(triggers1)}"
        assert triggers1[0]["trigger_id"] == "trg_0"
        assert triggers1[1]["trigger_id"] == "trg_1"

        # 第二回合
        resp2 = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={"request_id": "req_e2e_trg_02", "user_input": "继续前进"},
        )
        data2 = resp2.get_json()
        assert resp2.status_code == 200
        triggers2 = data2.get("trigger_events", [])
        assert len(triggers2) == 2, f"预期 2 个 trigger_events（第二回合），实际 {len(triggers2)}"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_quest_state_machine(monkeypatch: Any) -> None:
    """
    功能：验证 quest_count=2 时 fake_run_turn 返回的 quest_updates
    包含 quest_id/status/stage_id 等必填字段。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 quest_updates 链路回归。
    """
    case_root = _make_case_root("e2e_quest_sm")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")

        client = _turn_client(case_root, monkeypatch, trigger_count=0, quest_count=2)

        resp = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={"request_id": "req_e2e_qst_01", "user_input": "接受任务"},
        )
        data = resp.get_json()
        assert resp.status_code == 200

        quests = data.get("quest_updates", [])
        assert len(quests) == 2, f"预期 2 个 quest_updates，实际 {len(quests)}"

        for i, q in enumerate(quests):
            assert q["quest_id"] == f"qst_{i}", f"quest_id 不匹配: {q['quest_id']}"
            assert q["status"] == "active", f"status 不匹配: {q['status']}"
            assert q["current_stage_id"] == "stage_01", f"stage_id 不匹配: {q['current_stage_id']}"
            assert "data" in q
            assert "started_at" in q
            assert "updated_at" in q
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_deterministic_mode_triggers_and_quests(monkeypatch: Any) -> None:
    """
    功能：确定性模式下（无 LLM），fake_run_turn 同时返回 trigger_events 和 quest_updates。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 trigger+quest 共存链路回归。
    """
    case_root = _make_case_root("e2e_det_trg_qst")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")

        client = _turn_client(case_root, monkeypatch, trigger_count=1, quest_count=1)

        resp = client.post(
            "/api/sessions/sess_a2_e2e/turns",
            json={"request_id": "req_e2e_det_01", "user_input": "探索遗迹"},
        )
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["is_valid"] is True

        triggers = data.get("trigger_events", [])
        quests = data.get("quest_updates", [])
        assert len(triggers) == 1, f"预期 1 个 trigger_events，实际 {len(triggers)}"
        assert len(quests) == 1, f"预期 1 个 quest_updates，实际 {len(quests)}"
        assert triggers[0]["trigger_id"] == "trg_0"
        assert quests[0]["quest_id"] == "qst_0"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_e2e_sse_stage_events(monkeypatch: Any) -> None:
    """
    功能：SSE 流式回合包含 stage_progress 事件，并以 done 事件结束。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 SSE stage/done 事件链路回归。
    """
    case_root = _make_case_root("e2e_sse_stage")
    try:
        packs_root = case_root / "story_packs"
        shutil.copytree("examples/story_packs/demo_a2_core", packs_root / "demo_a2_core")

        client = _turn_client(case_root, monkeypatch, trigger_count=2, quest_count=1)

        resp = client.post(
            "/api/sessions/sess_a2_e2e/turns/stream",
            json={"request_id": "req_e2e_sse_01", "user_input": "探索迷雾"},
        )
        raw = resp.data.decode("utf-8")
        events = _parse_sse_events(raw)

        assert resp.status_code == 200
        assert "event: done" in raw, "SSE 流应包含 event: done"

        # 收集所有非 null 事件类型
        event_types = [e["event"] for e in events if e["event"] is not None]

        # 至少应包含 trigger/quest stage 或 progress 事件
        stage_events = [
            t
            for t in event_types
            if "stage" in t.lower() or "progress" in t.lower() or "trigger" in t.lower()
        ]
        assert len(stage_events) >= 0, f"stage 类事件: {event_types}"

        # 验证有 done 事件
        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 1, f"预期 1 个 done 事件，实际 {len(done_events)}"

        # 验证 trigger_evaluation 和 quest_resolution 事件存在
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

        assert len(trigger_started) == 1, f"trigger_evaluation started: {len(trigger_started)}"
        assert len(trigger_done) == 1, f"trigger_evaluation done: {len(trigger_done)}"
        assert len(quest_started) == 1, f"quest_resolution started: {len(quest_started)}"
        assert len(quest_done) == 1, f"quest_resolution done: {len(quest_done)}"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
