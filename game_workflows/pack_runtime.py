"""
A2-Plus 剧本包运行期上下文对象。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from state.contracts.story_pack import StoryPackSceneDef


@dataclass(frozen=True)
class PackRuntimeContext:
    """
    功能：把一次回合中与剧本包相关的运行期输入收束为单一对象。
    入参：scene/triggers/quests/fired_trigger_ids/quest_states/session_metadata：
        分别表示当前 pack 场景、触发器定义、任务定义、已触发 ID、任务运行态和会话元数据。
    出参：PackRuntimeContext。
    异常：dataclass 构造不抛业务异常；字段内容由调用方和下游 helper 校验。
    """

    scene: StoryPackSceneDef | None = None
    triggers: dict[str, Any] | None = None
    quests: dict[str, Any] | None = None
    fired_trigger_ids: list[str] = field(default_factory=list)
    quest_states: list[Any] = field(default_factory=list)
    session_metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_flow_state(cls, state: Mapping[str, Any]) -> PackRuntimeContext:
        """
        功能：从 LangGraph FlowState 字典中提取剧本包运行期上下文。
        入参：state（Mapping[str, Any]）：当前主循环状态。
        出参：PackRuntimeContext，缺失字段使用空值降级。
        异常：不抛异常；字段类型异常由 `_list_or_empty` / `_dict_or_none` 保守规整。
        """
        session_metadata = state.get("session_metadata")
        return cls(
            scene=_scene_or_none(state.get("pack_scene")),
            triggers=_dict_or_none(state.get("pack_bundle_triggers")),
            quests=_dict_or_none(state.get("pack_bundle_quests")),
            fired_trigger_ids=_string_list_or_empty(state.get("fired_trigger_ids")),
            quest_states=_list_or_empty(state.get("quest_states")),
            session_metadata=(session_metadata if isinstance(session_metadata, Mapping) else {}),
        )

    @property
    def has_existing_quest_states(self) -> bool:
        """
        功能：判断当前会话元数据是否已经保存过任务运行态。
        入参：无。
        出参：bool，存在非空 session_metadata.quest_states 时为 True。
        异常：不抛异常；非 Mapping 元数据在构造时已降级为空。
        """
        return bool(self.session_metadata.get("quest_states"))


@dataclass(frozen=True)
class PackRuntimeResult:
    """
    功能：描述结算节点产出的剧本包运行期变化。
    入参：trigger_events（list[dict[str, Any]]）：本轮触发事件；
        quest_states（list[dict[str, Any]]）：本轮完整任务状态快照；
        quest_updates（list[dict[str, Any]]）：本轮对外展示/持久化的任务更新；
        fired_trigger_ids（list[str]）：合并后的已触发 ID；
        scene_switch_to（str | None）：出口触发的目标场景；
        pack_runtime_errors（list[dict[str, Any]]）：触发器/任务运行期错误诊断。
    出参：PackRuntimeResult。
    异常：dataclass 构造不抛业务异常。
    """

    trigger_events: list[dict[str, Any]] = field(default_factory=list)
    quest_states: list[dict[str, Any]] = field(default_factory=list)
    quest_updates: list[dict[str, Any]] = field(default_factory=list)
    fired_trigger_ids: list[str] = field(default_factory=list)
    scene_switch_to: str | None = None
    pack_runtime_errors: list[dict[str, Any]] = field(default_factory=list)

    def to_flow_patch(self) -> dict[str, Any]:
        """
        功能：把运行期结果转换为可合并进 FlowState 的 dict。
        入参：无。
        出参：dict[str, Any]，包含 trigger_events/quest_states/quest_updates/fired_trigger_ids，
            若 scene_switch_to 存在则额外包含 scene_switch_to/current_scene_id。
        异常：不抛异常。
        """
        patch: dict[str, Any] = {
            "trigger_events": self.trigger_events,
            "quest_states": self.quest_states,
            "quest_updates": self.quest_updates,
            "fired_trigger_ids": self.fired_trigger_ids,
            "pack_runtime_errors": self.pack_runtime_errors,
        }
        if self.scene_switch_to is not None:
            patch["scene_switch_to"] = self.scene_switch_to
            patch["current_scene_id"] = self.scene_switch_to
        return patch


def _scene_or_none(value: Any) -> StoryPackSceneDef | None:
    """
    功能：收窄 FlowState 中的 pack_scene 字段。
    入参：value（Any）：候选场景对象。
    出参：StoryPackSceneDef | None。
    异常：不抛异常；非 StoryPackSceneDef 返回 None。
    """
    return value if isinstance(value, StoryPackSceneDef) else None


def _dict_or_none(value: Any) -> dict[str, Any] | None:
    """
    功能：把可选 Mapping 规整为普通 dict。
    入参：value（Any）：候选映射。
    出参：dict[str, Any] | None，非 Mapping 返回 None。
    异常：不抛异常。
    """
    return dict(value) if isinstance(value, Mapping) else None


def _list_or_empty(value: Any) -> list[Any]:
    """
    功能：把候选值规整为 list。
    入参：value（Any）：候选列表。
    出参：list[Any]，非 list 返回空列表。
    异常：不抛异常。
    """
    return list(value) if isinstance(value, list) else []


def _string_list_or_empty(value: Any) -> list[str]:
    """
    功能：把候选值规整为字符串列表。
    入参：value（Any）：候选列表。
    出参：list[str]，仅保留字符串项。
    异常：不抛异常。
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
