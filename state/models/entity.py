"""
功能：定义角色、NPC 和实体属性相关的状态模型。
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from .base import ResourcePool, Stats


class EntityType(StrEnum):
    """
    功能：定义实体分类枚举。
    入参：无；枚举成员在类定义中声明。
    出参：EntityType 枚举值，用于区分玩家、NPC、怪物和环境实体。
    异常：不抛异常；非法类型由 Pydantic/Enum 校验阶段拒绝。
    """

    PLAYER = "player"
    NPC = "npc"
    MONSTER = "monster"


class EntityTemplate(BaseModel):
    """实体/NPC 数据核心契约 (Entity Data Contract)

    定义了世界中活动对象的基本模板。
    """

    entity_id: str = Field(..., description="全局唯一标识符，如 'guard_captain_01'")
    name: str = Field(..., description="实体名称")
    entity_type: EntityType = Field(..., description="实体类型分类")
    description: str = Field(
        ...,
        description="人物背景、性格和外貌描述（供 RAG 和交互 Agent 生成对话使用）",
    )

    # 硬指标 (SQLite)
    base_stats: Stats = Field(default_factory=Stats, description="基础属性定义")
    resources: ResourcePool = Field(..., description="当前与最大生命/法力资源")

    # 软标签 (Semantic Traits/Graph)
    traits: list[str] = Field(
        default_factory=list,
        description="语义标签，如 ['狡诈', '恐惧蜘蛛', '效忠于王室']",
    )
    social_relations: dict[str, int] = Field(
        default_factory=dict,
        description="社交关系矩阵，Key 为目标实体 ID，Value 为好感度/仇恨值",
    )

    # 运行时状态
    current_location_id: str = Field(default="unknown", description="当前所处场景的 ID")
    behavior_pattern: str = Field(
        default="neutral",
        description="行为模式分类，用于指导状态机逻辑，如 'aggressive', 'cowardly'",
    )
    default_inventory: list[str] = Field(default_factory=list, description="默认携带的物品 ID 列表")
    state_flags: list[str] = Field(
        default_factory=list,
        description="当前状态标记，如 ['invisible', 'poisoned']",
    )
