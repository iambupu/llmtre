"""
功能：覆盖 nlu priority a1 的回归测试。
"""

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


def test_nlu_parse_scene_talk_interactable_target_ref() -> None:
    """
    功能：验证 Story Pack talk interactable 的按钮文案能解析出 target_ref，避免 UI 点击后进入澄清。
    入参：无。
    出参：None。
    异常：断言失败表示场景交互目标没有进入 NLU talk 候选。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    scene_snapshot = {
        "interactables": [
            {
                "interaction_id": "talk_ranger_ella",
                "kind": "talk",
                "label": "呼唤巡林人艾拉",
                "target_ref": "ranger_ella",
                "aliases": ["艾拉", "巡林人"],
            }
        ],
        "visible_npcs": [{"entity_id": "ranger_ella", "label": "巡林人艾拉"}],
        "affordances": [
            {
                "action_type": "talk",
                "label": "呼唤巡林人艾拉",
                "target_id": "ranger_ella",
                "user_input": "呼唤巡林人艾拉",
            }
        ],
    }

    parsed = agent.parse(
        "呼唤巡林人艾拉",
        context={"id": "player_01", "scene_snapshot": scene_snapshot},
    )

    assert parsed is not None
    assert parsed["type"] == "talk"
    assert parsed["target_id"] == "ranger_ella"


def test_nlu_parse_prefers_explicit_inspect_over_talk_alias_overlap() -> None:
    """
    功能：验证玩家明确使用“检查”动词时，inspect 交互优先于命中同一别名的 talk 交互。
    入参：无。
    出参：None。
    异常：断言失败表示可检查物会被 NPC 交谈别名抢走，导致剧本触发器漏判。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    scene_snapshot = {
        "interactables": [
            {
                "interaction_id": "talk_lantern_keeper_mo",
                "kind": "talk",
                "label": "询问守灯人莫婶",
                "target_ref": "lantern_keeper_mo",
                "aliases": ["莫婶", "守灯人", "灯绳"],
            },
            {
                "interaction_id": "inspect_lantern_knots",
                "kind": "inspect",
                "label": "检查反复打结的灯绳",
                "target_ref": "lantern_knots",
                "aliases": ["灯绳", "绳结", "赤灯"],
            },
        ],
        "visible_npcs": [{"entity_id": "lantern_keeper_mo", "label": "守灯人莫婶"}],
        "visible_items": [{"id": "lantern_knots", "label": "反复打结的灯绳"}],
    }

    parsed = agent.parse(
        "检查反复打结的灯绳",
        context={"id": "player_01", "scene_snapshot": scene_snapshot},
    )

    assert parsed is not None
    assert parsed["type"] == "inspect"
    assert parsed["parameters"]["object_hint"] == "反复打结的灯绳"


def test_nlu_parse_move_not_stolen_by_inspect_scene_alias() -> None:
    """
    功能：验证无检查动词的移动句优先按出口解析，不会被场景可检查物短别名截获。
    入参：无。
    出参：None。
    异常：断言失败表示移动出口会被 inspect 交互抢走，导致 UI 按钮无法换场景。
    """
    agent = NLUAgent(rules=_build_priority_test_rules())
    scene_snapshot = {
        "exits": [
            {
                "location_id": "tide_cellar",
                "label": "沿钟后石阶进入潮下水窖",
                "aliases": ["水窖", "石阶", "潮下"],
            }
        ],
        "interactables": [
            {
                "interaction_id": "inspect_silent_bell",
                "kind": "inspect",
                "label": "检查静默潮钟",
                "target_ref": "silent_bell",
                "aliases": ["潮钟", "钟", "红线"],
            }
        ],
    }

    parsed = agent.parse(
        "沿钟后石阶进入潮下水窖",
        context={"id": "player_01", "scene_snapshot": scene_snapshot},
    )

    assert parsed is not None
    assert parsed["type"] == "move"
    assert parsed["parameters"]["location_id"] == "tide_cellar"
