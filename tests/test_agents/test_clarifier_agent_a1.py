from __future__ import annotations

from agents.clarifier_agent import ClarifierAgent
from state.contracts.agent import AgentEnvelope


def test_clarifier_builds_move_question_and_candidates_from_scene() -> None:
    """
    功能：验证 Clarifier 在 move 缺参时会输出出口导向的澄清问题，并返回可执行候选输入。
    入参：无。
    出参：None。
    异常：断言失败表示 Clarifier 的澄清主路径或候选动作提取逻辑回归。
    """
    agent = ClarifierAgent()
    envelope = AgentEnvelope(
        trace_id="trace-a1",
        turn_id=0,
        sender="main_loop",
        recipient="clarifier",
        kind="clarify.request",
        payload={
            "action_intent": {"type": "move", "parameters": {"location_id": "unknown"}},
            "validation_errors": ["目标地点不在当前场景出口中"],
            "scene_snapshot": {
                "exits": [{"label": "森林边缘", "location_id": "forest_edge"}],
                "affordances": [
                    {"enabled": True, "user_input": "前往森林边缘"},
                    {"enabled": True, "user_input": "观察周围"},
                ],
            },
        },
    )

    response = agent.clarify(envelope)

    assert response.kind == "clarify.response"
    assert "你想往哪个方向走" in response.payload["clarification_question"]
    assert response.payload["failure_reason"] == "目标地点不在当前场景出口中"
    assert response.payload["candidate_inputs"][0] == "前往森林边缘"
    assert response.payload["suggested_next_step"] == "前往森林边缘"


def test_clarifier_falls_back_when_scene_affordances_missing() -> None:
    """
    功能：验证 Clarifier 在场景候选缺失时会降级到保底候选与通用失败原因，避免澄清链路中断。
    入参：无。
    出参：None。
    异常：断言失败表示 Clarifier 降级路径失效。
    """
    agent = ClarifierAgent()
    envelope = AgentEnvelope(
        trace_id="trace-a1",
        turn_id=0,
        sender="main_loop",
        recipient="clarifier",
        kind="clarify.request",
        payload={
            "action_intent": {"type": "inspect", "parameters": {}},
            "validation_errors": [],
            "scene_snapshot": {},
        },
    )

    response = agent.clarify(envelope)

    assert response.payload["clarification_question"] == "你想检查或互动哪个对象？"
    assert response.payload["failure_reason"] == "行动信息还不够明确。"
    assert response.payload["candidate_inputs"] == ["观察周围", "检查周围", "等待片刻", "短暂休息"]
    assert response.payload["suggested_next_step"] == "观察周围"


def test_clarifier_builds_target_question_for_talk_or_attack() -> None:
    """
    功能：验证 Clarifier 在 talk/attack 缺目标时会列出当前可见目标。
    入参：无。
    出参：None。
    异常：断言失败表示目标缺参澄清问题或候选降级失效。
    """
    agent = ClarifierAgent()
    envelope = AgentEnvelope(
        trace_id="trace-a1",
        turn_id=0,
        sender="main_loop",
        recipient="clarifier",
        kind="clarify.request",
        payload={
            "action_intent": {"type": "attack", "target_id": None, "parameters": {}},
            "validation_errors": ["缺少目标"],
            "scene_snapshot": {
                "visible_npcs": [
                    {"entity_id": "goblin_01", "name": "地精"},
                    {"entity_id": "guard_01", "name": "守卫"},
                ],
                "affordances": [
                    {"enabled": True, "user_input": "攻击地精"},
                    {"enabled": True, "user_input": "和守卫交谈"},
                ],
            },
        },
    )

    response = agent.clarify(envelope)

    assert (
        response.payload["clarification_question"]
        == "你想攻击哪个目标？当前可见目标：地精、守卫。"
    )
    assert response.payload["candidate_inputs"][0] == "攻击地精"


def test_clarifier_builds_item_question_from_use_item_affordances() -> None:
    """
    功能：验证 Clarifier 在 use_item 缺物品时会列出可用物品候选。
    入参：无。
    出参：None。
    异常：断言失败表示物品缺参澄清未利用确定性 affordance。
    """
    agent = ClarifierAgent()
    envelope = AgentEnvelope(
        trace_id="trace-a1",
        turn_id=0,
        sender="main_loop",
        recipient="clarifier",
        kind="clarify.request",
        payload={
            "action_intent": {"type": "use_item", "parameters": {"item_id": None}},
            "validation_errors": ["缺少物品"],
            "scene_snapshot": {
                "affordances": [
                    {
                        "action_type": "use_item",
                        "enabled": True,
                        "label": "使用治疗药水",
                        "user_input": "使用治疗药水",
                    }
                ],
            },
        },
    )

    response = agent.clarify(envelope)

    assert (
        response.payload["clarification_question"]
        == "你想使用哪个物品？当前可用物品：使用治疗药水。"
    )
    assert response.payload["candidate_inputs"][0] == "使用治疗药水"
