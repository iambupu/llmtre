from __future__ import annotations

from typing import Any

import pytest

import tools.logs.replay_outer_outbox as replay_outer_outbox


class _CollectingBridge:
    """
    功能：收集 outbox 重放投递的三类外环事件。
    入参：fail_event（str | None，默认 None）：指定要抛错的事件方法。
    出参：_CollectingBridge。
    异常：匹配 fail_event 时对应 emit 抛 RuntimeError。
    """

    def __init__(self, fail_event: str | None = None) -> None:
        self.fail_event = fail_event
        self.state_events: list[Any] = []
        self.turn_events: list[Any] = []
        self.world_events: list[Any] = []

    async def emit_state_changed(self, event: Any) -> None:
        if self.fail_event == "state_changed":
            raise RuntimeError("state failed")
        self.state_events.append(event)

    async def emit_turn_ended(self, event: Any) -> None:
        if self.fail_event == "turn_ended":
            raise RuntimeError("turn failed")
        self.turn_events.append(event)

    async def emit_world_evolution(self, event: Any) -> None:
        if self.fail_event == "world_evolution":
            raise RuntimeError("world failed")
        self.world_events.append(event)


class _FakeUpdater:
    """
    功能：替代 DBUpdater，提供可控 reserve rows 并记录 delivered/failed 回写。
    入参：rows（list[dict[str, Any]]）：待重放 outbox 行。
    出参：_FakeUpdater。
    异常：无显式异常。
    """

    rows: list[dict[str, Any]] = []
    last_instance: _FakeUpdater | None = None

    def __init__(self) -> None:
        self.delivered: list[int] = []
        self.failed: list[dict[str, Any]] = []
        self.reserve_args: dict[str, Any] = {}
        _FakeUpdater.last_instance = self

    def reserve_pending_outer_events(
        self,
        limit: int,
        processing_timeout_seconds: int,
    ) -> list[dict[str, Any]]:
        self.reserve_args = {
            "limit": limit,
            "processing_timeout_seconds": processing_timeout_seconds,
        }
        return list(self.rows)

    def mark_outer_event_delivered(self, event_id: int) -> None:
        self.delivered.append(event_id)

    def mark_outer_event_failed(
        self,
        event_id: int,
        error: str,
        max_attempts: int,
        base_backoff_seconds: int,
    ) -> None:
        self.failed.append(
            {
                "event_id": event_id,
                "error": error,
                "max_attempts": max_attempts,
                "base_backoff_seconds": base_backoff_seconds,
            }
        )


@pytest.mark.asyncio
async def test_dispatch_delivers_all_supported_event_types() -> None:
    """
    功能：验证 `_dispatch` 能把三类 outbox 事件路由到对应 bridge 方法。
    入参：无。
    出参：None。
    异常：断言失败表示外环事件投递映射回归。
    """
    bridge = _CollectingBridge()

    await replay_outer_outbox._dispatch(  # noqa: SLF001
        bridge,
        "state_changed",
        {"entity_id": "player_01", "diff": {"hp_delta": 1}, "is_sandbox": True},
    )
    await replay_outer_outbox._dispatch(  # noqa: SLF001
        bridge,
        "turn_ended",
        {"turn_id": "7", "user_input": "观察", "final_response": "叙事"},
    )
    await replay_outer_outbox._dispatch(  # noqa: SLF001
        bridge,
        "world_evolution",
        {"time_passed_minutes": True, "location_id": None},
    )

    assert bridge.state_events[0].entity_id == "player_01"
    assert bridge.state_events[0].is_sandbox is True
    assert bridge.turn_events[0].turn_id == 7
    assert bridge.world_events[0].time_passed_minutes == 1
    assert bridge.world_events[0].location_id is None


@pytest.mark.asyncio
async def test_dispatch_rejects_invalid_payload_fields() -> None:
    """
    功能：验证 `_dispatch` 对字段非法的事件抛出 ValueError，交由 `_run` 标记失败。
    入参：无。
    出参：None。
    异常：断言失败表示字段校验边界回归。
    """
    bridge = _CollectingBridge()

    with pytest.raises(ValueError, match="state_changed.diff 必须是字典"):
        await replay_outer_outbox._dispatch(bridge, "state_changed", {"entity_id": "player_01"})  # noqa: SLF001
    with pytest.raises(ValueError, match="turn_id 无法转换为整数"):
        await replay_outer_outbox._dispatch(  # noqa: SLF001
            bridge,
            "turn_ended",
            {"turn_id": "bad", "user_input": "观察", "final_response": "叙事"},
        )


def _patch_run_deps(monkeypatch: pytest.MonkeyPatch, bridge: _CollectingBridge) -> None:
    """
    功能：替换 `_run` 的数据库、外环桥和规则加载依赖。
    入参：monkeypatch；bridge（_CollectingBridge）：测试外环桥。
    出参：None。
    异常：补丁失败时由 pytest 抛出。
    """
    monkeypatch.setattr(replay_outer_outbox, "DBUpdater", _FakeUpdater)
    monkeypatch.setattr(replay_outer_outbox, "WorkflowOuterLoopBridge", lambda: bridge)
    monkeypatch.setattr(
        replay_outer_outbox,
        "load_main_loop_rules",
        lambda: {
            "outer_loop": {
                "outbox_max_attempts": 3,
                "outbox_backoff_seconds": 9,
                "outbox_processing_timeout_seconds": 11,
            }
        },
    )


@pytest.mark.asyncio
async def test_run_prints_empty_when_no_rows(monkeypatch, capsys) -> None:
    """
    功能：验证 `_run` 空队列返回 0 并输出 OUTBOX_EMPTY。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示空队列运维输出回归。
    """
    _FakeUpdater.rows = []
    _patch_run_deps(monkeypatch, _CollectingBridge())

    code = await replay_outer_outbox._run(limit=5)  # noqa: SLF001

    assert code == 0
    assert "OUTBOX_EMPTY" in capsys.readouterr().out
    assert _FakeUpdater.last_instance.reserve_args == {
        "limit": 5,
        "processing_timeout_seconds": 11,
    }


@pytest.mark.asyncio
async def test_run_marks_all_rows_delivered_and_prints_summary(monkeypatch, capsys) -> None:
    """
    功能：验证 `_run` 全部成功时标记 delivered，输出 delivered/failed 汇总并返回 0。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 outbox 全成功验收输出回归。
    """
    bridge = _CollectingBridge()
    _FakeUpdater.rows = [
        {
            "id": 1,
            "event_name": "state_changed",
            "payload": {"entity_id": "player_01", "diff": {"hp_delta": 1}},
        },
        {
            "id": 2,
            "event_name": "turn_ended",
            "payload": {"turn_id": 2, "user_input": "观察", "final_response": "叙事"},
        },
    ]
    _patch_run_deps(monkeypatch, bridge)

    code = await replay_outer_outbox._run(limit=10)  # noqa: SLF001

    assert code == 0
    assert _FakeUpdater.last_instance.delivered == [1, 2]
    assert _FakeUpdater.last_instance.failed == []
    assert "OUTBOX_REPLAY_DONE delivered=2 failed=0" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_marks_partial_failure_with_dead_letter_parameters(monkeypatch, capsys) -> None:
    """
    功能：验证 `_run` 部分失败时回写失败参数、输出汇总并返回 1。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 outbox 失败重试参数透传回归。
    """
    _FakeUpdater.rows = [
        {
            "id": "3",
            "event_name": "state_changed",
            "payload": {"entity_id": "player_01", "diff": {"hp_delta": 1}},
        }
    ]
    _patch_run_deps(monkeypatch, _CollectingBridge(fail_event="state_changed"))

    code = await replay_outer_outbox._run(limit=1)  # noqa: SLF001

    assert code == 1
    assert _FakeUpdater.last_instance.delivered == []
    assert _FakeUpdater.last_instance.failed == [
        {
            "event_id": 3,
            "error": "state failed",
            "max_attempts": 3,
            "base_backoff_seconds": 9,
        }
    ]
    assert "OUTBOX_REPLAY_DONE delivered=0 failed=1" in capsys.readouterr().out
