import asyncio
import json
import sqlite3

from core.event_bus import EventBus
from game_workflows.async_watchers import (
    NoOpOuterLoopBridge,
    OuterLoopBridge,
    WorkflowOuterLoopBridge,
)
from game_workflows.main_event_loop import MainEventLoop
from state.contracts.turn import TurnRequestContext
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
    result = asyncio.run(loop.run("观察周围"))
    assert result["outer_emit_result"]["status"] == "ok"
    assert result["outer_emit_result"]["detail"]["mode"] == "sync"


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


def test_main_event_loop_noop_outer_bridge_reports_skipped(tmp_path):
    """
    功能：验证 NoOp 外环桥接器会在回合结果中明确标记 skipped/noop，便于 service trace 映射。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 noop 外环结果契约回归。
    """
    loop = build_loop_with_outer(tmp_path, NoOpOuterLoopBridge())

    result = asyncio.run(loop.run("观察周围"))

    assert result["outer_emit_result"] == {"status": "skipped", "detail": {"mode": "noop"}}


def test_main_event_loop_request_context_overrides_runtime_inputs(tmp_path):
    """
    功能：验证 request_context 会覆盖角色、沙盒和会话记忆，保证 API 层追踪字段进入主循环。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示请求级上下文桥接回归。
    """
    loop = build_loop(tmp_path)
    context = TurnRequestContext(
        trace_id="trc_ctx_001",
        request_id="req_ctx_001",
        session_id="sess_ctx_001",
        character_id="player_01",
        sandbox_mode=False,
        recent_memory="上一回合摘要",
    )

    result = asyncio.run(
        loop.run(
            "观察周围",
            initial_character_id="ignored_character",
            is_sandbox_mode=True,
            recent_memory="ignored memory",
            request_context=context,
        )
    )

    assert result["trace_id"] == "trc_ctx_001"
    assert result["request_id"] == "req_ctx_001"
    assert result["session_id"] == "sess_ctx_001"
    assert result["active_character_id"] == "player_01"
    assert result["scene_snapshot"]["recent_memory"] == "上一回合摘要"


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
    assert result["outer_emit_result"]["status"] == "ok"
    assert result["outer_emit_result"]["detail"]["mode"] == "sync"


def test_main_event_loop_outer_failure_does_not_break_turn(tmp_path):
    loop = build_loop_with_outer(tmp_path, FailingOuterBridge())

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    assert result["final_response"]
    assert result["outer_emit_result"]["status"] == "failed"
    assert result["outer_emit_result"]["detail"]["mode"] == "sync"


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
    """
    功能：验证 A1 同步外环投递策略下，不再走后台任务溢出分支，也不会写入 outbox 补偿事件。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示仍残留旧的后台溢出行为。
    """
    loop = build_loop(tmp_path)
    loop.outer_max_pending_tasks = 0

    result = asyncio.run(loop.run("观察周围"))

    assert result["is_valid"] is True
    with sqlite3.connect(loop.db_updater.db_path) as conn:
        rows = conn.execute("SELECT event_name FROM outer_event_outbox").fetchall()
    event_names = {str(row[0]) for row in rows}
    assert "turn_batch" not in event_names
    assert event_names == set()


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


def test_main_event_loop_clarification_builders_use_fallbacks_without_scene(tmp_path):
    """
    功能：验证移动/目标澄清在缺少 scene_snapshot 时使用确定性兜底文本。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 clarification 边界文案回归。
    """
    loop = build_loop(tmp_path)

    move_question = loop._build_move_clarification({})  # noqa: SLF001
    target_question = loop._build_target_clarification({}, "attack")  # noqa: SLF001

    assert move_question == "这里暂时没有明确出口，你想先观察周围吗？"
    assert target_question == "你想和谁攻击？当前没有明确可见目标。"


def test_main_event_loop_clarifier_failure_uses_fallback_question(tmp_path):
    """
    功能：验证 Clarifier 异常时主循环会使用确定性兜底问题，避免澄清回合失败。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 `_clarify_with_agent` 降级路径回归。
    """
    loop = build_loop(tmp_path)

    def _raise_clarifier_error(_envelope):
        raise RuntimeError("clarifier unavailable")

    loop.clarifier_agent.clarify = _raise_clarifier_error
    result = asyncio.run(loop.run("天气真不错"))

    assert result["is_valid"] is False
    assert result["turn_outcome"] == "clarification"
    expected_question = "我还没有理解你的行动，你想观察、移动、交谈，还是休息？"
    assert result["clarification_question"] == expected_question
    assert result["should_advance_turn"] is False


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


def test_main_event_loop_config_and_write_plan_edge_branches(tmp_path):
    """
    功能：验证配置动作过滤非法 flag、整数转换降级、写计划消费物品/目标伤害与未知写操作分支。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示确定性结算或写计划边界回归。
    """
    loop = build_loop(tmp_path)
    loop.rules.setdefault("resolution", {})["inspect"] = {
        "hp_delta": 0,
        "mp_delta": 0,
        "state_flags_add": ["valid_flag", 123, None],
    }

    configured = loop._resolve_configured_action("inspect", {"base": True})  # noqa: SLF001
    write_plan = loop._build_write_plan(  # noqa: SLF001
        {
            "active_character_id": "player_01",
            "action_intent": {"type": "attack", "target_id": "goblin_01"},
            "physics_diff": {"consumed_item_id": "potion", "target_hp_delta": -3},
            "is_sandbox_mode": False,
        }
    )

    assert loop._to_int("bad", default=7) == 7  # noqa: SLF001
    assert configured["state_flags_add"] == ["valid_flag"]
    assert {
        "type": "consume_item",
        "owner_id": "player_01",
        "item_id": "potion",
        "use_shadow": False,
    } in write_plan
    assert {
        "type": "apply_diff",
        "entity_id": "goblin_01",
        "diff": {"hp_delta": -3},
        "use_shadow": False,
    } in write_plan
    assert loop._execute_write_op({"type": "unknown"}) is False  # noqa: SLF001


def test_main_event_loop_returns_controlled_failure_for_missing_character(tmp_path):
    loop = build_loop(tmp_path)

    result = asyncio.run(loop.run("攻击地精", initial_character_id="missing_player"))

    assert result["is_valid"] is False
    assert "当前角色不存在" in "；".join(result["validation_errors"])
    assert result["action_intent"] is None
    assert result["final_response"]


def test_main_event_loop_background_emit_overflow_enqueues_outbox_events(tmp_path):
    """
    功能：验证后台外环投递达到上限时会降级写入 outbox，且只写入可重放事件类型。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示后台溢出降级路径回归。
    """
    loop = build_loop(tmp_path)
    loop.outer_max_pending_tasks = 0
    state = {
        "is_valid": True,
        "physics_diff": {"hp_delta": 1},
        "active_character_id": "player_01",
        "is_sandbox_mode": False,
        "turn_id": 7,
        "user_input": "观察周围",
        "final_response": "叙事",
        "active_character": {"location": "unknown"},
    }

    result = loop._emit_outer_events_background(state)  # noqa: SLF001
    pending = loop.db_updater.list_pending_outer_events(limit=10)
    event_names = {str(item["event_name"]) for item in pending}
    assert result["status"] == "failed"
    assert result["detail"]["queued_to_outbox"] is True
    assert event_names == {"state_changed", "turn_ended", "world_evolution"}


def test_main_event_loop_background_emit_starts_async_task(tmp_path):
    """
    功能：验证后台外环投递在容量充足时会创建任务并返回 started 状态。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示后台任务调度分支回归。
    """
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)
    state = {
        "is_valid": True,
        "physics_diff": {"hp_delta": 1},
        "active_character_id": "player_01",
        "is_sandbox_mode": False,
        "turn_id": 8,
        "user_input": "观察周围",
        "final_response": "叙事",
        "should_advance_turn": True,
        "active_character": {"location": "unknown"},
    }

    async def _run_once() -> dict[str, object]:
        result = loop._emit_outer_events_background(state)  # noqa: SLF001
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(_run_once())
    assert result["status"] == "started"
    assert result["detail"]["mode"] == "workflow_background"


def test_main_event_loop_replay_outbox_unsupported_event_logs_failure(tmp_path, caplog):
    """
    功能：验证补偿重放遇到不支持事件类型时会回写失败状态并输出告警日志。
    入参：tmp_path（pytest fixture）；caplog（日志捕获器）。
    出参：None。
    异常：断言失败表示 outbox 失败回写路径不可观测。
    """
    loop = build_loop(tmp_path)
    loop.db_updater.enqueue_outer_event("unsupported_event", {"foo": "bar"}, "seed")

    async def _replay_once() -> None:
        await loop._replay_outbox_once()  # noqa: SLF001

    caplog.set_level("WARNING", logger="Workflow.MainLoop")
    asyncio.run(_replay_once())
    assert "外环补偿重放失败[event=unsupported_event" in caplog.text


def test_main_event_loop_replay_outbox_delivers_supported_events(tmp_path):
    """
    功能：验证补偿重放能投递三类支持事件，并标记 delivered。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 outbox 成功重放或 delivered 标记回归。
    """
    outer = CollectingOuterBridge()
    loop = build_loop_with_outer(tmp_path, outer)
    loop.db_updater.enqueue_outer_event(
        "state_changed",
        {"entity_id": "player_01", "diff": {"hp_delta": 1}, "is_sandbox": False},
        "seed",
    )
    loop.db_updater.enqueue_outer_event(
        "turn_ended",
        {"turn_id": 1, "user_input": "观察", "final_response": "叙事"},
        "seed",
    )
    loop.db_updater.enqueue_outer_event(
        "world_evolution",
        {"time_passed_minutes": 10, "location_id": "unknown"},
        "seed",
    )

    asyncio.run(loop._replay_outbox_once())  # noqa: SLF001

    assert len(outer.state_events) == 1
    assert len(outer.turn_events) == 1
    assert len(outer.world_events) == 1
    assert loop.db_updater.list_pending_outer_events(limit=10) == []


def test_main_event_loop_schedule_outbox_replay_respects_interval_and_active_task(tmp_path):
    """
    功能：验证 outbox 调度会遵守间隔和进行中任务限制，避免重复创建补偿任务。
    入参：tmp_path（pytest fixture）：临时目录。
    出参：None。
    异常：断言失败表示 outbox 调度节流回归。
    """
    loop = build_loop(tmp_path)

    async def _schedule_twice() -> None:
        loop._schedule_outbox_replay()  # noqa: SLF001
        first_task = loop._outer_replay_task
        loop._schedule_outbox_replay()  # noqa: SLF001
        assert loop._outer_replay_task is first_task
        assert first_task is not None
        await first_task

    asyncio.run(_schedule_twice())
