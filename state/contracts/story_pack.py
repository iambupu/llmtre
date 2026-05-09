"""
A2 Story Pack v0 契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class StoryPackExitDef(BaseModel):
    """
    功能：描述 Story Pack 场景出口。
    入参：target_scene_id（str）：目标场景 ID；label（str）：展示名；
        aliases（list[str]，默认空）：玩家可能输入的方向或别名；
        conditions（list[str]，默认空）：A2-Plus 触发器条件预留。
    出参：StoryPackExitDef。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    target_scene_id: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class StoryPackInteractionDef(BaseModel):
    """
    功能：描述当前场景内一个可展示的交互入口。
    入参：interaction_id（str）：交互稳定 ID；label（str）：展示文案；
        kind（Literal）：交互类型；target_ref（str | None，默认 None）：目标对象引用；
        aliases（list[str]，默认空）：NLU 可用别名；
        quick_action（bool，默认 True）：是否生成快捷动作。
    出参：StoryPackInteractionDef。
    异常：kind 或字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    interaction_id: str
    label: str
    kind: Literal["observe", "talk", "inspect", "use_item", "attack", "custom"]
    target_ref: str | None = None
    aliases: list[str] = Field(default_factory=list)
    quick_action: bool = True


class StoryPackSceneDef(BaseModel):
    """
    功能：描述 Story Pack v0 场景。
    入参：scene_id（str）：场景稳定 ID；display_name（str）：展示名；summary（str）：摘要；
        exits（list[StoryPackExitDef]，默认空）：出口；
        interactables（list[StoryPackInteractionDef]，默认空）：交互器；
        visible_npcs/visible_items（list[str]，默认空）：可见对象引用。
    出参：StoryPackSceneDef。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    scene_id: str
    display_name: str
    summary: str
    exits: list[StoryPackExitDef] = Field(default_factory=list)
    interactables: list[StoryPackInteractionDef] = Field(default_factory=list)
    visible_npcs: list[str] = Field(default_factory=list)
    visible_items: list[str] = Field(default_factory=list)


class StoryPackManifest(BaseModel):
    """
    功能：描述 Story Pack 入口 manifest。
    入参：pack_id/version/title（str）：pack 稳定标识、版本与展示名；
        author（str | None，默认 None）：作者；scenario_id（str，默认 default）：入口线；
        start_scene_id（str）：起始场景；supported_actions（list[str]，默认空）：动作白名单；
        lore_files（list[str]，默认空）：只读 lore 文件；rules_overlay（dict，默认空）：内容层覆盖。
    出参：StoryPackManifest。
    异常：字段类型或 ID 格式非法时由 Pydantic 抛出 ValidationError。
    """

    pack_id: str
    version: str
    title: str
    author: str | None = None
    scenario_id: str = "default"
    start_scene_id: str
    supported_actions: list[str] = Field(default_factory=list)
    lore_files: list[str] = Field(default_factory=list)
    rules_overlay: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pack_id", "version", "scenario_id", "start_scene_id")
    @classmethod
    def validate_non_empty_identifier(cls, value: str) -> str:
        """
        功能：校验 manifest 中关键 ID 字段非空且无首尾空白。
        入参：value（str）：待校验字段。
        出参：str，规整后的字段值。
        异常：字段为空时抛出 ValueError，由 Pydantic 汇总为 ValidationError。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be empty")
        return normalized


class StoryPackSummary(BaseModel):
    """
    功能：对外暴露 Story Pack registry 摘要。
    入参：pack_id/title/version/scenario_id/start_scene_id/hash：稳定元数据；
        scene_count/interaction_count（int）：内容规模；diagnostics（list[str]）：校验提示。
    出参：StoryPackSummary。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    pack_id: str
    title: str
    version: str
    scenario_id: str = "default"
    start_scene_id: str
    compiled_artifact_hash: str
    scene_count: int
    interaction_count: int
    diagnostics: list[str] = Field(default_factory=list)


class StoryPackBundle(BaseModel):
    """
    功能：承载已校验的 Story Pack manifest、scene 集合和摘要。
    入参：manifest（StoryPackManifest）：入口契约；
        scenes（dict[str, StoryPackSceneDef]）：按 scene_id 索引的场景；
        summary（StoryPackSummary）：registry/API 摘要。
    出参：StoryPackBundle。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    manifest: StoryPackManifest
    scenes: dict[str, StoryPackSceneDef]
    summary: StoryPackSummary
