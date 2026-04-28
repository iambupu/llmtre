from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
from typing import Any

from game_workflows.async_watchers import WorkflowOuterLoopBridge
from game_workflows.event_schemas import StateChangedEvent, TurnEndedEvent, WorldEvolutionEvent
from game_workflows.main_loop_config import load_main_loop_rules
from tools.sqlite_db.db_updater import DBUpdater


def _parse_args() -> argparse.Namespace:
    """
    功能：解析重放脚本命令行参数。
    入参：无。
    出参：argparse.Namespace，当前仅包含 `limit: int`。
    异常：参数不合法时由 argparse 抛出 `SystemExit` 并输出帮助信息。
    """
    parser = argparse.ArgumentParser(description="重放外环补偿队列（outer_event_outbox）")
    parser.add_argument("--limit", type=int, default=50, help="本次最多重放的事件数")
    return parser.parse_args()


def _coerce_int(value: object, field_name: str) -> int:
    """
    功能：将 outbox 载荷字段安全转换为整数。
    入参：value（object）：待转换值；field_name（str）：字段名，用于错误定位。
    出参：int，成功转换后的整数值。
    异常：值类型不支持或转换失败时抛出 ValueError；调用方按失败事件走重试链路。
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError as error:
            raise ValueError(f"{field_name} 无法转换为整数: {value!r}") from error
    raise ValueError(f"{field_name} 类型非法: {type(value).__name__}")


def _coerce_str(value: object, field_name: str) -> str:
    """
    功能：将 outbox 载荷字段安全转换为字符串。
    入参：value（object）：待转换值；field_name（str）：字段名，用于错误定位。
    出参：str，成功转换后的字符串。
    异常：值为 None 时抛出 ValueError；调用方按失败事件走重试链路。
    """
    if value is None:
        raise ValueError(f"{field_name} 不能为空")
    return str(value)


def _coerce_mapping(payload: object, event_name: str) -> Mapping[str, Any]:
    """
    功能：校验并提取 outbox 事件载荷字典。
    入参：payload（object）：数据库读取的 payload 字段；event_name（str）：事件类型。
    出参：Mapping[str, Any]，可供事件构造读取键值。
    异常：payload 不是字典时抛出 ValueError；调用方按失败事件走重试链路。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{event_name} payload 必须是字典，当前为 {type(payload).__name__}")
    return payload


async def _dispatch(
    bridge: WorkflowOuterLoopBridge,
    event_name: str,
    payload: Mapping[str, Any],
) -> None:
    """
    功能：按事件名将 outbox 载荷路由到对应外环事件。
    入参：bridge（WorkflowOuterLoopBridge）：外环桥接器；event_name（str）：事件名；
        payload（Mapping[str, Any]）：事件载荷字典。
    出参：None。
    异常：事件类型不支持或字段缺失时抛出 ValueError；
        下游 emit 异常不吞掉，交由调用方统一记失败并进入退避重试。
    """
    if event_name == "state_changed":
        entity_id = _coerce_str(payload.get("entity_id"), "entity_id")
        raw_diff = payload.get("diff")
        if not isinstance(raw_diff, dict):
            raise ValueError("state_changed.diff 必须是字典")
        is_sandbox = bool(payload.get("is_sandbox", False))
        await bridge.emit_state_changed(
            StateChangedEvent(entity_id=entity_id, diff=raw_diff, is_sandbox=is_sandbox)
        )
        return
    if event_name == "turn_ended":
        turn_id = _coerce_int(payload.get("turn_id"), "turn_id")
        user_input = _coerce_str(payload.get("user_input"), "user_input")
        final_response = _coerce_str(payload.get("final_response"), "final_response")
        await bridge.emit_turn_ended(
            TurnEndedEvent(
                turn_id=turn_id,
                user_input=user_input,
                final_response=final_response,
            )
        )
        return
    if event_name == "world_evolution":
        time_passed_minutes = _coerce_int(
            payload.get("time_passed_minutes"),
            "time_passed_minutes",
        )
        location_value = payload.get("location_id")
        location_id = None if location_value is None else str(location_value)
        await bridge.emit_world_evolution(
            WorldEvolutionEvent(
                time_passed_minutes=time_passed_minutes,
                location_id=location_id,
            )
        )
        return
    raise ValueError(f"不支持的 outbox 事件类型: {event_name}")


async def _run(limit: int) -> int:
    """
    功能：重放 outer_event_outbox 中待处理事件，并按结果更新状态机字段。
    入参：limit（int）：本次最多处理的 outbox 事件数量，建议大于 0。
    出参：int，全部成功返回 0；存在失败返回 1（便于 CI/运维脚本识别失败）。
    异常：函数内部不抛出单条事件异常，改为写回失败次数与退避时间；
        仅数据库连接等全局异常向上抛出并终止脚本。
    """
    updater = DBUpdater()
    bridge = WorkflowOuterLoopBridge()
    outer_rules = load_main_loop_rules().get("outer_loop", {})
    max_attempts = int(outer_rules.get("outbox_max_attempts", 5))
    base_backoff_seconds = int(outer_rules.get("outbox_backoff_seconds", 5))
    processing_timeout_seconds = int(outer_rules.get("outbox_processing_timeout_seconds", 30))
    rows = updater.reserve_pending_outer_events(
        limit=limit,
        processing_timeout_seconds=processing_timeout_seconds,
    )
    if not rows:
        print("OUTBOX_EMPTY")
        return 0

    delivered = 0
    failed = 0
    for row in rows:
        event_id = _coerce_int(row.get("id"), "id")
        event_name = _coerce_str(row.get("event_name"), "event_name")
        payload = _coerce_mapping(row.get("payload"), event_name)
        try:
            await _dispatch(bridge, event_name, payload)
            updater.mark_outer_event_delivered(event_id)
            delivered += 1
        except Exception as error:  # noqa: BLE001
            updater.mark_outer_event_failed(
                event_id=event_id,
                error=str(error),
                max_attempts=max_attempts,
                base_backoff_seconds=base_backoff_seconds,
            )
            failed += 1

    print(f"OUTBOX_REPLAY_DONE delivered={delivered} failed={failed}")
    return 0 if failed == 0 else 1


def main() -> None:
    """
    功能：脚本入口，串联参数解析与异步执行。
    入参：无。
    出参：None。
    异常：通过 `SystemExit(code)` 退出；`code` 由 `_run` 的执行结果决定。
    """
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(limit=args.limit)))


if __name__ == "__main__":
    main()
