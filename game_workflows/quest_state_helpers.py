"""
A2-Plus 任务运行态规整辅助函数。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from state.contracts.quest import QuestDef, QuestRuntimeState

logger = logging.getLogger("Workflow.MainLoop")


def quest_def_from_raw(quest_id: str, raw: Any) -> QuestDef | None:
    """
    功能：把 registry 或 FlowState 中的任务定义规整为 QuestDef。
    入参：quest_id（str）：调用方期望的任务 ID，用于错误日志定位；
        raw（Any）：可能已经是 QuestDef，也可能是 dict/Mapping。
    出参：QuestDef | None，规整成功返回任务定义，失败返回 None。
    异常：内部捕获 Pydantic 校验异常并记录日志，避免单个坏任务阻断回合结算。
    """
    try:
        if isinstance(raw, QuestDef):
            return raw
        if isinstance(raw, Mapping):
            return QuestDef.model_validate(dict(raw))
    except Exception as exc:
        logger.warning("任务定义反序列化失败: %s (%s)", quest_id, exc)
    return None


def quest_state_from_raw(raw: Any) -> QuestRuntimeState | None:
    """
    功能：把会话元数据或 FlowState 中的任务运行态规整为 QuestRuntimeState。
    入参：raw（Any）：可能已经是 QuestRuntimeState，也可能是 dict/Mapping。
    出参：QuestRuntimeState | None，规整成功返回运行态，失败返回 None。
    异常：内部捕获 Pydantic 校验异常并记录日志，避免坏的单项状态污染整轮任务列表。
    """
    try:
        if isinstance(raw, QuestRuntimeState):
            return raw
        if isinstance(raw, Mapping):
            return QuestRuntimeState.model_validate(dict(raw))
    except Exception as exc:
        logger.warning("任务运行态反序列化失败: %s", exc)
    return None


def normalize_quest_states(
    quest_states: list[Any] | None,
    pack_quests: Mapping[str, Any] | None,
) -> list[QuestRuntimeState]:
    """
    功能：合并会话中已有任务运行态与剧本包任务定义，补齐缺失任务的 locked 初始态。
    入参：quest_states（list[Any] | None）：会话或 FlowState 中保存的任务运行态列表；
        pack_quests（Mapping[str, Any] | None）：剧本包任务定义索引；为空时仅返回已解析状态。
    出参：list[QuestRuntimeState]，按 pack_quests 顺序返回补齐后的任务状态。
    异常：单个状态或任务定义解析失败会被跳过并记录日志；函数本身不抛业务异常。
    """
    states_by_id: dict[str, QuestRuntimeState] = {}
    for raw_state in quest_states or []:
        state = quest_state_from_raw(raw_state)
        if state is not None:
            states_by_id[state.quest_id] = state

    if not pack_quests:
        return list(states_by_id.values())

    normalized: list[QuestRuntimeState] = []
    for quest_id, raw_quest in pack_quests.items():
        quest_def = quest_def_from_raw(quest_id, raw_quest)
        if quest_def is None:
            continue
        # 剧本包定义是任务全集；会话缺项时补 locked，避免首回合任务列表缺失。
        normalized.append(
            states_by_id.get(
                quest_def.quest_id,
                QuestRuntimeState(
                    quest_id=quest_def.quest_id,
                    status="locked",
                    current_stage_id=quest_def.start_stage_id,
                ),
            )
        )
    return normalized


def dump_quest_states(states: list[QuestRuntimeState]) -> list[dict[str, Any]]:
    """
    功能：把 QuestRuntimeState 列表序列化为 Web/API 与 FlowState 可保存的 JSON dict 列表。
    入参：states（list[QuestRuntimeState]）：已规整的任务运行态列表。
    出参：list[dict[str, Any]]，每项为 Pydantic JSON 模式导出的任务状态。
    异常：Pydantic 序列化异常向上抛出，表示运行态对象本身已不符合契约。
    """
    return [state.model_dump(mode="json") for state in states]
