from enum import StrEnum

from pydantic import BaseModel, Field

from .base import Requirement


class ItemType(StrEnum):
    WEAPON = "weapon"
    ARMOR = "armor"
    CONSUMABLE = "consumable"
    QUEST = "quest"

class ItemEffect(BaseModel):
    """物品的数值影响契约"""
    target_attribute: str = Field(..., description="影响的属性，如 'hp', 'strength'")
    value: int = Field(..., description="变化值，正数为增加，负数为减少")

class ItemTemplate(BaseModel):
    """物品数据核心契约 (Item Data Contract)

    定义了一个物品必须具备哪些属性才能被数值引擎识别。
    Mod 开发者提供的 JSON 必须完全符合此结构。
    """
    item_id: str = Field(..., description="全局唯一标识符，如 'iron_sword_01'")
    name: str = Field(..., description="物品展示名称")
    description: str = Field(..., description="物品的叙事背景描述（供 RAG 和 LLM 使用）")
    item_type: ItemType = Field(..., description="物品大类分类")

    # 物理与基础属性
    requirements: Requirement = Field(
        default_factory=Requirement,
        description="装备/使用该物品的最低属性要求",
    )
    effects: list[ItemEffect] = Field(default_factory=list, description="物品产生的数值效果列表")
    weight: float = Field(default=0.0, description="重量")
    rarity: str = Field(default="common", description="稀有度：common, rare, epic, legendary")

    # 逻辑钩子 (Hooks)
    hooks: dict[str, str] = Field(
        default_factory=dict,
        description="事件钩子，如 {'on_use': 'heal_logic', 'on_equip': 'add_aura'}"
    )

    # 限制
    usage_limit: int = Field(default=-1, description="使用次数限制，-1 表示无限次")
    is_stackable: bool = Field(default=False, description="在背包中是否可堆叠")

