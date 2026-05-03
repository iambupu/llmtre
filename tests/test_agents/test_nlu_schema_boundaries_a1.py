from __future__ import annotations

from agents.nlu_schema import normalize_action_candidate


def test_normalize_action_candidate_returns_none_for_empty_or_non_dict_payload() -> None:
    """
    功能：验证空候选与非 dict 候选会降级为 None，不向主循环抛异常。
    入参：无。
    出参：None。
    异常：断言失败表示 NLU 候选输入边界回归。
    """
    assert normalize_action_candidate(None, raw_input="观察", actor_id="player_01") is None
    assert normalize_action_candidate("bad", raw_input="观察", actor_id="player_01") is None
    assert normalize_action_candidate(["bad"], raw_input="观察", actor_id="player_01") is None


def test_normalize_action_candidate_fills_defaults_and_strips_question() -> None:
    """
    功能：验证缺省 raw_input/actor_id/parameters/confidence 会被补齐，澄清问题会去空白。
    入参：无。
    出参：None。
    异常：断言失败表示 NLU 候选默认值清洗回归。
    """
    parsed = normalize_action_candidate(
        {
            "type": "observe",
            "needs_clarification": True,
            "clarification_question": "  你想观察哪里？  ",
        },
        raw_input="看看",
        actor_id="player_01",
    )

    assert parsed is not None
    assert parsed["raw_input"] == "看看"
    assert parsed["actor_id"] == "player_01"
    assert parsed["parameters"] == {}
    assert parsed["confidence"] == 1.0
    assert parsed["needs_clarification"] is True
    assert parsed["clarification_question"] == "你想观察哪里？"


def test_normalize_action_candidate_rejects_bad_field_types() -> None:
    """
    功能：验证 parameters、confidence、needs_clarification 等字段类型非法时返回 None。
    入参：无。
    出参：None。
    异常：断言失败表示 NLU schema 强校验回归。
    """
    assert (
        normalize_action_candidate(
            {"type": "observe", "parameters": "bad"},
            raw_input="观察",
            actor_id="player_01",
        )
        is None
    )
    assert (
        normalize_action_candidate(
            {"type": "observe", "confidence": 1.5},
            raw_input="观察",
            actor_id="player_01",
        )
        is None
    )
    assert (
        normalize_action_candidate(
            {"type": "observe", "needs_clarification": object()},
            raw_input="观察",
            actor_id="player_01",
        )
        is None
    )
