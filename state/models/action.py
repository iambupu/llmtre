"""
功能：定义行动、效果和主循环动作结果相关的状态模型。
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ActionType(StrEnum):
    """
    功能：定义主循环可识别的行动类型枚举。
    入参：无；枚举成员在类定义中声明。
    出参：ActionType 枚举值，用于约束移动、交谈、攻击、使用物品等动作。
    异常：不抛异常；非法类型由 Pydantic/Enum 校验阶段拒绝。
    """

    ATTACK = "attack"
    USE_ITEM = "use_item"
    TALK = "talk"
    MOVE = "move"
    SKILL = "skill"
    INTERACT = "interact"


class EffectType(StrEnum):
    """
    功能：定义动作效果的类型枚举。
    入参：无；枚举成员在类定义中声明。
    出参：EffectType 枚举值，用于描述伤害、治疗、状态变化和任务推进等效果。
    异常：不抛异常；非法类型由 Pydantic/Enum 校验阶段拒绝。
    """

    STAT_CHANGE = "stat_change"
    RESOURCE_CHANGE = "resource_change"
    STATE_FLAG = "state_flag"
    ITEM_TRANSFER = "item_transfer"


class ActionEffect(BaseModel):
    """行为产生的后果契约"""

    effect_type: EffectType = Field(..., description="效果类型")
    target_id: str = Field(..., description="目标实体 ID")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="效果具体参数，如 {'attribute': 'hp', 'value': -10}",
    )


class ActionTemplate(BaseModel):
    """原子化行为契约 (Action Contract)

    定义了一个动作从触发到结算的完整逻辑链路。
    """

    action_id: str = Field(..., description="行为唯一标识符")
    name: str = Field(..., description="行为名称")
    action_type: ActionType = Field(..., description="行为大类")

    trigger_description: str = Field(..., description="触发该行为的自然语言描述")

    pre_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "执行前置要求，如 "
            "[{'type': 'stat', 'attribute': 'strength', 'operator': '>=', 'value': 15}]"
        ),
    )

    success_effects: list[ActionEffect] = Field(
        default_factory=list,
        description="成功执行后的效果列表",
    )
    failure_path: str | None = Field(None, description="失败时的叙事引导或回退逻辑描述")

    mana_cost: int = Field(default=0, description="消耗的法力值")
    cooldown_turns: int = Field(default=0, description="冷却回合数")
