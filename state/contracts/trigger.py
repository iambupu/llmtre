"""
A2-Plus 触发器系统契约。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TriggerType = Literal[
    "enter_scene",
    "observe",
    "talk",
    "inspect",
    "item_owned",
    "quest_stage",
    "event",
]
"""
功能：触发器类型枚举。
   - enter_scene：进入场景时触发
   - observe：观察对象时触发
   - talk：与 NPC 对话时触发
   - inspect：检视物品/对象时触发
   - item_owned：持有指定物品时触发
   - quest_stage：任务到达指定阶段时触发
   - event：运行时结构化事件发生时触发，例如 action_resolved、scene_changed、item_consumed
"""

TriggerEffect = Literal["narrative", "grant_item", "set_flag", "update_quest", "move_entity"]
"""
功能：触发器效果类型枚举。
   - narrative：注入叙事文本
   - grant_item：授予物品
   - set_flag：设置标记位
   - update_quest：更新任务状态
   - move_entity：移动实体位置
"""


def _current_utc_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(UTC).isoformat()


class TriggerDef(BaseModel):
    """
    功能：触发器定义，描述触发条件与对应效果。
    入参：trigger_id（str）：触发器唯一标识；
        type（TriggerType）：触发器类型；
        label（str）：展示标签；
        description（str，默认 ""）：触发描述；
        effects（list[TriggerEffect]，默认 []）：触发时执行的效果列表；
        conditions（dict[str, Any]，默认 {}）：触发条件键值对；
        once（bool，默认 True）：是否仅触发一次；
        priority（int，默认 0）：优先级，数值越高越优先。
    出参：TriggerDef。
    异常：字段类型或值非法时由 Pydantic 抛出 ValidationError。
    """

    trigger_id: str
    type: TriggerType
    label: str
    description: str = ""
    effects: list[TriggerEffect] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    once: bool = True
    priority: int = 0


class TriggerEvent(BaseModel):
    """
    功能：运行时触发事件的 emit 契约，用于事件总线报告。
    入参：trigger_id（str）：触发器唯一标识；
        type（TriggerType）：触发器类型；
        label（str）：展示标签；
        description（str）：触发描述；
        effects（list[TriggerEffect]）：本次实际执行的效果列表；
        narrative_text（str，默认 ""）：pack 声明且已触发的玩家可见叙事文本；
        memory_text（str，默认 ""）：pack 声明且已触发的记忆摘要文本；
        event_name（str，默认 ""）：event 类型触发器命中的运行时事件名；
        timestamp（str）：触发时间，ISO 8601 UTC，默认当前时间。
    出参：TriggerEvent。
    异常：字段类型或值非法时由 Pydantic 抛出 ValidationError。
    """

    trigger_id: str
    type: TriggerType
    label: str
    description: str
    effects: list[TriggerEffect]
    narrative_text: str = ""
    memory_text: str = ""
    event_name: str = ""
    timestamp: str = Field(default_factory=_current_utc_iso)
