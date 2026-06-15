"""
A2-Plus 剧本包任务运行态状态机。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from state.contracts.quest import QuestDef, QuestRuntimeState, QuestStatus

VALID_QUEST_STATUSES = frozenset({"locked", "active", "completed", "failed"})


def now_iso() -> str:
    """
    功能：生成任务运行态使用的 UTC ISO 时间戳。
    入参：无。
    出参：str，当前 UTC 时间的 ISO 字符串。
    异常：系统时间读取异常向上抛出。
    """
    return datetime.now(UTC).isoformat()


def locked_state_from_def(quest: QuestDef) -> QuestRuntimeState:
    """
    功能：根据任务定义构造 locked 初始运行态。
    入参：quest（QuestDef）：任务静态定义，start_stage_id 必须已由 Pydantic 校验。
    出参：QuestRuntimeState，状态为 locked，阶段为 quest.start_stage_id。
    异常：Pydantic 构造异常向上抛出，表示任务定义契约异常。
    """
    return QuestRuntimeState(
        quest_id=quest.quest_id,
        status="locked",
        current_stage_id=quest.start_stage_id,
    )


def accept_state(
    state: QuestRuntimeState,
    *,
    timestamp: str | None = None,
) -> QuestRuntimeState:
    """
    功能：执行接取任务状态迁移，locked -> active。
    入参：state（QuestRuntimeState）：当前任务运行态；
        timestamp（str | None，默认 None）：可注入时间戳，缺省时使用当前 UTC。
    出参：QuestRuntimeState，合法迁移返回新状态；非法状态返回原 state。
    异常：Pydantic 构造异常向上抛出，表示传入状态不符合契约。
    """
    if state.status != "locked":
        return state
    resolved_time = timestamp or now_iso()
    return QuestRuntimeState(
        quest_id=state.quest_id,
        status="active",
        current_stage_id=state.current_stage_id,
        data=dict(state.data),
        started_at=resolved_time,
        updated_at=resolved_time,
    )


def advance_state(
    state: QuestRuntimeState,
    target_stage_id: str,
    *,
    valid_stage_ids: Collection[str] | None = None,
    timestamp: str | None = None,
) -> QuestRuntimeState:
    """
    功能：执行任务阶段推进，active -> active(stage changed)。
    入参：state（QuestRuntimeState）：当前任务运行态；target_stage_id（str）：目标阶段；
        valid_stage_ids（Collection[str] | None，默认 None）：合法阶段集合，提供时会拒绝越界阶段；
        timestamp（str | None，默认 None）：可注入时间戳，缺省时使用当前 UTC。
    出参：QuestRuntimeState，合法迁移返回新状态；非法状态、重复阶段或越界阶段返回原 state。
    异常：Pydantic 构造异常向上抛出，表示迁移结果不符合契约。
    """
    if state.status != "active":
        return state
    if valid_stage_ids is not None and target_stage_id not in valid_stage_ids:
        return state
    if state.current_stage_id == target_stage_id:
        return state
    resolved_time = timestamp or now_iso()
    completed = _completed_with_current_stage(state)
    return QuestRuntimeState(
        quest_id=state.quest_id,
        status="active",
        current_stage_id=target_stage_id,
        data={**state.data, "stages_completed": completed},
        started_at=state.started_at,
        updated_at=resolved_time,
    )


def complete_state(
    state: QuestRuntimeState,
    *,
    timestamp: str | None = None,
) -> QuestRuntimeState:
    """
    功能：执行任务完成迁移，active -> completed。
    入参：state（QuestRuntimeState）：当前任务运行态；
        timestamp（str | None，默认 None）：可注入时间戳，缺省时使用当前 UTC。
    出参：QuestRuntimeState，合法迁移返回新状态；非 active 状态返回原 state。
    异常：Pydantic 构造异常向上抛出，表示迁移结果不符合契约。
    """
    if state.status != "active":
        return state
    resolved_time = timestamp or now_iso()
    completed = _completed_with_current_stage(state)
    return QuestRuntimeState(
        quest_id=state.quest_id,
        status="completed",
        current_stage_id=state.current_stage_id,
        data={**state.data, "stages_completed": completed},
        started_at=state.started_at,
        updated_at=resolved_time,
    )


def fail_state(
    state: QuestRuntimeState,
    *,
    timestamp: str | None = None,
) -> QuestRuntimeState:
    """
    功能：执行任务失败迁移，active -> failed。
    入参：state（QuestRuntimeState）：当前任务运行态；
        timestamp（str | None，默认 None）：可注入时间戳，缺省时使用当前 UTC。
    出参：QuestRuntimeState，合法迁移返回新状态；非 active 状态返回原 state。
    异常：Pydantic 构造异常向上抛出，表示迁移结果不符合契约。
    """
    if state.status != "active":
        return state
    resolved_time = timestamp or now_iso()
    completed = _completed_with_current_stage(state)
    return QuestRuntimeState(
        quest_id=state.quest_id,
        status="failed",
        current_stage_id=state.current_stage_id,
        data={
            **state.data,
            "stages_completed": completed,
            "completed_at": resolved_time,
        },
        started_at=state.started_at,
        updated_at=resolved_time,
    )


def apply_trigger_update(
    state: QuestRuntimeState,
    *,
    next_status: str,
    next_stage_id: str,
    data_patch: Mapping[str, Any] | None = None,
    timestamp: str | None = None,
) -> QuestRuntimeState:
    """
    功能：根据触发器 update_quest effect 的目标字段更新任务运行态。
    入参：state（QuestRuntimeState）：当前任务状态；next_status（str）：目标状态，可为空；
        next_stage_id（str）：目标阶段，可为空；data_patch（Mapping[str, Any] | None，默认 None）：
        由触发器效果显式写入的任务运行态扩展数据，当前用于 A3 分支路线与后果引用；
        timestamp（str | None，默认 None）：可注入时间。
    出参：QuestRuntimeState，按触发器语义更新后的任务状态。
    异常：next_status 不在 VALID_QUEST_STATUSES 时抛出 ValueError；Pydantic 构造异常向上抛出。
    """
    if next_status and next_status not in VALID_QUEST_STATUSES:
        raise ValueError(f"非法任务状态: {next_status}")
    resolved_status = next_status or state.status
    resolved_stage = next_stage_id or state.current_stage_id
    resolved_time = timestamp or now_iso()
    data = dict(state.data)
    completed = list(data.get("stages_completed", []))
    if next_stage_id and next_stage_id != state.current_stage_id:
        _append_unique(completed, state.current_stage_id)
        data["stages_completed"] = completed
    if resolved_status in {"completed", "failed"}:
        _append_unique(completed, state.current_stage_id)
        data["stages_completed"] = completed
        data.setdefault("completed_at", resolved_time)
    if data_patch:
        _apply_data_patch(data, data_patch)
    return QuestRuntimeState(
        quest_id=state.quest_id,
        status=cast(QuestStatus, resolved_status),
        current_stage_id=resolved_stage,
        data=data,
        started_at=state.started_at or (resolved_time if resolved_status == "active" else None),
        updated_at=resolved_time,
    )


def _completed_with_current_stage(state: QuestRuntimeState) -> list[str]:
    """
    功能：复制 stages_completed 并确保当前阶段已记录。
    入参：state（QuestRuntimeState）：当前任务运行态。
    出参：list[str]，包含当前阶段的已完成阶段列表。
    异常：不抛异常；非字符串阶段项会按原数据保留。
    """
    completed: list[str] = list(state.data.get("stages_completed", []))
    _append_unique(completed, state.current_stage_id)
    return completed


def _append_unique(target: list[str], value: str) -> None:
    """
    功能：向字符串列表追加非空且未存在的值。
    入参：target（list[str]）：待修改列表；value（str）：候选值。
    出参：None，原地修改 target。
    异常：不抛异常；空字符串被忽略。
    """
    if value and value not in target:
        target.append(value)


def _apply_data_patch(data: dict[str, Any], data_patch: Mapping[str, Any]) -> None:
    """
    功能：把触发器声明的运行态扩展数据合并进 QuestRuntimeState.data。
    入参：data（dict[str, Any]）：本次迁移的可变 data 副本；data_patch（Mapping[str, Any]）：
        触发器效果层组装的扩展字段。
    出参：None，原地修改 data。
    异常：不抛业务异常；非法结构会被忽略，避免坏触发器污染任务运行态。
    """
    branch_path = data_patch.get("branch_path")
    if isinstance(branch_path, str) and branch_path.strip():
        data["branch_path"] = branch_path.strip()

    branch_choice = data_patch.get("branch_choice")
    if isinstance(branch_choice, Mapping):
        normalized_choice = _normalize_string_mapping(branch_choice)
        if normalized_choice:
            choices = data.get("branch_choices")
            if not isinstance(choices, list):
                choices = []
            if normalized_choice not in choices:
                choices.append(normalized_choice)
            data["branch_choices"] = choices

    consequence_ref = data_patch.get("consequence_ref")
    if isinstance(consequence_ref, str) and consequence_ref.strip():
        refs = data.get("consequence_refs")
        if not isinstance(refs, list):
            refs = []
        _append_unique(refs, consequence_ref.strip())
        data["consequence_refs"] = refs


def _normalize_string_mapping(raw: Mapping[str, Any]) -> dict[str, str]:
    """
    功能：把分支选择记录规整为只含非空字符串值的稳定字典，便于幂等去重。
    入参：raw（Mapping[str, Any]）：触发器效果层传入的分支选择记录。
    出参：dict[str, str]，非法或空值被过滤。
    异常：不抛异常。
    """
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    return normalized
