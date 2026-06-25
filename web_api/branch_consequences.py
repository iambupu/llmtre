"""
A3 分支后果摘要生成器。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from state.contracts.branch import BranchConsequenceSummary, BranchStateChange

A3_BRANCH_GROUP_KEY = "a3_branch_group"
A3_BRANCH_VALUE_KEY = "branch_value"


@dataclass(frozen=True)
class _BranchConsequenceContext:
    """
    功能：承载单个触发器可生成分支后果摘要的结构化上下文。
    入参：trigger_id/quest_id/branch_group/branch_path/from_stage_id/to_stage_id/memory_text（str）：
        后果摘要来源事实；stage_meta（dict[str, str]）：阶段展示标签；
        trigger_effects（list[Any]）：触发器效果列表。
    出参：_BranchConsequenceContext。
    异常：dataclass 构造不做业务校验，调用方只在字段齐全时创建。
    """

    trigger_id: str
    quest_id: str
    branch_group: str
    branch_path: str
    from_stage_id: str
    to_stage_id: str
    stage_meta: dict[str, str]
    memory_text: str
    trigger_effects: list[Any]


def build_branch_consequence_summaries(
    *,
    payload: dict[str, Any],
    source_turn_id: int,
) -> list[dict[str, Any]]:
    """
    功能：从回合 payload 中提取 A3 分支触发器，并生成结构化后果摘要。
    入参：payload（dict[str, Any]）：run_turn 返回的标准化回合负载；
        source_turn_id（int）：已持久化的 Web 会话回合号。
    出参：list[dict[str, Any]]，每项符合 BranchConsequenceSummary JSON 契约。
    异常：不抛业务异常；缺少 pack_triggers/pack_quests 时降级为空摘要。
    """
    trigger_events = _dict_list(payload.get("trigger_events"))
    if not trigger_events:
        return []
    trigger_defs = _trigger_defs_by_id(payload.get("pack_triggers"))
    if not trigger_defs:
        return []
    branch_stages = _branch_stage_metadata(payload.get("pack_quests"))
    if not branch_stages:
        return []

    physics_diff = _dict_or_empty(payload.get("physics_diff"))
    quest_states = _quest_states_by_id(payload.get("quest_states"))
    source_action = _source_action(payload.get("action_intent"))
    summaries: list[dict[str, Any]] = []
    for event in trigger_events:
        context = _branch_consequence_context(
            event=event,
            trigger_defs=trigger_defs,
            branch_stages=branch_stages,
            quest_states=quest_states,
        )
        if context is None:
            continue
        state_changes = _build_state_changes(
            context=context,
            physics_diff=physics_diff,
        )
        if len(state_changes) < 2:
            continue
        summary = BranchConsequenceSummary(
            consequence_id=_consequence_id(
                source_turn_id=source_turn_id,
                quest_id=context.quest_id,
                trigger_id=context.trigger_id,
                branch_path=context.branch_path,
            ),
            source_turn_id=source_turn_id,
            source_action=source_action,
            quest_id=context.quest_id,
            from_stage_id=context.from_stage_id,
            to_stage_id=context.to_stage_id,
            branch_path=context.branch_path,
            state_changes=state_changes,
            memory_text=context.memory_text,
            evidence={
                "trigger_id": context.trigger_id,
                "branch_group": context.branch_group,
                "trigger_effects": context.trigger_effects,
            },
        )
        summaries.append(summary.model_dump(mode="json"))
    return summaries


def _branch_consequence_context(
    *,
    event: dict[str, Any],
    trigger_defs: dict[str, dict[str, Any]],
    branch_stages: dict[tuple[str, str, str], dict[str, str]],
    quest_states: dict[str, dict[str, Any]],
) -> _BranchConsequenceContext | None:
    """
    功能：从触发器事件中提取可生成 A3 分支后果摘要的上下文。
    入参：event（dict）：本回合触发事件；
        trigger_defs/branch_stages/quest_states：回合 payload 索引。
    出参：_BranchConsequenceContext | None，非 A3 分支入口触发器返回 None。
    异常：不抛异常；缺字段或非分支阶段按 None 降级。
    """
    trigger_id = str(event.get("trigger_id") or "").strip()
    trigger_def = trigger_defs.get(trigger_id)
    if trigger_def is None:
        return None
    conditions = _dict_or_empty(trigger_def.get("conditions"))
    branch_group = str(conditions.get(A3_BRANCH_GROUP_KEY) or "").strip()
    branch_path = str(conditions.get(A3_BRANCH_VALUE_KEY) or "").strip()
    quest_id = str(conditions.get("quest_id") or "").strip()
    target_stage_id = str(
        conditions.get("target_stage_id") or conditions.get("next_stage_id") or ""
    ).strip()
    if not branch_group or not branch_path or not quest_id or not target_stage_id:
        return None
    stage_meta = branch_stages.get((quest_id, branch_group, target_stage_id))
    if stage_meta is None:
        # A3-3 只把进入互斥分支阶段的触发器视为“选择后果”，汇合触发器留给日志展示。
        return None
    return _BranchConsequenceContext(
        trigger_id=trigger_id,
        quest_id=quest_id,
        branch_group=branch_group,
        branch_path=branch_path,
        from_stage_id=_previous_stage_id(quest_states.get(quest_id, {}), target_stage_id),
        to_stage_id=target_stage_id,
        stage_meta=stage_meta,
        memory_text=str(event.get("memory_text") or conditions.get("memory_text") or "").strip(),
        trigger_effects=_list_or_empty(trigger_def.get("effects")),
    )


def _build_state_changes(
    *,
    context: _BranchConsequenceContext,
    physics_diff: dict[str, Any],
) -> list[BranchStateChange]:
    """
    功能：把任务阶段、状态标签、物品和记忆摘要组装为分支后果变化列表。
    入参：context（_BranchConsequenceContext）：触发器与任务阶段上下文；
        physics_diff（dict）：确定性状态差异。
    出参：list[BranchStateChange]。
    异常：不抛异常；非法 physics_diff 字段按空值降级。
    """
    source = {
        "quest_id": context.quest_id,
        "trigger_id": context.trigger_id,
        "branch_group": context.branch_group,
    }
    changes: list[BranchStateChange] = [
        BranchStateChange(
            kind="quest_stage",
            label=_quest_stage_label(
                before_label=context.stage_meta.get("from_stage_label", ""),
                after_label=context.stage_meta.get("stage_label", ""),
                before_id=context.from_stage_id,
                after_id=context.to_stage_id,
            ),
            field="current_stage_id",
            before=context.from_stage_id,
            after=context.to_stage_id,
            source=source,
        ),
        BranchStateChange(
            kind="trigger",
            label=f"分支路线：{context.branch_path}",
            field="branch_path",
            before="",
            after=context.branch_path,
            source=source,
        ),
    ]
    _append_flag_changes(changes, physics_diff, context.trigger_id)
    _append_item_changes(changes, physics_diff, context.trigger_id)
    _append_memory_change(changes, context.memory_text, context.trigger_id)
    return changes


def _append_flag_changes(
    changes: list[BranchStateChange],
    physics_diff: dict[str, Any],
    trigger_id: str,
) -> None:
    """
    功能：把新增状态标记追加为分支后果变化项。
    入参：changes（list）：待追加列表；physics_diff（dict）：确定性差异；trigger_id（str）：来源触发器。
    出参：None，原地修改 changes。
    异常：不抛异常；非法 state_flags_add 按空列表处理。
    """
    for flag in _string_list(physics_diff.get("state_flags_add")):
        changes.append(
            BranchStateChange(
                kind="state_flag",
                label=f"新增状态：{flag}",
                field="state_flags_add",
                before=None,
                after=flag,
                source={"trigger_id": trigger_id},
            )
        )


def _append_item_changes(
    changes: list[BranchStateChange],
    physics_diff: dict[str, Any],
    trigger_id: str,
) -> None:
    """
    功能：把获得物品追加为分支后果变化项。
    入参：changes（list）：待追加列表；physics_diff（dict）：确定性差异；trigger_id（str）：来源触发器。
    出参：None，原地修改 changes。
    异常：不抛异常；缺 item_id 的物品项会被跳过。
    """
    for item in _dict_list(physics_diff.get("granted_items")):
        item_id = str(item.get("item_id") or "").strip()
        if item_id:
            changes.append(
                BranchStateChange(
                    kind="item",
                    label=f"获得物品：{item_id} x{item.get('quantity', 1)}",
                    field="granted_items",
                    before=None,
                    after={"item_id": item_id, "quantity": item.get("quantity", 1)},
                    source={"trigger_id": trigger_id},
                )
            )


def _append_memory_change(
    changes: list[BranchStateChange],
    memory_text: str,
    trigger_id: str,
) -> None:
    """
    功能：把触发器记忆文本追加为分支后果变化项。
    入参：changes（list）：待追加列表；memory_text（str）：触发器记忆；trigger_id（str）：来源触发器。
    出参：None，原地修改 changes。
    异常：不抛异常；空记忆文本不追加。
    """
    if not memory_text:
        return
    changes.append(
        BranchStateChange(
            kind="memory",
            label=f"记忆：{memory_text}",
            field="memory_text",
            before="",
            after=memory_text,
            source={"trigger_id": trigger_id},
        )
    )


def _branch_stage_metadata(raw_quests: Any) -> dict[tuple[str, str, str], dict[str, str]]:
    """
    功能：从 pack_quests 中提取带 a3_branch_group 的互斥分支阶段。
    入参：raw_quests（Any）：期望为任务定义对象列表。
    出参：dict[tuple[str, str, str], dict[str, str]]，key 为 quest_id/group/stage_id。
    异常：不抛异常；非法任务或阶段项会被跳过。
    """
    metadata: dict[tuple[str, str, str], dict[str, str]] = {}
    for quest in _dict_list(raw_quests):
        quest_id = str(quest.get("quest_id") or "").strip()
        stages = _dict_list(quest.get("stages"))
        labels_by_id = {
            str(stage.get("stage_id") or "").strip(): str(stage.get("label") or "").strip()
            for stage in stages
            if str(stage.get("stage_id") or "").strip()
        }
        for stage in stages:
            stage_id = str(stage.get("stage_id") or "").strip()
            condition = _dict_or_empty(stage.get("completion_condition"))
            branch_group = str(condition.get(A3_BRANCH_GROUP_KEY) or "").strip()
            if not quest_id or not stage_id or not branch_group:
                continue
            metadata[(quest_id, branch_group, stage_id)] = {
                "stage_label": str(stage.get("label") or stage_id),
                "from_stage_label": _previous_stage_label(stages, labels_by_id, stage_id),
            }
    return metadata


def _previous_stage_label(
    stages: list[dict[str, Any]],
    labels_by_id: dict[str, str],
    stage_id: str,
) -> str:
    """
    功能：按任务定义顺序推断目标阶段前一阶段的展示名。
    入参：stages（list[dict]）：任务阶段列表；labels_by_id（dict[str, str]）：阶段标签索引；
        stage_id（str）：目标阶段 ID。
    出参：str，无法推断时返回空字符串。
    异常：不抛异常。
    """
    stage_ids = [str(stage.get("stage_id") or "").strip() for stage in stages]
    try:
        index = stage_ids.index(stage_id)
    except ValueError:
        return ""
    if index <= 0:
        return ""
    return labels_by_id.get(stage_ids[index - 1], "")


def _trigger_defs_by_id(raw_triggers: Any) -> dict[str, dict[str, Any]]:
    """
    功能：把 pack_triggers 列表规整为 trigger_id 索引。
    入参：raw_triggers（Any）：期望为触发器定义对象列表。
    出参：dict[str, dict[str, Any]]。
    异常：不抛异常；缺少 trigger_id 的项会被跳过。
    """
    triggers: dict[str, dict[str, Any]] = {}
    for trigger in _dict_list(raw_triggers):
        trigger_id = str(trigger.get("trigger_id") or "").strip()
        if trigger_id:
            triggers[trigger_id] = trigger
    return triggers


def _quest_states_by_id(raw_states: Any) -> dict[str, dict[str, Any]]:
    """
    功能：把 quest_states 列表规整为 quest_id 索引。
    入参：raw_states（Any）：期望为任务运行态对象列表。
    出参：dict[str, dict[str, Any]]。
    异常：不抛异常；缺少 quest_id 的项会被跳过。
    """
    states: dict[str, dict[str, Any]] = {}
    for state in _dict_list(raw_states):
        quest_id = str(state.get("quest_id") or "").strip()
        if quest_id:
            states[quest_id] = state
    return states


def _previous_stage_id(quest_state: dict[str, Any], current_stage_id: str) -> str:
    """
    功能：从 QuestRuntimeState.data.stages_completed 推断本次阶段迁移的来源阶段。
    入参：quest_state（dict[str, Any]）：当前任务运行态；current_stage_id（str）：目标阶段。
    出参：str，优先返回已完成阶段最后一项，缺失时返回空字符串。
    异常：不抛异常；非法 data 字段按空对象处理。
    """
    data = _dict_or_empty(quest_state.get("data"))
    completed = _string_list(data.get("stages_completed"))
    if completed:
        return completed[-1]
    previous = str(data.get("previous_stage_id") or "").strip()
    if previous and previous != current_stage_id:
        return previous
    return ""


def _quest_stage_label(
    *,
    before_label: str,
    after_label: str,
    before_id: str,
    after_id: str,
) -> str:
    """
    功能：生成任务阶段变化的玩家可读标签。
    入参：before_label/after_label（str）：阶段展示名；before_id/after_id（str）：阶段 ID。
    出参：str。
    异常：不抛异常。
    """
    before_text = before_label or before_id or "未记录"
    after_text = after_label or after_id or "未记录"
    return f"任务阶段：{before_text} -> {after_text}"


def _source_action(raw_action_intent: Any) -> str:
    """
    功能：从 action_intent 中读取动作类型。
    入参：raw_action_intent（Any）：回合动作意图。
    出参：str，缺失时返回空字符串。
    异常：不抛异常。
    """
    action_intent = _dict_or_empty(raw_action_intent)
    return str(action_intent.get("type") or action_intent.get("action") or "").strip()


def _consequence_id(
    *,
    source_turn_id: int,
    quest_id: str,
    trigger_id: str,
    branch_path: str,
) -> str:
    """
    功能：生成稳定分支后果 ID，避免依赖叙事文本。
    入参：source_turn_id（int）：会话回合号；quest_id/trigger_id/branch_path（str）：结构化来源。
    出参：str，形如 bc_<hash>。
    异常：不抛异常。
    """
    raw = f"{source_turn_id}:{quest_id}:{trigger_id}:{branch_path}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"bc_{digest}"


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """
    功能：把任意值规整为 dict。
    入参：value（Any）：候选对象。
    出参：dict[str, Any]，非 dict 时返回空对象。
    异常：不抛异常。
    """
    return dict(value) if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    """
    功能：把任意值规整为对象列表。
    入参：value（Any）：候选列表。
    出参：list[dict[str, Any]]，仅保留 dict 项。
    异常：不抛异常。
    """
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _list_or_empty(value: Any) -> list[Any]:
    """
    功能：把任意值规整为普通列表。
    入参：value（Any）：候选列表。
    出参：list[Any]，非 list 时返回空列表。
    异常：不抛异常。
    """
    return list(value) if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    """
    功能：把任意值规整为非空字符串列表。
    入参：value（Any）：候选列表。
    出参：list[str]。
    异常：不抛异常。
    """
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
