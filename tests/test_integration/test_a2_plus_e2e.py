"""
A2-Plus E2E 集成测试：验证 demo_a2_core 剧本包在确定性主循环中的
场景解析、触发器评估、任务推进和场景切换全链路。
"""

import asyncio
import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any

from flask import Flask

from core.event_bus import EventBus
from game_workflows.main_event_loop import MainEventLoop
from state.contracts.turn import TurnRequestContext
from state.tools.db_initializer import DBInitializer
from state.tools.runtime_schema import ensure_runtime_tables
from tools.entity.entity_probes import EntityProbes
from tools.packs.registry import StoryPackRegistry
from tools.sqlite_db.db_updater import DBUpdater
from web_api.blueprints.turns import turns_blueprint
from web_api.service import ApiRuntimeContext
from web_api.session_store import WebSessionStore, build_session_runtime_character_id

A3_PACK_ID = "a3_branching_quest"
A3_QUEST_ID = "unmask_the_salt_deal"


@dataclass(slots=True)
class _A3PlaythroughSpec:
    """
    功能：封装一条 A3 golden playthrough 路线的输入与预期后果。
    入参：各字段为路线标识、会话 ID、关键输入、预期触发器、任务阶段与状态标签。
    出参：无；作为测试 helper 参数对象传递。
    异常：不抛异常；字段错误会在断言阶段暴露。
    """

    route: str
    session_id: str
    branch_input: str
    branch_trigger_id: str
    branch_stage_id: str
    branch_flag: str
    merge_input: str
    merge_trigger_id: str
    merge_flag: str
    archive_input: str


class DummyRAGBridge:
    """功能：测试用 RAG 桥接桩，恒返回就绪状态避免 RAG 索引初始化副作用。"""

    def __init__(self, ready: bool = True) -> None:
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.ready = ready

    def build_snapshot(self, query: str):  # type: ignore[no-untyped-def]
        """
        功能：提供 build snapshot 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        if self.ready:
            return {
                "rag_enabled": True,
                "rag_ready": True,
                "rag_query": query,
                "rag_context": "规则片段",
                "rag_error": "",
            }
        return {
            "rag_enabled": True,
            "rag_ready": False,
            "rag_query": query,
            "rag_context": "",
            "rag_error": "rag unavailable",
        }


class CollectingOuterBridge:
    """功能：收集外环投递事件，供 A2 pack 场景位置口径断言使用。"""

    def __init__(self) -> None:
        """
        功能：初始化外环事件收集容器。
        入参：无。
        出参：None。
        异常：无显式异常；内存分配异常向上抛出。
        """
        self.state_events = []
        self.turn_events = []
        self.world_events = []

    async def emit_state_changed(self, event):  # type: ignore[no-untyped-def]
        """
        功能：记录 state_changed 外环事件。
        入参：event：外环状态变更事件对象。
        出参：None。
        异常：不抛业务异常；测试桩仅追加到内存列表。
        """
        self.state_events.append(event)

    async def emit_turn_ended(self, event):  # type: ignore[no-untyped-def]
        """
        功能：记录 turn_ended 外环事件。
        入参：event：外环回合结束事件对象。
        出参：None。
        异常：不抛业务异常；测试桩仅追加到内存列表。
        """
        self.turn_events.append(event)

    async def emit_world_evolution(self, event):  # type: ignore[no-untyped-def]
        """
        功能：记录 world_evolution 外环事件。
        入参：event：外环世界演化事件对象。
        出参：None。
        异常：不抛业务异常；测试桩仅追加到内存列表。
        """
        self.world_events.append(event)


class A3WebRuntimeContext(ApiRuntimeContext):
    """
    功能：为 A3 Web 普通/SSE golden playthrough 提供真实主循环与 session_store。
    """

    def __init__(
        self,
        *,
        db_path: str,
        main_loop: MainEventLoop,
        registry: StoryPackRegistry,
        agent_context_dir: Any,
    ) -> None:
        """
        功能：初始化 Web 集成测试运行时，复用同一个 SQLite 与 Story Pack registry。
        入参：db_path（str）：测试 SQLite 路径；main_loop（MainEventLoop）：真实确定性主循环；
            registry（StoryPackRegistry）：指向 examples/story_packs 的 registry；
            agent_context_dir（Any）：会话记忆测试目录。
        出参：None。
        异常：父类或 WebSessionStore 初始化失败时向上抛出。
        """
        super().__init__()
        self.main_loop = main_loop
        self.session_store = WebSessionStore(db_path)
        self.story_pack_registry = registry
        self.agent_context_dir = str(agent_context_dir)
        self._locks: dict[str, threading.Lock] = {}

    def get_session_lock(self, session_id: str) -> threading.Lock:
        """
        功能：返回会话级串行锁，保证普通与 SSE 回合不会并发写同一 session。
        入参：session_id（str）：会话 ID。
        出参：threading.Lock。
        异常：无。
        """
        if session_id not in self._locks:
            self._locks[session_id] = threading.Lock()
        return self._locks[session_id]


def _build_loop_with_pack(tmp_path, outer_bridge=None):
    """
    功能：构建注入 StoryPackRegistry 的确定性 MainEventLoop。
    入参：tmp_path（Path）：pytest tmp_path fixture；outer_bridge（Any | None，默认 None）：
        可选外环桥接桩，用于断言外环事件负载。
    出参：MainEventLoop，已关闭 NLU/GM LLM。
    异常：数据库初始化失败时向上抛出。
    """
    db_path = tmp_path / "tre_state.db"
    initializer = DBInitializer(db_path=str(db_path))
    initializer.initialize_db()

    db_updater = DBUpdater(str(db_path))
    entity_probes = EntityProbes(str(db_path))
    event_bus = EventBus("config/mod_registry.yml", "mods")

    from pathlib import Path as _Path

    # 示例剧本只作为外部测试 fixture 注入，默认 story_packs/ 保持空目录边界。
    registry = StoryPackRegistry(
        str(_Path(__file__).resolve().parents[2] / "examples" / "story_packs")
    )
    registry.refresh()

    loop = MainEventLoop(
        event_bus,
        rag_bridge=DummyRAGBridge(),
        db_updater=db_updater,
        entity_probes=entity_probes,
        story_pack_registry=registry,
        outer_bridge=outer_bridge,
        agent_context_dir=tmp_path / ".agent_context",
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    # 移动方向别名：使确定性 NLU 能解析「向北走」等自然语言
    loop.nlu_agent.nlu_rules["location_aliases"] = {
        "north": "北",
        "east": "东",
        "south": "南",
        "west": "西",
    }
    return loop


def _build_a3_web_client(tmp_path):
    """
    功能：构建使用真实 MainEventLoop 的 Flask test client，覆盖普通与 SSE Web 路由。
    入参：tmp_path（Path）：pytest tmp_path fixture。
    出参：tuple[Any, A3WebRuntimeContext]，FlaskClient 与运行时上下文。
    异常：数据库迁移、registry 刷新或 Flask 初始化失败时向上抛出。
    """
    loop = _build_loop_with_pack(tmp_path)
    db_path = str(loop.db_updater.db_path)
    with sqlite3.connect(db_path) as connection:
        ensure_runtime_tables(connection.cursor())
        connection.commit()

    registry = loop.story_pack_registry
    assert registry is not None
    context = A3WebRuntimeContext(
        db_path=db_path,
        main_loop=loop,
        registry=registry,
        agent_context_dir=tmp_path / ".agent_context_web",
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.extensions["tre_api_context"] = context
    app.register_blueprint(turns_blueprint)
    return app.test_client(), context


def _create_a3_web_session(context: A3WebRuntimeContext, session_id: str) -> None:
    """
    功能：在 Web session_store 中创建绑定 A3 pack 的会话，并把运行角色放到起点场景。
    入参：context（A3WebRuntimeContext）：测试运行时；session_id（str）：会话 ID。
    出参：None。
    异常：pack 不存在或 SQLite 写入失败时向上抛出。
    """
    bundle = context.story_pack_registry.get(A3_PACK_ID)
    assert bundle is not None
    summary = bundle.summary
    context.session_store.create_session(
        session_id=session_id,
        character_id="player_01",
        sandbox_mode=False,
        now_iso="2026-06-15T00:00:00Z",
        memory_policy={"mode": "auto", "max_turns": 20},
        pack_metadata={
            "pack_id": summary.pack_id,
            "scenario_id": summary.scenario_id,
            "pack_version": summary.version,
            "compiled_artifact_hash": summary.compiled_artifact_hash,
        },
        runtime_character_id=build_session_runtime_character_id(session_id),
        initial_location_id=summary.start_scene_id,
        initial_session_metadata={"fired_trigger_ids": []},
    )


def _done_payload_from_sse(raw_stream: str) -> dict[str, Any]:
    """
    功能：从 text/event-stream 文本中提取最终 done payload。
    入参：raw_stream（str）：SSE 响应文本。
    出参：dict[str, Any]，done 事件 JSON 负载。
    异常：缺少 done 或 data 行时触发 StopIteration/AssertionError，表示 SSE 未完成。
    """
    frames = [frame for frame in raw_stream.split("\n\n") if frame.strip()]
    done_frame = next(frame for frame in frames if "event: done" in frame)
    payload_line = next(line for line in done_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(payload_line.replace("data: ", "", 1))
    assert isinstance(payload, dict)
    return payload


def _post_a3_normal_turn(
    client: Any, session_id: str, index: int, user_input: str
) -> dict[str, Any]:
    """
    功能：通过普通 Web 回合路由提交 A3 输入并返回 JSON payload。
    入参：client（Any）：FlaskClient；session_id（str）：会话 ID；index（int）：回合序号；
        user_input（str）：玩家输入。
    出参：dict[str, Any]，普通回合响应体。
    异常：HTTP 非 200 或响应非对象时断言失败。
    """
    response = client.post(
        f"/api/sessions/{session_id}/turns",
        json={"request_id": f"req_{session_id}_{index:02d}", "user_input": user_input},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert isinstance(payload, dict)
    return payload


def _post_a3_stream_turn(
    client: Any, session_id: str, index: int, user_input: str
) -> dict[str, Any]:
    """
    功能：通过 SSE Web 回合路由提交 A3 输入，并消费到 done 后返回最终 payload。
    入参：client（Any）：FlaskClient；session_id（str）：会话 ID；index（int）：回合序号；
        user_input（str）：玩家输入。
    出参：dict[str, Any]，SSE done 事件负载。
    异常：HTTP 非 200、缺少 done 或最终 payload 非对象时断言失败。
    """
    response = client.post(
        f"/api/sessions/{session_id}/turns/stream",
        json={"request_id": f"req_{session_id}_{index:02d}", "user_input": user_input},
    )
    raw_stream = response.data.decode("utf-8")
    assert response.status_code == 200, raw_stream
    assert "event: done" in raw_stream
    return _done_payload_from_sse(raw_stream)


def _assert_a3_web_route_completed(
    final_payload: dict[str, Any],
    *,
    expected_branch: str,
    expected_turns: int,
) -> None:
    """
    功能：断言 Web 路由返回的 A3 最终 payload 已完成任务并保留分支事实。
    入参：final_payload（dict[str, Any]）：普通或 SSE 最终回合 payload；expected_branch（str）：
        预期 branch_path；expected_turns（int）：预期 session_turn_id。
    出参：None。
    异常：断言失败表示 Web 路由没有完成 A3 golden playthrough。
    """
    quest_state = _find_a3_quest_state(final_payload)
    assert final_payload["session_turn_id"] == expected_turns
    assert quest_state["status"] == "completed"
    assert quest_state["current_stage_id"] == "case_closed"
    assert quest_state["data"]["branch_path"] == expected_branch
    assert "inspect_archive_seal" in {
        event.get("trigger_id", "") for event in final_payload.get("trigger_events", [])
    }
    assert "salt_contract_case_closed" in final_payload.get("physics_diff", {}).get(
        "state_flags_add",
        [],
    )


def _build_no_pack_loop(tmp_path):
    """
    功能：构建不含 StoryPackRegistry 的确定性 MainEventLoop。
    入参：tmp_path（Path）：pytest tmp_path fixture。
    出参：MainEventLoop，已关闭 NLU/GM LLM。
    异常：数据库初始化失败时向上抛出。
    """
    db_path = tmp_path / "tre_state.db"
    initializer = DBInitializer(db_path=str(db_path))
    initializer.initialize_db()

    db_updater = DBUpdater(str(db_path))
    entity_probes = EntityProbes(str(db_path))
    event_bus = EventBus("config/mod_registry.yml", "mods")

    loop = MainEventLoop(
        event_bus,
        rag_bridge=DummyRAGBridge(),
        db_updater=db_updater,
        entity_probes=entity_probes,
        agent_context_dir=tmp_path / ".agent_context",
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    return loop


def _run_a3_turn(
    loop: MainEventLoop,
    metadata: dict[str, Any],
    user_input: str,
    request_suffix: str,
    session_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    功能：执行 A3 golden pack 单回合，并把完整任务态转成下一回合 session metadata。
    入参：loop（MainEventLoop）：关闭 LLM 的确定性主循环；metadata（dict[str, Any]）：
        上一回合返回的 fired_trigger_ids 与 quest_states；user_input（str）：玩家输入；
        request_suffix（str）：生成 trace_id/request_id 的稳定后缀；session_id（str）：
        测试会话 ID，要求同一 playthrough 内保持不变。
    出参：tuple[dict[str, Any], dict[str, Any]]，第一项为本回合结果，第二项为下一回合 metadata。
    异常：主循环异常向上抛出；断言失败由调用方 pytest 断言暴露。
    """
    context = TurnRequestContext(
        trace_id=f"t-a3-{request_suffix}",
        request_id=f"r-a3-{request_suffix}",
        session_id=session_id,
        pack_id=A3_PACK_ID,
        character_id="player_01",
        session_metadata=dict(metadata),
    )
    result = asyncio.run(loop.run(user_input, request_context=context))
    next_metadata = {
        "fired_trigger_ids": result.get("fired_trigger_ids", []),
        "quest_states": result.get("quest_states", []),
    }
    return result, next_metadata


def _find_a3_quest_state(result: dict[str, Any]) -> dict[str, Any]:
    """
    功能：从回合结果中提取 A3 主任务的完整运行态。
    入参：result（dict[str, Any]）：MainEventLoop 返回的回合结果，需包含 quest_states。
    出参：dict[str, Any]，`unmask_the_salt_deal` 对应的 QuestRuntimeState 字典。
    异常：找不到任务态时触发 AssertionError，表示 A3 任务持久化链路回归。
    """
    quest_states = result.get("quest_states", [])
    quest_state = next(
        (state for state in quest_states if state.get("quest_id") == A3_QUEST_ID),
        None,
    )
    assert quest_state is not None, f"A3 回合结果应包含 {A3_QUEST_ID} quest_state"
    return quest_state


def _assert_a3_playthrough_complete(tmp_path, spec: _A3PlaythroughSpec) -> None:
    """
    功能：执行 A3 golden pack 的完整分支路线，并断言任务完成与结构化后果。
    入参：tmp_path（Path）：pytest tmp_path；spec（_A3PlaythroughSpec）：路线输入与预期。
    出参：None。
    异常：断言失败表示 A3 golden playthrough 的触发器、任务状态、状态标签或后续场景回归。
    """
    loop = _build_loop_with_pack(tmp_path)
    metadata: dict[str, Any] = {"fired_trigger_ids": []}

    _, metadata = _run_a3_turn(loop, metadata, "检查潮湿告示", f"{spec.route}-001", spec.session_id)
    _, metadata = _run_a3_turn(loop, metadata, "去旧码头", f"{spec.route}-002", spec.session_id)
    _, metadata = _run_a3_turn(loop, metadata, "检查网下盐箱", f"{spec.route}-003", spec.session_id)

    if spec.route == "public":
        _, metadata = _run_a3_turn(loop, metadata, "去守备所", f"{spec.route}-004", spec.session_id)

    branch_result, metadata = _run_a3_turn(
        loop, metadata, spec.branch_input, f"{spec.route}-006", spec.session_id
    )
    branch_state = _find_a3_quest_state(branch_result)
    branch_fired_ids = {
        event.get("trigger_id", "") for event in branch_result.get("trigger_events", [])
    }
    branch_flags = set(branch_result.get("physics_diff", {}).get("state_flags_add", []))

    assert spec.branch_trigger_id in branch_fired_ids
    assert branch_state["current_stage_id"] == spec.branch_stage_id
    assert branch_state["data"]["branch_path"] == spec.branch_stage_id
    assert spec.branch_flag in branch_flags
    assert branch_result["session_metadata"]["quest_states"] == branch_result["quest_states"]

    merge_result, metadata = _run_a3_turn(
        loop, metadata, spec.merge_input, f"{spec.route}-007", spec.session_id
    )
    merge_state = _find_a3_quest_state(merge_result)
    merge_fired_ids = {
        event.get("trigger_id", "") for event in merge_result.get("trigger_events", [])
    }
    merge_flags = set(merge_result.get("physics_diff", {}).get("state_flags_add", []))

    assert spec.merge_trigger_id in merge_fired_ids
    assert merge_state["current_stage_id"] == "seal_the_evidence"
    assert merge_state["data"]["branch_path"] == spec.branch_stage_id
    assert spec.merge_flag in merge_flags

    _, metadata = _run_a3_turn(
        loop, metadata, spec.archive_input, f"{spec.route}-008", spec.session_id
    )
    final_result, metadata = _run_a3_turn(
        loop, metadata, "比对封契缺口", f"{spec.route}-009", spec.session_id
    )
    final_state = _find_a3_quest_state(final_result)
    final_fired_ids = {
        event.get("trigger_id", "") for event in final_result.get("trigger_events", [])
    }
    final_flags = set(final_result.get("physics_diff", {}).get("state_flags_add", []))
    completed_stages = set(final_state["data"].get("stages_completed", []))
    current_scene_id = final_result.get("scene_snapshot", {}).get("current_location", {}).get("id")

    assert "inspect_archive_seal" in final_fired_ids
    assert final_state["status"] == "completed"
    assert final_state["current_stage_id"] == "case_closed"
    assert spec.branch_stage_id in completed_stages
    assert "seal_the_evidence" in completed_stages
    assert final_state["data"]["branch_path"] == spec.branch_stage_id
    assert "salt_contract_case_closed" in final_flags
    assert current_scene_id == "archive_hall"
    assert metadata["quest_states"] == final_result["quest_states"]


# ---------------------------------------------------------------------------
# 1. pack 绑定后的场景解析
# ---------------------------------------------------------------------------
def test_pack_bound_session_resolves_start_scene(tmp_path) -> None:
    """
    功能：首回合 pack 绑定后，场景快照应反映 manifest.start_scene_id 对应的 pack 场景。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示 pack 场景解析回归。
    """
    loop = _build_loop_with_pack(tmp_path)
    context = TurnRequestContext(
        trace_id="t-a2-001",
        request_id="r-a2-001",
        session_id="s-a2-001",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )
    result = asyncio.run(loop.run("观察周围", request_context=context))

    snapshot = result.get("scene_snapshot", {})
    assert snapshot, "首回合应产生 scene_snapshot"
    assert (
        snapshot.get("current_location", {}).get("id") == "forest_edge"
    ), f"起始场景应为 forest_edge，实际 {snapshot.get('current_location')}"
    exits = snapshot.get("exits", [])
    exit_ids = {e.get("location_id", "") for e in exits}
    assert "old_camp" in exit_ids, "forest_edge 出口应包含 old_camp"
    assert "ruins_gate" in exit_ids, "forest_edge 出口应包含 ruins_gate"
    interactions = snapshot.get("interactables", [])
    interaction_ids = {item.get("interaction_id", "") for item in interactions}
    assert "inspect_mist_marker" in interaction_ids, "pack interactable 应进入 scene_snapshot"


# ---------------------------------------------------------------------------
# 2. enter_scene 触发器
# ---------------------------------------------------------------------------
def test_enter_scene_trigger_fires_on_first_turn(tmp_path) -> None:
    """
    功能：首回合进入 forest_edge 时，enter_scene 类型触发器 fire。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示 enter_scene 触发器未按预期 fire。
    """
    loop = _build_loop_with_pack(tmp_path)
    context = TurnRequestContext(
        trace_id="t-a2-002",
        request_id="r-a2-002",
        session_id="s-a2-002",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )
    result = asyncio.run(loop.run("观察周围", request_context=context))

    trigger_events = result.get("trigger_events", [])
    fired_ids = {e.get("trigger_id", "") for e in trigger_events}
    assert (
        "enter_forest_edge_intro" in fired_ids
    ), f"首回合应触发 enter_forest_edge_intro，实际 fire: {fired_ids}"


# ---------------------------------------------------------------------------
# 3. once 触发器重复进入时不重复 fire
# ---------------------------------------------------------------------------
def test_once_trigger_does_not_fire_twice(tmp_path) -> None:
    """
    功能：once=true 的 enter_scene 触发器在跨回合 session_metadata 传递 fired 列表后不应重复 fire。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示 once 语义失效。
    """
    loop = _build_loop_with_pack(tmp_path)
    context = TurnRequestContext(
        trace_id="t-a2-003",
        request_id="r-a2-003",
        session_id="s-a2-003",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )
    result1 = asyncio.run(loop.run("观察周围", request_context=context))
    fired1 = {e["trigger_id"] for e in result1.get("trigger_events", [])}
    assert "enter_forest_edge_intro" in fired1, "首回合应 fire enter_forest_edge_intro"

    # 第二回合传入已 fire 列表
    context2 = TurnRequestContext(
        trace_id="t-a2-003b",
        request_id="r-a2-003b",
        session_id="s-a2-003",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": list(fired1)},
    )
    result2 = asyncio.run(loop.run("观察周围", request_context=context2))
    fired2 = {e["trigger_id"] for e in result2.get("trigger_events", [])}
    assert (
        "enter_forest_edge_intro" not in fired2
    ), "once 触发器 enter_forest_edge_intro 不应重复 fire"


# ---------------------------------------------------------------------------
# 4. quest 初始化
# ---------------------------------------------------------------------------
def test_quest_find_the_key_is_initialized(tmp_path) -> None:
    """
    功能：pack 绑定后 find_the_key 任务状态应注入 quest_updates。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示任务初始化逻辑回归。
    """
    loop = _build_loop_with_pack(tmp_path)
    context = TurnRequestContext(
        trace_id="t-a2-004",
        request_id="r-a2-004",
        session_id="s-a2-004",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )
    result = asyncio.run(loop.run("观察周围", request_context=context))

    quest_updates = result.get("quest_updates", [])
    quest_ids = {q.get("quest_id", "") for q in quest_updates}
    assert "find_the_key" in quest_ids, f"quest_updates 应包含 find_the_key，实际: {quest_ids}"
    key_quest = next((q for q in quest_updates if q.get("quest_id") == "find_the_key"), None)
    assert key_quest is not None
    assert (
        key_quest.get("status") == "locked"
    ), f"find_the_key 初始状态应为 locked，实际: {key_quest.get('status')}"
    assert (
        key_quest.get("current_stage_id") == "find_clue"
    ), f"初始 stage 应为 find_clue，实际: {key_quest.get('current_stage_id')}"


def test_scene_switch_refreshes_snapshot_and_action_trigger_fires(tmp_path) -> None:
    """
    功能：真实主循环中 move 应刷新到目标 pack scene，随后 inspect 触发 action trigger。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示 A2-Plus resolution 链路或场景刷新未闭合。
    """
    loop = _build_loop_with_pack(tmp_path)
    context1 = TurnRequestContext(
        trace_id="t-a2-004b",
        request_id="r-a2-004b",
        session_id="s-a2-004b",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )
    result1 = asyncio.run(loop.run("向北走", request_context=context1))

    snapshot1 = result1.get("scene_snapshot", {})
    assert result1.get("physics_diff", {}).get("location_change", {}).get("from") == "forest_edge"
    assert (
        snapshot1.get("current_location", {}).get("id") == "old_camp"
    ), f"移动后应刷新到 old_camp，实际: {snapshot1.get('current_location')}"
    interaction_ids = {
        item.get("interaction_id", "") for item in snapshot1.get("interactables", [])
    }
    assert "talk_ranger_ella" in interaction_ids
    parsed_talk = loop.nlu_agent.parse("艾拉", {"scene_snapshot": snapshot1})
    assert parsed_talk is not None
    assert parsed_talk["type"] == "talk"
    context2 = TurnRequestContext(
        trace_id="t-a2-004c",
        request_id="r-a2-004c",
        session_id="s-a2-004b",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={
            "fired_trigger_ids": result1.get("fired_trigger_ids", []),
            "quest_states": result1.get("quest_states", []),
        },
    )
    result2 = asyncio.run(loop.run("检查火坑", request_context=context2))
    fired_ids = {event.get("trigger_id", "") for event in result2.get("trigger_events", [])}
    assert (
        "inspect_camp_firepit" in fired_ids
    ), f"检查火坑应触发 inspect_camp_firepit，实际 fire: {fired_ids}"
    granted_items = result2.get("physics_diff", {}).get("granted_items", [])
    assert any(item.get("item_id") == "burnt_letter" for item in granted_items)
    inventory = loop.entity_probes.check_inventory("player_01")
    assert any(item.get("item_id") == "burnt_letter" for item in inventory)
    assert result2.get("quest_updates", []) == []
    snapshot2 = result2.get("scene_snapshot", {})
    assert snapshot2.get("current_location", {}).get("id") == "old_camp"


def test_a3_branch_choice_persists_in_full_quest_states(tmp_path) -> None:
    """
    功能：用 A3 golden pack 真实主循环验证分支选择会进入完整 quest_states。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示 A3 分支任务状态未能跨回合通过 session_metadata 延续。
    """
    loop = _build_loop_with_pack(tmp_path)
    metadata: dict[str, list[dict[str, object]] | list[str]] = {"fired_trigger_ids": []}

    def run_a3_turn(user_input: str, request_suffix: str) -> dict:
        """
        功能：执行 A3 pack 单回合并把完整任务态回写为下一回合 session metadata。
        入参：user_input（str）：玩家输入；request_suffix（str）：请求编号后缀。
        出参：dict，MainEventLoop 回合结果。
        异常：主循环异常向上抛出，由测试失败暴露。
        """
        nonlocal metadata
        context = TurnRequestContext(
            trace_id=f"t-a3-{request_suffix}",
            request_id=f"r-a3-{request_suffix}",
            session_id="s-a3-branch",
            pack_id="a3_branching_quest",
            character_id="player_01",
            session_metadata=dict(metadata),
        )
        result = asyncio.run(loop.run(user_input, request_context=context))
        metadata = {
            "fired_trigger_ids": result.get("fired_trigger_ids", []),
            "quest_states": result.get("quest_states", []),
        }
        return result

    run_a3_turn("检查潮湿告示", "001")
    run_a3_turn("去旧码头", "002")
    run_a3_turn("检查网下盐箱", "003")
    run_a3_turn("返回夜市", "004")
    run_a3_turn("去守备所", "005")
    branch_result = run_a3_turn("向云校尉公开证据", "006")

    fired_ids = {event.get("trigger_id", "") for event in branch_result.get("trigger_events", [])}
    quest_states = branch_result.get("quest_states", [])
    branch_state = next(
        (state for state in quest_states if state.get("quest_id") == "unmask_the_salt_deal"),
        None,
    )
    flags = branch_result.get("physics_diff", {}).get("state_flags_add", [])

    assert "talk_captain_yun" in fired_ids
    assert branch_state is not None
    assert branch_state["current_stage_id"] == "report_to_watch"
    assert branch_result["session_metadata"]["quest_states"] == quest_states
    assert "branch_report_to_watch" in flags


def test_a3_public_route_golden_playthrough_reaches_completed_state(tmp_path) -> None:
    """
    功能：验证 A3 公开交证据路线可从起点完整跑到结案，且结构化任务态与后果不靠文案。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示公开路线 golden playthrough、分支后果或任务完成态回归。
    """
    _assert_a3_playthrough_complete(
        tmp_path,
        _A3PlaythroughSpec(
            route="public",
            session_id="s-a3-public-playthrough",
            branch_input="向云校尉公开证据",
            branch_trigger_id="talk_captain_yun",
            branch_stage_id="report_to_watch",
            branch_flag="branch_report_to_watch",
            merge_input="检查案板",
            merge_trigger_id="inspect_caseboard_after_report",
            merge_flag="watch_route_evidence_sealed",
            archive_input="进入封契库",
        ),
    )


def test_a3_private_route_golden_playthrough_reaches_completed_state(tmp_path) -> None:
    """
    功能：验证 A3 暗中交易路线可从起点完整跑到结案，并产生物品、标记和任务阶段差异。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示暗线路线 golden playthrough、分支后果或任务完成态回归。
    """
    _assert_a3_playthrough_complete(
        tmp_path,
        _A3PlaythroughSpec(
            route="private",
            session_id="s-a3-private-playthrough",
            branch_input="与线人席舟交易",
            branch_trigger_id="talk_runner_xi",
            branch_stage_id="strike_quay_bargain",
            branch_flag="branch_strike_quay_bargain",
            merge_input="复查封好的盐箱",
            merge_trigger_id="inspect_sealed_crate_after_bargain",
            merge_flag="quay_route_evidence_sealed",
            archive_input="绕去封契库后门",
        ),
    )


def test_a3_web_normal_and_sse_routes_reach_completed_state(tmp_path, monkeypatch) -> None:
    """
    功能：用真实 Web 普通路由与 SSE 路由分别完成 A3 golden playthrough。
    入参：tmp_path（Path）：pytest tmp_path；monkeypatch：pytest monkeypatch fixture。
    出参：None。
    异常：断言失败表示 A3 只在主循环直连测试中成立，Web 普通/SSE 边界未到终态。
    """
    monkeypatch.setattr("web_api.blueprints.turns.ensure_character_available", lambda _cid: True)
    client, context = _build_a3_web_client(tmp_path)
    normal_session_id = "s_a3_web_normal"
    stream_session_id = "s_a3_web_stream"
    _create_a3_web_session(context, normal_session_id)
    _create_a3_web_session(context, stream_session_id)

    private_inputs = [
        "检查潮湿告示",
        "去旧码头",
        "检查网下盐箱",
        "与线人席舟交易",
        "复查封好的盐箱",
        "绕去封契库后门",
        "比对封契缺口",
    ]
    public_inputs = [
        "检查潮湿告示",
        "去旧码头",
        "检查网下盐箱",
        "去守备所",
        "向云校尉公开证据",
        "检查案板",
        "进入封契库",
        "比对封契缺口",
    ]

    normal_payload: dict[str, Any] = {}
    for index, user_input in enumerate(private_inputs, start=1):
        normal_payload = _post_a3_normal_turn(client, normal_session_id, index, user_input)
    _assert_a3_web_route_completed(
        normal_payload,
        expected_branch="strike_quay_bargain",
        expected_turns=len(private_inputs),
    )

    stream_payload: dict[str, Any] = {}
    for index, user_input in enumerate(public_inputs, start=1):
        stream_payload = _post_a3_stream_turn(client, stream_session_id, index, user_input)
    _assert_a3_web_route_completed(
        stream_payload,
        expected_branch="report_to_watch",
        expected_turns=len(public_inputs),
    )


def test_pack_current_scene_drives_outer_world_evolution_location(tmp_path) -> None:
    """
    功能：首个 pack 回合即使角色 DB location 仍是历史值，
        入队的外环 world_evolution 也应使用当前 pack 场景。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示外环入队位置口径从 pack scene 漂移回角色历史 location。
    """
    outer = CollectingOuterBridge()
    loop = _build_loop_with_pack(tmp_path, outer_bridge=outer)
    context = TurnRequestContext(
        trace_id="t-a2-004d",
        request_id="r-a2-004d",
        session_id="s-a2-004d",
        pack_id="demo_a2_core",
        character_id="player_01",
        session_metadata={"fired_trigger_ids": []},
    )

    asyncio.run(loop.run("观察周围", request_context=context))

    pending_events = loop.db_updater.list_pending_outer_events(limit=10)
    world_events = [
        event for event in pending_events if event.get("event_name") == "world_evolution"
    ]
    assert world_events, "pack 回合应入队 world_evolution 外环事件"
    assert world_events[-1]["payload"]["location_id"] == "forest_edge"
    assert outer.world_events == []


# ---------------------------------------------------------------------------
# 5. 旧 session/测试态无 pack_id 时不注入剧本信息
# ---------------------------------------------------------------------------
def test_no_pack_context_does_not_inject_pack_trigger(tmp_path) -> None:
    """
    功能：兼容旧 session 或测试态无 pack_id 上下文，trigger_events 和 quest_updates 应为空。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示兼容态无 pack 上下文时意外注入剧本内容。
    """
    loop = _build_no_pack_loop(tmp_path)
    context = TurnRequestContext(
        trace_id="t-a2-005",
        request_id="r-a2-005",
        session_id="s-a2-005",
        character_id="player_01",
    )
    result = asyncio.run(loop.run("观察周围", request_context=context))

    trigger_events = result.get("trigger_events", [])
    quest_updates = result.get("quest_updates", [])
    assert len(trigger_events) == 0, f"无 pack 时不应有 trigger_events，实际: {trigger_events}"
    assert len(quest_updates) == 0, f"无 pack 时不应有 quest_updates，实际: {quest_updates}"
