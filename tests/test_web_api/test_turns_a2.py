"""
功能：覆盖 turns a2 的回归测试。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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
        self.main_loop = cast(Any, object())
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


@dataclass(slots=True)
class _FakeTurnInput:
    """
    功能：封装 turns 测试 fake run_turn 所需的调用参数。
    入参：字段对应生产 run_turn 的核心入参，并附带是否返回 A3 分支 payload 的开关。
    出参：无；作为测试数据构造载体传递。
    异常：不抛异常；调用参数缺失会在构造前由取值 helper 暴露。
    """

    session: dict[str, Any]
    user_input: str
    character_id: str
    sandbox_mode: bool
    trace_id: str | None
    request_id: str
    branch_payload: bool


def _branch_quest_states(branch_payload: bool) -> list[dict[str, Any]]:
    """
    功能：构造默认 A2 或 A3 分支测试所需的 quest_states。
    入参：branch_payload（bool）：True 时返回 A3 分支任务态，否则返回 A2 默认任务态。
    出参：list[dict[str, Any]]，可直接写入 fake 回合结果。
    异常：不抛异常；纯测试常量构造。
    """
    if branch_payload:
        return [
            {
                "quest_id": "unmask_the_salt_deal",
                "status": "active",
                "current_stage_id": "report_to_watch",
                "data": {"stages_completed": ["gather_leads", "choose_approach"]},
                "started_at": "2026-05-07T00:00:00Z",
                "updated_at": "2026-05-07T00:00:01Z",
            }
        ]
    return [
        {
            "quest_id": "find_the_key",
            "status": "locked",
            "current_stage_id": "find_clue",
            "data": {},
            "started_at": None,
            "updated_at": None,
        }
    ]


def _branch_trigger_events(branch_payload: bool) -> list[dict[str, Any]]:
    """
    功能：构造 fake 回合中的 trigger_events，覆盖普通 A2 与 A3 分支后果。
    入参：branch_payload（bool）：True 时返回 A3 分支触发事件。
    出参：list[dict[str, Any]]，可直接写入 fake 回合结果。
    异常：不抛异常；纯测试常量构造。
    """
    if branch_payload:
        return [
            {
                "trigger_id": "talk_captain_yun",
                "type": "talk",
                "label": "公开交证据",
                "description": "玩家向云校尉公开证据。",
                "effects": ["narrative", "update_quest", "set_flag"],
                "narrative_text": "云校尉接过证据。",
                "memory_text": "你选择公开把证据交给守备所。",
                "timestamp": "2026-05-07T00:00:00Z",
            }
        ]
    return [
        {
            "trigger_id": "fake_once_trigger",
            "type": "enter_scene",
            "label": "Fake once",
            "description": "Fake once trigger",
            "effects": ["narrative"],
            "narrative_text": "雾林路标背面露出一行新刻的潮湿字迹。",
            "memory_text": "玩家发现雾林路标背面的新刻字迹。",
            "timestamp": "2026-05-07T00:00:00Z",
        }
    ]


def _branch_pack_quests(branch_payload: bool) -> list[dict[str, Any]]:
    """
    功能：为 A3 分支响应构造最小 pack_quests 定义。
    入参：branch_payload（bool）：False 时返回空列表，保持 A2 测试最小响应。
    出参：list[dict[str, Any]]，可供 branch_consequences 解析阶段使用。
    异常：不抛异常；纯测试常量构造。
    """
    if not branch_payload:
        return []
    return [
        {
            "quest_id": "unmask_the_salt_deal",
            "title": "追查私盐契",
            "description": "",
            "stages": [
                {"stage_id": "gather_leads", "label": "搜集盐契线索"},
                {"stage_id": "choose_approach", "label": "决定调查路线"},
                {
                    "stage_id": "report_to_watch",
                    "label": "公开交给守备所",
                    "completion_condition": {
                        "a3_branch_group": "salt_deal_approach",
                        "branch_value": "report_to_watch",
                        "merge_stage_id": "seal_the_evidence",
                    },
                },
                {
                    "stage_id": "strike_quay_bargain",
                    "label": "暗中与线人交易",
                    "completion_condition": {
                        "a3_branch_group": "salt_deal_approach",
                        "branch_value": "strike_quay_bargain",
                        "merge_stage_id": "seal_the_evidence",
                    },
                },
            ],
            "start_stage_id": "gather_leads",
        }
    ]


def _branch_pack_triggers(branch_payload: bool) -> list[dict[str, Any]]:
    """
    功能：为 A3 分支响应构造最小 pack_triggers 定义。
    入参：branch_payload（bool）：False 时返回空列表，保持 A2 默认测试轻量。
    出参：list[dict[str, Any]]，供分支后果摘要根据 trigger 元数据补全描述。
    异常：不抛异常；纯测试常量构造。
    """
    if not branch_payload:
        return []
    return [
        {
            "trigger_id": "talk_captain_yun",
            "type": "talk",
            "label": "公开交证据",
            "description": "玩家向云校尉公开证据。",
            "effects": ["narrative", "update_quest", "set_flag"],
            "conditions": {
                "quest_id": "unmask_the_salt_deal",
                "target_stage_id": "report_to_watch",
                "a3_branch_group": "salt_deal_approach",
                "branch_value": "report_to_watch",
                "memory_text": "你选择公开把证据交给守备所。",
            },
        }
    ]


def _fake_turn_payload(fake_input: _FakeTurnInput, fired_ids: list[str]) -> dict[str, Any]:
    """
    功能：组装 turns 路由测试所需的最小有效 TurnResult。
    入参：fake_input（_FakeTurnInput）：fake run_turn 调用参数；fired_ids（list[str]）：已触发 ID。
    出参：dict[str, Any]，可通过 TurnResultResponse 校验并覆盖 A2/A3 元数据路径。
    异常：不抛异常；测试常量字段缺失应由响应模型断言暴露。
    """
    quest_states = _branch_quest_states(fake_input.branch_payload)
    return {
        "session_id": fake_input.session["session_id"],
        "runtime_turn_id": 7,
        "trace_id": fake_input.trace_id or "trc_a2",
        "request_id": fake_input.request_id,
        "is_valid": True,
        "action_intent": {"type": "observe", "target_id": "", "parameters": {}},
        "physics_diff": (
            {"state_flags_add": ["branch_report_to_watch"]} if fake_input.branch_payload else {}
        ),
        "final_response": f"响应:{fake_input.user_input}",
        "quick_actions": ["检查路标"],
        "affordances": [],
        "is_sandbox_mode": bool(fake_input.sandbox_mode),
        "active_character": {"id": fake_input.character_id, "inventory": []},
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
        "trigger_events": _branch_trigger_events(fake_input.branch_payload),
        "quest_updates": quest_states,
        "quest_states": quest_states,
        "fired_trigger_ids": fired_ids,
        "session_metadata": {
            "fired_trigger_ids": fired_ids,
            "quest_states": quest_states,
        },
        "pack_quests": _branch_pack_quests(fake_input.branch_payload),
        "pack_triggers": _branch_pack_triggers(fake_input.branch_payload),
    }


def _call_arg(args: tuple[Any, ...], kwargs: dict[str, Any], index: int, key: str) -> Any:
    """
    功能：从 fake run_turn 的位置参数或关键字参数中读取指定字段。
    入参：args（tuple[Any, ...]）：位置参数；kwargs（dict[str, Any]）：关键字参数；
        index（int）：位置参数下标；key（str）：关键字名称。
    出参：Any，对应调用值。
    异常：字段缺失时抛 KeyError 或 IndexError，暴露测试 fake 与生产调用签名不匹配。
    """
    return kwargs[key] if key in kwargs else args[index]


class _FakeRunTurn:
    """
    功能：替代主循环，返回满足 TurnResult 的稳定 A2/A3 pack 回合结果。
    入参：seen_metadata（list[dict] | None）：记录 run_turn 入参元数据；
        branch_payload（bool）：是否返回 A3 分支后果 payload。
    出参：_FakeRunTurn 实例，可直接 monkeypatch 到 web_api.blueprints.turns.run_turn。
    异常：调用参数缺失时由 _call_arg 抛出，表示测试 fake 与生产调用签名不一致。
    """

    def __init__(
        self,
        seen_metadata: list[dict[str, Any]] | None,
        branch_payload: bool,
    ) -> None:
        """
        功能：保存 fake run_turn 的测试配置。
        入参：seen_metadata（list[dict] | None）：元数据记录容器；
            branch_payload（bool）：A3 分支 payload 开关。
        出参：None。
        异常：不抛异常。
        """
        self.seen_metadata = seen_metadata
        self.branch_payload = branch_payload

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """
        功能：模拟生产 run_turn 调用并生成稳定回合结果。
        入参：*args/**kwargs：兼容生产 run_turn 的位置或关键字调用方式。
        出参：dict[str, Any]，最小有效回合结果。
        异常：调用参数缺失时由 _call_arg 抛出；测试应据此修正 fake 签名。
        """
        session = cast(dict[str, Any], _call_arg(args, kwargs, 0, "session"))
        metadata = dict(session.get("session_metadata", {}))
        if self.seen_metadata is not None:
            self.seen_metadata.append(metadata)
        fired_ids = list(metadata.get("fired_trigger_ids", []))
        if "fake_once_trigger" not in fired_ids:
            fired_ids.append("fake_once_trigger")
        callback = kwargs.get("narrative_stream_callback")
        if callback is not None:
            callback("雾气涌动。")
        return _fake_turn_payload(
            _FakeTurnInput(
                session=session,
                user_input=str(_call_arg(args, kwargs, 1, "user_input")),
                character_id=str(_call_arg(args, kwargs, 2, "character_id")),
                sandbox_mode=bool(_call_arg(args, kwargs, 3, "sandbox_mode")),
                trace_id=cast(str | None, kwargs.get("trace_id")),
                request_id=str(kwargs.get("request_id", "")),
                branch_payload=self.branch_payload,
            ),
            fired_ids,
        )


def _client(
    case_root: Path,
    monkeypatch: Any,
    seen_metadata: list[dict[str, Any]] | None = None,
    branch_payload: bool = False,
) -> Any:
    """
    功能：构造带 pack 绑定 session 的 turns 测试客户端。
    入参：case_root（Path）：测试目录；monkeypatch（Any）：pytest monkeypatch；
        seen_metadata（list[dict] | None，默认 None）：记录 run_turn 入参元数据；
        branch_payload（bool，默认 False）：为 True 时返回带 A3 分支后果证据的 payload。
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

    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    monkeypatch.setattr(
        "web_api.blueprints.turns.run_turn",
        _FakeRunTurn(seen_metadata, branch_payload),
    )
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
        session = client.application.extensions["tre_api_context"].session_store.get_session(
            "sess_a2pack01"
        )

        assert normal.status_code == 200
        assert stream.status_code == 200
        assert "event: done" in raw_stream
        normal_payload = normal.get_json()
        assert "雾林路标背面露出一行新刻的潮湿字迹。" in normal_payload["final_response"]
        assert normal_payload["quest_states"][0]["quest_id"] == "find_the_key"
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
        assert done_payload["quest_states"][0]["quest_id"] == "find_the_key"
        assert "雾林路标背面露出一行新刻的潮湿字迹。" in done_payload["final_response"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_turn_routes_persist_a2_runtime_metadata(monkeypatch: Any) -> None:
    """
    功能：验证普通回合会持久化 A2 runtime metadata，下一回合可从 session 读取。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 once trigger 或 quest runtime 状态跨 Web 回合丢失。
    """
    case_root = _make_case_root("turns_a2_runtime_metadata")
    seen_metadata: list[dict[str, Any]] = []
    try:
        client = _client(case_root, monkeypatch, seen_metadata=seen_metadata)
        first = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a2_runtime_01", "user_input": "观察路标"},
        )
        second = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a2_runtime_02", "user_input": "继续观察"},
        )
        session = client.application.extensions["tre_api_context"].session_store.get_session(
            "sess_a2pack01"
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert seen_metadata[0] == {}
        assert "fake_once_trigger" in seen_metadata[1].get("fired_trigger_ids", [])
        assert session is not None
        session_metadata = session["session_metadata"]
        assert "fake_once_trigger" in session_metadata.get("fired_trigger_ids", [])
        quest_states = session_metadata.get("quest_states", [])
        assert quest_states[0]["quest_id"] == "find_the_key"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_turn_idempotency_replays_complete_quest_states(monkeypatch: Any) -> None:
    """
    功能：验证同一 request_id 的幂等重放会返回完整 quest_states，且不会再次执行 run_turn。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 A3 任务运行态在幂等缓存或回合历史中丢失。
    """
    case_root = _make_case_root("turns_a3_quest_state_idempotency")
    seen_metadata: list[dict[str, Any]] = []
    try:
        client = _client(case_root, monkeypatch, seen_metadata=seen_metadata)
        first = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a3_qstate_01", "user_input": "观察路标"},
        )
        second = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a3_qstate_01", "user_input": "这次输入不应生效"},
        )
        detail = client.get("/api/sessions/sess_a2pack01/turns/1")

        first_payload = first.get_json()
        second_payload = second.get_json()
        detail_payload = detail.get_json()

        assert first.status_code == 200
        assert second.status_code == 200
        assert detail.status_code == 200
        assert len(seen_metadata) == 1
        assert second_payload["session_turn_id"] == first_payload["session_turn_id"] == 1
        assert second_payload["quest_states"] == first_payload["quest_states"]
        assert detail_payload["quest_states"] == first_payload["quest_states"]
    finally:
        shutil.rmtree(case_root, ignore_errors=True)


def test_turn_routes_return_branch_consequences_for_normal_stream_and_history(
    monkeypatch: Any,
) -> None:
    """
    功能：验证 A3 分支后果摘要在普通响应、SSE done、幂等重放和回合详情中保持一致。
    入参：monkeypatch（Any）：pytest monkeypatch。
    出参：None。
    异常：断言失败表示 branch_consequences 在 API 边界丢失或普通/SSE 不同构。
    """
    case_root = _make_case_root("turns_a3_branch_consequence")
    try:
        client = _client(case_root, monkeypatch, branch_payload=True)
        normal = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a3_branch_01", "user_input": "公开证据"},
        )
        idem = client.post(
            "/api/sessions/sess_a2pack01/turns",
            json={"request_id": "req_a3_branch_01", "user_input": "不应再次执行"},
        )
        stream = client.post(
            "/api/sessions/sess_a2pack01/turns/stream",
            json={"request_id": "req_a3_branch_02", "user_input": "公开证据"},
        )
        detail = client.get("/api/sessions/sess_a2pack01/turns/1")

        normal_payload = normal.get_json()
        idem_payload = idem.get_json()
        detail_payload = detail.get_json()
        frames = [frame for frame in stream.data.decode("utf-8").split("\n\n") if frame.strip()]
        done_frame = next(frame for frame in frames if "event: done" in frame)
        payload_line = next(line for line in done_frame.splitlines() if line.startswith("data: "))
        stream_payload = json.loads(payload_line.replace("data: ", "", 1))

        assert normal.status_code == 200
        assert idem.status_code == 200
        assert stream.status_code == 200
        assert detail.status_code == 200
        consequences = normal_payload["branch_consequences"]
        assert consequences
        assert consequences[0]["quest_id"] == "unmask_the_salt_deal"
        assert consequences[0]["branch_path"] == "report_to_watch"
        assert consequences[0]["to_stage_id"] == "report_to_watch"
        assert len(consequences[0]["state_changes"]) >= 2
        assert idem_payload["branch_consequences"] == consequences
        assert detail_payload["branch_consequences"] == consequences
        assert stream_payload["branch_consequences"][0]["branch_path"] == "report_to_watch"
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
