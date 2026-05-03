from __future__ import annotations

import pytest

from game_workflows.async_watchers import (
    GlobalEventWorkflow,
    NoOpOuterLoopBridge,
    WorkflowOuterLoopBridge,
)
from game_workflows.event_schemas import StateChangedEvent, TurnEndedEvent, WorldEvolutionEvent


class _FakeWorkflow:
    """
    功能：模拟工作流 run 接口，记录 start_event 并返回可等待结果。
    入参：无。
    出参：_FakeWorkflow。
    异常：不抛异常。
    """

    def __init__(self) -> None:
        """
        功能：初始化事件记录容器。
        入参：无。
        出参：None。
        异常：不抛异常。
        """
        self.last_start_event = None

    def run(self, start_event):
        """
        功能：记录 start_event，并返回异步 handler。
        入参：start_event：工作流启动事件对象。
        出参：coroutine，await 后返回固定字符串。
        异常：不抛异常。
        """
        self.last_start_event = start_event

        async def _handler():
            return "handled"

        return _handler()


class _FakeDBUpdater:
    """
    功能：为 GlobalEventWorkflow 提供最小 DB 依赖替身。
    入参：无。
    出参：_FakeDBUpdater。
    异常：不抛异常。
    """

    def __init__(self, *args, **kwargs) -> None:
        self.unlocked: set[tuple[str, str]] = set()

    def is_achievement_unlocked(self, entity_id: str, achievement_id: str) -> bool:
        return (entity_id, achievement_id) in self.unlocked


@pytest.mark.asyncio
async def test_workflow_bridge_dispatch_maps_event_name_and_payload() -> None:
    """
    功能：验证 WorkflowOuterLoopBridge 会把事件映射为 start_event 并透传 payload。
    入参：无。
    出参：None。
    异常：断言失败表示桥接投递主分支回归。
    """
    workflow = _FakeWorkflow()
    bridge = WorkflowOuterLoopBridge(workflow=workflow)
    event = TurnEndedEvent(turn_id=7, user_input="观察", final_response="你看到树林")
    result = await bridge.emit_turn_ended(event)
    assert result == "handled"
    assert workflow.last_start_event is not None
    assert workflow.last_start_event.get("event_name") == "turn_ended"
    assert workflow.last_start_event.get("payload") == {
        "turn_id": 7,
        "user_input": "观察",
        "final_response": "你看到树林",
    }


def test_workflow_bridge_event_name_and_payload_fallbacks() -> None:
    """
    功能：验证 unknown 事件与非模型输入时，桥接层采用保守回退值。
    入参：无。
    出参：None。
    异常：断言失败表示回退分支退化。
    """
    state_changed = StateChangedEvent(entity_id="p1", diff={})
    world_evo = WorldEvolutionEvent(time_passed_minutes=5, location_id=None)
    assert WorkflowOuterLoopBridge._event_name(state_changed) == "state_changed"
    assert WorkflowOuterLoopBridge._event_name(world_evo) == "world_evolution"
    assert WorkflowOuterLoopBridge._event_name({"foo": "bar"}) == "unknown"
    assert WorkflowOuterLoopBridge._event_payload({"a": 1}) == {"a": 1}
    assert WorkflowOuterLoopBridge._event_payload("not_dict") == {}


@pytest.mark.asyncio
async def test_noop_bridge_methods_are_callable() -> None:
    """
    功能：验证 NoOpOuterLoopBridge 三类 emit 接口可正常 await 且不抛异常。
    入参：无。
    出参：None。
    异常：断言失败表示默认桥接接口稳定性退化。
    """
    bridge = NoOpOuterLoopBridge()
    await bridge.emit_turn_ended(
        TurnEndedEvent(turn_id=1, user_input="等待", final_response="时间流逝")
    )
    await bridge.emit_state_changed(StateChangedEvent(entity_id="p1", diff={"hp_delta": -1}))
    await bridge.emit_world_evolution(
        WorldEvolutionEvent(time_passed_minutes=10, location_id="loc_1")
    )


def test_global_workflow_achievement_helpers(monkeypatch) -> None:
    """
    功能：验证 GlobalEventWorkflow 的成就推导、一次性标记与奖励过滤分支。
    入参：monkeypatch（pytest fixture）：函数替换工具。
    出参：None。
    异常：断言失败表示 async_watchers 核心辅助逻辑回归。
    """

    class _FakeEvolutionAgent:
        def __init__(self, db_updater) -> None:  # noqa: ARG002
            pass

    monkeypatch.setattr("game_workflows.async_watchers.DBUpdater", _FakeDBUpdater)
    monkeypatch.setattr("game_workflows.async_watchers.EvolutionAgent", _FakeEvolutionAgent)
    monkeypatch.setattr(
        "game_workflows.async_watchers.load_main_loop_rules",
        lambda: {
            "outer_loop": {
                "achievement_rewards": {
                    "first_blood": {"hp_delta": 2, "mp_delta": 1, "ignored": "x"},
                    "broken": "not_dict",
                }
            }
        },
    )
    workflow = GlobalEventWorkflow(timeout=1, verbose=False)

    event_damage = StateChangedEvent(entity_id="p1", diff={"target_hp_delta": -3})
    achievement_damage = workflow._derive_achievement_event(event_damage)
    assert achievement_damage is not None
    assert achievement_damage.achievement_id == "first_blood"

    event_observe = StateChangedEvent(
        entity_id="p2",
        diff={"state_flags_add": ["observed_surroundings"]},
    )
    achievement_observe = workflow._derive_achievement_event(event_observe)
    assert achievement_observe is not None
    assert achievement_observe.achievement_id == "keen_observer"

    assert workflow._mark_achievement_once("p1", "first_blood") is True
    assert workflow._mark_achievement_once("p1", "first_blood") is False

    rewards = workflow._achievement_rewards()
    assert rewards["first_blood"] == {"hp_delta": 2, "mp_delta": 1}
