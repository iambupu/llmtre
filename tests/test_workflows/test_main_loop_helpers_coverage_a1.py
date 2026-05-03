from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from game_workflows import main_loop_outer_helpers as outer_helpers
from game_workflows.main_loop_resolution_helpers import resolve_action_sync
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
    def clarify(self, _envelope: AgentEnvelope) -> Any:
        return SimpleNamespace(payload={"clarification_question": "请补充细节"})


class _DummyEntityProbes:
    def __init__(self) -> None:
        self._character_stats: dict[str, Any] | None = {"entity_id": "player_01"}
        self._inventory_item: dict[str, Any] | None = {"quantity": 1}
        self._item_definition: dict[str, Any] | None = {"effects": []}
        self._location_info: dict[str, Any] | None = None
        self._nearby_entities: list[dict[str, Any]] = []

    def get_character_stats(self, _entity_id: str, use_shadow: bool = False) -> Any:
        del use_shadow
        return self._character_stats

    def check_inventory(self, _entity_id: str, use_shadow: bool = False) -> list[dict[str, Any]]:
        del use_shadow
        return [{"item_id": "health_potion_01"}]

    def get_inventory_item(
        self, _owner_id: str, _item_id: str, use_shadow: bool = False
    ) -> dict[str, Any] | None:
        del use_shadow
        return self._inventory_item

    def get_item_definition(self, _item_id: str) -> dict[str, Any] | None:
        return self._item_definition

    def get_location_info(self, _location_id: str, use_shadow: bool = False) -> dict[str, Any] | None:
        del use_shadow
        return self._location_info

    def list_nearby_entities(
        self, _location_id: str, use_shadow: bool = False
    ) -> list[dict[str, Any]]:
        del use_shadow
        return list(self._nearby_entities)


class _DummyLoop:
    def __init__(self) -> None:
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
        return None

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _resolve_configured_action(
        self, action_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"configured": action_type, **payload}


async def _noop_async(_event: Any) -> None:
    return None


def test_scene_helpers_handle_invalid_and_fallback_paths() -> None:
    assert normalize_scene_exits("invalid") == []
    assert normalize_scene_exits([1, {"direction": "N"}]) == []
    assert normalize_dict_list("invalid") == []

    probes = _DummyEntityProbes()
    probes._character_stats = None
    assert build_character_state(probes, "player_01") is None

    probes._character_stats = {
        "entity_id": "player_01",
        "name": "玩家",
        "hp": 10,
        "max_hp": 10,
        "mp": 5,
        "max_mp": 5,
        "current_location_id": "unknown",
    }
    character = build_character_state(probes, "player_01")
    assert character is not None

    snapshot_none = build_scene_snapshot(probes, {}, None)
    assert snapshot_none is None

    rules = {"scene_defaults": {"locations": {"unknown": {"id": "unknown", "exits": []}}}}
    snapshot = build_scene_snapshot(probes, rules, character, recent_memory="memo")
    assert snapshot is not None
    assert snapshot["current_location"]["id"] == "unknown"
    assert snapshot["recent_memory"] == "memo"


def test_validation_helpers_cover_branches() -> None:
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


def test_outer_helpers_callbacks_cover_cancel_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
        await asyncio.sleep(0.05)
        return {"status": "ok", "detail": {"mode": "sync"}}

    monkeypatch.setattr(outer_helpers, "emit_outer_events", _sleep_emit)

    async def _cancel_case() -> None:
        result = outer_helpers.emit_outer_events_background(loop, state)
        assert result["status"] == "started"
        task = next(iter(loop._outer_emit_tasks))
        task.cancel()
        await asyncio.sleep(0.05)

    asyncio.run(_cancel_case())

    async def _raise_emit(_loop: Any, _state: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(outer_helpers, "emit_outer_events", _raise_emit)

    async def _error_case() -> None:
        result = outer_helpers.emit_outer_events_background(loop, state)
        assert result["status"] == "started"
        await asyncio.sleep(0.05)

    asyncio.run(_error_case())


def test_schedule_outbox_replay_callback_cover_cancel_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = _DummyLoop()

    async def _cancelled_replay(_loop: Any) -> None:
        await asyncio.sleep(0.2)

    monkeypatch.setattr(outer_helpers, "replay_outbox_once", _cancelled_replay)

    async def _cancel_case() -> None:
        outer_helpers.schedule_outbox_replay(loop)
        assert loop._outer_replay_task is not None
        loop._outer_replay_task.cancel()
        await asyncio.sleep(0.05)

    asyncio.run(_cancel_case())

    async def _error_replay(_loop: Any) -> None:
        raise RuntimeError("replay failed")

    monkeypatch.setattr(outer_helpers, "replay_outbox_once", _error_replay)

    async def _error_case() -> None:
        outer_helpers.schedule_outbox_replay(loop)
        assert loop._outer_replay_task is not None
        await asyncio.sleep(0.05)

    asyncio.run(_error_case())
