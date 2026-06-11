"""
A2 Story Pack v0 契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from state.contracts.media import MediaPlaybackMode, MediaPlaybackPolicy, MediaPreload
from state.contracts.quest import QuestDef
from state.contracts.trigger import TriggerDef


class StoryPackExitDef(BaseModel):
    """
    功能：描述 Story Pack 场景出口。
    入参：target_scene_id（str）：目标场景 ID；label（str）：展示名；
        aliases（list[str]，默认空）：玩家可能输入的方向或别名；
        conditions（list[str]，默认空）：A2-Plus 触发器条件预留；
        asset_id（str | None，默认 None）：出口相关多媒体物料引用。
    出参：StoryPackExitDef。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    target_scene_id: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    asset_id: str | None = None


StoryPackAssetMediaType = Literal["image", "gif", "video", "audio"]
StoryPackAssetPlaybackMode = MediaPlaybackMode
StoryPackAssetPreload = MediaPreload
StoryPackAssetPlaybackPolicy = MediaPlaybackPolicy


class StoryPackAssetDef(BaseModel):
    """
    功能：描述 Story Pack 内声明的多媒体物料。
    入参：kind（Literal）：物料用途；src（str）：pack/assets 下的相对媒体路径；
        media_type（StoryPackAssetMediaType | None）：图片/GIF/视频/音频类型，缺省时由扩展名推断；
        alt（str，默认空）：无障碍替代文本；caption（str | None，默认 None）：展示说明；
        mime_type（str | None，默认 None）：导入或服务时可用的媒体类型提示；
        playback（StoryPackAssetPlaybackPolicy | None，默认 None）：音视频播放生命周期策略。
    出参：StoryPackAssetDef。
    异常：字段类型非法或 src 为空时由 Pydantic 抛出 ValidationError。
    """

    kind: Literal[
        "background",
        "scene",
        "npc",
        "npc_portrait",
        "item",
        "item_icon",
        "illustration",
        "ui",
    ]
    src: str
    media_type: StoryPackAssetMediaType | None = None
    alt: str = ""
    caption: str | None = None
    mime_type: str | None = None
    playback: StoryPackAssetPlaybackPolicy | None = None

    @field_validator("src")
    @classmethod
    def validate_src_not_empty(cls, value: str) -> str:
        """
        功能：校验多媒体物料路径非空；路径边界由 registry validator 负责。
        入参：value（str）：manifest.assets.<id>.src 原始值。
        出参：str，去除首尾空白后的路径。
        异常：路径为空时抛出 ValueError。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("asset src must not be empty")
        return normalized


class StoryPackVisibleRefDef(BaseModel):
    """
    功能：描述场景中可见 NPC 或物品的展示引用。
    入参：id（str）：对象稳定 ID；label（str | None）：玩家可读名称；
        description（str | None）：展示描述；aliases（list[str]）：NLU 别名；
        asset_id/portrait_asset_id/icon_asset_id/image_asset_id：多媒体物料引用。
    出参：StoryPackVisibleRefDef。
    异常：id 为空或字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    id: str
    label: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    asset_id: str | None = None
    portrait_asset_id: str | None = None
    icon_asset_id: str | None = None
    image_asset_id: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id_not_empty(cls, value: str) -> str:
        """
        功能：校验可见对象 ID 非空。
        入参：value（str）：NPC 或物品 ID。
        出参：str，去除首尾空白后的 ID。
        异常：ID 为空时抛出 ValueError。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("visible ref id must not be empty")
        return normalized


StoryPackVisibleRef = str | StoryPackVisibleRefDef


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
    asset_id: str | None = None


class StoryPackSceneDef(BaseModel):
    """
    功能：描述 Story Pack v0 场景。
    入参：scene_id（str）：场景稳定 ID；display_name（str）：展示名；summary（str）：摘要；
        exits（list[StoryPackExitDef]，默认空）：出口；
        interactables（list[StoryPackInteractionDef]，默认空）：交互器；
        visible_npcs/visible_items（list[str | StoryPackVisibleRefDef]，默认空）：可见对象引用；
        background_asset_id/image_asset_id（str | None）：场景背景或插图物料引用。
    出参：StoryPackSceneDef。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    scene_id: str
    display_name: str
    summary: str
    exits: list[StoryPackExitDef] = Field(default_factory=list)
    interactables: list[StoryPackInteractionDef] = Field(default_factory=list)
    visible_npcs: list[StoryPackVisibleRef] = Field(default_factory=list)
    visible_items: list[StoryPackVisibleRef] = Field(default_factory=list)
    background_asset_id: str | None = None
    image_asset_id: str | None = None


class StoryPackManifest(BaseModel):
    """
    功能：描述 Story Pack 入口 manifest。
    入参：pack_id/version/title（str）：pack 稳定标识、版本与展示名；
        author（str | None，默认 None）：作者；scenario_id（str，默认 default）：入口线；
        start_scene_id（str）：起始场景；supported_actions（list[str]，默认空）：动作白名单；
        lore_files（list[str]，默认空）：只读 lore 文件；assets（dict，默认空）：多媒体物料声明；
        rules_overlay（dict，默认空）：内容层覆盖；
        source_background_hash（str | None，默认 None）：生成服务写入的背景来源 hash。
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
    assets: dict[str, StoryPackAssetDef] = Field(default_factory=dict)
    rules_overlay: dict[str, Any] = Field(default_factory=dict)
    source_background_hash: str | None = None

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
        start_scene_title（str）：起始场景的玩家可读展示名；
        source_background_hash（str | None）：生成服务写入的背景来源 hash；
        scene_count/interaction_count/quest_count/trigger_count/asset_count（int）：内容规模；
        diagnostics（list[str]）：校验提示。
    出参：StoryPackSummary。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    pack_id: str
    title: str
    version: str
    scenario_id: str = "default"
    start_scene_id: str
    start_scene_title: str
    compiled_artifact_hash: str
    source_background_hash: str | None = None
    scene_count: int
    interaction_count: int
    quest_count: int = 0
    trigger_count: int = 0
    asset_count: int = 0
    diagnostics: list[str] = Field(default_factory=list)


class StoryPackBundle(BaseModel):
    """
    功能：承载已校验的 Story Pack manifest、scene 集合、quest/trigger 数据和摘要。
    入参：manifest（StoryPackManifest）：入口契约；
        scenes（dict[str, StoryPackSceneDef]）：按 scene_id 索引的场景；
        quests（dict[str, QuestDef]）：按 quest_id 索引的任务定义，默认空；
        triggers（dict[str, TriggerDef]）：按 trigger_id 索引的触发器定义，默认空；
        summary（StoryPackSummary）：registry/API 摘要。
    出参：StoryPackBundle。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    manifest: StoryPackManifest
    scenes: dict[str, StoryPackSceneDef]
    quests: dict[str, QuestDef] = Field(default_factory=dict)
    triggers: dict[str, TriggerDef] = Field(default_factory=dict)
    summary: StoryPackSummary
