from __future__ import annotations

from agents.gm_agent import GMAgent


def test_gm_prompt_includes_turn_context_for_repeated_input() -> None:
    """
    功能：验证 GM 叙事提示词包含回合上下文，避免相同输入跨回合复用旧响应。
    入参：无，使用内联状态夹具。
    出参：None，通过断言表达期望。
    异常：断言失败表示 prompt 丢失 user_input、turn_id 或 recent_memory。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    prompt = agent._build_llm_prompt(  # noqa: SLF001
        {
            "turn_id": 7,
            "user_input": "观察周围",
            "is_valid": True,
            "turn_outcome": "valid_action",
            "action_intent": {"type": "observe"},
            "physics_diff": {"state_flags_add": ["observed_surroundings"]},
            "active_character": {"id": "player_01", "location": "camp"},
            "scene_snapshot": {
                "current_location": {"id": "camp", "name": "营地"},
                "recent_memory": "第6回合：输入[观察周围] -> 响应[你发现了新的脚印]",
            },
        }
    )

    assert '"turn_id": 7' in prompt
    assert '"user_input": "观察周围"' in prompt
    assert "第6回合" in prompt
    assert "复用旧响应" in prompt


def test_quick_action_prompt_includes_turn_context_for_repeated_input() -> None:
    """
    功能：验证快捷行动生成提示词包含回合上下文，避免相同输入下复用旧选项。
    入参：无，使用内联状态夹具。
    出参：None，通过断言表达期望。
    异常：断言失败表示 quick_actions prompt 未纳入回合上下文。
    """
    agent = GMAgent(event_bus=None, rules={"narrative_templates": {}})
    prompt = agent._build_quick_actions_prompt(  # noqa: SLF001
        {
            "turn_id": 8,
            "user_input": "观察周围",
            "scene_snapshot": {
                "current_location": {"id": "camp", "name": "营地"},
                "recent_memory": "上一回合已经观察过火堆。",
                "exits": [],
                "visible_npcs": [],
                "visible_items": [],
            },
        },
        "你又一次观察营地，这次注意到灰烬旁的新痕迹。",
    )

    assert '"turn_id": 8' in prompt
    assert '"user_input": "观察周围"' in prompt
    assert "上一回合已经观察过火堆" in prompt
