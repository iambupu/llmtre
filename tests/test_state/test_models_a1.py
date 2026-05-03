from __future__ import annotations

import pytest
from pydantic import ValidationError

from state.models.action import ActionTemplate
from state.models.entity import EntityTemplate
from state.models.item import ItemTemplate
from state.models.quest import (
    EvaluatorType,
    ObjectiveEvaluator,
    QuestObjective,
    QuestStage,
    QuestTemplate,
)
from state.models.timeline import ScheduledEvent, TimelineState
from state.models.world import LocationTemplate, WorldState


def test_item_template_defaults_and_effects_schema() -> None:
    """
    功能：验证 ItemTemplate 默认值与 effects 子结构可被正确解析。
    入参：无。
    出参：None。
    异常：断言失败表示物品契约默认值或子结构映射退化。
    """
    item = ItemTemplate.model_validate(
        {
            "item_id": "potion_01",
            "name": "治疗药水",
            "description": "恢复少量生命。",
            "item_type": "consumable",
            "effects": [{"target_attribute": "hp", "value": 10}],
        }
    )
    assert item.weight == 0.0
    assert item.rarity == "common"
    assert item.usage_limit == -1
    assert item.is_stackable is False
    assert item.requirements.min_strength == 0
    assert item.effects[0].target_attribute == "hp"


def test_entity_template_defaults_and_required_resources() -> None:
    """
    功能：验证 EntityTemplate 默认值注入与 resources 必填约束。
    入参：无。
    出参：None。
    异常：断言失败表示实体契约默认值或必填校验退化。
    """
    entity = EntityTemplate.model_validate(
        {
            "entity_id": "npc_01",
            "name": "守卫",
            "entity_type": "npc",
            "description": "城门守卫。",
            "resources": {"hp": 12, "max_hp": 12, "mp": 3, "max_mp": 3},
        }
    )
    assert entity.base_stats.strength == 10
    assert entity.current_location_id == "unknown"
    assert entity.behavior_pattern == "neutral"
    assert entity.traits == []
    with pytest.raises(ValidationError):
        EntityTemplate.model_validate(
            {
                "entity_id": "npc_02",
                "name": "缺资源实体",
                "entity_type": "npc",
                "description": "bad",
            }
        )


def test_action_template_enum_and_defaults() -> None:
    """
    功能：验证 ActionTemplate 枚举校验与默认字段注入。
    入参：无。
    出参：None。
    异常：断言失败表示行为契约校验规则退化。
    """
    action = ActionTemplate.model_validate(
        {
            "action_id": "act_move_01",
            "name": "前进",
            "action_type": "move",
            "trigger_description": "向前走",
        }
    )
    assert action.pre_conditions == []
    assert action.success_effects == []
    assert action.mana_cost == 0
    with pytest.raises(ValidationError):
        ActionTemplate.model_validate(
            {
                "action_id": "act_bad_01",
                "name": "非法动作",
                "action_type": "teleport",
                "trigger_description": "瞬移",
            }
        )


def test_quest_template_nested_defaults() -> None:
    """
    功能：验证 QuestTemplate 的嵌套结构与默认值（is_mandatory/is_completed）。
    入参：无。
    出参：None。
    异常：断言失败表示任务模板嵌套契约退化。
    """
    evaluator = ObjectiveEvaluator(
        evaluator_type=EvaluatorType.DETERMINISTIC,
        condition="hp >= 1",
    )
    objective = QuestObjective(
        objective_id="obj_01",
        description="与守卫对话",
        evaluator=evaluator,
    )
    stage = QuestStage(
        stage_id="stage_01",
        name="开场",
        description="任务开始",
        objectives=[objective],
    )
    quest = QuestTemplate(
        quest_id="quest_01",
        name="初始任务",
        description="测试任务",
        stages=[stage],
    )
    assert quest.prerequisites == {}
    assert quest.rewards == []
    assert quest.stages[0].objectives[0].is_mandatory is True
    assert quest.stages[0].objectives[0].is_completed is False


def test_timeline_models_defaults_and_event_type_validation() -> None:
    """
    功能：验证 TimelineState 默认值与 ScheduledEvent 枚举校验。
    入参：无。
    出参：None。
    异常：断言失败表示时间轴模型约束退化。
    """
    timeline = TimelineState()
    assert timeline.total_turns == 0
    assert timeline.current_time_minutes == 0
    assert timeline.active_timers == {}
    event = ScheduledEvent.model_validate(
        {
            "event_id": "evt_01",
            "trigger_at_minute": 15,
            "event_type": "world_change",
            "payload": {"weather": "rain"},
        }
    )
    assert event.is_triggered is False
    with pytest.raises(ValidationError):
        ScheduledEvent.model_validate(
            {
                "event_id": "evt_bad",
                "trigger_at_minute": 1,
                "event_type": "invalid_type",
                "payload": {},
            }
        )


def test_world_models_defaults() -> None:
    """
    功能：验证 LocationTemplate 与 WorldState 默认值字段。
    入参：无。
    出参：None。
    异常：断言失败表示世界模型默认值语义退化。
    """
    location = LocationTemplate(
        location_id="loc_01",
        name="林间小道",
        description="潮湿且阴暗。",
    )
    world = WorldState()
    assert location.connected_locations == []
    assert location.environmental_tags == []
    assert location.status_effects == {}
    assert world.current_time_minutes == 0
    assert world.global_flags == {}
    assert world.weather == "clear"
