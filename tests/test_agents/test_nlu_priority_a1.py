from __future__ import annotations

from agents.nlu_agent import NLUAgent


def _build_priority_test_rules() -> dict[str, object]:
    """
    功能：构造最小化 NLU 规则，显式制造关键词重叠以验证 parse 分支优先级。
    入参：无。
    出参：dict[str, object]，仅包含本测试所需的 nlu 配置。
    异常：不抛异常。
    """
    return {
        "nlu": {
            "action_keywords": {
                "commit_sandbox": ["提交"],
                "discard_sandbox": ["放弃"],
                "use_item": ["使用", "喝下"],
                "attack": ["攻击"],
                "talk": ["交谈", "问"],
                "move": ["前进", "靠近", "走"],
                "inspect": ["检查"],
                "interact": ["互动"],
                "rest": ["休息"],
                "wait": ["等待"],
                "observe": ["观察"],
            },
            "target_aliases": {"goblin_01": ["地精"], "guard_01": ["守卫"]},
            "location_aliases": {},
            "item_aliases": {"potion_001": ["药水"]},
        }
    }


def test_nlu_parse_prefers_commit_over_other_keywords() -> None:
    """
    功能：验证同一句输入同时命中提交与移动关键词时，parse 会优先走 commit_sandbox 分支。
    入参：无。
    出参：None。
    异常：断言失败表示 A1 约定的高优先级分支顺序回归。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    parsed = agent.parse("提交并前进", context={"id": "player_01"})

    assert parsed is not None
    assert parsed["type"] == "commit_sandbox"


def test_nlu_parse_keeps_inspect_potion_as_inspect() -> None:
    """
    功能：验证“检查药水”按 A1 冲突矩阵识别为 inspect，而不是 use_item。
    入参：无。
    出参：None。
    异常：断言失败表示物品动作优先级过宽，可能把检查误判为使用。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    parsed = agent.parse("检查药水", context={"id": "player_01"})

    assert parsed is not None
    assert parsed["type"] == "inspect"
    assert parsed["parameters"]["intent"] == "inspect"


def test_nlu_parse_drink_potion_as_use_item() -> None:
    """
    功能：验证“喝下药水”按 A1 冲突矩阵识别为 use_item 并提取 item_id。
    入参：无。
    出参：None。
    异常：断言失败表示物品使用动作无法稳定命中。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    parsed = agent.parse("喝下药水", context={"id": "player_01"})

    assert parsed is not None
    assert parsed["type"] == "use_item"
    assert parsed["parameters"]["item_id"] == "potion_001"


def test_nlu_parse_prefers_attack_over_move_when_approaching_enemy() -> None:
    """
    功能：验证“靠近地精并攻击”优先识别为 attack，而不是 move。
    入参：无。
    出参：None。
    异常：断言失败表示攻击与移动关键词冲突优先级回归。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    parsed = agent.parse("靠近地精并攻击", context={"id": "player_01"})

    assert parsed is not None
    assert parsed["type"] == "attack"
    assert parsed["target_id"] == "goblin_01"


def test_nlu_parse_prefers_talk_over_move_for_direction_question() -> None:
    """
    功能：验证“问守卫森林怎么走”优先识别为 talk，而不是 move。
    入参：无。
    出参：None。
    异常：断言失败表示社交询问被错误解析为移动。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    parsed = agent.parse("问守卫森林怎么走", context={"id": "player_01"})

    assert parsed is not None
    assert parsed["type"] == "talk"
    assert parsed["target_id"] == "guard_01"
    assert parsed["parameters"]["topic"] == "问守卫森林怎么走"
