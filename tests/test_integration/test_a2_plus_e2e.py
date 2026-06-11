"""
A2-Plus E2E 集成测试：验证 demo_a2_core 剧本包在确定性主循环中的
场景解析、触发器评估、任务推进和场景切换全链路。
"""

import asyncio

from core.event_bus import EventBus
from game_workflows.main_event_loop import MainEventLoop
from state.contracts.turn import TurnRequestContext
from state.tools.db_initializer import DBInitializer
from tools.entity.entity_probes import EntityProbes
from tools.packs.registry import StoryPackRegistry
from tools.sqlite_db.db_updater import DBUpdater


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


def test_pack_current_scene_drives_outer_world_evolution_location(tmp_path) -> None:
    """
    功能：首个 pack 回合即使角色 DB location 仍是历史值，
        外环 world_evolution 也应使用当前 pack 场景。
    入参：tmp_path（Path）：pytest tmp_path。
    出参：None。
    异常：断言失败表示外环位置口径从 pack scene 漂移回角色历史 location。
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

    assert outer.world_events, "pack 回合应投递 world_evolution 外环事件"
    assert outer.world_events[-1].location_id == "forest_edge"


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
