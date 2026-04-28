import json
from pathlib import Path

from agents.nlu_agent import NLUAgent
from agents.nlu_schema import normalize_action_candidate
from game_workflows.main_loop_config import load_main_loop_rules


def _context() -> dict[str, object]:
    return {
        "id": "player_01",
        "scene_snapshot": {
            "current_location": {
                "id": "unknown",
                "name": "无名道路",
                "description": "道路向森林延伸。",
            },
            "exits": [
                {
                    "direction": "forward",
                    "location_id": "forest_edge",
                    "label": "森林边缘",
                    "aliases": ["森林", "前方", "路上", "继续"],
                }
            ],
            "visible_npcs": [{"entity_id": "goblin_01", "name": "瘦弱的地精"}],
            "visible_items": [],
            "active_quests": [],
            "recent_memory": "",
            "available_actions": ["observe", "wait", "move", "talk"],
            "suggested_actions": ["看看周围", "继续前进"],
        },
    }


def test_nlu_eval_fixtures_match_rule_first_behavior() -> None:
    """
    功能：验证 NLU eval 样例在规则优先路径下保持稳定输出。
    入参：无。
    出参：None。
    异常：断言失败表示 NLU prompt/eval 基线发生回归。
    """
    agent = NLUAgent(rules=load_main_loop_rules())
    agent.llm_enabled = False
    fixture_path = Path("tests/fixtures/nlu_eval_cases.json")
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        parsed = agent.parse(str(case["input"]), context=_context())
        assert parsed is not None, case["name"]
        assert parsed["type"] == case["expected_type"], case["name"]
        assert bool(parsed["needs_clarification"]) is bool(case["expected_clarification"])
        expected_location_id = case.get("expected_location_id")
        if expected_location_id:
            assert parsed["parameters"]["location_id"] == expected_location_id


def test_nlu_schema_rejects_invalid_llm_payload() -> None:
    """
    功能：验证 LLM 非法动作类型会被 schema 层拒绝，不能进入主循环结算。
    入参：无。
    出参：None。
    异常：断言失败表示 schema 强校验失效。
    """
    parsed = normalize_action_candidate(
        {"type": "teleport", "parameters": {"location_id": "forbidden"}},
        raw_input="传送到城堡",
        actor_id="player_01",
    )

    assert parsed is None


def test_nlu_schema_keeps_clarification_payload() -> None:
    """
    功能：验证 LLM 澄清候选可以保留问题并交给主循环澄清路由。
    入参：无。
    出参：None。
    异常：断言失败表示澄清字段被 schema 清洗丢失。
    """
    parsed = normalize_action_candidate(
        {
            "type": "move",
            "parameters": {"location_id": "unknown"},
            "confidence": 0.4,
            "needs_clarification": True,
            "clarification_question": "你想往哪条路走？",
        },
        raw_input="继续",
        actor_id="player_01",
    )

    assert parsed is not None
    assert parsed["needs_clarification"] is True
    assert parsed["clarification_question"] == "你想往哪条路走？"
