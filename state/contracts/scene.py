"""
场景快照 v2 与 A2 可操作化预留契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SceneObjectRef(BaseModel):
    """
    功能：描述当前场景中可展示、可选择的对象，为 A2 对象化探索预留入口。
    入参：object_id（str）：场景内稳定对象 ID；object_type（Literal）：对象类型；
        label（str）：展示名；description（str，默认空）：描述；
        state_tags（list[str]）：对象状态标签；source_ref（dict）：来源对象原始引用；
        priority（int，默认 100）：展示排序权重。
    出参：SceneObjectRef。
    异常：字段类型或 object_type 非法时由 Pydantic 抛出 ValidationError。
    """

    object_id: str
    object_type: Literal["exit", "npc", "item", "location", "system"]
    label: str
    description: str = ""
    state_tags: list[str] = Field(default_factory=list)
    source_ref: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100


class InteractionSlot(BaseModel):
    """
    功能：描述场景对象上的一个交互入口，供 A1 quick action 与 A2 对象按钮共用。
    入参：slot_id（str）：交互槽 ID；object_id（str）：所属对象；
        action_type（str）：标准动作族；label（str）：展示文案；
        enabled（bool）：是否可执行；disabled_reason（str）：不可执行原因；
        default_input（str）：提交给现有自然语言回合入口的默认文本；
        required_params（list[str]）：A2 后续结构化交互需要补齐的参数名。
    出参：InteractionSlot。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    slot_id: str
    object_id: str
    action_type: str
    label: str
    enabled: bool
    disabled_reason: str = ""
    default_input: str
    required_params: list[str] = Field(default_factory=list)


class SceneAffordance(BaseModel):
    """
    功能：描述当前场景中一个可执行或可解释的行动候选。
    入参：id（str）：行动 ID；label（str）：展示文案；action_type（str）：标准动作族；
        enabled（bool）：是否可执行；reason（str）：不可执行原因；
        user_input（str）：可直接提交的中文输入；target_id/location_id（str | None）：目标；
        object_id/slot_id（str | None）：A2 对象化来源；priority（int）：排序权重。
    出参：SceneAffordance。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    id: str
    label: str
    action_type: str
    enabled: bool
    reason: str = ""
    user_input: str
    target_id: str | None = None
    location_id: str | None = None
    object_id: str | None = None
    slot_id: str | None = None
    priority: int = 100


class SceneSnapshotV2(BaseModel):
    """
    功能：统一场景快照 v2，兼容 A1 回合闭环并为 A2 场景可操作化预留对象接口。
    入参：schema_version（Literal，默认 scene_snapshot.v2）；其余字段为场景、对象和交互列表。
    出参：SceneSnapshotV2。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    schema_version: Literal["scene_snapshot.v2"] = "scene_snapshot.v2"
    current_location: dict[str, Any] = Field(default_factory=dict)
    exits: list[dict[str, Any]] = Field(default_factory=list)
    visible_npcs: list[dict[str, Any]] = Field(default_factory=list)
    visible_items: list[dict[str, Any]] = Field(default_factory=list)
    active_quests: list[dict[str, Any]] = Field(default_factory=list)
    recent_memory: str = ""
    available_actions: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    scene_objects: list[SceneObjectRef] = Field(default_factory=list)
    interaction_slots: list[InteractionSlot] = Field(default_factory=list)
    affordances: list[SceneAffordance] = Field(default_factory=list)
    ui_hints: dict[str, Any] = Field(default_factory=dict)
