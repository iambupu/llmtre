"""
A2-Plus 剧本包触发器效果执行器。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from state.contracts.quest import QuestRuntimeState
from state.contracts.trigger import TriggerDef
from tools.packs.quest_runtime import VALID_QUEST_STATUSES, apply_trigger_update

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerEffectApplyResult:
    """
    功能：描述一次触发器效果执行后的变更摘要。
    入参：changed_quest_ids（set[str]）：本次被 update_quest effect 改动的任务 ID 集合。
    出参：TriggerEffectApplyResult。
    异常：dataclass 构造不抛业务异常。
    """

    changed_quest_ids: set[str]


class TriggerEffectApplier:
    """
    功能：执行 TriggerDef.effects 中的确定性效果，写入 physics_diff 与任务运行态。
    入参：pack_triggers（dict[str, Any]）：按 trigger_id 索引的触发器定义。
    出参：TriggerEffectApplier。
    异常：初始化阶段会跳过无法解析的触发器并记录日志，不抛业务异常。
    """

    def __init__(self, pack_triggers: dict[str, Any]) -> None:
        """
        功能：解析触发器定义索引，供后续按事件 trigger_id 查找完整 effect 定义。
        入参：pack_triggers（dict[str, Any]）：可能包含 TriggerDef 或 dict payload。
        出参：None。
        异常：单个触发器解析失败时记录日志并跳过。
        """
        self._trigger_defs = _trigger_defs_by_id(pack_triggers)

    def apply(
        self,
        *,
        trigger_events: list[dict[str, Any]],
        physics_diff: dict[str, Any],
        quest_states: list[QuestRuntimeState],
        active_character_id: str,
    ) -> TriggerEffectApplyResult:
        """
        功能：按触发事件列表执行对应触发器的非叙事效果。
        入参：trigger_events（list[dict[str, Any]]）：本轮已触发事件；
            physics_diff（dict[str, Any]）：主循环确定性状态差异，函数会原地追加效果；
            quest_states（list[QuestRuntimeState]）：任务运行态列表；
            update_quest 会原地替换目标状态；
            active_character_id（str）：当前角色 ID，用作默认物品/移动效果目标。
        出参：TriggerEffectApplyResult，包含被修改的任务 ID 集合。
        异常：单个非法 effect 记录日志并跳过；函数不抛业务异常。
        """
        changed_quest_ids: set[str] = set()
        for event in trigger_events:
            trigger_id = str(event.get("trigger_id") or "")
            trigger_def = self._trigger_defs.get(trigger_id)
            if trigger_def is None:
                continue
            for effect in trigger_def.effects:
                if effect == "narrative":
                    continue
                if effect == "grant_item":
                    self._apply_grant_item(
                        trigger_def.conditions, physics_diff, active_character_id
                    )
                elif effect == "set_flag":
                    self._apply_set_flag(trigger_def.conditions, physics_diff)
                elif effect == "update_quest":
                    changed = self._apply_update_quest(trigger_def, quest_states)
                    if changed:
                        changed_quest_ids.add(changed)
                elif effect == "move_entity":
                    self._apply_move_entity(
                        trigger_def.conditions,
                        physics_diff,
                        active_character_id,
                    )
        return TriggerEffectApplyResult(changed_quest_ids=changed_quest_ids)

    def _apply_grant_item(
        self,
        conditions: Mapping[str, Any],
        physics_diff: dict[str, Any],
        active_character_id: str,
    ) -> None:
        """
        功能：执行 grant_item effect，把待授予物品写入 physics_diff.granted_items。
        入参：conditions（Mapping[str, Any]）：触发器条件/效果参数；
            physics_diff（dict[str, Any]）：待追加的确定性差异；
            active_character_id（str）：默认 owner_id。
        出参：None。
        异常：数量解析失败时降级为 1，不向上抛出。
        """
        item_id = _string_from_conditions(conditions, "item_id", "grant_item_id")
        if not item_id:
            return
        quantity = conditions.get("quantity", 1)
        try:
            qty = int(quantity)
        except TypeError, ValueError:
            qty = 1
        owner_id = _string_from_conditions(conditions, "owner_id") or active_character_id
        _append_granted_item(physics_diff, item_id, owner_id, max(1, qty))

    def _apply_set_flag(
        self,
        conditions: Mapping[str, Any],
        physics_diff: dict[str, Any],
    ) -> None:
        """
        功能：执行 set_flag effect，把状态标签追加到 physics_diff.state_flags_add。
        入参：conditions（Mapping[str, Any]）：触发器条件/效果参数；
            physics_diff（dict[str, Any]）：待追加的确定性差异。
        出参：None。
        异常：不抛异常；非字符串 flag 会被忽略。
        """
        flags = physics_diff.setdefault("state_flags_add", [])
        if not isinstance(flags, list):
            physics_diff["state_flags_add"] = flags = []
        _append_unique_string(flags, conditions.get("flag"))
        _append_unique_string(flags, conditions.get("state_flag"))
        raw_flags = conditions.get("state_flags_add")
        if isinstance(raw_flags, list):
            for flag in raw_flags:
                _append_unique_string(flags, flag)

    def _apply_update_quest(
        self,
        trigger_def: TriggerDef,
        quest_states: list[QuestRuntimeState],
    ) -> str | None:
        """
        功能：执行 update_quest effect，按 quest_id 找到运行态并委托任务状态机更新。
        入参：trigger_def（TriggerDef）：携带 effect 条件的触发器定义；
            quest_states（list[QuestRuntimeState]）：待原地更新的任务状态列表。
        出参：str | None，成功更新返回 quest_id；缺失或非法时返回 None。
        异常：状态机异常内部记录并跳过，不向上抛出。
        """
        conditions = trigger_def.conditions
        quest_id = _string_from_conditions(conditions, "quest_id")
        if not quest_id:
            return None
        current_index = next(
            (index for index, state in enumerate(quest_states) if state.quest_id == quest_id),
            None,
        )
        if current_index is None:
            logger.warning("update_quest effect 引用了未知任务: %s", quest_id)
            return None
        next_status = _string_from_conditions(conditions, "quest_status", "status")
        next_stage = _string_from_conditions(
            conditions,
            "target_stage_id",
            "next_stage_id",
            "current_stage_id",
        )
        if not next_status and not next_stage:
            next_status = "active"
        if next_status and next_status not in VALID_QUEST_STATUSES:
            logger.warning("update_quest effect 状态非法: %s", next_status)
            return None
        try:
            quest_states[current_index] = apply_trigger_update(
                quest_states[current_index],
                next_status=next_status,
                next_stage_id=next_stage,
            )
        except Exception as exc:
            logger.warning("update_quest effect 执行失败: %s", exc)
            return None
        return quest_id

    def _apply_move_entity(
        self,
        conditions: Mapping[str, Any],
        physics_diff: dict[str, Any],
        active_character_id: str,
    ) -> None:
        """
        功能：执行 move_entity effect，为当前角色或其他实体追加位置差异。
        入参：conditions（Mapping[str, Any]）：触发器条件/效果参数；
            physics_diff（dict[str, Any]）：待追加的确定性差异；
            active_character_id（str）：当前角色 ID。
        出参：None。
        异常：不抛异常；缺少 location_id 时跳过。
        """
        entity_id = _string_from_conditions(conditions, "entity_id") or active_character_id
        location_id = _string_from_conditions(
            conditions,
            "location_id",
            "target_location_id",
            "target_scene_id",
        )
        if location_id and entity_id == active_character_id:
            physics_diff["location_id"] = location_id
        elif location_id:
            _append_entity_diff(physics_diff, entity_id, {"location_id": location_id})


def apply_trigger_effects(
    *,
    trigger_events: list[dict[str, Any]],
    pack_triggers: dict[str, Any],
    physics_diff: dict[str, Any],
    quest_states: list[QuestRuntimeState],
    active_character_id: str,
) -> TriggerEffectApplyResult:
    """
    功能：便捷执行入口，创建 TriggerEffectApplier 并执行本轮触发效果。
    入参：trigger_events/pack_triggers/physics_diff/quest_states/active_character_id：
        与 TriggerEffectApplier.apply 相同。
    出参：TriggerEffectApplyResult。
    异常：不抛业务异常；非法单项由执行器记录并跳过。
    """
    return TriggerEffectApplier(pack_triggers).apply(
        trigger_events=trigger_events,
        physics_diff=physics_diff,
        quest_states=quest_states,
        active_character_id=active_character_id,
    )


def _trigger_defs_by_id(pack_triggers: dict[str, Any]) -> dict[str, TriggerDef]:
    """
    功能：把触发器原始索引规整为 TriggerDef 索引。
    入参：pack_triggers（dict[str, Any]）：TriggerDef 或 dict payload。
    出参：dict[str, TriggerDef]，key 为 trigger_id。
    异常：单项解析失败记录日志并跳过。
    """
    trigger_defs: dict[str, TriggerDef] = {}
    for raw in pack_triggers.values():
        try:
            if isinstance(raw, TriggerDef):
                trigger_def = raw
            elif isinstance(raw, Mapping):
                trigger_def = TriggerDef.model_validate(dict(raw))
            else:
                continue
        except Exception as exc:
            logger.warning("触发器定义反序列化失败: %s", exc)
            continue
        trigger_defs[trigger_def.trigger_id] = trigger_def
    return trigger_defs


def _string_from_conditions(conditions: Mapping[str, Any], *keys: str) -> str:
    """
    功能：从触发器条件中按候选 key 读取第一个非空字符串。
    入参：conditions（Mapping[str, Any]）：触发器条件；keys（str）：候选字段名。
    出参：str，未找到时返回空字符串。
    异常：不抛异常；非字符串值会被忽略。
    """
    for key in keys:
        value = conditions.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _append_unique_string(target: list[Any], value: Any) -> None:
    """
    功能：向列表追加非空且未存在的字符串。
    入参：target（list[Any]）：待修改列表；value（Any）：候选值。
    出参：None。
    异常：不抛异常；非字符串和空字符串会被忽略。
    """
    if not isinstance(value, str):
        return
    normalized = value.strip()
    if normalized and normalized not in target:
        target.append(normalized)


def _append_granted_item(
    physics_diff: dict[str, Any],
    item_id: str,
    owner_id: str,
    quantity: int,
) -> None:
    """
    功能：把授予物品追加或合并到 physics_diff.granted_items。
    入参：physics_diff（dict[str, Any]）：待修改差异；item_id（str）：物品 ID；
        owner_id（str）：归属实体 ID；quantity（int）：授予数量。
    出参：None。
    异常：不抛异常；已有非列表字段会被重置为列表。
    """
    granted_items = physics_diff.setdefault("granted_items", [])
    if not isinstance(granted_items, list):
        physics_diff["granted_items"] = granted_items = []
    for item in granted_items:
        if not isinstance(item, dict):
            continue
        if item.get("item_id") == item_id and item.get("owner_id") == owner_id:
            item["quantity"] = int(item.get("quantity", 1)) + quantity
            return
    granted_items.append({"item_id": item_id, "owner_id": owner_id, "quantity": quantity})


def _append_entity_diff(
    physics_diff: dict[str, Any],
    entity_id: str,
    entity_diff: dict[str, Any],
) -> None:
    """
    功能：把非当前角色实体差异追加到 physics_diff.entity_diffs。
    入参：physics_diff（dict[str, Any]）：待修改差异；entity_id（str）：实体 ID；
        entity_diff（dict[str, Any]）：实体差异。
    出参：None。
    异常：不抛异常；已有非列表字段会被重置为列表。
    """
    entity_diffs = physics_diff.setdefault("entity_diffs", [])
    if not isinstance(entity_diffs, list):
        physics_diff["entity_diffs"] = entity_diffs = []
    entity_diffs.append({"entity_id": entity_id, "diff": entity_diff})
