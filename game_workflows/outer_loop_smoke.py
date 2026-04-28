from __future__ import annotations

import asyncio

from game_workflows.async_watchers import WorkflowOuterLoopBridge
from game_workflows.event_schemas import TurnEndedEvent


async def _run() -> None:
    """
    功能：执行 `_run` 相关业务逻辑。
    入参：无。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    bridge = WorkflowOuterLoopBridge()
    result = await bridge.emit_turn_ended(
        TurnEndedEvent(
            turn_id=1,
            user_input="观察周围",
            final_response="测试回合结束",
        )
    )
    text = str(result)
    print(f"OUTER_RESULT={text}")
    if "audit completed" not in text:
        raise RuntimeError(f"外环回合事件未返回预期结果: {text}")


def main() -> None:
    """
    功能：执行 `main` 相关业务逻辑。
    入参：无。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    asyncio.run(_run())
    print("OUTER_LOOP_SMOKE_OK")


if __name__ == "__main__":
    main()
