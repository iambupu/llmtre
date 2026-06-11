"""
功能：整理叙事记忆条目并生成主循环可消费的上下文文本。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from state.contracts.memory import NarrativeMemoryItem

_WHITESPACE_RE = re.compile(r"\s+")


def build_narrative_memory_items(
    *,
    session_id: str,
    session_turn_id: int,
    user_input: str,
    turn_result: Mapping[str, Any],
) -> list[NarrativeMemoryItem]:
    """
    功能：从一次已确认的有效回合中提取长期叙事记忆候选。
    入参：session_id（str）：Web 会话 ID；session_turn_id（int）：会话内证据回合；
        user_input（str）：玩家原始输入；turn_result（Mapping）：主循环返回的回合结果。
    出参：list[NarrativeMemoryItem]，可直接交给存储层去重写入。
    异常：不主动抛业务异常；字段缺失按空值降级，Pydantic 校验失败表示调用方传入非法契约。
    """
    if not bool(turn_result.get("is_valid")) or not bool(
        turn_result.get("should_write_story_memory")
    ):
        return []

    action = _as_mapping(turn_result.get("action_intent"))
    physics_diff = _as_mapping(turn_result.get("physics_diff"))
    scene_snapshot = _as_mapping(turn_result.get("scene_snapshot"))
    location = _as_mapping(scene_snapshot.get("current_location"))
    location_id = _safe_identifier(location.get("id"), fallback="session")
    location_name = _safe_text(location.get("name") or location_id)
    action_type = _safe_text(action.get("type") or "unknown")
    final_response = _excerpt(turn_result.get("final_response"), limit=140)
    user_excerpt = _excerpt(user_input, limit=80)

    items: list[NarrativeMemoryItem] = []
    _append_action_memory(
        items=items,
        session_id=session_id,
        session_turn_id=session_turn_id,
        action_type=action_type,
        action=action,
        physics_diff=physics_diff,
        location_id=location_id,
        location_name=location_name,
        user_excerpt=user_excerpt,
        final_response=final_response,
    )
    _append_trigger_memories(
        items=items,
        session_id=session_id,
        session_turn_id=session_turn_id,
        trigger_events=_dict_list(turn_result.get("trigger_events")),
        location_id=location_id,
    )
    _append_quest_memories(
        items=items,
        session_id=session_id,
        session_turn_id=session_turn_id,
        quest_updates=_dict_list(turn_result.get("quest_updates")),
    )
    return _dedupe_items(items)


def format_narrative_memory_context(
    items: Sequence[Mapping[str, Any]],
    *,
    max_items: int = 8,
) -> str:
    """
    功能：把长期叙事记忆记录格式化为 GM prompt 可读的短上下文。
    入参：items（Sequence[Mapping]）：存储层返回的记忆项；max_items（int，默认 8）：输出上限。
    出参：str，空列表返回空字符串；非空返回带标题的中文条目。
    异常：不抛异常；脏字段按空值跳过，避免记忆展示阻断回合。
    """
    lines: list[str] = []
    for item in items[: max(0, max_items)]:
        text = _safe_text(item.get("text"))
        if not text:
            continue
        kind = _safe_text(item.get("kind") or "memory")
        subject = _safe_text(item.get("subject_id") or "")
        prefix = f"[{kind}:{subject}] " if subject else f"[{kind}] "
        lines.append(f"- {prefix}{text}")
    if not lines:
        return ""
    return "## 长期叙事记忆\n" + "\n".join(lines)


def build_narrative_memory_relevance(
    scene_snapshot: Mapping[str, Any] | None,
) -> dict[str, set[str]]:
    """
    功能：从当前场景快照提取长期记忆检索相关性键。
    入参：scene_snapshot（Mapping | None）：当前回合只读场景快照，包含地点、NPC、物品和任务。
    出参：dict[str, set[str]]，按 subject_type 聚合当前相关 ID，并包含 any 汇总键。
    异常：不抛异常；字段缺失时返回空集合，调用方按全局记忆降级。
    """
    scene = scene_snapshot if isinstance(scene_snapshot, Mapping) else {}
    relevance: dict[str, set[str]] = {
        "location": set(),
        "npc": set(),
        "character": set(),
        "quest": set(),
        "item": set(),
        "interaction": set(),
        "any": set(),
    }

    current_location = _as_mapping(scene.get("current_location"))
    _add_relevance_value(relevance, "location", current_location.get("id"))

    for npc in _dict_list(scene.get("visible_npcs")):
        npc_id = npc.get("entity_id") or npc.get("id") or npc.get("npc_id") or npc.get("target_id")
        _add_relevance_value(relevance, "npc", npc_id)
        _add_relevance_value(relevance, "character", npc_id)

    for item in _dict_list(scene.get("visible_items")):
        item_id = item.get("item_id") or item.get("id") or item.get("object_id")
        _add_relevance_value(relevance, "item", item_id)

    for quest in _dict_list(scene.get("active_quests")):
        quest_id = quest.get("quest_id") or quest.get("id")
        _add_relevance_value(relevance, "quest", quest_id)

    for scene_object in _dict_list(scene.get("scene_objects")):
        object_type = _safe_text(scene_object.get("object_type"))
        object_id = scene_object.get("object_id")
        if object_type in relevance:
            _add_relevance_value(relevance, object_type, object_id)
        elif object_type:
            _add_relevance_value(relevance, "interaction", object_id)

    for interaction in _dict_list(scene.get("interactables")):
        interaction_id = interaction.get("interaction_id") or interaction.get("id")
        target_ref = interaction.get("target_ref")
        _add_relevance_value(relevance, "interaction", interaction_id)
        _add_relevance_value(relevance, "any", target_ref)

    return relevance


def rank_narrative_memory_items(
    items: Sequence[Mapping[str, Any]],
    relevance: Mapping[str, set[str]] | None,
) -> list[dict[str, Any]]:
    """
    功能：按当前地点/NPC/任务相关性过滤并排序长期叙事记忆。
    入参：items（Sequence[Mapping]）：存储层候选记忆；relevance（Mapping | None）：相关性键。
    出参：list[dict[str, Any]]，只保留当前相关或全局适用的 active 记忆。
    异常：不抛异常；候选脏字段按低相关性处理。
    """
    if not relevance:
        return [dict(item) for item in items]

    ranked: list[tuple[int, dict[str, Any]]] = []
    for raw_item in items:
        item = dict(raw_item)
        score = _memory_relevance_score(item, relevance)
        if score <= 0:
            continue
        ranked.append((score, item))
    ranked.sort(
        key=lambda pair: (
            pair[0],
            _to_int(pair[1].get("importance"), 0),
            _to_int(pair[1].get("last_seen_turn_id"), 0),
        ),
        reverse=True,
    )
    return [item for _, item in ranked]


def _append_action_memory(
    *,
    items: list[NarrativeMemoryItem],
    session_id: str,
    session_turn_id: int,
    action_type: str,
    action: Mapping[str, Any],
    physics_diff: Mapping[str, Any],
    location_id: str,
    location_name: str,
    user_excerpt: str,
    final_response: str,
) -> None:
    """
    功能：根据动作类型提取地点、目标、物品和后果记忆。
    入参：items（list）：输出列表；session_id/session_turn_id：证据定位；
        action_type/action/physics_diff：主循环确定结果；location_id/location_name：当前场景；
        user_excerpt/final_response：叙事摘要素材。
    出参：None，原地追加记忆项。
    异常：不抛业务异常；无法识别的动作降级为场景片段。
    """
    target_id = _safe_text(action.get("target_id"))
    parameters = _as_mapping(action.get("parameters"))

    if action_type == "move":
        location_change = _as_mapping(physics_diff.get("location_change"))
        from_location = _safe_text(location_change.get("from") or "未知地点")
        to_location = _safe_text(
            location_change.get("to") or parameters.get("location_id") or location_id
        )
        text = f"玩家从 {from_location} 移动到 {to_location}。"
        if final_response:
            text = f"{text} {final_response}"
        items.append(
            _new_item(
                session_id=session_id,
                session_turn_id=session_turn_id,
                kind="scene_beat",
                scope="location",
                subject_type="location",
                subject_id=_safe_identifier(to_location, fallback=location_id),
                text=text,
                importance=5,
                metadata={"action_type": action_type},
            )
        )
        return

    if action_type == "talk" and target_id:
        items.append(
            _new_item(
                session_id=session_id,
                session_turn_id=session_turn_id,
                kind="relationship",
                scope="npc",
                subject_type="npc",
                subject_id=target_id,
                text=f"玩家与 {target_id} 交谈：{final_response or user_excerpt}",
                importance=6,
                metadata={"action_type": action_type},
            )
        )
        return

    if action_type == "attack" and target_id:
        hit_text = "命中" if bool(physics_diff.get("attack_hit")) else "未命中"
        damage = physics_diff.get("target_hp_delta")
        damage_text = f"，造成 {abs(int(damage))} 点伤害" if isinstance(damage, int) else ""
        items.append(
            _new_item(
                session_id=session_id,
                session_turn_id=session_turn_id,
                kind="consequence",
                scope="npc",
                subject_type="npc",
                subject_id=target_id,
                text=f"玩家攻击了 {target_id}，结果为{hit_text}{damage_text}。",
                importance=7,
                metadata={"action_type": action_type, "physics_diff": dict(physics_diff)},
            )
        )
        return

    if action_type == "use_item":
        item_id = _safe_identifier(
            physics_diff.get("consumed_item_id") or parameters.get("item_id"),
            fallback="unknown_item",
        )
        effect_text = _describe_resource_effect(physics_diff)
        items.append(
            _new_item(
                session_id=session_id,
                session_turn_id=session_turn_id,
                kind="consequence",
                scope="item",
                subject_type="item",
                subject_id=item_id,
                text=f"玩家使用了 {item_id}{effect_text}。",
                importance=5,
                metadata={"action_type": action_type, "physics_diff": dict(physics_diff)},
            )
        )
        return

    kind = "discovery" if action_type in {"observe", "inspect", "interact"} else "scene_beat"
    items.append(
        _new_item(
            session_id=session_id,
            session_turn_id=session_turn_id,
            kind=kind,
            scope="location",
            subject_type="location",
            subject_id=location_id,
            text=f"玩家在 {location_name} 执行 {action_type}：{final_response or user_excerpt}",
            importance=4,
            metadata={"action_type": action_type},
        )
    )


def _append_trigger_memories(
    *,
    items: list[NarrativeMemoryItem],
    session_id: str,
    session_turn_id: int,
    trigger_events: list[dict[str, Any]],
    location_id: str,
) -> None:
    """
    功能：把剧本触发器声明的 memory_text 转换为长期叙事记忆。
    入参：items（list）：输出列表；session_id/session_turn_id：证据定位；
        trigger_events（list[dict]）：主循环返回的触发器事件；location_id（str）：场景兜底对象。
    出参：None，原地追加记忆项。
    异常：不抛异常；缺少 memory_text 的触发器跳过。
    """
    for event in trigger_events:
        memory_text = _safe_text(event.get("memory_text"))
        if not memory_text:
            continue
        trigger_id = _safe_identifier(event.get("trigger_id"), fallback="trigger")
        items.append(
            _new_item(
                session_id=session_id,
                session_turn_id=session_turn_id,
                kind="unresolved_hook",
                scope="location",
                subject_type="location",
                subject_id=location_id,
                text=memory_text,
                importance=8,
                metadata={"trigger_id": trigger_id, "trigger_type": event.get("type")},
            )
        )


def _append_quest_memories(
    *,
    items: list[NarrativeMemoryItem],
    session_id: str,
    session_turn_id: int,
    quest_updates: list[dict[str, Any]],
) -> None:
    """
    功能：把任务运行态更新转换为 GM 可用的任务线索记忆。
    入参：items（list）：输出列表；session_id/session_turn_id：证据定位；
        quest_updates（list[dict]）：主循环返回的任务状态。
    出参：None，原地追加记忆项。
    异常：不抛异常；缺少 quest_id 的记录跳过。
    """
    for quest in quest_updates:
        quest_id = _safe_identifier(quest.get("quest_id"), fallback="")
        if not quest_id:
            continue
        status = _safe_text(quest.get("status") or "unknown")
        stage = _safe_text(quest.get("current_stage_id") or "")
        stage_text = f"，当前阶段 {stage}" if stage else ""
        items.append(
            _new_item(
                session_id=session_id,
                session_turn_id=session_turn_id,
                kind="quest",
                scope="quest",
                subject_type="quest",
                subject_id=quest_id,
                text=f"任务 {quest_id} 状态为 {status}{stage_text}。",
                importance=7,
                metadata={"quest_update": dict(quest)},
            )
        )


def _new_item(
    *,
    session_id: str,
    session_turn_id: int,
    kind: str,
    scope: str,
    subject_type: str,
    subject_id: str,
    text: str,
    importance: int,
    metadata: dict[str, Any],
) -> NarrativeMemoryItem:
    """
    功能：创建带稳定 memory_id 的长期记忆项。
    入参：session_id/session_turn_id：证据定位；kind/scope/subject_type/subject_id：分类；
        text（str）：记忆文本；importance（int）：检索权重；metadata（dict）：诊断来源。
    出参：NarrativeMemoryItem。
    异常：字段枚举或取值非法时由 Pydantic 抛出 ValidationError。
    """
    normalized_text = _safe_text(text)
    memory_id = _memory_id(
        session_id=session_id,
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        text=normalized_text,
    )
    return NarrativeMemoryItem(
        memory_id=memory_id,
        session_id=session_id,
        scope=scope,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        subject_type=subject_type,  # type: ignore[arg-type]
        subject_id=subject_id,
        text=normalized_text,
        evidence_turn_id=session_turn_id,
        importance=importance,
        confidence=1.0,
        status="active",
        metadata=metadata,
        created_turn_id=session_turn_id,
        last_seen_turn_id=session_turn_id,
    )


def _memory_id(
    *,
    session_id: str,
    kind: str,
    subject_type: str,
    subject_id: str,
    text: str,
) -> str:
    """
    功能：根据会话、分类、对象和文本生成稳定记忆 ID，支撑幂等重放去重。
    入参：session_id/kind/subject_type/subject_id/text（str）：记忆唯一语义键。
    出参：str，短 SHA256 十六进制 ID。
    异常：编码失败等系统异常向上抛出。
    """
    raw = "|".join([session_id, kind, subject_type, subject_id, text])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _dedupe_items(items: list[NarrativeMemoryItem]) -> list[NarrativeMemoryItem]:
    """
    功能：按 memory_id 去重，避免同一回合动作与触发器重复产出。
    入参：items（list[NarrativeMemoryItem]）：候选列表。
    出参：list[NarrativeMemoryItem]，保持首次出现顺序。
    异常：无。
    """
    seen: set[str] = set()
    result: list[NarrativeMemoryItem] = []
    for item in items:
        if item.memory_id in seen:
            continue
        seen.add(item.memory_id)
        result.append(item)
    return result


def _as_mapping(raw: Any) -> Mapping[str, Any]:
    """
    功能：把未知值收敛为只读映射，简化上层字段读取。
    入参：raw（Any）：可能为 dict 或其他值。
    出参：Mapping[str, Any]，非映射返回空 dict。
    异常：无。
    """
    return raw if isinstance(raw, Mapping) else {}


def _dict_list(raw: Any) -> list[dict[str, Any]]:
    """
    功能：把未知列表规整为 dict 列表。
    入参：raw（Any）：可能为 list。
    出参：list[dict[str, Any]]，非法项跳过。
    异常：无。
    """
    values = raw if isinstance(raw, list) else []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _safe_text(raw: Any) -> str:
    """
    功能：清理文本空白，避免 prompt 中出现不可控格式。
    入参：raw（Any）：待展示值。
    出参：str，压缩空白后的文本。
    异常：无。
    """
    return _WHITESPACE_RE.sub(" ", str(raw or "")).strip()


def _safe_identifier(raw: Any, *, fallback: str) -> str:
    """
    功能：生成适合作为 subject_id 的非空标识。
    入参：raw（Any）：候选值；fallback（str）：空值兜底。
    出参：str，压缩空白后的标识。
    异常：无。
    """
    value = _safe_text(raw)
    return value or fallback


def _add_relevance_value(
    relevance: dict[str, set[str]],
    subject_type: str,
    raw_value: Any,
) -> None:
    """
    功能：向相关性集合添加一个规整后的对象 ID，并同步写入 any 汇总键。
    入参：relevance（dict[str, set[str]]）：待更新集合；subject_type（str）：对象类型；
        raw_value（Any）：候选 ID。
    出参：None。
    异常：无；空值直接跳过。
    """
    value = _safe_text(raw_value)
    if not value:
        return
    relevance.setdefault(subject_type, set()).add(value)
    relevance.setdefault("any", set()).add(value)


def _memory_relevance_score(
    item: Mapping[str, Any],
    relevance: Mapping[str, set[str]],
) -> int:
    """
    功能：计算单条长期记忆与当前场景的相关性分数。
    入参：item（Mapping）：长期记忆项；relevance（Mapping[str, set[str]]）：当前场景相关键。
    出参：int，大于 0 表示可进入 GM 上下文。
    异常：无；非法字段按 0 分处理。
    """
    kind = _safe_text(item.get("kind"))
    subject_type = _safe_text(item.get("subject_type"))
    subject_id = _safe_text(item.get("subject_id"))
    if not subject_id:
        return 0
    if kind == "player_style" or subject_type in {"session", "world"}:
        return 25
    if subject_id in relevance.get(subject_type, set()):
        return 100
    if subject_id in relevance.get("any", set()):
        return 70
    return 0


def _to_int(raw: Any, fallback: int) -> int:
    """
    功能：把未知值转换为整数排序键。
    入参：raw（Any）：候选数值；fallback（int）：转换失败兜底。
    出参：int。
    异常：内部捕获 ValueError/TypeError，按 fallback 降级。
    """
    try:
        return int(raw)
    except TypeError, ValueError:
        return fallback


def _excerpt(raw: Any, *, limit: int) -> str:
    """
    功能：截断叙事片段，防止长期记忆单条过长。
    入参：raw（Any）：原文本；limit（int）：最大字符数。
    出参：str，必要时追加省略号。
    异常：无。
    """
    text = _safe_text(raw)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _describe_resource_effect(physics_diff: Mapping[str, Any]) -> str:
    """
    功能：从确定性结算差异中提取资源变化描述。
    入参：physics_diff（Mapping）：主循环已确认的结算差异。
    出参：str，以中文短语描述 HP/MP 变化；无变化返回空字符串。
    异常：无。
    """
    parts: list[str] = []
    for key, label in (("hp_delta", "HP"), ("mp_delta", "MP")):
        value = physics_diff.get(key)
        if isinstance(value, int) and value != 0:
            verb = "恢复" if value > 0 else "减少"
            parts.append(f"{label}{verb}{abs(value)}")
    return "，" + "，".join(parts) if parts else ""
