"""
MainEventLoop 持久化写链辅助函数。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("Workflow.MainLoop")


def build_write_plan(loop: Any, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """
    功能：根据动作类型与结算差异生成数据库写计划，定义事务内执行顺序。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：当前回合状态。
    出参：list[dict[str, Any]]，写计划列表。
    异常：无显式捕获时向上抛出；依赖读取异常由调用方处理。
    """
    action = state.get("action_intent") or {}
    action_type = str(action.get("type", ""))
    diff = state.get("physics_diff") or {}
    is_sandbox = state.get("is_sandbox_mode", False)
    entity_id = state["active_character_id"]
    session_id = str(state.get("session_id", ""))
    write_plan: list[dict[str, Any]] = []

    if action_type == "commit_sandbox":
        write_plan.append({"type": "merge_shadow", "session_id": session_id})
        write_plan.append({"type": "drop_shadow", "session_id": session_id})
        write_plan.append({"type": "advance_turn", "turns": 1})
        return write_plan

    if action_type == "discard_sandbox":
        write_plan.append({"type": "drop_shadow", "session_id": session_id})
        write_plan.append({"type": "advance_turn", "turns": 1})
        return write_plan

    if is_sandbox and not loop.db_updater.has_shadow_state():
        write_plan.append({"type": "fork_shadow", "session_id": session_id})

    if diff:
        write_plan.append(
            {
                "type": "apply_diff",
                "entity_id": entity_id,
                "diff": diff,
                "use_shadow": is_sandbox,
            }
        )

    consumed_item_id = diff.get("consumed_item_id")
    if isinstance(consumed_item_id, str):
        write_plan.append(
            {
                "type": "consume_item",
                "owner_id": entity_id,
                "item_id": consumed_item_id,
                "use_shadow": is_sandbox,
            }
        )

    target_id = action.get("target_id")
    target_hp_delta = diff.get("target_hp_delta")
    if target_id and isinstance(target_hp_delta, int):
        write_plan.append(
            {
                "type": "apply_diff",
                "entity_id": str(target_id),
                "diff": {"hp_delta": target_hp_delta},
                "use_shadow": is_sandbox,
            }
        )

    write_plan.append({"type": "advance_turn", "turns": 1})
    return write_plan


def execute_write_op(loop: Any, op: dict[str, Any], conn: Any | None = None) -> bool:
    """
    功能：执行单条写计划指令，并路由到 DBUpdater 对应方法。
    入参：loop（Any）：MainEventLoop 实例；op（dict[str, Any]）：单条写操作；
        conn（Any | None，默认 None）：事务连接句柄。
    出参：bool，操作执行成功返回 True。
    异常：DBUpdater 调用异常向上抛出，由事务边界统一回滚。
    """
    op_type = str(op.get("type", ""))
    if op_type == "fork_shadow":
        return bool(
            loop.db_updater.fork_shadow_state(
                conn=conn,
                session_id=str(op.get("session_id", "")) or None,
            )
        )
    if op_type == "merge_shadow":
        return bool(
            loop.db_updater.merge_shadow_state(
                conn=conn,
                session_id=str(op.get("session_id", "")) or None,
            )
        )
    if op_type == "drop_shadow":
        return bool(
            loop.db_updater.drop_shadow_state(
                conn=conn,
                session_id=str(op.get("session_id", "")) or None,
            )
        )
    if op_type == "apply_diff":
        return bool(
            loop.db_updater.apply_diff(
                entity_id=str(op.get("entity_id", "")),
                diff=dict(op.get("diff", {})),
                use_shadow=bool(op.get("use_shadow", False)),
                conn=conn,
            )
        )
    if op_type == "consume_item":
        return bool(
            loop.db_updater.consume_item(
                owner_id=str(op.get("owner_id", "")),
                item_id=str(op.get("item_id", "")),
                quantity=int(op.get("quantity", 1)),
                use_shadow=bool(op.get("use_shadow", False)),
                conn=conn,
            )
        )
    if op_type == "advance_turn":
        return bool(loop.db_updater.advance_turn(turns=int(op.get("turns", 1)), conn=conn))
    return False


def update_state_sync(loop: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    """
    功能：在事件总线事务拦截器内执行写计划，并回传回合推进后的状态补丁。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：当前回合状态。
    出参：dict[str, Any]，包含 turn_id/runtime_turn_id/write_results 等字段。
    异常：事务执行异常向上抛出，由调用方决定失败处理策略。
    """
    write_plan = build_write_plan(loop, state)
    # 事务句柄通过闭包在 begin/execute/commit/rollback 之间共享。
    txn: dict[str, Any] = {"conn": None}

    def _begin() -> None:
        """
        功能：开启数据库事务，并把连接句柄写入共享闭包。
        入参：无。
        出参：None，无返回值。
        异常：连接创建异常向上抛出，阻止后续写链执行。
        """
        txn["conn"] = loop.db_updater.begin_transaction()

    def _execute(op: dict[str, Any]) -> bool:
        """
        功能：在同一事务连接上执行单条写操作。
        入参：op（dict[str, Any]）：写计划中的操作项。
        出参：bool，操作执行成功返回 True。
        异常：执行异常向上抛出，由事件总线触发回滚。
        """
        return execute_write_op(loop, op, conn=txn["conn"])

    def _commit() -> None:
        """
        功能：提交事务并清理共享连接句柄。
        入参：无。
        出参：None，无返回值。
        异常：提交异常向上抛出，交由上层失败处理。
        """
        if txn["conn"] is None:
            return
        loop.db_updater.commit_transaction(txn["conn"])
        txn["conn"] = None

    def _rollback() -> None:
        """
        功能：事务失败时回滚并清理共享连接句柄。
        入参：无。
        出参：None，无返回值。
        异常：回滚异常向上抛出，交由调用方记录与处理。
        """
        if txn["conn"] is None:
            return
        loop.db_updater.rollback_transaction(txn["conn"])
        txn["conn"] = None

    write_result = loop.event_bus.apply_write_plan(
        dict(state),
        write_plan,
        _execute,
        begin=_begin,
        commit=_commit,
        rollback=_rollback,
    )
    loop.event_bus.emit("on_action_post", dict(state))
    logger.info("数据库更新已提交")
    # turn_id 以 timeline 真值为准，避免回合号被请求级初始值污染。
    current_turn = loop.db_updater.get_total_turns()
    action_intent = state.get("action_intent")
    action_type = action_intent.get("type") if isinstance(action_intent, dict) else None
    write_results_raw = write_result.get("results", [])
    write_results = write_results_raw if isinstance(write_results_raw, list) else []
    if action_type in {"commit_sandbox", "discard_sandbox"}:
        return {
            "turn_id": current_turn,
            "runtime_turn_id": current_turn,
            "is_sandbox_mode": False,
            "write_results": write_results,
        }
    return {
        "turn_id": current_turn,
        "runtime_turn_id": current_turn,
        "write_results": write_results,
    }
