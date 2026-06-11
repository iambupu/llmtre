"""
功能：覆盖 main loop helpers coverage a1 的回归测试。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from game_workflows import main_loop_outer_helpers as outer_helpers
from game_workflows.main_loop_config import DEFAULT_MAIN_LOOP_RULES
from game_workflows.main_loop_resolution_helpers import (
    _build_runtime_event_contexts,
    _evaluate_pack_triggers,
    resolve_action_sync,
)
from game_workflows.main_loop_scene_helpers import (
    build_character_state,
    build_scene_snapshot,
    normalize_dict_list,
    normalize_scene_exits,
)
from game_workflows.main_loop_validation_helpers import (
    _validate_action_type,
    _validate_attack,
    _validate_use_item,
    build_move_clarification,
    build_target_clarification,
    is_reachable_location,
    validate_action_sync,
)
from state.contracts.agent import AgentEnvelope


class _DummyClarifier:
    """
    功能：提供 DummyClarifier 测试替身或辅助对象。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：_DummyClarifier 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    def clarify(self, _envelope: AgentEnvelope) -> Any:
        """
        功能：提供 clarify 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        return SimpleNamespace(payload={"clarification_question": "请补充细节"})


class _DummyEntityProbes:
    """
    功能：提供 DummyEntityProbes 测试替身或辅助对象。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：_DummyEntityProbes 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    def __init__(self) -> None:
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self._character_stats: dict[str, Any] | None = {"entity_id": "player_01"}
        self._inventory_item: dict[str, Any] | None = {"quantity": 1}
        self._item_definition: dict[str, Any] | None = {"effects": []}
        self._location_info: dict[str, Any] | None = None
        self._nearby_entities: list[dict[str, Any]] = []

    def get_character_stats(self, _entity_id: str, use_shadow: bool = False) -> Any:
        """
        功能：提供 get character stats 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        del use_shadow
        return self._character_stats

    def check_inventory(self, _entity_id: str, use_shadow: bool = False) -> list[dict[str, Any]]:
        """
        功能：提供 check inventory 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        del use_shadow
        return [{"item_id": "health_potion_01"}]

    def get_inventory_item(
        self, _owner_id: str, _item_id: str, use_shadow: bool = False
    ) -> dict[str, Any] | None:
        """
        功能：提供 get inventory item 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        del use_shadow
        return self._inventory_item

    def get_item_definition(self, _item_id: str) -> dict[str, Any] | None:
        """
        功能：提供 get item definition 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        return self._item_definition

    def get_location_info(
        self,
        _location_id: str,
        use_shadow: bool = False,
    ) -> dict[str, Any] | None:
        """
        功能：提供 get location info 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        del use_shadow
        return self._location_info

    def list_nearby_entities(
        self, _location_id: str, use_shadow: bool = False
    ) -> list[dict[str, Any]]:
        """
        功能：提供 list nearby entities 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        del use_shadow
        return list(self._nearby_entities)


class _DummyLoop:
    """
    功能：提供 DummyLoop 测试替身或辅助对象。
    入参：无；类初始化参数由各方法或构造函数声明。
    出参：_DummyLoop 类，用于承载测试替身或分组场景。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """

    def __init__(self) -> None:
        """
        功能：实现测试替身的 __init__ 协议方法。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        self.clarifier_agent = _DummyClarifier()
        self.entity_probes = _DummyEntityProbes()
        self.event_bus = SimpleNamespace(emit=lambda _name, state: state)
        self.rules: dict[str, Any] = {"resolution": {"attack": {}}}
        self.db_updater = SimpleNamespace(
            enqueue_outer_event=lambda *args, **kwargs: None,
            reserve_pending_outer_events=lambda **kwargs: [],
            mark_outer_event_delivered=lambda _event_id: None,
            mark_outer_event_failed=lambda **kwargs: None,
        )
        self.outer_bridge = SimpleNamespace(
            emit_state_changed=_noop_async,
            emit_turn_ended=_noop_async,
            emit_world_evolution=_noop_async,
        )
        self.outer_emit_timeout_seconds = 1
        self.outer_emit_world_evolution = True
        self.outer_world_minutes_per_turn = 10
        self.outer_max_pending_tasks = 8
        self._outer_emit_tasks: set[asyncio.Task[Any]] = set()
        self._outer_replay_task: asyncio.Task[Any] | None = None
        self._last_outbox_replay_ts = 0.0
        self.outer_outbox_replay_interval_seconds = 0.0
        self.outer_outbox_replay_limit = 10
        self.outer_outbox_processing_timeout_seconds = 30
        self.outer_outbox_max_attempts = 3
        self.outer_outbox_backoff_seconds = 1

    def _build_action_rng(self, _state: Any) -> Any:
        """
        功能：提供 build action rng 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        return None

    def _to_int(self, value: Any, default: int = 0) -> int:
        """
        功能：提供 to int 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        try:
            return int(value)
        except TypeError, ValueError:
            return default

    def _resolve_configured_action(
        self, action_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """
        功能：提供 resolve configured action 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        return {"configured": action_type, **payload}


async def _noop_async(_event: Any) -> None:
    """
    功能：提供 noop async 测试辅助逻辑。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    return None


def test_scene_helpers_handle_invalid_and_fallback_paths() -> None:
    """
    功能：验证 scene helpers handle invalid and fallback paths 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    assert normalize_scene_exits("invalid") == []
    assert normalize_scene_exits([1, {"direction": "N"}]) == []
    assert normalize_dict_list("invalid") == []

    probes = _DummyEntityProbes()
    probes._character_stats = None
    assert build_character_state(probes, "player_01") is None

    probes._character_stats = {
        "entity_id": "player_01",
        "name": "玩家",
        "hp": 2,
        "max_hp": 10,
        "mp": 1,
        "max_mp": 5,
        "current_location_id": "unknown",
        "state_flags_json": '["moved_recently", "unknown_flag", "moved_recently"]',
    }
    character = build_character_state(
        probes,
        "player_01",
        rules={
            "character_status": {
                "flags": {
                    "moved_recently": {
                        "label": "刚刚移动",
                        "kind": "activity",
                        "severity": "info",
                        "description": "角色刚完成移动。",
                    }
                }
            }
        },
    )
    assert character is not None
    assert character["state_flags"] == ["moved_recently", "unknown_flag"]
    assert character["status_summary"] == "濒危、法力不足、刚刚移动、unknown flag"
    assert character["status_context"]["resource_state"] == "hp_critical"
    assert "角色刚完成移动" in character["status_context"]["prompt_text"]

    snapshot_none = build_scene_snapshot(probes, {}, None)
    assert snapshot_none is None

    rules = {"scene_defaults": {"locations": {"unknown": {"id": "unknown", "exits": []}}}}
    snapshot = build_scene_snapshot(probes, rules, character, recent_memory="memo")
    assert snapshot is not None
    assert snapshot["current_location"]["id"] == "unknown"
    assert snapshot["recent_memory"] == "memo"


def test_red_lantern_state_flags_use_backend_status_labels() -> None:
    """
    功能：验证赤灯剧本写入的线索 flag 会在后端状态派生阶段转换为中文标签。
    入参：无；使用内联角色快照与默认主循环规则。
    出参：None。
    异常：断言失败表示玩家界面或 GM 上下文可能继续泄漏内部状态 key。
    """
    probes = _DummyEntityProbes()
    probes._character_stats = {
        "entity_id": "player_01",
        "name": "玩家",
        "hp": 10,
        "max_hp": 10,
        "mp": 5,
        "max_mp": 5,
        "current_location_id": "dawn_causeway",
        "state_flags_json": (
            '["inspected_surroundings","notice_scraped_name_found",'
            '"tide_oath_shard_recovered","red_lantern_story_complete"]'
        ),
    }

    character = build_character_state(
        probes,
        "player_01",
        rules=DEFAULT_MAIN_LOOP_RULES,
    )

    assert character is not None
    assert character["status_summary"] == ("仔细检查、发现潮汐告示线索、找回潮誓碎片、赤灯事件完成")
    assert [effect["label"] for effect in character["status_effects"]] == [
        "仔细检查",
        "发现潮汐告示线索",
        "找回潮誓碎片",
        "赤灯事件完成",
    ]
    assert "red lantern story complete" not in character["status_context"]["prompt_text"]
    assert "赤灯事件完成" in character["status_context"]["prompt_text"]


def test_validation_helpers_cover_branches() -> None:
    """
    功能：验证 validation helpers cover branches 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = _DummyLoop()
    state = {
        "trace_id": "trc_1",
        "turn_id": 3,
        "user_input": "测试",
        "active_character": {"id": "player_01"},
        "scene_snapshot": {
            "exits": [{"location_id": "forest_edge", "label": "森林边缘"}],
            "visible_npcs": [{"entity_id": "npc_1", "name": "守卫"}],
        },
    }
    assert "森林边缘" in build_move_clarification(state)
    assert "守卫" in build_target_clarification(state, "talk")
    assert is_reachable_location(state, "forest_edge") is True
    assert is_reachable_location({}, "forest_edge") is False
    assert _validate_action_type("unknown") == ["动作类型暂不支持"]

    errors: list[str] = []
    loop.entity_probes._character_stats = None
    _validate_attack(loop, {"type": "attack", "target_id": "missing"}, errors)
    assert "攻击目标不存在" in errors

    use_item_errors: list[str] = []
    loop.entity_probes._item_definition = None
    _validate_use_item(
        loop,
        {"is_sandbox_mode": False},
        {"type": "use_item", "parameters": {"item_id": "health_potion_01"}},
        {"id": "player_01"},
        use_item_errors,
    )
    assert "该物品缺少可用定义" in use_item_errors


def test_validate_action_sync_clarification_paths() -> None:
    """
    功能：验证 validate action sync clarification paths 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = _DummyLoop()
    base_state = {
        "active_character": {"id": "player_01"},
        "scene_snapshot": {"exits": [], "visible_npcs": []},
    }

    result_needs = validate_action_sync(
        loop,
        {
            **base_state,
            "action_intent": {
                "type": "move",
                "needs_clarification": True,
                "clarification_question": "",
            },
        },
    )
    assert result_needs["turn_outcome"] == "clarification"

    result_move = validate_action_sync(
        loop,
        {
            **base_state,
            "action_intent": {"type": "move", "parameters": {"location_id": "unknown"}},
        },
    )
    assert result_move["turn_outcome"] == "clarification"

    result_use_item = validate_action_sync(
        loop,
        {
            **base_state,
            "action_intent": {"type": "use_item", "parameters": {}},
        },
    )
    assert result_use_item["turn_outcome"] == "clarification"


def test_resolution_helper_use_item_effects_cover_non_dict_and_mp() -> None:
    """
    功能：验证 resolution helper use item effects cover non dict and mp 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = _DummyLoop()
    loop.entity_probes._item_definition = {
        "effects": [
            "invalid_effect",
            {"target_attribute": "hp", "value": 3},
            {"target_attribute": "mp", "value": 2},
        ]
    }
    state = {
        "action_intent": {"type": "use_item", "parameters": {"item_id": "health_potion_01"}},
    }
    result = resolve_action_sync(loop, state)
    assert result["physics_diff"]["hp_delta"] == 3
    assert result["physics_diff"]["mp_delta"] == 2
    assert result["physics_diff"]["consumed_item_id"] == "health_potion_01"


def test_runtime_event_contexts_cover_action_scene_and_item() -> None:
    """
    功能：验证主循环 helper 会从确定性结算结果生成 event 触发器上下文。
    入参：无。
    出参：None。
    异常：断言失败表示事件触发器缺少动作、场景切换或物品消耗输入。
    """
    events = _build_runtime_event_contexts(
        state={"active_character_id": "player_01"},
        action_result={
            "action": "move",
            "target_id": "red_lantern_lane",
            "interaction_id": "exit_lane",
            "raw_input": "去红灯笼巷",
        },
        physics_diff={"consumed_item_id": "boat_ticket"},
        current_scene_id="ferry_landing",
        scene_switch_to="red_lantern_lane",
    )

    by_name = {event["event_name"]: event for event in events}
    assert set(by_name) == {"action_resolved", "scene_changed", "item_consumed"}
    assert by_name["action_resolved"]["target_id"] == "red_lantern_lane"
    assert by_name["action_resolved"]["interaction_id"] == "exit_lane"
    assert "raw_input" not in by_name["action_resolved"]
    assert by_name["scene_changed"]["from_scene_id"] == "ferry_landing"
    assert by_name["scene_changed"]["to_scene_id"] == "red_lantern_lane"
    assert by_name["item_consumed"]["item_id"] == "boat_ticket"


def test_evaluate_pack_triggers_supports_item_consumed_event() -> None:
    """
    功能：验证 pack 触发器评估会消费主循环生成的 item_consumed 运行时事件。
    入参：无。
    出参：None。
    异常：断言失败表示 event 类型未接入主循环触发器入口。
    """
    trigger = {
        "trigger_id": "evt_ticket_consumed",
        "type": "event",
        "label": "船票已交付",
        "description": "玩家交出船票后触发。",
        "effects": ["narrative"],
        "conditions": {
            "match": {
                "event_name": "item_consumed",
                "item_id": "boat_ticket",
            },
            "narrative_text": "船夫收起船票，竹篙点向雾面。",
        },
        "once": True,
        "priority": 5,
    }

    events, fired_ids = _evaluate_pack_triggers(
        pack_triggers={"evt_ticket_consumed": trigger},
        state={"active_character_id": "player_01", "active_character": {}},
        action_result={"action": "use_item"},
        physics_diff={"consumed_item_id": "boat_ticket"},
        current_scene_id="ferry_landing",
        fired_ids=set(),
        quest_states=[],
    )

    assert fired_ids == {"evt_ticket_consumed"}
    assert len(events) == 1
    assert events[0]["trigger_id"] == "evt_ticket_consumed"
    assert events[0]["type"] == "event"
    assert events[0]["event_name"] == "item_consumed"
    assert events[0]["narrative_text"] == "船夫收起船票，竹篙点向雾面。"


def test_evaluate_pack_triggers_skips_source_enter_scene_when_moving() -> None:
    """
    功能：验证移动回合只触发目标场景 enter_scene，不会把源场景进入事件延迟到离场时触发。
    入参：无。
    出参：None。
    异常：断言失败表示剧本 once 进入触发器时序回归，会造成玩家离开场景时看到错误剧情。
    """
    source_trigger = {
        "trigger_id": "enter_ferry_landing_intro",
        "type": "enter_scene",
        "label": "渡口开场",
        "description": "玩家进入渡口时触发。",
        "effects": ["narrative"],
        "conditions": {
            "scene_id": "ferry_landing",
            "narrative_text": "赤灯在大雾中提前亮起。",
        },
        "once": True,
        "priority": 100,
    }
    target_trigger = {
        "trigger_id": "enter_ledgers_room_intro",
        "type": "enter_scene",
        "label": "旧账房",
        "description": "玩家进入旧账房时触发。",
        "effects": ["narrative"],
        "conditions": {
            "scene_id": "ledgers_room",
            "narrative_text": "旧账房里纸页潮湿发卷。",
        },
        "once": True,
        "priority": 90,
    }

    events, fired_ids = _evaluate_pack_triggers(
        pack_triggers={
            "enter_ferry_landing_intro": source_trigger,
            "enter_ledgers_room_intro": target_trigger,
        },
        state={"active_character_id": "player_01", "active_character": {}},
        action_result={"action": "move", "target_id": "ledgers_room"},
        physics_diff={"location_id": "ledgers_room"},
        current_scene_id="ferry_landing",
        fired_ids=set(),
        scene_switch_to="ledgers_room",
        quest_states=[],
    )

    assert [event["trigger_id"] for event in events] == ["enter_ledgers_room_intro"]
    assert fired_ids == {"enter_ledgers_room_intro"}


def test_resolve_action_exposes_pack_trigger_runtime_errors() -> None:
    """
    功能：验证剧本包触发器运行期错误会进入回合结果诊断，而不是静默丢失。
    入参：无。
    出参：None。
    异常：断言失败表示 pack runtime 错误可观测性回归。
    """
    loop = _DummyLoop()
    result = resolve_action_sync(
        loop,
        {
            "active_character_id": "player_01",
            "active_character": {"id": "player_01", "state_flags": []},
            "current_scene_id": "ferry_landing",
            "action_intent": {"type": "observe"},
            "pack_bundle_triggers": {
                "bad_trigger": {
                    "trigger_id": "bad_trigger",
                    "type": "unsupported",
                    "label": "坏触发器",
                    "effects": ["narrative"],
                }
            },
            "pack_bundle_quests": {},
            "fired_trigger_ids": [],
            "quest_states": [],
            "session_metadata": {},
        },
    )

    assert result["trigger_events"] == []
    assert result["pack_runtime_errors"]
    assert "bad_trigger" in result["pack_runtime_errors"][0]["error"]


def test_outer_helpers_callbacks_cover_cancel_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    功能：验证 outer helpers callbacks cover cancel and error 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = _DummyLoop()
    state = {
        "is_valid": True,
        "physics_diff": {"hp_delta": 1},
        "active_character_id": "player_01",
        "is_sandbox_mode": False,
        "turn_id": 1,
        "user_input": "观察周围",
        "final_response": "叙事",
        "active_character": {"location": "unknown"},
        "should_advance_turn": True,
    }

    async def _sleep_emit(_loop: Any, _state: Any) -> dict[str, Any]:
        """
        功能：提供 sleep emit 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        await asyncio.sleep(0.05)
        return {"status": "ok", "detail": {"mode": "sync"}}

    monkeypatch.setattr(outer_helpers, "emit_outer_events", _sleep_emit)

    async def _cancel_case() -> None:
        """
        功能：提供 cancel case 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        result = outer_helpers.emit_outer_events_background(loop, state)
        assert result["status"] == "started"
        task = next(iter(loop._outer_emit_tasks))
        task.cancel()
        await asyncio.sleep(0.05)

    asyncio.run(_cancel_case())

    async def _raise_emit(_loop: Any, _state: Any) -> dict[str, Any]:
        """
        功能：提供 raise emit 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("boom")

    monkeypatch.setattr(outer_helpers, "emit_outer_events", _raise_emit)

    async def _error_case() -> None:
        """
        功能：提供 error case 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        result = outer_helpers.emit_outer_events_background(loop, state)
        assert result["status"] == "started"
        await asyncio.sleep(0.05)

    asyncio.run(_error_case())


def test_schedule_outbox_replay_callback_cover_cancel_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 schedule outbox replay callback cover cancel and error 场景。
    入参：按函数签名接收 pytest fixture 或测试辅助参数。
    出参：None；通过断言表达测试结果。
    异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
    """
    loop = _DummyLoop()

    async def _cancelled_replay(_loop: Any) -> None:
        """
        功能：提供 cancelled replay 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        await asyncio.sleep(0.2)

    monkeypatch.setattr(outer_helpers, "replay_outbox_once", _cancelled_replay)

    async def _cancel_case() -> None:
        """
        功能：提供 cancel case 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        outer_helpers.schedule_outbox_replay(loop)
        assert loop._outer_replay_task is not None
        loop._outer_replay_task.cancel()
        await asyncio.sleep(0.05)

    asyncio.run(_cancel_case())

    async def _error_replay(_loop: Any) -> None:
        """
        功能：提供 error replay 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        raise RuntimeError("replay failed")

    monkeypatch.setattr(outer_helpers, "replay_outbox_once", _error_replay)

    async def _error_case() -> None:
        """
        功能：提供 error case 测试辅助逻辑。
        入参：按函数签名接收 pytest fixture 或测试辅助参数。
        出参：按测试辅助语义返回模拟值、上下文对象或 None；具体语义由调用断言约束。
        异常：断言失败由 pytest 报告；未捕获异常表示被测路径回归。
        """
        outer_helpers.schedule_outbox_replay(loop)
        assert loop._outer_replay_task is not None
        await asyncio.sleep(0.05)

    asyncio.run(_error_case())
