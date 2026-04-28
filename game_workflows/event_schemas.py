from typing import Any

from llama_index.core.workflow import Event


class StateChangedEvent(Event):
    """当主流程中的状态发生变更时抛出"""
    entity_id: str
    diff: dict[str, Any]
    is_sandbox: bool = False

class TurnEndedEvent(Event):
    """当一轮主循环结束时抛出"""
    turn_id: int
    user_input: str
    final_response: str

class AchievementUnlockedEvent(Event):
    """当达成某项成就时抛出"""
    achievement_id: str
    entity_id: str
    description: str

class WorldEvolutionEvent(Event):
    """触发世界后台演化的事件"""
    time_passed_minutes: int
    location_id: str | None = None
