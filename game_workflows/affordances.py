"""
场景对象、交互槽与行动候选构建器。
"""

from __future__ import annotations

from typing import Any

from game_workflows.action_text import build_move_action_text
from state.contracts.scene import InteractionSlot, SceneAffordance, SceneObjectRef


def build_scene_interaction_model(
    scene_snapshot: dict[str, Any],
    active_character: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    功能：从现有场景快照投影 A2 预留的对象、交互槽和 A1 affordance。
    入参：scene_snapshot（dict[str, Any]）：包含 exits、visible_npcs、visible_items 等字段；
        active_character（dict[str, Any] | None）：当前角色快照，可用于背包物品交互。
    出参：dict[str, Any]，包含 scene_objects、interaction_slots、affordances、ui_hints。
    异常：不抛异常；缺失或脏字段按空列表降级，保证主循环可继续执行。
    """
    objects, slots, location_id = _build_location_entries(scene_snapshot)
    _append_exit_entries(scene_snapshot, objects, slots)
    _append_interactable_entries(scene_snapshot, objects, slots)
    _append_visible_npc_entries(scene_snapshot, objects, slots)
    _append_visible_item_entries(scene_snapshot, objects, slots)
    _append_inventory_item_entries(active_character, objects, slots)
    slots.extend(_build_baseline_slots(location_id))
    affordances = _build_affordances(slots, objects)
    return _build_scene_interaction_payload(objects, slots, affordances)


def _build_location_entries(
    scene_snapshot: dict[str, Any],
) -> tuple[list[SceneObjectRef], list[InteractionSlot], str]:
    """
    功能：从 current_location 构造地点对象与“检查这里”交互槽。
    入参：scene_snapshot（dict[str, Any]）：场景快照。
    出参：tuple[list[SceneObjectRef], list[InteractionSlot], str]，对象、槽与 location_id。
    异常：不抛异常；current_location 非 dict 时降级为 unknown。
    """
    objects: list[SceneObjectRef] = []
    slots: list[InteractionSlot] = []
    current_location = _as_mapping(scene_snapshot.get("current_location"))
    location_id = str(current_location.get("id") or "unknown")
    location_name = str(current_location.get("name") or location_id)
    objects.append(
        SceneObjectRef(
            object_id=f"location:{location_id}",
            object_type="location",
            label=location_name,
            description=str(current_location.get("description") or ""),
            source_ref=current_location,
            priority=10,
        )
    )
    slots.append(
        InteractionSlot(
            slot_id=f"slot:inspect:location:{location_id}",
            object_id=f"location:{location_id}",
            action_type="inspect",
            label="检查这里",
            enabled=True,
            default_input="检查周围",
        )
    )
    return objects, slots, location_id


def _append_exit_entries(
    scene_snapshot: dict[str, Any],
    objects: list[SceneObjectRef],
    slots: list[InteractionSlot],
) -> None:
    """
    功能：把场景 exits 投影为出口对象与 move 交互槽；未解锁出口保留对象展示但禁用动作槽。
    入参：scene_snapshot（dict[str, Any]）：场景快照；objects/slots：待追加的输出列表。
    出参：None，原地追加对象与交互槽。
    异常：不抛异常；缺少 location_id 的出口会被跳过，缺少 enabled 时按可通行降级。
    """
    for index, exit_info in enumerate(_as_list(scene_snapshot.get("exits"))):
        exit_map = _as_mapping(exit_info)
        target_location_id = str(exit_map.get("location_id") or "")
        if not target_location_id:
            continue
        label = str(exit_map.get("label") or target_location_id)
        enabled = exit_map.get("enabled", True) is not False
        disabled_reason = "" if enabled else str(exit_map.get("disabled_reason") or "")
        object_id = f"exit:{target_location_id}"
        move_text = build_move_action_text(label)
        objects.append(
            SceneObjectRef(
                object_id=object_id,
                object_type="exit",
                label=label,
                description=str(exit_map.get("direction") or ""),
                state_tags=["reachable"] if enabled else ["locked"],
                source_ref=exit_map,
                priority=20 + index,
            )
        )
        slots.append(
            InteractionSlot(
                slot_id=f"slot:move:{target_location_id}",
                object_id=object_id,
                action_type="move",
                label=move_text,
                enabled=enabled,
                disabled_reason=disabled_reason,
                default_input=move_text,
            )
        )


def _append_interactable_entries(
    scene_snapshot: dict[str, Any],
    objects: list[SceneObjectRef],
    slots: list[InteractionSlot],
) -> None:
    """
    功能：把 Story Pack interactables 投影为 interaction 对象与对应动作槽。
    入参：scene_snapshot（dict[str, Any]）：场景快照；objects/slots：待追加的输出列表。
    出参：None，原地追加对象与交互槽。
    异常：不抛异常；缺少 interaction_id 的条目会被跳过。
    """
    for index, interaction_info in enumerate(_as_list(scene_snapshot.get("interactables"))):
        interaction_map = _as_mapping(interaction_info)
        interaction_id = str(interaction_map.get("interaction_id") or "")
        if not interaction_id:
            continue
        label = str(interaction_map.get("label") or interaction_id)
        action_type = str(interaction_map.get("kind") or "interact")
        object_id = f"interaction:{interaction_id}"
        objects.append(
            SceneObjectRef(
                object_id=object_id,
                object_type="interaction",
                label=label,
                description=str(interaction_map.get("target_ref") or ""),
                source_ref=interaction_map,
                priority=30 + index,
            )
        )
        slots.append(
            InteractionSlot(
                slot_id=f"slot:{action_type}:interaction:{interaction_id}",
                object_id=object_id,
                action_type=action_type,
                label=label,
                enabled=True,
                default_input=label,
            )
        )


def _append_visible_npc_entries(
    scene_snapshot: dict[str, Any],
    objects: list[SceneObjectRef],
    slots: list[InteractionSlot],
) -> None:
    """
    功能：把 visible_npcs 投影为 NPC 对象，并生成 talk/attack 两类交互槽。
    入参：scene_snapshot（dict[str, Any]）：场景快照；objects/slots：待追加的输出列表。
    出参：None，原地追加对象与交互槽。
    异常：不抛异常；缺少 entity_id/id 的 NPC 会被跳过。
    """
    for index, npc_info in enumerate(_as_list(scene_snapshot.get("visible_npcs"))):
        npc_map = _as_mapping(npc_info)
        entity_id = str(npc_map.get("entity_id") or npc_map.get("id") or "")
        if not entity_id:
            continue
        label = str(npc_map.get("name") or entity_id)
        object_id = f"npc:{entity_id}"
        objects.append(
            SceneObjectRef(
                object_id=object_id,
                object_type="npc",
                label=label,
                description=str(npc_map.get("description") or ""),
                source_ref=npc_map,
                priority=40 + index,
            )
        )
        slots.extend(
            [
                InteractionSlot(
                    slot_id=f"slot:talk:{entity_id}",
                    object_id=object_id,
                    action_type="talk",
                    label=f"询问{label}",
                    enabled=True,
                    default_input=f"和{label}交谈",
                ),
                InteractionSlot(
                    slot_id=f"slot:attack:{entity_id}",
                    object_id=object_id,
                    action_type="attack",
                    label=f"攻击{label}",
                    enabled=True,
                    default_input=f"攻击{label}",
                ),
            ]
        )


def _append_visible_item_entries(
    scene_snapshot: dict[str, Any],
    objects: list[SceneObjectRef],
    slots: list[InteractionSlot],
) -> None:
    """
    功能：把 visible_items 投影为物品对象与 inspect/interact 交互槽。
    入参：scene_snapshot（dict[str, Any]）：场景快照；objects/slots：待追加的输出列表。
    出参：None，原地追加对象与交互槽。
    异常：不抛异常；缺少 item_id/id 的物品会被跳过。
    """
    for index, item_info in enumerate(_as_list(scene_snapshot.get("visible_items"))):
        item_map = _as_mapping(item_info)
        item_id = str(item_map.get("item_id") or item_map.get("id") or "")
        if not item_id:
            continue
        label = str(item_map.get("name") or item_id)
        object_id = f"item:{item_id}"
        objects.append(
            SceneObjectRef(
                object_id=object_id,
                object_type="item",
                label=label,
                description=str(item_map.get("description") or ""),
                source_ref=item_map,
                priority=60 + index,
            )
        )
        slots.append(
            InteractionSlot(
                slot_id=f"slot:interact:item:{item_id}",
                object_id=object_id,
                action_type="interact",
                label=f"检查{label}",
                enabled=True,
                default_input=f"检查{label}",
            )
        )


def _append_inventory_item_entries(
    active_character: dict[str, Any] | None,
    objects: list[SceneObjectRef],
    slots: list[InteractionSlot],
) -> None:
    """
    功能：把当前角色背包物品投影为 inventory 对象与 use_item 交互槽。
    入参：active_character（dict[str, Any] | None）：角色快照；objects/slots：待追加列表。
    出参：None，原地追加对象与交互槽。
    异常：不抛异常；背包字段脏数据或缺 item_id 的条目会被跳过。
    """
    character = active_character or {}
    inventory_items = _as_list(character.get("inventory_items"))
    for index, item_info in enumerate(inventory_items):
        item_map = _as_mapping(item_info)
        item_id = str(item_map.get("item_id") or "")
        if not item_id:
            continue
        label = str(item_map.get("name") or item_id)
        object_id = f"inventory:{item_id}"
        objects.append(
            SceneObjectRef(
                object_id=object_id,
                object_type="item",
                label=label,
                description=str(item_map.get("description") or ""),
                state_tags=["inventory"],
                source_ref=item_map,
                priority=80 + index,
            )
        )
        slots.append(
            InteractionSlot(
                slot_id=f"slot:use_item:{item_id}",
                object_id=object_id,
                action_type="use_item",
                label=f"使用{label}",
                enabled=True,
                default_input=f"使用{label}",
            )
        )


def _build_scene_interaction_payload(
    objects: list[SceneObjectRef],
    slots: list[InteractionSlot],
    affordances: list[SceneAffordance],
) -> dict[str, Any]:
    """
    功能：把对象、交互槽与 affordance 模型转换为前端/API 使用的字典 payload。
    入参：objects（list[SceneObjectRef]）：场景对象；slots（list[InteractionSlot]）：交互槽；
        affordances（list[SceneAffordance]）：可执行动作候选。
    出参：dict[str, Any]，包含 scene_objects、interaction_slots、affordances、ui_hints。
    异常：不抛业务异常；model_dump 异常表示契约对象构造已失败并向上暴露。
    """
    return {
        "scene_objects": [item.model_dump() for item in objects],
        "interaction_slots": [item.model_dump() for item in slots],
        "affordances": [item.model_dump() for item in affordances],
        "ui_hints": {
            "layout": "object_list",
            "quick_action_limit": 4,
            "primary_object_ids": [item.object_id for item in objects[:6]],
        },
    }


def _build_baseline_slots(location_id: str) -> list[InteractionSlot]:
    """
    功能：生成不依赖具体对象的保底交互槽，保证 LLM 关闭时仍有可点击动作。
    入参：location_id（str）：当前地点 ID。
    出参：list[InteractionSlot]。
    异常：不抛异常。
    """
    object_id = f"location:{location_id}"
    return [
        InteractionSlot(
            slot_id=f"slot:observe:{location_id}",
            object_id=object_id,
            action_type="observe",
            label="观察周围",
            enabled=True,
            default_input="观察周围",
        ),
        InteractionSlot(
            slot_id=f"slot:wait:{location_id}",
            object_id=object_id,
            action_type="wait",
            label="等待片刻",
            enabled=True,
            default_input="等待片刻",
        ),
        InteractionSlot(
            slot_id=f"slot:rest:{location_id}",
            object_id=object_id,
            action_type="rest",
            label="短暂休息",
            enabled=True,
            default_input="短暂休息",
        ),
    ]


def _build_affordances(
    slots: list[InteractionSlot],
    objects: list[SceneObjectRef],
) -> list[SceneAffordance]:
    """
    功能：从交互槽构造可执行行动候选，保留对象来源方便 A2 前端追溯。
    入参：slots（list[InteractionSlot]）：交互槽；objects（list[SceneObjectRef]）：对象列表。
    出参：list[SceneAffordance]，按优先级排序。
    异常：不抛异常。
    """
    object_by_id = {item.object_id: item for item in objects}
    affordances: list[SceneAffordance] = []
    seen_inputs: set[str] = set()
    for index, slot in enumerate(slots):
        if slot.default_input in seen_inputs:
            continue
        seen_inputs.add(slot.default_input)
        source_object = object_by_id.get(slot.object_id)
        source_ref = source_object.source_ref if source_object else {}
        # 意图：interaction 来源的按钮也必须可直接提交；target_ref 是 Story Pack
        # 对交互目标的权威引用，缺失时才回退到实体/物品字段。
        target_id = (
            source_ref.get("entity_id") or source_ref.get("item_id") or source_ref.get("target_ref")
        )
        location_id = source_ref.get("location_id")
        affordances.append(
            SceneAffordance(
                id=f"aff:{slot.slot_id}",
                label=slot.label,
                action_type=slot.action_type,
                enabled=slot.enabled,
                reason=slot.disabled_reason,
                user_input=slot.default_input,
                target_id=str(target_id) if target_id else None,
                location_id=str(location_id) if location_id else None,
                object_id=slot.object_id,
                slot_id=slot.slot_id,
                priority=index,
            )
        )
    return sorted(affordances, key=lambda item: item.priority)


def _as_mapping(value: Any) -> dict[str, Any]:
    """
    功能：把未知值收敛为字典。
    入参：value（Any）：待处理值。
    出参：dict[str, Any]。
    异常：不抛异常，非 dict 返回空字典。
    """
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """
    功能：把未知值收敛为列表。
    入参：value（Any）：待处理值。
    出参：list[Any]。
    异常：不抛异常，非 list 返回空列表。
    """
    return value if isinstance(value, list) else []
