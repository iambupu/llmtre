"""
MainEventLoop 动作结算辅助函数。
"""

from __future__ import annotations

import logging
from typing import Any, cast

from tools.roll.dice_roller import check_success, roll_d20, roll_dice

logger = logging.getLogger("Workflow.MainLoop")


def resolve_action_sync(loop: Any, state: dict[str, Any]) -> dict[str, Any]:
    """
    功能：同步执行动作结算逻辑；数值变化仅来自确定性规则和骰子工具。
    入参：loop（Any）：MainEventLoop 实例；state（dict[str, Any]）：已通过校验的动作状态。
    出参：dict[str, Any]，包含 physics_diff，供写计划消费。
    异常：事件总线钩子或只读查询异常向上抛出，由主循环调用方处理。
    """
    hooked_state = loop.event_bus.emit("on_action_pre", dict(state))
    action = cast(dict[str, Any], hooked_state["action_intent"])
    action_type = action["type"]

    physics_diff: dict[str, Any] = {}
    if action_type == "attack":
        attacker: dict[str, Any] = dict(state.get("active_character") or {})
        target = loop.entity_probes.get_character_stats(str(action["target_id"]))
        rng = loop._build_action_rng(state)
        attack_rules = loop.rules.get("resolution", {}).get("attack", {})
        attacker_strength = loop._to_int(attacker.get("strength", 10), 10)
        target_agility = loop._to_int(target.get("agility", 10), 10) if target else 10
        attack_roll = roll_d20(modifier=attacker_strength, rng=rng)
        base_dc = loop._to_int(attack_rules.get("base_dc", 10), 10)
        agility_divisor = max(1, loop._to_int(attack_rules.get("agility_divisor", 2), 2))
        attack_dc = base_dc + target_agility // agility_divisor
        attack_hit = check_success(attack_roll, attack_dc)
        physics_diff = {
            "attack_roll": attack_roll,
            "attack_dc": attack_dc,
            "attack_hit": attack_hit,
        }
        if attack_hit:
            damage_dice = str(attack_rules.get("damage_dice", "d6"))
            damage_roll = roll_dice(damage_dice, rng=rng)[0]
            strength_divisor = max(
                1,
                loop._to_int(attack_rules.get("strength_damage_divisor", 3), 3),
            )
            min_damage = max(1, loop._to_int(attack_rules.get("min_damage", 1), 1))
            damage = max(min_damage, damage_roll + attacker_strength // strength_divisor)
            physics_diff["damage_roll"] = damage_roll
            physics_diff["target_hp_delta"] = -damage
    elif action_type == "move":
        location_id = action.get("parameters", {}).get("location_id", "unknown")
        physics_diff = loop._resolve_configured_action("move", {"location_id": location_id})
    elif action_type == "use_item":
        item_id = action.get("parameters", {}).get("item_id")
        item_definition = loop.entity_probes.get_item_definition(str(item_id))
        if item_definition:
            for effect in item_definition.get("effects", []):
                if not isinstance(effect, dict):
                    continue
                target_attribute = effect.get("target_attribute")
                value = loop._to_int(effect.get("value", 0))
                if target_attribute == "hp":
                    physics_diff["hp_delta"] = physics_diff.get("hp_delta", 0) + value
                elif target_attribute == "mp":
                    physics_diff["mp_delta"] = physics_diff.get("mp_delta", 0) + value
            physics_diff["consumed_item_id"] = str(item_id)
    elif action_type == "talk":
        physics_diff = loop._resolve_configured_action("talk", {})
    elif action_type in {"observe", "wait", "rest", "inspect", "interact"}:
        physics_diff = loop._resolve_configured_action(action_type, {})
    elif action_type in {"commit_sandbox", "discard_sandbox"}:
        physics_diff = loop._resolve_configured_action(action_type, {})

    logger.info("物理结算完成，结果: %s", physics_diff)
    return {"physics_diff": physics_diff}
