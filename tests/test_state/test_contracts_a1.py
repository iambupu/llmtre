from __future__ import annotations

import pytest
from pydantic import ValidationError

from state.contracts.agent import AgentEnvelope, GMOutputBlock
from state.contracts.scene import (
    InteractionSlot,
    SceneAffordance,
    SceneObjectRef,
    SceneSnapshotV2,
)
from state.contracts.turn import TurnResult, TurnTrace, TurnTraceStage


def _turn_payload(outcome: str, *, advance: bool, write_memory: bool) -> dict[str, object]:
    """
    功能：构造 TurnResult 测试所需的最小出站负载。
    入参：outcome（str）：回合结果；advance（bool）：是否推进回合；
        write_memory（bool）：是否写剧情记忆。
    出参：dict[str, object]，TurnResult 输入负载。
    异常：无。
    """
    return {
        "session_id": "sess_contract01",
        "session_turn_id": 1,
        "runtime_turn_id": 10,
        "trace_id": "trc_contract01",
        "request_id": "req_contract01",
        "outcome": outcome,
        "is_valid": outcome == "valid_action",
        "final_response": "叙事",
        "should_advance_turn": advance,
        "should_write_story_memory": write_memory,
    }


def test_turn_result_accepts_three_outcomes_with_correct_side_effect_flags() -> None:
    """
    功能：验证 TurnResult 三态 outcome 与副作用标志的合法组合。
    入参：无。
    出参：None。
    异常：断言失败表示出站回合副作用契约回归。
    """
    valid = TurnResult.model_validate(
        _turn_payload("valid_action", advance=True, write_memory=True)
    )
    clarification = TurnResult.model_validate(
        _turn_payload("clarification", advance=False, write_memory=False)
    )
    invalid = TurnResult.model_validate(_turn_payload("invalid", advance=False, write_memory=False))

    assert valid.outcome == "valid_action"
    assert clarification.outcome == "clarification"
    assert invalid.outcome == "invalid"
    assert valid.model_dump(mode="json")["quick_actions"] == []


def test_turn_result_rejects_invalid_outcome_or_side_effect_combinations() -> None:
    """
    功能：验证非法 outcome 或副作用标志组合会触发 ValidationError。
    入参：无。
    出参：None。
    异常：断言失败表示 TurnResult 组合校验回归。
    """
    with pytest.raises(ValidationError, match="valid_action must advance turn"):
        TurnResult.model_validate(_turn_payload("valid_action", advance=False, write_memory=True))

    with pytest.raises(ValidationError, match="clarification and invalid outcomes"):
        TurnResult.model_validate(_turn_payload("clarification", advance=True, write_memory=False))

    with pytest.raises(ValidationError):
        TurnResult.model_validate(_turn_payload("success", advance=True, write_memory=True))


def test_turn_trace_stage_status_validation_and_serialization() -> None:
    """
    功能：验证 TurnTraceStage 状态枚举与 TurnTrace JSON 序列化结构。
    入参：无。
    出参：None。
    异常：断言失败表示 trace 契约回归。
    """
    stage = TurnTraceStage(stage="nlu.parsed", status="ok", at="2026-05-02T00:00:00Z")
    trace = TurnTrace(trace_id="trc_contract01", stages=[stage], errors=[{"stage": "x"}])

    dumped = trace.model_dump(mode="json")
    assert dumped["stages"][0]["status"] == "ok"
    assert dumped["errors"] == [{"stage": "x"}]
    with pytest.raises(ValidationError):
        TurnTraceStage(stage="bad", status="done", at="now")


def test_scene_contract_defaults_required_fields_and_invalid_enums() -> None:
    """
    功能：验证 scene contract 的默认字段、必填字段和对象类型枚举。
    入参：无。
    出参：None。
    异常：断言失败表示场景契约校验回归。
    """
    object_ref = SceneObjectRef(
        object_id="location:road",
        object_type="location",
        label="道路",
    )
    slot = InteractionSlot(
        slot_id="slot:observe:road",
        object_id="location:road",
        action_type="observe",
        label="观察",
        enabled=True,
        default_input="观察周围",
    )
    affordance = SceneAffordance(
        id="aff:observe",
        label="观察",
        action_type="observe",
        enabled=True,
        user_input="观察周围",
    )
    snapshot = SceneSnapshotV2(
        scene_objects=[object_ref],
        interaction_slots=[slot],
        affordances=[affordance],
    )

    assert snapshot.schema_version == "scene_snapshot.v2"
    assert snapshot.current_location == {}
    assert snapshot.scene_objects[0].priority == 100
    assert snapshot.interaction_slots[0].required_params == []
    assert snapshot.affordances[0].reason == ""

    with pytest.raises(ValidationError):
        SceneObjectRef(object_id="bad", object_type="door", label="门")
    with pytest.raises(ValidationError):
        InteractionSlot(
            object_id="location:road",
            action_type="observe",
            label="观察",
            enabled=True,
        )


def test_agent_contract_defaults_required_fields_and_dump() -> None:
    """
    功能：验证 AgentEnvelope 与 GMOutputBlock 的默认字段、必填字段和 JSON dump。
    入参：无。
    出参：None。
    异常：断言失败表示 agent 契约回归。
    """
    envelope = AgentEnvelope(
        trace_id="trc_contract01",
        turn_id=1,
        sender="nlu",
        recipient="gm",
        kind="result",
    )
    output = GMOutputBlock(narrative="你看到道路。")

    assert envelope.payload == {}
    assert envelope.ack_required is False
    assert envelope.model_dump(mode="json")["turn_id"] == 1
    assert output.failure_reason == ""
    assert output.quick_actions == []

    with pytest.raises(ValidationError):
        AgentEnvelope(trace_id="trc_contract01", turn_id=1, sender="nlu", recipient="gm")
    with pytest.raises(ValidationError):
        GMOutputBlock()
