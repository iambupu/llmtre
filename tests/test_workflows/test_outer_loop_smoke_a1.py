from __future__ import annotations

import pytest

from game_workflows import outer_loop_smoke


class _FakeBridge:
    """
    功能：模拟外环桥接器，记录 turn_ended 事件并返回预置结果。
    入参：result（object）：emit_turn_ended 的异步返回值。
    出参：测试辅助对象。
    异常：无。
    """

    last_instance: _FakeBridge | None = None
    result: object = "audit completed"

    def __init__(self) -> None:
        self.events: list[object] = []
        _FakeBridge.last_instance = self

    async def emit_turn_ended(self, event: object) -> object:
        """
        功能：记录收到的回合结束事件并返回预置结果。
        入参：event（object）：TurnEndedEvent。
        出参：object，预置外环结果。
        异常：无。
        """
        self.events.append(event)
        return _FakeBridge.result


@pytest.mark.asyncio
async def test_run_prints_outer_result_for_success(monkeypatch, capsys) -> None:
    """
    功能：验证 _run 成功时投递固定 smoke 事件并输出外环结果。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 smoke 成功路径回归。
    """
    _FakeBridge.result = "audit completed: ok"
    monkeypatch.setattr(outer_loop_smoke, "WorkflowOuterLoopBridge", _FakeBridge)

    await outer_loop_smoke._run()  # noqa: SLF001

    output = capsys.readouterr().out
    assert "OUTER_RESULT=audit completed: ok" in output
    assert _FakeBridge.last_instance is not None
    event = _FakeBridge.last_instance.events[0]
    assert event.turn_id == 1
    assert event.user_input == "观察周围"
    assert event.final_response == "测试回合结束"


@pytest.mark.asyncio
async def test_run_raises_when_audit_marker_missing(monkeypatch, capsys) -> None:
    """
    功能：验证外环结果缺少 audit completed 标记时抛出 RuntimeError 并保留输出证据。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 smoke 失败路径回归。
    """
    _FakeBridge.result = "unexpected result"
    monkeypatch.setattr(outer_loop_smoke, "WorkflowOuterLoopBridge", _FakeBridge)

    with pytest.raises(RuntimeError, match="外环回合事件未返回预期结果"):
        await outer_loop_smoke._run()  # noqa: SLF001

    assert "OUTER_RESULT=unexpected result" in capsys.readouterr().out


def test_main_runs_async_smoke_and_prints_ok(monkeypatch, capsys) -> None:
    """
    功能：验证 main 会调用 asyncio.run 执行 smoke，并输出 OK 标记。
    入参：monkeypatch；capsys。
    出参：None。
    异常：断言失败表示 CLI 主入口输出回归。
    """
    calls: list[object] = []

    def _fake_run(coro: object) -> None:
        """
        功能：记录传入 asyncio.run 的协程对象并主动关闭，避免未 await 告警。
        入参：coro（object）：待运行协程。
        出参：None。
        异常：无。
        """
        calls.append(coro)
        close = getattr(coro, "close", None)
        if close is not None:
            close()

    monkeypatch.setattr(outer_loop_smoke.asyncio, "run", _fake_run)

    outer_loop_smoke.main()

    assert len(calls) == 1
    assert "OUTER_LOOP_SMOKE_OK" in capsys.readouterr().out
