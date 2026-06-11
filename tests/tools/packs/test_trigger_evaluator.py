"""
功能：覆盖 trigger evaluator 的回归测试。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from state.contracts.quest import QuestRuntimeState
from state.contracts.trigger import TriggerDef, TriggerType
from tools.packs.trigger_evaluator import (
    TriggerEvaluator,
    evaluate_triggers,
    load_trigger_defs,
)

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_case_root(name: str) -> Path:
    """在 test_runs 下创建测试专用目录。"""
    root = Path("test_runs") / f"{name}_{uuid.uuid4().hex[:8]}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _remove_case_root(root: Path) -> None:
    """清理测试专用目录。"""
    if root.exists():
        shutil.rmtree(root)


def _make_trigger_def(
    trigger_id: str,
    trigger_type: TriggerType,
    conditions: dict[str, Any] | None = None,
    once: bool = True,
    priority: int = 0,
    effects: list[str] | None = None,
) -> TriggerDef:
    """快速构建 TriggerDef 的辅助函数。"""
    return TriggerDef(
        trigger_id=trigger_id,
        type=trigger_type,
        label=f"Test: {trigger_id}",
        description=f"Auto-generated trigger {trigger_id}",
        conditions=conditions or {},
        once=once,
        priority=priority,
        effects=effects or ["narrative"],
    )


def _make_quest_state(quest_id: str, current_stage_id: str) -> QuestRuntimeState:
    """快速构建 QuestRuntimeState 的辅助函数。"""
    return QuestRuntimeState(
        quest_id=quest_id,
        status="active",
        current_stage_id=current_stage_id,
    )


# ---------------------------------------------------------------------------
# load_trigger_defs 测试
# ---------------------------------------------------------------------------


class TestLoadTriggerDefs:
    """load_trigger_defs 函数单元测试。"""

    def test_loads_real_demo_triggers(self):
        """从 demo_a2_core 加载真实触发器。"""
        pack_root = Path("examples/story_packs/demo_a2_core")
        triggers = load_trigger_defs(pack_root)
        assert len(triggers) >= 3
        ids = {t.trigger_id for t in triggers}
        assert "enter_forest_edge_intro" in ids
        assert "observe_forest_edge" in ids
        assert "inspect_camp_firepit" in ids

    def test_empty_triggers_dir(self):
        """trigger/ 目录为空时返回空列表。"""
        root = _make_case_root("empty_triggers")
        try:
            (root / "triggers").mkdir()
            triggers = load_trigger_defs(root)
            assert triggers == []
        finally:
            _remove_case_root(root)

    def test_no_triggers_dir(self):
        """无 trigger/ 目录时返回空列表。"""
        root = _make_case_root("no_triggers")
        try:
            triggers = load_trigger_defs(root)
            assert triggers == []
        finally:
            _remove_case_root(root)


# ---------------------------------------------------------------------------
# evaluate_triggers 测试
# ---------------------------------------------------------------------------


class TestTriggerEvaluatorState:
    """TriggerEvaluator 内部状态封装测试。"""

    def test_get_fired_ids_returns_copy(self) -> None:
        """
        功能：验证 get_fired_ids 返回副本，外部修改不污染评估器内部 once 状态。
        入参：无。
        出参：None。
        异常：断言失败表示触发器 once 状态可被外部误改。
        """
        td = _make_trigger_def("enter_test", "enter_scene", {"scene_id": "forest_edge"})
        evaluator = TriggerEvaluator([td])
        evaluator.evaluate("enter_scene", {"scene_id": "forest_edge"})

        fired_ids = evaluator.get_fired_ids()
        fired_ids.clear()

        assert evaluator.get_fired_ids() == {"enter_test"}


class TestEvaluateTriggersEnterScene:
    """enter_scene 类型触发器测试。"""

    def test_fires_when_scene_matches(self):
        """场景 ID 匹配时 enter_scene 触发。"""
        td = _make_trigger_def("enter_test", "enter_scene", {"scene_id": "forest_edge"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 1
        assert events[0].trigger_id == "enter_test"
        assert events[0].type == "enter_scene"

    def test_event_carries_narrative_and_memory_text(self) -> None:
        """
        功能：验证命中的 trigger 会把 conditions 中的叙事与记忆文本带入事件。
        入参：无。
        出参：None。
        异常：断言失败表示 Story Pack 叙事事实没有进入运行时事件。
        """
        td = _make_trigger_def(
            "enter_with_text",
            "enter_scene",
            {
                "scene_id": "forest_edge",
                "narrative_text": "雾林边缘的路标亮起一行新字。",
                "memory_text": "玩家看见雾林路标的新字。",
            },
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 1
        assert events[0].narrative_text == "雾林边缘的路标亮起一行新字。"
        assert events[0].memory_text == "玩家看见雾林路标的新字。"

    def test_does_not_fire_when_scene_mismatches(self):
        """场景 ID 不匹配时 enter_scene 不触发。"""
        td = _make_trigger_def("enter_test", "enter_scene", {"scene_id": "forest_edge"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="old_camp",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 0


class TestEvaluateTriggersObserve:
    """observe 类型触发器测试。"""

    def test_fires_when_observe_action_with_matching_scene(self):
        """observe 动作 + 匹配 scene_id 时触发。"""
        td = _make_trigger_def("obs_edge", "observe", {"scene_id": "forest_edge"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={"action": "observe"},
            quest_states=[],
        )
        assert len(events) == 1
        assert events[0].trigger_id == "obs_edge"

    def test_does_not_fire_for_non_observe_action(self):
        """非 observe 动作不触发 observe 类型触发器。"""
        td = _make_trigger_def("obs_edge", "observe", {"scene_id": "forest_edge"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={"action": "talk"},
            quest_states=[],
        )
        assert len(events) == 0

    def test_fires_with_interaction_id_match(self):
        """observe 动作 + 匹配 interaction_id 时触发。"""
        td = _make_trigger_def(
            "obs_firepit",
            "observe",
            {"scene_id": "old_camp", "interaction_id": "look_firepit"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="old_camp",
            action_result={"action": "observe", "interaction_id": "look_firepit"},
            quest_states=[],
        )
        assert len(events) == 1


class TestEvaluateTriggersInspect:
    """inspect 类型触发器测试。"""

    def test_fires_with_inspect_and_matching_interaction(self):
        """inspect 动作 + 匹配 interaction_id + scene_id 时触发。"""
        td = _make_trigger_def(
            "insp_firepit",
            "inspect",
            {"scene_id": "old_camp", "interaction_id": "inspect_firepit"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="old_camp",
            action_result={"action": "inspect", "interaction_id": "inspect_firepit"},
            quest_states=[],
        )
        assert len(events) == 1
        assert events[0].trigger_id == "insp_firepit"

    def test_does_not_fire_when_interaction_id_mismatches(self):
        """inspect 动作但 interaction_id 不匹配时不触发。"""
        td = _make_trigger_def(
            "insp_firepit",
            "inspect",
            {"scene_id": "old_camp", "interaction_id": "inspect_firepit"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="old_camp",
            action_result={"action": "inspect", "interaction_id": "inspect_altar"},
            quest_states=[],
        )
        assert len(events) == 0


class TestEvaluateTriggersTalk:
    """talk 类型触发器测试。"""

    def test_fires_with_talk_and_matching_interaction(self):
        """talk 动作 + 匹配 interaction_id 时触发。"""
        td = _make_trigger_def(
            "talk_guard",
            "talk",
            {"scene_id": "ruins_gate", "interaction_id": "talk_guard"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="ruins_gate",
            action_result={"action": "talk", "interaction_id": "talk_guard"},
            quest_states=[],
        )
        assert len(events) == 1
        assert events[0].trigger_id == "talk_guard"


class TestEvaluateTriggersItemOwned:
    """item_owned 类型触发器测试。"""

    def test_fires_when_item_in_inventory(self):
        """物品在背包中时触发。"""
        td = _make_trigger_def("has_sword", "item_owned", {"item_id": "rusty_sword"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={"active_character": {"inventory": ["rusty_sword", "health_potion"]}},
            current_scene_id="",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 1

    def test_does_not_fire_when_item_not_in_inventory(self):
        """物品不在背包中时不触发。"""
        td = _make_trigger_def("has_sword", "item_owned", {"item_id": "rusty_sword"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={"active_character": {"inventory": ["health_potion"]}},
            current_scene_id="",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 0


class TestEvaluateTriggersQuestStage:
    """quest_stage 类型触发器测试。"""

    def test_fires_when_quest_at_matching_stage(self):
        """任务处于匹配阶段时触发。"""
        td = _make_trigger_def(
            "q_progress",
            "quest_stage",
            {"quest_id": "find_key", "quest_stage": "collected_key"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="",
            action_result={},
            quest_states=[_make_quest_state("find_key", "collected_key")],
        )
        assert len(events) == 1

    def test_does_not_fire_when_quest_stage_mismatches(self):
        """任务阶段不匹配时不触发。"""
        td = _make_trigger_def(
            "q_progress",
            "quest_stage",
            {"quest_id": "find_key", "quest_stage": "collected_key"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="",
            action_result={},
            quest_states=[_make_quest_state("find_key", "start")],
        )
        assert len(events) == 0

    def test_does_not_fire_when_quest_id_mismatches(self):
        """任务 ID 不匹配时不触发。"""
        td = _make_trigger_def(
            "q_progress",
            "quest_stage",
            {"quest_id": "find_key", "quest_stage": "collected_key"},
        )
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="",
            action_result={},
            quest_states=[_make_quest_state("other_quest", "collected_key")],
        )
        assert len(events) == 0


class TestEvaluateTriggersEvent:
    """event 类型触发器测试。"""

    def test_event_trigger_fires_on_matching_runtime_event(self) -> None:
        """
        功能：验证 event 触发器可通过 runtime_events 中的结构化事件命中。
        入参：无。
        出参：None。
        异常：断言失败表示 Story Pack event 触发器未进入统一评估入口。
        """
        td = _make_trigger_def(
            "evt_inspect_notice",
            "event",
            {
                "match": {
                    "event_name": "action_resolved",
                    "scene_id": "ferry_landing",
                    "action": "inspect",
                    "target_id": "tide_notice",
                },
                "narrative_text": "潮汐告示背面渗出一行旧墨。",
                "memory_text": "玩家检视潮汐告示并发现旧墨。",
            },
        )

        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="ferry_landing",
            action_result={},
            quest_states=[],
            runtime_events=[
                {
                    "event_name": "action_resolved",
                    "scene_id": "ferry_landing",
                    "action": "inspect",
                    "target_id": "tide_notice",
                }
            ],
        )

        assert len(events) == 1
        assert events[0].trigger_id == "evt_inspect_notice"
        assert events[0].type == "event"
        assert events[0].event_name == "action_resolved"
        assert events[0].narrative_text == "潮汐告示背面渗出一行旧墨。"
        assert events[0].memory_text == "玩家检视潮汐告示并发现旧墨。"

    def test_event_trigger_match_block_ignores_root_effect_params(self) -> None:
        """
        功能：验证 event 触发器的 match 块与根级 effect 参数互不干扰。
        入参：无。
        出参：None。
        异常：断言失败表示 quest/effect 参数会误参与事件条件匹配。
        """
        td = _make_trigger_def(
            "evt_scene_changed_updates_quest",
            "event",
            {
                "match": {
                    "event_name": "scene_changed",
                    "from_scene_id": "ferry_landing",
                    "to_scene_id": "red_lantern_lane",
                },
                "quest_id": "follow_red_lantern",
                "target_stage_id": "arrived_lane",
            },
            effects=["update_quest"],
        )

        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="ferry_landing",
            action_result={},
            quest_states=[],
            runtime_events=[
                {
                    "event_name": "scene_changed",
                    "from_scene_id": "ferry_landing",
                    "to_scene_id": "red_lantern_lane",
                }
            ],
        )

        assert len(events) == 1
        assert events[0].trigger_id == "evt_scene_changed_updates_quest"

    def test_event_trigger_does_not_fire_when_match_value_mismatches(self) -> None:
        """
        功能：验证 event 触发器对 match 内字段执行精确匹配。
        入参：无。
        出参：None。
        异常：断言失败表示错误动作也会触发事件型触发器。
        """
        td = _make_trigger_def(
            "evt_inspect_only",
            "event",
            {
                "match": {
                    "event_name": "action_resolved",
                    "action": "inspect",
                }
            },
        )

        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="ferry_landing",
            action_result={},
            quest_states=[],
            runtime_events=[{"event_name": "action_resolved", "action": "talk"}],
        )

        assert len(events) == 0


# ---------------------------------------------------------------------------
# Once 语义测试
# ---------------------------------------------------------------------------


class TestOnceSemantics:
    """once 语义测试。"""

    def test_once_true_does_not_fire_again(self):
        """once=True 的触发器只触发一次。"""
        td = _make_trigger_def("once_entry", "enter_scene", {"scene_id": "forest_edge"}, once=True)
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={"fired_trigger_ids": ["once_entry"]},
            current_scene_id="forest_edge",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 0

    def test_once_false_fires_again(self):
        """once=False 的触发器可以多次触发。"""
        td = _make_trigger_def("repeatable_obs", "observe", {"scene_id": "forest_edge"}, once=False)
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={"fired_trigger_ids": ["repeatable_obs"]},
            current_scene_id="forest_edge",
            action_result={"action": "observe"},
            quest_states=[],
        )
        assert len(events) == 1


# ---------------------------------------------------------------------------
# 优先级排序测试
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    """触发器优先级排序测试。"""

    def test_higher_priority_fires_first(self):
        """priority 更高的触发器在结果列表中排在前面。"""
        td_low = _make_trigger_def("low", "enter_scene", {"scene_id": "forest_edge"}, priority=1)
        td_high = _make_trigger_def("high", "enter_scene", {"scene_id": "forest_edge"}, priority=10)
        events = evaluate_triggers(
            trigger_defs=[td_low, td_high],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 2
        # priority 高的应排在前面
        assert events[0].trigger_id == "high"
        assert events[1].trigger_id == "low"


# ---------------------------------------------------------------------------
# 多类型混合触发测试
# ---------------------------------------------------------------------------


class TestMixedTriggerTypes:
    """一次 evaluate_triggers 调用中多种类型同时触发。"""

    def test_enter_scene_and_observe_both_fire(self):
        """进入场景 + observe 动作，两类触发器同时触发。"""
        td_enter = _make_trigger_def("enter_1", "enter_scene", {"scene_id": "forest_edge"})
        td_obs = _make_trigger_def("obs_1", "observe", {"scene_id": "forest_edge"})
        events = evaluate_triggers(
            trigger_defs=[td_enter, td_obs],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={"action": "observe"},
            quest_states=[],
        )
        assert len(events) == 2
        types = {e.type for e in events}
        assert "enter_scene" in types
        assert "observe" in types


# ---------------------------------------------------------------------------
# TriggerEvent 结构验证
# ---------------------------------------------------------------------------


class TestTriggerEventStructure:
    """TriggerEvent 产出结构验证。"""

    def test_event_has_all_required_fields(self):
        """TriggerEvent 包含 trigger_id, type, label, description, effects, timestamp。"""
        td = _make_trigger_def("struct_test", "enter_scene", {"scene_id": "forest_edge"})
        events = evaluate_triggers(
            trigger_defs=[td],
            session_metadata={},
            current_scene_id="forest_edge",
            action_result={},
            quest_states=[],
        )
        assert len(events) == 1
        event = events[0]
        assert event.trigger_id == "struct_test"
        assert event.type == "enter_scene"
        assert event.label
        assert event.description
        assert isinstance(event.effects, list)
        assert event.timestamp  # ISO 8601 时间戳
        # timestamp 应为有效 ISO 8601 格式
        assert "T" in event.timestamp
