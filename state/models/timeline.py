"""
功能：定义世界时间线事件的状态模型。
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """
    功能：定义时间线事件的分类枚举。
    入参：无；枚举成员在类定义中声明。
    出参：EventType 枚举值，用于标记剧情、系统和世界事件类型。
    异常：不抛异常；非法枚举值由 Pydantic/Enum 校验阶段拒绝。
    """

    QUEST_UPDATE = "quest_update"
    WORLD_CHANGE = "world_change"
    SCRIPTED_SCENE = "scripted_scene"


class ScheduledEvent(BaseModel):
    """定时时间事件契约"""

    event_id: str = Field(..., description="事件 ID")
    trigger_at_minute: int = Field(..., description="在第几分钟触发")
    event_type: EventType = Field(..., description="事件类型")
    payload: dict[str, Any] = Field(..., description="事件携带的动作载荷")
    is_triggered: bool = Field(default=False, description="是否已触发")


class TimelineState(BaseModel):
    """全局时间轴进度契约"""

    total_turns: int = Field(default=0, description="行动总回合数")
    current_time_minutes: int = Field(default=0, description="当前全局时间（分钟）")
    active_timers: dict[str, int] = Field(
        default_factory=dict,
        description="激活的倒计时器: ID -> 剩余分钟",
    )
