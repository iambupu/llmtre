"""
MainEventLoop 动作校验辅助函数。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from state.contracts.agent import AgentEnvelope

logger = logging.getLogger("Workflow.MainLoop")


def invalid_result(message: str) -> dict[str, Any]:
    """
    功能：构造受控失败结果，禁止进入结算和剧情记忆。
    入参：message（str）：失败原因。
    出参：dict[str, Any]，主循环状态补丁。
    异常：不抛异常；输入按字符串原样记录。
    """
    return {
        "is_valid": False,
        "validation_errors": [message],
        "turn_outcome": "invalid",
        "clarification_question": "",
        "should_advance_turn": False,
        "should_write_story_memory": False,
        "failure_reason": message,
        "suggested_next_step": "观察周围",
        "debug_trace": [{"stage": "validate_action", "status": "invalid", "message": message}],
    }


def clarification_result(question: str) -> dict[str, Any]:
    """
    功能：构造澄清回合结果，返回问题但不推进世界状态。
    入参：question（str）：面向玩家的中文澄清问题。
    出参：dict[str, Any]，主循环状态补丁。
    异常：不抛异常；空问题由调用方提供默认值。
    """
    return {
        "is_valid": False,
        "validation_errors": [],
        "turn_outcome": "clarification",
        "clarification_question": question,
        "should_advance_turn": False,
        "should_write_story_memory": False,
        "failure_reason": "行动信息还不够明确。",
        "suggested_next_step": question,
        "debug_trace": [
            {"stage": "validate_action", "status": "clarification", "question": question}
        ],
    }


def clarify_with_agent(
    loop: Any,
    state: Mapping[str, Any],
    action: Mapping[str, Any] | None,
    fallback_question: str,
) -> str:
    """
    功能：调用最小 Clarifier 生成澄清问题，失败时使用调用方提供的确定性问题。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：当前回合状态；
        action（dict[str, Any] | None）：候选动作；fallback_question（str）：兜底问题。
    出参：str，玩家可见澄清问题。
    异常：内部捕获 Clarifier 异常并降级为 fallback_question，避免阻断回合。
    """
    try:
        response = loop.clarifier_agent.clarify(
            AgentEnvelope(
                trace_id=str(state.get("trace_id", "")),
                turn_id=int(state.get("turn_id", 0)),
                sender="main_loop",
                recipient="clarifier",
                kind="clarify.request",
                payload={
                    "user_input": str(state.get("user_input", "")),
                    "action_intent": action,
                    "validation_errors": state.get("validation_errors", []),
                    "scene_snapshot": state.get("scene_snapshot") or {},
                },
            )
        )
        question = str(response.payload.get("clarification_question") or "").strip()
        return question or fallback_question
    except Exception as error:  # noqa: BLE001
        logger.warning("Clarifier 生成澄清问题失败，使用兜底问题: %s", error)
        return fallback_question


def build_move_clarification(state: Mapping[str, Any]) -> str:
    """
    功能：根据当前出口生成移动澄清问题。
    入参：state（dict[str, Any]）：当前场景状态。
    出参：str，面向玩家的问题。
    异常：不抛异常；无出口时返回通用问题。
    """
    scene_snapshot_obj = state.get("scene_snapshot")
    scene_snapshot = scene_snapshot_obj if isinstance(scene_snapshot_obj, dict) else {}
    exits_raw = scene_snapshot.get("exits", [])
    exits = exits_raw if isinstance(exits_raw, list) else []
    if not exits:
        return "这里暂时没有明确出口，你想先观察周围吗？"
    labels = "、".join(exit_info["label"] for exit_info in exits)
    return f"你想往哪个方向走？当前可选出口：{labels}。"


def build_target_clarification(state: Mapping[str, Any], action_type: Any) -> str:
    """
    功能：根据可见 NPC 生成交谈或攻击目标澄清问题。
    入参：state（dict[str, Any]）：当前场景状态；action_type（Any）：候选动作类型。
    出参：str，面向玩家的问题。
    异常：不抛异常；无可见对象时返回通用问题。
    """
    scene_snapshot_obj = state.get("scene_snapshot")
    scene_snapshot = scene_snapshot_obj if isinstance(scene_snapshot_obj, dict) else {}
    npcs_raw = scene_snapshot.get("visible_npcs", [])
    npcs = npcs_raw if isinstance(npcs_raw, list) else []
    verb = "攻击" if action_type == "attack" else "交谈"
    if not npcs:
        return f"你想和谁{verb}？当前没有明确可见目标。"
    labels = "、".join(str(npc.get("name") or npc.get("entity_id")) for npc in npcs)
    return f"你想{verb}哪个目标？当前可见目标：{labels}。"


def is_reachable_location(state: Mapping[str, Any], location_id: str) -> bool:
    """
    功能：判断目标地点是否属于当前场景出口，用于阻止 NLU 生成越界移动。
    入参：state（dict[str, Any]）：当前回合状态；location_id（str）：候选目标地点。
    出参：bool，目标在出口列表中返回 True。
    异常：不抛异常；场景快照缺失时保守返回 False。
    """
    scene_snapshot = state.get("scene_snapshot")
    if not scene_snapshot:
        return False
    return any(exit_info["location_id"] == location_id for exit_info in scene_snapshot["exits"])


def _validate_action_type(action_type: Any) -> list[str]:
    """
    功能：校验动作类型是否属于当前工作流支持集合。
    入参：action_type（Any）：NLU 解析出的动作类型字段。
    出参：list[str]，非法时返回错误列表，合法时返回空列表。
    异常：不抛异常；仅做纯函数判定。
    """
    supported_actions = {
        "attack",
        "talk",
        "move",
        "observe",
        "wait",
        "rest",
        "inspect",
        "use_item",
        "interact",
        "commit_sandbox",
        "discard_sandbox",
    }
    if action_type in supported_actions:
        return []
    return ["动作类型暂不支持"]


def _validate_attack(loop: Any, action: dict[str, Any], errors: list[str]) -> None:
    """
    功能：执行攻击动作的目标存在性校验。
    入参：loop（Any）：MainEventLoop 实例；action（dict[str, Any]）：候选动作；
        errors（list[str]）：外部可变错误列表。
    出参：None，通过 errors 原地追加错误。
    异常：数据库只读探针异常向上抛出，不在此处吞掉。
    """
    if action.get("type") != "attack":
        return
    target = loop.entity_probes.get_character_stats(str(action["target_id"]))
    if target is None:
        errors.append("攻击目标不存在")


def _validate_move(
    loop: Any,
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    errors: list[str],
) -> dict[str, Any] | None:
    """
    功能：执行移动动作校验，目标缺失时返回澄清结果，越界目标追加错误。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：当前回合状态；
        action（dict[str, Any]）：候选动作；errors（list[str]）：外部错误列表。
    出参：dict[str, Any] | None，需澄清时返回主循环补丁，否则返回 None。
    异常：不抛业务异常；澄清路径走受控降级。
    """
    if action.get("type") != "move":
        return None
    location_id = action.get("parameters", {}).get("location_id")
    if not location_id or location_id == "unknown":
        return clarification_result(
            clarify_with_agent(loop, state, action, build_move_clarification(state))
        )
    if not is_reachable_location(state, str(location_id)):
        errors.append("目标地点不在当前场景出口中")
    return None


def _validate_sandbox_action(
    state: Mapping[str, Any], action_type: Any, errors: list[str]
) -> None:
    """
    功能：校验沙盒控制动作仅在沙盒模式下可执行。
    入参：state（dict[str, Any]）：当前回合状态；action_type（Any）：动作类型；
        errors（list[str]）：外部可变错误列表。
    出参：None，通过 errors 原地追加错误。
    异常：不抛异常；仅做状态判定。
    """
    if action_type in {"commit_sandbox", "discard_sandbox"} and not state.get("is_sandbox_mode"):
        errors.append("当前不在沙盒模式，无法执行沙盒控制动作")


def _validate_use_item(
    loop: Any,
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    active_character: Mapping[str, Any],
    errors: list[str],
) -> dict[str, Any] | None:
    """
    功能：执行使用物品动作校验，缺参数走澄清，库存/定义异常追加错误。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：当前回合状态；
        action（dict[str, Any]）：候选动作；active_character（dict[str, Any]）：当前角色；
        errors（list[str]）：外部可变错误列表。
    出参：dict[str, Any] | None，需澄清时返回主循环补丁，否则返回 None。
    异常：数据库只读探针异常向上抛出，不在此处吞掉。
    """
    if action.get("type") != "use_item":
        return None
    item_id = action.get("parameters", {}).get("item_id")
    if not item_id:
        return clarification_result(clarify_with_agent(loop, state, action, "你想使用哪个物品？"))
    inventory_item = loop.entity_probes.get_inventory_item(
        active_character["id"],
        str(item_id),
        use_shadow=state.get("is_sandbox_mode", False),
    )
    if inventory_item is None or int(inventory_item.get("quantity", 0)) <= 0:
        errors.append("背包中不存在该物品")
    elif loop.entity_probes.get_item_definition(str(item_id)) is None:
        errors.append("该物品缺少可用定义")
    return None


def validate_action_sync(loop: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    """
    功能：同步执行动作校验逻辑；所有候选动作必须在这里完成确定性合法性确认。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：候选动作与场景状态。
    出参：dict[str, Any]，包含 is_valid 与 validation_errors。
    异常：数据库只读探针异常向上抛出；校验失败通过 errors 返回，不抛业务异常。
    """
    action = state.get("action_intent")
    active_character = state.get("active_character")
    if not active_character:
        return invalid_result("当前角色不存在，无法执行动作")

    if not action:
        return clarification_result(
            clarify_with_agent(
                loop,
                state,
                None,
                "我还没有理解你的行动，你想观察、移动、交谈，还是休息？",
            )
        )

    if bool(action.get("needs_clarification", False)):
        question = str(action.get("clarification_question") or "").strip()
        return clarification_result(
            clarify_with_agent(loop, state, action, question or "你能再具体说明目标或方向吗？")
        )

    action_type = action.get("type")
    errors: list[str] = _validate_action_type(action_type)

    if action_type in {"attack", "talk"} and not action.get("target_id"):
        return clarification_result(
            clarify_with_agent(loop, state, action, build_target_clarification(state, action_type))
        )

    _validate_attack(loop, action, errors)
    move_result = _validate_move(loop, state, action, errors)
    if move_result is not None:
        return move_result
    _validate_sandbox_action(state, action_type, errors)
    use_item_result = _validate_use_item(loop, state, action, active_character, errors)
    if use_item_result is not None:
        return use_item_result

    if errors:
        return invalid_result("；".join(errors))
    return {
        "is_valid": True,
        "validation_errors": [],
        "turn_outcome": "valid_action",
        "clarification_question": "",
        "should_advance_turn": True,
        "should_write_story_memory": True,
        "debug_trace": [
            {"stage": "validate_action", "status": "valid", "action_type": str(action_type)}
        ],
    }
