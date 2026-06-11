"""
任务运行时状态管理器 - 管理剧本包任务的状态机（locked->active->completed/failed）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from state.contracts.quest import QuestDef, QuestRuntimeState
from tools.packs.quest_runtime import (
    accept_state,
    advance_state,
    complete_state,
    fail_state,
    locked_state_from_def,
)

logger = logging.getLogger(__name__)


class QuestManager:
    """
    功能：管理剧本包中所有 quest 的运行时索引、脏标记与状态机委托。
    入参：quests（list[QuestDef]）：剧本包任务定义列表。
    出参：QuestManager。
    异常：构造 QuestRuntimeState 失败时向上抛出，表示任务定义契约异常。
    """

    def __init__(self, quests: list[QuestDef]) -> None:
        """
        功能：建立任务定义索引并初始化 locked 运行态。
        入参：quests（list[QuestDef]）：静态任务定义列表，quest_id 应唯一。
        出参：None。
        异常：初始化状态构造失败时向上抛出。
        """
        self._quests: dict[str, QuestDef] = {q.quest_id: q for q in quests}
        self._states: dict[str, QuestRuntimeState] = {}
        self._dirty: set[str] = set()
        self._init_states()

    def _init_states(self) -> None:
        """
        功能：将所有 quest 初始化为 locked 状态，current_stage_id 指向 start_stage_id。
        入参：无。
        出参：None。
        异常：locked_state_from_def 构造异常向上抛出。
        """
        for qid, qdef in self._quests.items():
            if qid not in self._states:
                self._states[qid] = locked_state_from_def(qdef)
        logger.info("QuestManager initialized %d quests to locked", len(self._states))

    def accept_quest(self, quest_id: str) -> QuestRuntimeState | None:
        """
        功能：接取任务，委托任务状态机执行 locked -> active。
        入参：quest_id（str）：目标任务 ID。
        出参：QuestRuntimeState | None，未知任务返回 None，非法迁移返回原状态。
        异常：状态机构造异常向上抛出。
        """
        state = self._states.get(quest_id)
        if state is None:
            logger.warning("accept_quest: unknown quest_id %s", quest_id)
            return None
        if state.status != "locked":
            logger.warning(
                "accept_quest: quest %s status is %s, cannot accept", quest_id, state.status
            )
            return state
        new_state = accept_state(state)
        self._states[quest_id] = new_state
        self._dirty.add(quest_id)
        logger.info("quest %s: locked -> active", quest_id)
        return new_state

    def advance_quest_stage(self, quest_id: str, target_stage_id: str) -> QuestRuntimeState | None:
        """
        功能：推进任务阶段，先校验目标阶段属于任务定义，再委托状态机更新。
        入参：quest_id（str）：目标任务 ID；target_stage_id（str）：目标阶段 ID。
        出参：QuestRuntimeState | None，未知任务返回 None，非法迁移返回原状态。
        异常：状态机构造异常向上抛出。
        """
        state = self._states.get(quest_id)
        if state is None:
            logger.warning("advance_quest_stage: unknown quest_id %s", quest_id)
            return None
        qdef = self._quests.get(quest_id)
        if qdef is None:
            logger.warning("advance_quest_stage: quest %s missing definition", quest_id)
            return None
        if state.status != "active":
            logger.warning(
                "advance_quest_stage: quest %s status is %s, cannot advance", quest_id, state.status
            )
            return state
        stage_ids = {s.stage_id for s in qdef.stages}
        if target_stage_id not in stage_ids:
            logger.warning(
                "advance_quest_stage: target stage %s not in quest %s", target_stage_id, quest_id
            )
            return state
        if state.current_stage_id == target_stage_id:
            logger.warning(
                "advance_quest_stage: quest %s already at stage %s", quest_id, target_stage_id
            )
            return state
        new_state = advance_state(
            state,
            target_stage_id,
            valid_stage_ids=stage_ids,
        )
        self._states[quest_id] = new_state
        self._dirty.add(quest_id)
        logger.info("quest %s stage: %s -> %s", quest_id, state.current_stage_id, target_stage_id)
        return new_state

    def complete_quest(self, quest_id: str) -> QuestRuntimeState | None:
        """
        功能：完成任务，委托任务状态机执行 active -> completed。
        入参：quest_id（str）：目标任务 ID。
        出参：QuestRuntimeState | None，未知任务返回 None，非法迁移返回原状态。
        异常：状态机构造异常向上抛出。
        """
        state = self._states.get(quest_id)
        if state is None:
            logger.warning("complete_quest: unknown quest_id %s", quest_id)
            return None
        if state.status == "completed":
            logger.warning("complete_quest: quest %s already completed", quest_id)
            return state
        if state.status == "failed":
            logger.warning("complete_quest: quest %s already failed", quest_id)
            return state
        if state.status == "locked":
            logger.warning("complete_quest: quest %s is locked, cannot complete directly", quest_id)
            return state
        new_state = complete_state(state)
        self._states[quest_id] = new_state
        self._dirty.add(quest_id)
        logger.info("quest %s: active -> completed", quest_id)
        return new_state

    def fail_quest(self, quest_id: str) -> QuestRuntimeState | None:
        """
        功能：将 active 状态的任务标记为 failed，并记录当前阶段到已完成列表。
        入参：quest_id（str）：目标任务 ID。
        出参：QuestRuntimeState | None，未知任务或非法迁移返回 None。
        异常：状态机构造异常向上抛出。
        """
        state = self._states.get(quest_id)
        if state is None:
            logger.warning("fail_quest: unknown quest_id %s", quest_id)
            return None
        if state.status != "active":
            logger.warning(
                "fail_quest: quest %s status is %s, only active can fail", quest_id, state.status
            )
            return None
        new_state = fail_state(state)
        self._states[quest_id] = new_state
        self._dirty.add(quest_id)
        logger.info(
            "quest %s: active -> failed (completed stages: %s)",
            quest_id,
            new_state.data.get("stages_completed", []),
        )
        return new_state

    def get_quest_state(self, quest_id: str) -> QuestRuntimeState | None:
        """
        功能：返回单个任务的当前运行时状态快照。
        入参：quest_id（str）：目标任务 ID。
        出参：QuestRuntimeState | None，存在返回状态，否则 None。
        异常：无。
        """
        return self._states.get(quest_id)

    def get_quest_updates(self) -> list[QuestRuntimeState]:
        """
        功能：返回自上次调用以来有变动的 quest 状态，并清空脏标记。
        入参：无。
        出参：list[QuestRuntimeState]，按 quest_id 排序的变更状态。
        异常：无。
        """
        updates = [self._states[qid] for qid in sorted(self._dirty) if qid in self._states]
        self._dirty.clear()
        return updates

    def get_all_states(self) -> dict[str, QuestRuntimeState]:
        """
        功能：返回所有任务的当前运行时状态快照（浅拷贝字典）。
        入参：无。
        出参：dict[str, QuestRuntimeState]，key 为 quest_id。
        异常：无。
        """
        return dict(self._states)

    def get_dirty_quest_ids(self) -> list[str]:
        """
        功能：返回自上次清脏以来有变更的任务 ID 列表，不清空脏标记。
        入参：无。
        出参：list[str]，按字典序排序的 quest_id。
        异常：无。
        """
        return sorted(self._dirty)

    def clear_dirty(self) -> None:
        """
        功能：清空所有脏标记，将所有任务视为已持久化。
        入参：无。
        出参：None。
        异常：无。
        """
        self._dirty.clear()
        logger.debug("QuestManager dirty markers cleared")

    def reset_quest(self, quest_id: str) -> QuestRuntimeState | None:
        """
        功能：将任务重置为 locked 初始状态，清除所有运行期数据与时间戳。
        入参：quest_id（str）：目标任务 ID。
        出参：QuestRuntimeState | None，未知任务返回 None。
        异常：locked_state_from_def 构造异常向上抛出。
        """
        qdef = self._quests.get(quest_id)
        if qdef is None:
            logger.warning("reset_quest: unknown quest_id %s", quest_id)
            return None
        new_state = locked_state_from_def(qdef)
        self._states[quest_id] = new_state
        self._dirty.add(quest_id)
        logger.info("quest %s reset to locked", quest_id)
        return new_state


# ====== standalone functions ======


def load_quest_defs(pack_root: Path) -> list[QuestDef]:
    """
    功能：从 Story Pack 的 quests 目录读取并校验任务定义。
    入参：pack_root（Path）：Story Pack 根目录。
    出参：list[QuestDef]，跳过非法文件和重复 quest_id。
    异常：单文件读取/校验异常会记录日志并跳过；目录遍历异常向上抛出。
    """
    quests_dir = pack_root / "quests"
    if not quests_dir.is_dir():
        logger.debug("load_quest_defs: no quests dir %r", quests_dir)
        return []
    import json as _json

    defs: list[QuestDef] = []
    seen: set[str] = set()
    for fp in sorted(quests_dir.glob("*.json")):
        try:
            data = _json.loads(fp.read_text(encoding="utf-8"))
            qdef = QuestDef.model_validate(data)
        except Exception:
            logger.warning("load_quest_defs: skip invalid %r", fp, exc_info=True)
            continue
        if qdef.quest_id in seen:
            logger.warning("load_quest_defs: duplicate quest_id %r, skip %s", qdef.quest_id, fp)
            continue
        seen.add(qdef.quest_id)
        defs.append(qdef)
    logger.info("load_quest_defs: loaded %d quest defs", len(defs))
    return defs


def init_quest_states(pack_root: Path) -> list[QuestRuntimeState]:
    """
    功能：从 Story Pack 任务定义初始化 locked 运行态列表。
    入参：pack_root（Path）：Story Pack 根目录。
    出参：list[QuestRuntimeState]，每个任务对应一个 locked 初始状态。
    异常：load_quest_defs 或状态构造异常按各自策略处理。
    """
    defs = load_quest_defs(pack_root)
    states = [locked_state_from_def(q) for q in defs]
    logger.info("init_quest_states: initialized %d locked states", len(states))
    return states


def accept_quest(quest_id: str, quest_states: list[QuestRuntimeState]) -> list[QuestRuntimeState]:
    """
    功能：模块级纯函数入口，接取任务并返回替换后的状态列表。
    入参：quest_id（str）：目标任务 ID；quest_states（list[QuestRuntimeState]）：当前状态快照。
    出参：list[QuestRuntimeState]，合法迁移返回新列表，否则返回原列表。
    异常：状态机构造异常向上抛出。
    """
    idx = None
    for i, s in enumerate(quest_states):
        if s.quest_id == quest_id:
            idx = i
            break
    if idx is None:
        logger.warning("accept_quest: unknown quest_id %r", quest_id)
        return quest_states
    state = quest_states[idx]
    if state.status != "locked":
        logger.warning("accept_quest: quest %r status is %r, cannot accept", quest_id, state.status)
        return quest_states
    new_state = accept_state(state)
    new_states = list(quest_states)
    new_states[idx] = new_state
    logger.info("accept_quest: %r locked -> active", quest_id)
    return new_states


def advance_quest_stage(
    quest_id: str, target_stage_id: str, quest_states: list[QuestRuntimeState]
) -> list[QuestRuntimeState]:
    """
    功能：模块级纯函数入口，推进任务阶段并返回替换后的状态列表。
    入参：quest_id（str）：目标任务 ID；target_stage_id（str）：目标阶段 ID；
        quest_states（list[QuestRuntimeState]）：当前状态快照。
    出参：list[QuestRuntimeState]，合法迁移返回新列表，否则返回原列表。
    异常：状态机构造异常向上抛出；该兼容入口不校验 target_stage_id 是否存在于任务定义。
    """
    idx = None
    for i, s in enumerate(quest_states):
        if s.quest_id == quest_id:
            idx = i
            break
    if idx is None:
        logger.warning("advance_quest_stage: unknown quest_id %r", quest_id)
        return quest_states
    state = quest_states[idx]
    if state.status != "active":
        logger.warning(
            "advance_quest_stage: quest %r status is %r, cannot advance", quest_id, state.status
        )
        return quest_states
    if target_stage_id == state.current_stage_id:
        logger.debug("advance_quest_stage: quest %r already at stage %r", quest_id, target_stage_id)
        return quest_states
    new_state = advance_state(state, target_stage_id)
    new_states = list(quest_states)
    new_states[idx] = new_state
    logger.info(
        "advance_quest_stage: %r %s -> %s", quest_id, state.current_stage_id, target_stage_id
    )
    return new_states


def complete_quest(quest_id: str, quest_states: list[QuestRuntimeState]) -> list[QuestRuntimeState]:
    """
    功能：模块级纯函数入口，完成任务并返回替换后的状态列表。
    入参：quest_id（str）：目标任务 ID；quest_states（list[QuestRuntimeState]）：当前状态快照。
    出参：list[QuestRuntimeState]，合法迁移返回新列表，否则返回原列表。
    异常：状态机构造异常向上抛出。
    """
    idx = None
    for i, s in enumerate(quest_states):
        if s.quest_id == quest_id:
            idx = i
            break
    if idx is None:
        logger.warning("complete_quest: unknown quest_id %r", quest_id)
        return quest_states
    state = quest_states[idx]
    if state.status == "completed":
        logger.warning("complete_quest: quest %r already completed", quest_id)
        return quest_states
    if state.status == "failed":
        logger.warning("complete_quest: quest %r already failed", quest_id)
        return quest_states
    if state.status == "locked":
        logger.warning("complete_quest: quest %r is locked, cannot complete", quest_id)
        return quest_states
    new_state = complete_state(state)
    new_states = list(quest_states)
    new_states[idx] = new_state
    logger.info("complete_quest: %r active -> completed", quest_id)
    return new_states


def fail_quest(quest_id: str, quest_states: list[QuestRuntimeState]) -> list[QuestRuntimeState]:
    """
    功能：模块级纯函数入口，将 active 状态任务标记为 failed。
    入参：quest_id（str）：目标任务 ID；quest_states（list[QuestRuntimeState]）：当前状态快照。
    出参：list[QuestRuntimeState]，合法迁移返回新列表，否则返回原列表。
    异常：状态机构造异常向上抛出。
    """
    idx = None
    for i, s in enumerate(quest_states):
        if s.quest_id == quest_id:
            idx = i
            break
    if idx is None:
        logger.warning("fail_quest: unknown quest_id %r", quest_id)
        return quest_states
    state = quest_states[idx]
    if state.status != "active":
        logger.warning("fail_quest: quest %r status is %r, cannot fail", quest_id, state.status)
        return quest_states
    new_state = fail_state(state)
    new_states = list(quest_states)
    new_states[idx] = new_state
    logger.info("fail_quest: %r active -> failed", quest_id)
    return new_states


def get_quest_state(
    quest_id: str, quest_states: list[QuestRuntimeState]
) -> QuestRuntimeState | None:
    """
    功能：从状态列表中查找指定 quest_id 的状态（模块级函数版本）。
    入参：quest_id（str）：目标任务 ID；quest_states（list[QuestRuntimeState]）：当前状态快照。
    出参：QuestRuntimeState | None，找到返回状态，否则 None。
    异常：无。
    """
    for s in quest_states:
        if s.quest_id == quest_id:
            return s
    return None
