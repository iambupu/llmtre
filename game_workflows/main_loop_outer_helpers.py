"""
MainEventLoop 外环事件投递与补偿重放辅助函数。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast

from game_workflows.event_schemas import StateChangedEvent, TurnEndedEvent, WorldEvolutionEvent
from game_workflows.graph_schema import FlowState

logger = logging.getLogger("Workflow.MainLoop")


async def emit_outer_events(loop: Any, state: FlowState) -> dict[str, Any]:
    """
    功能：同步投递最小外环事件，并返回本回合外环投递摘要。
    入参：loop（Any）：MainEventLoop 实例；state（FlowState）：当前回合状态快照。
    出参：dict[str, Any]，包含 status/detail，用于 trace 写入真实投递语义。
    异常：内部捕获所有投递异常并降级入 outbox，不向上抛出以免阻断内环。
    """
    failed_events: list[dict[str, str]] = []
    if state.get("is_valid") and state.get("physics_diff"):
        try:
            await asyncio.wait_for(
                loop.outer_bridge.emit_state_changed(
                    StateChangedEvent(
                        entity_id=str(state.get("active_character_id", "")),
                        diff=dict(state.get("physics_diff") or {}),
                        is_sandbox=bool(state.get("is_sandbox_mode", False)),
                    )
                ),
                timeout=loop.outer_emit_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("外环事件投递失败[event=state_changed]，已降级忽略: %s", error)
            failed_events.append({"event": "state_changed", "error": str(error)})
            loop.db_updater.enqueue_outer_event(
                "state_changed",
                StateChangedEvent(
                    entity_id=str(state.get("active_character_id", "")),
                    diff=dict(state.get("physics_diff") or {}),
                    is_sandbox=bool(state.get("is_sandbox_mode", False)),
                ).model_dump(),
                str(error),
            )

    try:
        await asyncio.wait_for(
            loop.outer_bridge.emit_turn_ended(
                TurnEndedEvent(
                    turn_id=int(state.get("turn_id", 0)),
                    user_input=str(state.get("user_input", "")),
                    final_response=str(state.get("final_response", "")),
                )
            ),
            timeout=loop.outer_emit_timeout_seconds,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning("外环事件投递失败[event=turn_ended]，已降级忽略: %s", error)
        failed_events.append({"event": "turn_ended", "error": str(error)})
        loop.db_updater.enqueue_outer_event(
            "turn_ended",
            TurnEndedEvent(
                turn_id=int(state.get("turn_id", 0)),
                user_input=str(state.get("user_input", "")),
                final_response=str(state.get("final_response", "")),
            ).model_dump(),
            str(error),
        )

    if loop.outer_emit_world_evolution and state.get("should_advance_turn", True):
        active_character: dict[str, Any] = dict(state.get("active_character") or {})
        # Pack 回合的场景运行态可能与角色 DB location 暂时不同步；外环演化
        # 应围绕本回合可见场景推进，非 pack 回合继续回退到角色位置。
        current_scene_id = str(state.get("current_scene_id") or "").strip()
        location_id = current_scene_id or str(active_character.get("location", "unknown"))
        try:
            await asyncio.wait_for(
                loop.outer_bridge.emit_world_evolution(
                    WorldEvolutionEvent(
                        time_passed_minutes=loop.outer_world_minutes_per_turn,
                        location_id=location_id,
                    )
                ),
                timeout=loop.outer_emit_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("外环事件投递失败[event=world_evolution]，已降级忽略: %s", error)
            failed_events.append({"event": "world_evolution", "error": str(error)})
            loop.db_updater.enqueue_outer_event(
                "world_evolution",
                WorldEvolutionEvent(
                    time_passed_minutes=loop.outer_world_minutes_per_turn,
                    location_id=location_id,
                ).model_dump(),
                str(error),
            )

    if failed_events:
        return {"status": "failed", "detail": {"mode": "sync", "failed_events": failed_events}}
    return {"status": "ok", "detail": {"mode": "sync"}}


def enqueue_outer_events(loop: Any, state: FlowState) -> dict[str, Any]:
    """
    功能：把当前回合外环事件快速写入补偿队列，避免玩家响应等待外环 Workflow 执行。
    入参：loop（Any）：MainEventLoop 实例，需提供 db_updater 与外环配置；
        state（FlowState）：当前回合状态。
    出参：dict[str, Any]，包含 status/detail，detail.mode 固定为 outbox。
    异常：单个事件入队失败会被捕获并写入 failed_events，不向上抛出阻断主回合。
    """
    queued_events: list[dict[str, Any]] = []
    failed_events: list[dict[str, str]] = []

    def _enqueue(event_name: str, payload: dict[str, object]) -> None:
        """
        功能：入队单个外环事件并记录队列 ID。
        入参：event_name（str）：外环事件名；payload（dict[str, object]）：可 JSON 序列化载荷。
        出参：None。
        异常：DB 写入失败时捕获并记录 failed_events。
        """
        try:
            event_id = loop.db_updater.enqueue_outer_event(
                event_name,
                payload,
                "queued_for_async_replay",
                attempts=0,
            )
            queued_events.append({"event": event_name, "id": event_id})
        except Exception as error:  # noqa: BLE001
            logger.warning("外环事件入队失败[event=%s]，已降级忽略: %s", event_name, error)
            failed_events.append({"event": event_name, "error": str(error)})

    if state.get("is_valid") and state.get("physics_diff"):
        _enqueue(
            "state_changed",
            StateChangedEvent(
                entity_id=str(state.get("active_character_id", "")),
                diff=dict(state.get("physics_diff") or {}),
                is_sandbox=bool(state.get("is_sandbox_mode", False)),
            ).model_dump(),
        )

    _enqueue(
        "turn_ended",
        TurnEndedEvent(
            turn_id=int(state.get("turn_id", 0)),
            user_input=str(state.get("user_input", "")),
            final_response=str(state.get("final_response", "")),
        ).model_dump(),
    )

    if loop.outer_emit_world_evolution and state.get("should_advance_turn", True):
        active_character: dict[str, Any] = dict(state.get("active_character") or {})
        # Pack 回合优先使用 current_scene_id，避免世界演化围绕旧 DB location 入队。
        current_scene_id = str(state.get("current_scene_id") or "").strip()
        location_id = current_scene_id or str(active_character.get("location", "unknown"))
        _enqueue(
            "world_evolution",
            WorldEvolutionEvent(
                time_passed_minutes=loop.outer_world_minutes_per_turn,
                location_id=location_id,
            ).model_dump(),
        )

    detail: dict[str, Any] = {
        "mode": "outbox",
        "queued_events": queued_events,
        "queued_to_outbox": bool(queued_events),
    }
    if failed_events:
        detail["failed_events"] = failed_events
        return {"status": "failed", "detail": detail}
    return {"status": "ok", "detail": detail}


def emit_outer_events_background(loop: Any, state: FlowState) -> dict[str, Any]:
    """
    功能：将外环投递放到后台任务，避免阻塞主回合返回，并返回调度结果。
    入参：loop（Any）：MainEventLoop 实例；state（FlowState）：当前回合状态快照。
    出参：dict[str, Any]，包含 status/detail，用于标记 started/skipped/failed。
    异常：内部仅记录并降级，不向上抛出。
    """
    if len(loop._outer_emit_tasks) >= loop.outer_max_pending_tasks:
        logger.warning(
            "外环后台任务达到上限，当前回合事件写入补偿队列: pending=%s",
            len(loop._outer_emit_tasks),
        )
        if state.get("is_valid") and state.get("physics_diff"):
            loop.db_updater.enqueue_outer_event(
                "state_changed",
                StateChangedEvent(
                    entity_id=str(state.get("active_character_id", "")),
                    diff=dict(state.get("physics_diff") or {}),
                    is_sandbox=bool(state.get("is_sandbox_mode", False)),
                ).model_dump(),
                "pending tasks overflow",
            )
        loop.db_updater.enqueue_outer_event(
            "turn_ended",
            TurnEndedEvent(
                turn_id=int(state.get("turn_id", 0)),
                user_input=str(state.get("user_input", "")),
                final_response=str(state.get("final_response", "")),
            ).model_dump(),
            "pending tasks overflow",
        )
        if loop.outer_emit_world_evolution:
            active_character: dict[str, Any] = dict(state.get("active_character") or {})
            loop.db_updater.enqueue_outer_event(
                "world_evolution",
                WorldEvolutionEvent(
                    time_passed_minutes=loop.outer_world_minutes_per_turn,
                    location_id=str(active_character.get("location", "unknown")),
                ).model_dump(),
                "pending tasks overflow",
            )
        logger.warning(
            "外环事件已拆分入补偿队列: actor=%s turn=%s",
            str(state.get("active_character_id", "")),
            int(state.get("turn_id", 0)),
        )
        return {
            "status": "failed",
            "detail": {
                "mode": "workflow_background",
                "reason": "pending tasks overflow",
                "queued_to_outbox": True,
            },
        }
    task = asyncio.create_task(emit_outer_events(loop, state))
    loop._outer_emit_tasks.add(task)

    def _on_done(done_task: asyncio.Task[Any]) -> None:
        """
        功能：后台投递完成回调，回收任务并记录失败信息。
        入参：done_task（asyncio.Task[Any]）：已完成后台任务。
        出参：None，无返回值。
        异常：不主动抛异常；任务异常仅记录日志。
        """
        loop._outer_emit_tasks.discard(done_task)
        if done_task.cancelled():
            logger.warning("外环事件投递后台任务被取消。")
            return
        error = done_task.exception()
        if error is not None:
            logger.warning("外环事件投递后台任务失败: %s", error)

    task.add_done_callback(_on_done)
    return {
        "status": "started",
        "detail": {
            "mode": "workflow_background",
            "task_created": True,
            "pending_tasks": len(loop._outer_emit_tasks),
        },
    }


async def replay_outbox_once(loop: Any) -> None:
    """
    功能：执行一次外环补偿队列重放，把 pending/processing 事件重新投递。
    入参：loop（Any）：MainEventLoop 实例，读取 outbox 配置与桥接器。
    出参：None，无返回值。
    异常：单条事件异常会被捕获并回写失败重试信息，不中断后续重放。
    """
    rows = loop.db_updater.reserve_pending_outer_events(
        limit=loop.outer_outbox_replay_limit,
        processing_timeout_seconds=loop.outer_outbox_processing_timeout_seconds,
    )
    for row in rows:
        event_id = loop._to_int(row.get("id", 0), 0)
        event_name = str(row.get("event_name", ""))
        payload_raw = row.get("payload", {})
        payload = cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}
        try:
            if event_name == "state_changed":
                await asyncio.wait_for(
                    loop.outer_bridge.emit_state_changed(StateChangedEvent(**payload)),
                    timeout=loop.outer_emit_timeout_seconds,
                )
            elif event_name == "turn_ended":
                await asyncio.wait_for(
                    loop.outer_bridge.emit_turn_ended(TurnEndedEvent(**payload)),
                    timeout=loop.outer_emit_timeout_seconds,
                )
            elif event_name == "world_evolution":
                await asyncio.wait_for(
                    loop.outer_bridge.emit_world_evolution(WorldEvolutionEvent(**payload)),
                    timeout=loop.outer_emit_timeout_seconds,
                )
            else:
                raise ValueError(f"unsupported outbox event: {event_name}")
            loop.db_updater.mark_outer_event_delivered(event_id)
        except Exception as error:  # noqa: BLE001
            loop.db_updater.mark_outer_event_failed(
                event_id=event_id,
                error=str(error),
                max_attempts=loop.outer_outbox_max_attempts,
                base_backoff_seconds=loop.outer_outbox_backoff_seconds,
            )
            logger.warning(
                "外环补偿重放失败[event=%s id=%s]，已回写重试状态: %s",
                event_name,
                event_id,
                error,
            )


def schedule_outbox_replay(loop: Any) -> None:
    """
    功能：按最小间隔调度 outbox 重放任务，避免每回合重复创建重放协程。
    入参：loop（Any）：MainEventLoop 实例，持有调度时间戳与任务句柄。
    出参：None，无返回值。
    异常：不主动抛异常；任务异常在回调中记录日志。
    """
    now = time.monotonic()
    if now - loop._last_outbox_replay_ts < loop.outer_outbox_replay_interval_seconds:
        return
    if loop._outer_replay_task is not None and not loop._outer_replay_task.done():
        return
    loop._last_outbox_replay_ts = now
    loop._outer_replay_task = asyncio.create_task(replay_outbox_once(loop))

    def _on_done(done_task: asyncio.Task[Any]) -> None:
        """
        功能：外环补偿重放任务结束回调，记录取消/异常状态。
        入参：done_task（asyncio.Task[Any]）：已结束任务。
        出参：None，无返回值。
        异常：不主动抛异常；异常仅记录日志。
        """
        if done_task.cancelled():
            logger.warning("外环补偿重放任务被取消。")
            return
        error = done_task.exception()
        if error is not None:
            logger.warning("外环补偿重放任务失败: %s", error)

    loop._outer_replay_task.add_done_callback(_on_done)
