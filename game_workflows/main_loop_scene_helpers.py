"""
MainEventLoop 场景与角色快照辅助函数。
"""

from __future__ import annotations

import json
from typing import Any, cast

from game_workflows.action_text import build_move_action_text
from game_workflows.affordances import build_scene_interaction_model
from game_workflows.graph_schema import (
    CharacterState,
    SceneExitState,
    SceneSnapshot,
    StatusContextState,
    StatusEffectState,
)
from state.contracts.story_pack import StoryPackSceneDef, StoryPackVisibleRefDef


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


def _pack_visible_ref_to_snapshot(
    value: str | StoryPackVisibleRefDef,
    id_key: str,
    kind: str,
) -> dict[str, Any]:
    """
    功能：把 Story Pack 可见对象引用转换为 scene_snapshot 可展示对象。
    入参：value（str | StoryPackVisibleRefDef）：旧字符串 ID 或新对象引用；
        id_key（str）：输出 ID 字段名，如 entity_id/item_id；kind（str）：对象类别。
    出参：dict[str, Any]，包含 ID、label/name、description、aliases 和 asset 引用。
    异常：不抛异常；字符串引用按 ID 降级，复杂引用缺失字段时省略。
    """
    if isinstance(value, str):
        return {id_key: value, "label": value, "kind": kind}
    payload: dict[str, Any] = {
        id_key: value.id,
        "id": value.id,
        "label": value.label or value.id,
        "kind": kind,
        "aliases": list(value.aliases),
    }
    if value.label:
        payload["name"] = value.label
    if value.description:
        payload["description"] = value.description
    for field_name in ("asset_id", "portrait_asset_id", "icon_asset_id", "image_asset_id"):
        asset_id = getattr(value, field_name)
        if asset_id:
            payload[field_name] = asset_id
    return payload


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


def _to_int(value: Any, default: int = 0) -> int:
    """
    功能：将数据库或配置值收敛为整数，供状态阈值计算复用。
    入参：value（Any）：待转换值；default（int，默认 0）：转换失败时使用的兜底值。
    出参：int，成功转换后的整数。
    异常：内部捕获 TypeError/ValueError，避免脏数据中断只读快照构建。
    """
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _to_ratio(value: Any, default: float) -> float:
    """
    功能：将规则配置中的比例阈值规整到 0..1 区间。
    入参：value（Any）：配置值；default（float）：缺失或非法时的兜底比例。
    出参：float，已裁剪到 0..1 的比例。
    异常：内部捕获 TypeError/ValueError；非法配置降级为 default。
    """
    try:
        ratio = float(value)
    except TypeError, ValueError:
        ratio = default
    return max(0.0, min(1.0, ratio))


def parse_state_flags(raw_flags: Any) -> list[str]:
    """
    功能：解析 entities_active/entities_shadow.state_flags_json，得到稳定去重的状态标签列表。
    入参：raw_flags（Any）：SQLite 字段原值，期望为 JSON 数组字符串，也兼容列表输入。
    出参：list[str]，按原顺序去重后的非空字符串标签。
    异常：JSON 解析失败内部降级为空列表，不阻断主循环只读快照。
    """
    loaded: Any
    if isinstance(raw_flags, str):
        if not raw_flags.strip():
            return []
        try:
            loaded = json.loads(raw_flags)
        except json.JSONDecodeError:
            return []
    else:
        loaded = raw_flags
    if not isinstance(loaded, list):
        return []
    flags: list[str] = []
    seen: set[str] = set()
    for item in loaded:
        flag = str(item).strip() if isinstance(item, str) else ""
        if not flag or flag in seen:
            continue
        seen.add(flag)
        flags.append(flag)
    return flags


def _pack_exit_conditions_met(
    conditions: list[str],
    active_character: CharacterState | None,
) -> bool:
    """
    功能：判断 Story Pack 出口前置条件是否已被当前角色状态标签满足。
    入参：conditions（list[str]）：出口要求的状态 flag；active_character（CharacterState | None）：
        当前角色快照，缺失时无法证明条件满足。
    出参：bool，条件为空或全部命中 state_flags 时返回 True。
    异常：不抛异常；脏 flag 会被跳过，未知条件按未满足处理。
    """
    if not conditions:
        return True
    if active_character is None:
        return False
    raw_flags = active_character.get("state_flags", [])
    if not isinstance(raw_flags, list):
        return False
    state_flag_set = {str(flag).strip().lower() for flag in raw_flags if isinstance(flag, str)}
    for condition in conditions:
        normalized = condition.strip().lower()
        if normalized and normalized not in state_flag_set:
            return False
    return True


def _pack_exit_disabled_reason(label: str) -> str:
    """
    功能：为未解锁出口生成玩家可读的禁用原因。
    入参：label（str）：出口展示名。
    出参：str，中文禁用原因。
    异常：不抛异常；空 label 时使用通用方向文案。
    """
    return f"需要先完成前置线索，才能{build_move_action_text(label)}。"


def _read_effect_template(
    templates: dict[str, Any],
    key: str,
    default: dict[str, str],
) -> StatusEffectState:
    """
    功能：读取单个状态效果模板，允许规则/MOD 覆盖 label/kind/severity/description。
    入参：templates（dict[str, Any]）：规则层模板集合；key（str）：效果键；
        default（dict[str, str]）：默认效果文案。
    出参：StatusEffectState，字段完整的状态效果对象。
    异常：不抛异常；模板缺字段时按 default 或 key 降级。
    """
    raw_template = templates.get(key)
    template = raw_template if isinstance(raw_template, dict) else {}
    return {
        "key": str(template.get("key") or default.get("key") or key),
        "label": str(template.get("label") or default.get("label") or key.replace("_", " ")),
        "kind": str(template.get("kind") or default.get("kind") or "flag"),
        "severity": str(template.get("severity") or default.get("severity") or "info"),
        "description": str(template.get("description") or default.get("description") or ""),
    }


def _unknown_flag_effect(flag: str) -> StatusEffectState:
    """
    功能：为规则未声明的 state flag 生成可读降级效果。
    入参：flag（str）：SQLite 中读取到的状态标签。
    出参：StatusEffectState，可直接返回 API/前端展示。
    异常：不抛异常；空 flag 由调用方过滤。
    """
    return {
        "key": flag,
        "label": flag.replace("_", " "),
        "kind": "flag",
        "severity": "info",
        "description": "规则层未声明该状态文案，已按标签降级展示。",
    }


def derive_character_status(
    snapshot: dict[str, Any],
    state_flags: list[str],
    rules: dict[str, Any] | None = None,
) -> tuple[str, list[StatusEffectState], StatusContextState]:
    """
    功能：根据 HP/MP 阈值、state_flags 与 character_status 规则派生角色状态展示与 Agent 上下文。
    入参：snapshot（dict[str, Any]）：角色数据库快照，至少包含 hp/max_hp/mp/max_mp；
        state_flags（list[str]）：已解析的状态标签；
        rules（dict[str, Any] | None，默认 None）：分层合并后的规则快照，允许 MOD/剧本覆盖文案。
    出参：tuple[str, list[StatusEffectState], StatusContextState]，
        分别为摘要、效果列表与 prompt 上下文。
    异常：不抛异常；数值或配置缺失时降级为“状态稳定”。
    """
    status_rules_raw = (rules or {}).get("character_status", {})
    status_rules = status_rules_raw if isinstance(status_rules_raw, dict) else {}
    thresholds_raw = status_rules.get("resource_thresholds", {})
    thresholds = thresholds_raw if isinstance(thresholds_raw, dict) else {}
    resource_templates_raw = status_rules.get("resource_effects", {})
    resource_templates = resource_templates_raw if isinstance(resource_templates_raw, dict) else {}
    flag_templates_raw = status_rules.get("flags", {})
    flag_templates = flag_templates_raw if isinstance(flag_templates_raw, dict) else {}

    effects: list[StatusEffectState] = []
    hp = _to_int(snapshot.get("hp"), 0)
    max_hp = max(0, _to_int(snapshot.get("max_hp"), 0))
    mp = _to_int(snapshot.get("mp"), 0)
    max_mp = max(0, _to_int(snapshot.get("max_mp"), 0))
    resource_state = "stable"

    if max_hp > 0:
        hp_ratio = hp / max_hp
        critical_ratio = _to_ratio(thresholds.get("hp_critical_ratio"), 0.25)
        wounded_ratio = _to_ratio(thresholds.get("hp_wounded_ratio"), 0.5)
        if hp_ratio <= critical_ratio:
            resource_state = "hp_critical"
            effects.append(
                _read_effect_template(
                    resource_templates,
                    "hp_critical",
                    {
                        "key": "hp_critical",
                        "label": "濒危",
                        "kind": "resource",
                        "severity": "critical",
                        "description": "生命值极低，行动叙事应体现明显危险。",
                    },
                )
            )
        elif hp_ratio <= wounded_ratio:
            resource_state = "hp_wounded"
            effects.append(
                _read_effect_template(
                    resource_templates,
                    "hp_wounded",
                    {
                        "key": "hp_wounded",
                        "label": "受伤",
                        "kind": "resource",
                        "severity": "warning",
                        "description": "生命值低于安全线，行动叙事应体现体力受损。",
                    },
                )
            )

    if max_mp > 0 and (mp / max_mp) <= _to_ratio(thresholds.get("mp_low_ratio"), 0.25):
        if resource_state == "stable":
            resource_state = "mp_low"
        effects.append(
            _read_effect_template(
                resource_templates,
                "mp_low",
                {
                    "key": "mp_low",
                    "label": "法力不足",
                    "kind": "resource",
                    "severity": "warning",
                    "description": "法力值较低，施法或持续行动应显得吃力。",
                },
            )
        )

    for flag in state_flags:
        raw_template = flag_templates.get(flag)
        if isinstance(raw_template, dict):
            effects.append(_read_effect_template(flag_templates, flag, {"key": flag}))
        else:
            effects.append(_unknown_flag_effect(flag))

    stable_summary = str(status_rules.get("stable_summary") or "状态稳定")
    status_summary = (
        stable_summary if not effects else "、".join(effect["label"] for effect in effects)
    )
    prompt_text = (
        stable_summary
        if not effects
        else "；".join(
            f"{effect['label']}({effect['severity']}): {effect['description']}"
            for effect in effects
        )
    )
    status_context: StatusContextState = {
        "resource_state": resource_state,
        "flags": state_flags,
        "prompt_text": prompt_text,
    }
    return status_summary, effects, status_context


def build_character_state(
    entity_probes: Any,
    entity_id: str,
    use_shadow: bool = False,
    rules: dict[str, Any] | None = None,
) -> CharacterState | None:
    """
    功能：从只读探针构建角色快照，供主循环初始态和回合结束刷新复用。
    入参：entity_probes（Any）：实体探针实例；entity_id（str）：角色 ID；
        use_shadow（bool，默认 False）：是否从 Shadow 读取；
        rules（dict[str, Any] | None，默认 None）：分层规则快照，提供 character_status 文案覆盖。
    出参：CharacterState | None，角色不存在时返回 None。
    异常：只读数据库异常向上抛出，交由主循环调用方处理。
    """
    snapshot = entity_probes.get_character_stats(entity_id, use_shadow=use_shadow)
    if snapshot is None:
        return None
    inventory_rows = entity_probes.check_inventory(entity_id, use_shadow=use_shadow)
    state_flags = parse_state_flags(snapshot.get("state_flags_json", "[]"))
    status_summary, status_effects, status_context = derive_character_status(
        snapshot,
        state_flags,
        rules=rules,
    )
    return {
        "id": snapshot["entity_id"],
        "name": snapshot["name"],
        "hp": snapshot["hp"],
        "max_hp": snapshot["max_hp"],
        "mp": snapshot["mp"],
        "max_mp": snapshot["max_mp"],
        "inventory": [row["item_id"] for row in inventory_rows],
        "location": snapshot.get("current_location_id") or "unknown",
        "state_flags": state_flags,
        "status_summary": status_summary,
        "status_effects": status_effects,
        "status_context": status_context,
    }


def build_scene_snapshot(
    entity_probes: Any,
    rules: dict[str, Any],
    active_character: CharacterState | None,
    recent_memory: str = "",
    use_shadow: bool = False,
    pack_scene: StoryPackSceneDef | None = None,
) -> SceneSnapshot | None:
    """
    功能：构造当前回合场景快照，供 NLU、校验、叙事和 Web 可操作提示共享。
    入参：entity_probes（Any）：实体探针实例；rules（dict[str, Any]）：主循环规则配置；
        active_character（CharacterState | None）：当前角色快照；
        recent_memory（str，默认空）：会话记忆摘要；
        use_shadow（bool，默认 False）：是否读取 Shadow 世界状态；
        pack_scene（StoryPackSceneDef | None，默认 None）：当提供时，使用 Story Pack 场景定义
            填充快照（outputs、名称、摘要、交互器、NPC、物品），合并或替换 DB 来源内容。
    出参：SceneSnapshot | None，角色缺失时返回 None。
    异常：只读数据库异常向上抛出；地点定义缺失时使用配置降级场景，不中断回合。
    """
    if active_character is None:
        return None
    # 剧本包分支：当提供 pack_scene 时，使用 Story Pack 场景定义构建快照
    if pack_scene is not None:
        pack_exits: list[SceneExitState] = []
        for ext in pack_scene.exits:
            exit_conditions = list(ext.conditions)
            exit_enabled = _pack_exit_conditions_met(exit_conditions, active_character)
            exit_payload: SceneExitState = {
                "direction": "exit",
                "location_id": ext.target_scene_id,
                "label": ext.label,
                "aliases": ext.aliases,
                "enabled": exit_enabled,
            }
            if exit_conditions:
                exit_payload["conditions"] = exit_conditions
            if not exit_enabled:
                exit_payload["disabled_reason"] = _pack_exit_disabled_reason(ext.label)
            if ext.asset_id:
                exit_payload["asset_id"] = ext.asset_id
            pack_exits.append(exit_payload)
        pack_npcs: list[dict[str, Any]] = [
            _pack_visible_ref_to_snapshot(npc_ref, "entity_id", "npc")
            for npc_ref in pack_scene.visible_npcs
        ]
        pack_items: list[dict[str, Any]] = [
            _pack_visible_ref_to_snapshot(item_ref, "item_id", "item")
            for item_ref in pack_scene.visible_items
        ]
        pack_interactions: list[dict[str, Any]] = []
        for ia in pack_scene.interactables:
            interaction_payload: dict[str, Any] = {
                "interaction_id": ia.interaction_id,
                "label": ia.label,
                "kind": ia.kind,
                "target_ref": ia.target_ref,
                "aliases": list(ia.aliases),
                "quick_action": ia.quick_action,
            }
            if ia.asset_id:
                interaction_payload["asset_id"] = ia.asset_id
            pack_interactions.append(interaction_payload)
        pack_actions: list[str] = []
        if pack_exits:
            pack_actions.append("move")
        for interaction in pack_interactions:
            action_kind = str(interaction.get("kind") or "interact")
            if action_kind not in pack_actions:
                pack_actions.append(action_kind)
        if pack_interactions and "interact" not in pack_actions:
            pack_actions.append("interact")
        pack_snapshot: dict[str, Any] = {
            "schema_version": "scene_snapshot.v2",
            "current_location": {
                "id": pack_scene.scene_id,
                "name": pack_scene.display_name,
                "description": pack_scene.summary,
                "background_asset_id": pack_scene.background_asset_id,
                "image_asset_id": pack_scene.image_asset_id,
            },
            "exits": pack_exits,
            "interactables": pack_interactions,
            "visible_npcs": pack_npcs,
            "visible_items": pack_items,
            "active_quests": [],
            "recent_memory": recent_memory,
            "available_actions": pack_actions,
            "suggested_actions": [],
        }
        pack_snapshot.update(build_scene_interaction_model(pack_snapshot))
        return cast(SceneSnapshot, pack_snapshot)
    # DB/配置降级分支（pack_scene 为 None 时走此路径）
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
