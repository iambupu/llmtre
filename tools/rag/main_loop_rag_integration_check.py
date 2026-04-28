import asyncio

from core.event_bus import EventBus
from game_workflows.main_event_loop import MainEventLoop


async def _run_check() -> None:
    """
    功能：执行 `_run_check` 相关业务逻辑。
    入参：无。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    event_bus = EventBus("config/mod_registry.yml", "mods")
    loop = MainEventLoop(event_bus)
    result = await loop.run("观察周围")
    snapshot = result.get("world_snapshot") or {}

    print(f"IS_VALID={result.get('is_valid')}")
    print(f"ACTION={(result.get('action_intent') or {}).get('type')}")
    print(f"RAG_READY={snapshot.get('rag_ready')}")
    print(f"RAG_ERROR={snapshot.get('rag_error')}")
    print(f"RAG_CONTEXT_LENGTH={len(snapshot.get('rag_context', ''))}")

    if not result.get("is_valid"):
        raise RuntimeError("主循环未通过基础校验。")
    if not snapshot.get("rag_ready"):
        raise RuntimeError(f"RAG 只读快照未就绪: {snapshot.get('rag_error')}")
    if len(snapshot.get("rag_context", "")) == 0:
        raise RuntimeError("RAG 上下文为空，未形成有效只读快照。")


def main() -> None:
    """
    功能：执行 `main` 相关业务逻辑。
    入参：无。
    出参：None。
    异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
    """
    asyncio.run(_run_check())
    print("MAIN_LOOP_RAG_OK")


if __name__ == "__main__":
    main()
