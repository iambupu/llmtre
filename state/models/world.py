from typing import Any

from pydantic import BaseModel, Field


class LocationTemplate(BaseModel):
    """场景/地点契约"""
    location_id: str = Field(..., description="场景唯一标识符")
    name: str = Field(..., description="场景名称")
    description: str = Field(..., description="场景的叙事描述（光照、气味、氛围）")
    connected_locations: list[str] = Field(
        default_factory=list,
        description="逻辑上相邻的场景 ID 列表",
    )

    # 场景属性与状态
    environmental_tags: list[str] = Field(
        default_factory=list,
        description="环境标签，如 'dark', 'wet'，用于影响判定",
    )
    status_effects: dict[str, Any] = Field(
        default_factory=dict,
        description="当前场景的状态，如 {'on_fire': True, 'alert_level': 2}"
    )

class WorldState(BaseModel):
    """全局世界状态契约"""
    current_time_minutes: int = Field(default=0, description="游戏内流逝的时间（单位：分钟）")
    global_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="全局布尔事件标记（如 is_dragon_dead: True）",
    )
    weather: str = Field(default="clear", description="当前全局天气")

