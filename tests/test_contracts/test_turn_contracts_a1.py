from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from state.contracts.turn import TurnResult, TurnTrace


def _build_min_turn_result_payload() -> dict[str, Any]:
    """
    功能：构造满足 A1 TurnResult 最小必填字段的测试载荷。
    入参：无。
    出参：dict[str, Any]，可直接用于 TurnResult.model_validate。
    异常：不抛异常；仅返回静态字典。
    """
    return {
        "session_id": "sess_a1_contract_01",
        "session_turn_id": 1,
        "runtime_turn_id": 1,
        "trace_id": "trc_a1_contract_01",
        "request_id": "req_a1_contract_01",
        "outcome": "valid_action",
        "is_valid": True,
        "final_response": "你观察了周围。",
    }


def test_turn_result_accepts_a1_outcome_enum_values() -> None:
    """
    功能：验证 TurnResult 仅接受 A1 三态 outcome 中的合法值。
    入参：无。
    出参：None。
    异常：断言失败表示 outcome 枚举契约失效。
    """
    allowed = ["valid_action", "clarification", "invalid"]
    for outcome in allowed:
        payload = _build_min_turn_result_payload()
        payload["outcome"] = outcome
        payload["should_advance_turn"] = outcome == "valid_action"
        payload["should_write_story_memory"] = outcome == "valid_action"
        validated = TurnResult.model_validate(payload)
        assert validated.outcome == outcome


def test_turn_result_rejects_invalid_outcome_value() -> None:
    """
    功能：验证 TurnResult 对非法 outcome 触发 ValidationError。
    入参：无。
    出参：None。
    异常：断言失败表示非法 outcome 被错误放行。
    """
    payload = _build_min_turn_result_payload()
    payload["outcome"] = "success"
    with pytest.raises(ValidationError):
        TurnResult.model_validate(payload)


def test_turn_result_rejects_outcome_flag_mismatch() -> None:
    """
    功能：验证 outcome 与推进、剧情记忆写入标志必须保持 A1 契约一致。
    入参：无。
    出参：None。
    异常：断言失败表示 clarification/invalid 可能误推进世界或 valid_action 被错误阻断。
    """
    valid_payload = _build_min_turn_result_payload()
    valid_payload["should_advance_turn"] = False
    valid_payload["should_write_story_memory"] = True
    with pytest.raises(ValidationError):
        TurnResult.model_validate(valid_payload)

    clarification_payload = _build_min_turn_result_payload()
    clarification_payload["outcome"] = "clarification"
    clarification_payload["should_advance_turn"] = True
    clarification_payload["should_write_story_memory"] = False
    with pytest.raises(ValidationError):
        TurnResult.model_validate(clarification_payload)


def test_turn_trace_requires_core_fields_and_stage_status() -> None:
    """
    功能：验证 TurnTrace 核心字段和 stage.status 枚举约束。
    入参：无。
    出参：None。
    异常：断言失败表示 trace 契约约束退化。
    """
    valid_trace = TurnTrace.model_validate(
        {
            "trace_id": "trc_a1_trace_01",
            "request_id": "req_a1_trace_01",
            "session_id": "sess_a1_trace_01",
            "session_turn_id": 1,
            "runtime_turn_id": 2,
            "stages": [
                {
                    "stage": "api.received",
                    "status": "ok",
                    "at": "2026-05-02T00:00:00Z",
                    "detail": {"note": "start"},
                }
            ],
            "errors": [],
        }
    )
    assert valid_trace.trace_id == "trc_a1_trace_01"
    with pytest.raises(ValidationError):
        TurnTrace.model_validate(
            {
                "trace_id": "trc_a1_trace_02",
                "stages": [
                    {
                        "stage": "api.received",
                        "status": "done",
                        "at": "2026-05-02T00:00:00Z",
                    }
                ],
            }
        )
