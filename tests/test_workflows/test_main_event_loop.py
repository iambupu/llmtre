import asyncio
import json
import sqlite3

from core.event_bus import EventBus
from game_workflows.async_watchers import OuterLoopBridge, WorkflowOuterLoopBridge
from game_workflows.main_event_loop import MainEventLoop
from state.tools.db_initializer import DBInitializer
from tools.entity.entity_probes import EntityProbes
from tools.sqlite_db.db_updater import DBUpdater


def build_loop(tmp_path):
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
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    return loop


class CollectingOuterBridge(OuterLoopBridge):
    def __init__(self):
        self.state_events = []
        self.turn_events = []
        self.world_events = []

    async def emit_state_changed(self, event):
        self.state_events.append(event)

    async def emit_turn_ended(self, event):
        self.turn_events.append(event)

    async def emit_world_evolution(self, event):
        self.world_events.append(event)


class FailingOuterBridge(OuterLoopBridge):
    async def emit_state_changed(self, event):
        raise RuntimeError("outer unavailable")

    async def emit_turn_ended(self, event):
        raise RuntimeError("outer unavailable")

    async def emit_world_evolution(self, event):
        raise RuntimeError("outer unavailable")


class PartialFailingOuterBridge(OuterLoopBridge):
    def __init__(self):
        self.turn_events = []
        self.world_events = []

    async def emit_state_changed(self, event):
        raise RuntimeError("state_changed failed")

    async def emit_turn_ended(self, event):
        self.turn_events.append(event)

    async def emit_world_evolution(self, event):
        self.world_events.append(event)


def build_loop_with_outer(tmp_path, outer_bridge):
    db_path = tmp_path / "tre_state.db"
    initializer = DBInitializer(db_path=str(db_path))
    initializer.initialize_db()

    db_updater = DBUpdater(str(db_path))
    entity_probes = EntityProbes(str(db_path))
    event_bus = EventBus("config/mod_registry.yml", "mods")
    loop = MainEventLoop(
        event_bus,
        rag_bridge=DummyRAGBridge(),
        outer_bridge=outer_bridge,
        db_updater=db_updater,
        entity_probes=entity_probes,
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    return loop


class DummyRAGBridge:
    def __init__(self, ready: bool = True):
        self.ready = ready

    def build_snapshot(self, query: str):
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


def build_loop_with_rag(tmp_path, rag_bridge):
    db_path = tmp_path / "tre_state.db"
    initializer = DBInitializer(db_path=str(db_path))
    initializer.initialize_db()
    db_updater = DBUpdater(str(db_path))
    entity_probes = EntityProbes(str(db_path))
    event_bus = EventBus("config/mod_registry.yml", "mods")
    loop = MainEventLoop(
        event_bus,
        rag_bridge=rag_bridge,
        db_updater=db_updater,
        entity_probes=entity_probes,
    )
    loop.nlu_agent.llm_enabled = False
    loop.gm_agent.llm_enabled = False
    return loop


def test_main_event_loop_can_be_initialized(tmp_path):
    loop = build_loop(tmp_path)
    assert loop.graph is not None


def test_main_event_loop_uses_workflow_outer_bridge_by_default(tmp_path):
    loop = build_loop(tmp_path)
    assert isinstance(loop.outer_bridge, WorkflowOuterLoopBridge)


def test_main_event_loop_success_path_updates_state(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("攻击地精"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "attack"
    assert result["physics_diff"]["attack_hit"] is True
    assert result["physics_diff"]["attack_dc"] == 16
    assert result["physics_diff"]["attack_roll"] >= result["physics_diff"]["attack_dc"]
    assert 1 <= result["physics_diff"]["damage_roll"] <= 6
    assert result["physics_diff"]["target_hp_delta"] < 0
    assert result["turn_id"] == 1
    assert result["final_response"]

    goblin = loop.entity_probes.get_character_stats("goblin_01")
    assert goblin is not None
    assert goblin["hp"] == 30 + result["physics_diff"]["target_hp_delta"]


def test_main_event_loop_turn_id_is_monotonic_across_turns(tmp_path):
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)

    first = asyncio.run(loop.run("观察周围"))
    second = asyncio.run(loop.run("观察周围"))

    assert first["turn_id"] == 1
    assert second["turn_id"] == 2


def test_main_event_loop_returns_controlled_failure_for_unknown_input(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("天气真不错"))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "clarification"
    assert result["clarification_question"]
    assert result["should_advance_turn"] is False
    assert result["should_write_story_memory"] is False
    assert result["final_response"]


def test_main_event_loop_emits_minimal_outer_events(tmp_path):
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert len(outer.state_events) == 1
    assert len(outer.turn_events) == 1
    assert len(outer.world_events) == 1
    assert outer.state_events[0].entity_id == "player_01"
    assert outer.turn_events[0].turn_id == 1
    assert outer.world_events[0].time_passed_minutes == loop.outer_world_minutes_per_turn


def test_main_event_loop_outer_failure_does_not_break_turn(tmp_path):
    loop = build_loop_with_outer(tmp_path, FailingOuterBridge())

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["final_response"]


def test_main_event_loop_partial_outer_failure_does_not_block_following_events(tmp_path):
    outer = PartialFailingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert len(outer.turn_events) == 1
    assert len(outer.world_events) == 1
    pending = loop.db_updater.list_pending_outer_events(limit=10)
    assert pending
    assert pending[0]["event_name"] == "state_changed"


def test_main_event_loop_overflow_outbox_only_enqueues_supported_event_types(tmp_path):
    loop = build_loop(tmp_path)
    loop.outer_max_pending_tasks = 0

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    with sqlite3.connect(loop.db_updater.db_path) as conn:
        rows = conn.execute("SELECT event_name FROM outer_event_outbox").fetchall()
    event_names = {str(row[0]) for row in rows}
    assert "turn_batch" not in event_names
    assert "turn_ended" in event_names
    assert event_names.issubset({"state_changed", "turn_ended", "world_evolution"})


def test_main_event_loop_populates_world_snapshot_from_rag_bridge(tmp_path):
    loop = build_loop_with_rag(tmp_path, DummyRAGBridge(ready=True))

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["world_snapshot"]["rag_ready"] is True
    assert result["world_snapshot"]["rag_context"] == "规则片段"


def test_main_event_loop_rag_unavailable_does_not_break_main_logic(tmp_path):
    loop = build_loop_with_rag(tmp_path, DummyRAGBridge(ready=False))

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["world_snapshot"]["rag_ready"] is False
    assert result["final_response"]


def test_main_event_loop_routes_writes_through_event_bus(tmp_path):
    loop = build_loop(tmp_path)
    emitted_events: list[str] = []
    original_emit = loop.event_bus.emit

    def tracking_emit(event_name, state):
        emitted_events.append(event_name)
        return original_emit(event_name, state)

    loop.event_bus.emit = tracking_emit

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["write_results"]
    assert "on_state_write_pre" in emitted_events
    assert "on_state_write_post" in emitted_events
    assert "on_action_post" in emitted_events


def test_main_event_loop_move_updates_location_mp_and_flag(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("前往森林"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "move"
    assert result["physics_diff"]["location_id"] == "forest_edge"
    assert result["physics_diff"]["mp_delta"] == -1
    assert "moved_recently" in result["physics_diff"]["state_flags_add"]

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["current_location_id"] == "forest_edge"
    assert player["mp"] == 49
    assert "moved_recently" in json.loads(player["state_flags_json"] or "[]")


def test_main_event_loop_talk_updates_mp_and_flag(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("和地精说话"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "talk"
    assert result["action_intent"]["target_id"] == "goblin_01"
    assert result["physics_diff"]["mp_delta"] == -1
    assert "conversation_started" in result["physics_diff"]["state_flags_add"]

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["mp"] == 49
    assert "conversation_started" in json.loads(player["state_flags_json"] or "[]")


def test_main_event_loop_talk_without_target_returns_clarification(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("我想和他说话"))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "clarification"
    assert "你想交谈哪个目标" in result["clarification_question"]
    assert result["should_advance_turn"] is False
    assert result["physics_diff"] is None
    assert result["turn_id"] == 0


def test_main_event_loop_interact_updates_mp_and_flag(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "observe"
    assert "observed_surroundings" in result["physics_diff"]["state_flags_add"]
    assert result["scene_snapshot"]["current_location"]["id"] == "unknown"
    assert result["scene_snapshot"]["exits"]

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert "observed_surroundings" in json.loads(player["state_flags_json"] or "[]")


def test_main_event_loop_wait_accepts_sit_naturally(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("我坐一会"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "wait"
    assert "waited_recently" in result["physics_diff"]["state_flags_add"]
    assert result["turn_id"] == 1


def test_main_event_loop_rest_recovers_resources_deterministically(tmp_path):
    loop = build_loop(tmp_path)
    loop.db_updater.apply_diff("player_01", {"hp_delta": -10, "mp_delta": -10})

    result = asyncio.run(loop.run("我休息一下"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "rest"
    assert result["physics_diff"]["hp_delta"] == 1
    assert result["physics_diff"]["mp_delta"] == 2

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["hp"] == 91
    assert player["mp"] == 42


def test_main_event_loop_continue_move_uses_scene_exit(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("在路上移动"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "move"
    assert result["action_intent"]["parameters"]["location_id"] == "forest_edge"
    assert result["physics_diff"]["location_id"] == "forest_edge"

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["current_location_id"] == "forest_edge"


def test_main_event_loop_commit_sandbox_merges_shadow_state(tmp_path):
    loop = build_loop(tmp_path)

    sandbox_turn = asyncio.run(loop.run("前往森林", is_sandbox_mode=True))
    assert sandbox_turn["is_valid"] is True

    active_player_before = loop.entity_probes.get_character_stats("player_01")
    shadow_player_before = loop.entity_probes.get_character_stats("player_01", use_shadow=True)
    assert active_player_before is not None
    assert shadow_player_before is not None
    assert active_player_before["current_location_id"] == "unknown"
    assert shadow_player_before["current_location_id"] == "forest_edge"

    merged = asyncio.run(loop.run("并入主线", is_sandbox_mode=True))
    assert merged["is_valid"] is True
    assert merged["is_sandbox_mode"] is False

    active_player_after = loop.entity_probes.get_character_stats("player_01")
    assert active_player_after is not None
    assert active_player_after["current_location_id"] == "forest_edge"
    assert loop.db_updater.has_shadow_state() is False


def test_main_event_loop_discard_sandbox_rolls_back_shadow_state(tmp_path):
    loop = build_loop(tmp_path)

    sandbox_turn = asyncio.run(loop.run("前往森林", is_sandbox_mode=True))
    assert sandbox_turn["is_valid"] is True
    assert loop.db_updater.has_shadow_state() is True

    discarded = asyncio.run(loop.run("回滚沙盒", is_sandbox_mode=True))
    assert discarded["is_valid"] is True
    assert discarded["is_sandbox_mode"] is False
    assert loop.db_updater.has_shadow_state() is False

    active_player = loop.entity_probes.get_character_stats("player_01")
    assert active_player is not None
    assert active_player["current_location_id"] == "unknown"


def test_main_event_loop_sandbox_control_fails_outside_sandbox_mode(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("并入主线"))

    assert result["is_valid"] is False
    assert "当前不在沙盒模式" in "；".join(result["validation_errors"])


def test_main_event_loop_use_item_consumes_inventory_and_applies_effect(tmp_path):
    loop = build_loop(tmp_path)
    loop.db_updater.apply_diff("player_01", {"hp_delta": -40})

    result = asyncio.run(loop.run("使用药水"))

    assert result["is_valid"] is True
    assert result["action_intent"]["type"] == "use_item"
    assert result["physics_diff"]["hp_delta"] == 20

    player = loop.entity_probes.get_character_stats("player_01")
    assert player is not None
    assert player["hp"] == 80
    assert loop.entity_probes.get_inventory_item("player_01", "health_potion_01") is None


def test_main_event_loop_use_item_fails_when_inventory_missing_item(tmp_path):
    loop = build_loop(tmp_path)
    loop.db_updater.consume_item("player_01", "health_potion_01")

    result = asyncio.run(loop.run("使用药水"))

    assert result["is_valid"] is False
    assert "背包中不存在该物品" in result["validation_errors"]
    assert result["final_response"]


def test_main_event_loop_returns_controlled_failure_for_missing_character(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("攻击地精", initial_character_id="missing_player"))

    assert result["is_valid"] is False
    assert "当前角色不存在" in "；".join(result["validation_errors"])
    assert result["action_intent"] is None
    assert result["final_response"]
