"""
MainEventLoop 场景与角色快照辅助函数。
"""

from __future__ import annotations

from typing import Any, cast

from game_workflows.affordances import build_scene_interaction_model
from game_workflows.graph_schema import CharacterState, SceneExitState, SceneSnapshot


def normalize_scene_exits(raw_exits: Any) -> list[SceneExitState]:
    """
    功能：把配置或数据库中的出口定义收敛为稳定结构。
    入参：raw_exits（Any）：可能为列表或其他值。
    出参：list[SceneExitState]，每项包含 direction、location_id、label、aliases。
    异常：不抛异常；非法项会被跳过。
    """
    if not isinstance(raw_exits, list):
        return []
    exits: list[SceneExitState] = []
    for raw_exit in raw_exits:
        if not isinstance(raw_exit, dict):
            continue
        location_id = raw_exit.get("location_id")
        if not isinstance(location_id, str) or not location_id:
            continue
        aliases = raw_exit.get("aliases", [])
        exits.append(
            {
                "direction": str(raw_exit.get("direction", "")),
                "location_id": location_id,
                "label": str(raw_exit.get("label", location_id)),
                "aliases": (
                    [str(alias) for alias in aliases if isinstance(alias, str)]
                    if isinstance(aliases, list)
                    else []
                ),
            }
        )
    return exits


def normalize_dict_list(value: Any) -> list[dict[str, Any]]:
    """
    功能：过滤配置中的对象列表，避免 Web 响应暴露非对象脏数据。
    入参：value（Any）：待过滤值。
    出参：list[dict[str, Any]]，仅保留 dict 项。
    异常：不抛异常；非列表输入返回空列表。
    """
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def build_character_state(
    entity_probes: Any,
    entity_id: str,
    use_shadow: bool = False,
) -> CharacterState | None:
    """
    功能：从只读探针构建角色快照，供主循环初始态和回合结束刷新复用。
    入参：entity_probes（Any）：实体探针实例；entity_id（str）：角色 ID；
        use_shadow（bool，默认 False）：是否从 Shadow 读取。
    出参：CharacterState | None，角色不存在时返回 None。
    异常：只读数据库异常向上抛出，交由主循环调用方处理。
    """
    snapshot = entity_probes.get_character_stats(entity_id, use_shadow=use_shadow)
    if snapshot is None:
        return None
    inventory_rows = entity_probes.check_inventory(entity_id, use_shadow=use_shadow)
    return {
        "id": snapshot["entity_id"],
        "name": snapshot["name"],
        "hp": snapshot["hp"],
        "max_hp": snapshot["max_hp"],
        "mp": snapshot["mp"],
        "max_mp": snapshot["max_mp"],
        "inventory": [row["item_id"] for row in inventory_rows],
        "location": snapshot.get("current_location_id") or "unknown",
    }


def build_scene_snapshot(
    entity_probes: Any,
    rules: dict[str, Any],
    active_character: CharacterState | None,
    recent_memory: str = "",
    use_shadow: bool = False,
) -> SceneSnapshot | None:
    """
    功能：构造当前回合场景快照，供 NLU、校验、叙事和 Web 可操作提示共享。
    入参：entity_probes（Any）：实体探针实例；rules（dict[str, Any]）：主循环规则配置；
        active_character（CharacterState | None）：当前角色快照；
        recent_memory（str，默认空）：会话记忆摘要；
        use_shadow（bool，默认 False）：是否读取 Shadow 世界状态。
    出参：SceneSnapshot | None，角色缺失时返回 None。
    异常：只读数据库异常向上抛出；地点定义缺失时使用配置降级场景，不中断回合。
    """
    if active_character is None:
        return None
    location_id = active_character.get("location", "unknown")
    location_info = entity_probes.get_location_info(location_id, use_shadow=use_shadow)
    scene_defaults = rules.get("scene_defaults", {})
    fallback_locations = scene_defaults.get("locations", {})
    if location_info is None and isinstance(fallback_locations, dict):
        fallback = fallback_locations.get(location_id) or fallback_locations.get("unknown") or {}
        location_info = dict(fallback) if isinstance(fallback, dict) else {}
    location_info = location_info or {"id": location_id, "name": location_id, "description": ""}

    exits = normalize_scene_exits(location_info.get("exits", []))
    nearby_entities = entity_probes.list_nearby_entities(location_id, use_shadow=use_shadow)
    visible_npcs = [
        entity for entity in nearby_entities if entity.get("entity_id") != active_character["id"]
    ]
    available_actions = scene_defaults.get("available_actions", [])
    suggested_actions = scene_defaults.get("suggested_actions", [])
    snapshot: dict[str, Any] = {
        "schema_version": "scene_snapshot.v2",
        "current_location": {
            "id": str(location_info.get("id", location_id)),
            "name": str(location_info.get("name", location_id)),
            "description": str(location_info.get("description", "")),
        },
        "exits": exits,
        "visible_npcs": visible_npcs,
        "visible_items": normalize_dict_list(location_info.get("visible_items", [])),
        "active_quests": normalize_dict_list(location_info.get("active_quests", [])),
        "recent_memory": recent_memory,
        "available_actions": [
            str(action) for action in available_actions if isinstance(action, str)
        ],
        "suggested_actions": [
            str(action) for action in suggested_actions if isinstance(action, str)
        ],
    }
    # A2 场景可操作化预留：从现有出口/NPC/物品投影对象与交互槽，
    # A1 仍通过 quick_actions 兜底，避免前端必须一次性切到对象化交互。
    snapshot.update(build_scene_interaction_model(snapshot))
    return cast(SceneSnapshot, snapshot)
