"""
A1 回合请求、结果与追踪契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from state.contracts.scene import SceneAffordance, SceneSnapshotV2


class TurnTraceStage(BaseModel):
    """
    功能：记录单个回合处理阶段的状态。
    入参：stage（str）：阶段名；status（Literal）：阶段状态；at（str）：UTC 时间；
        detail（dict）：诊断细节。
    出参：TurnTraceStage。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    stage: str
    status: Literal["started", "ok", "skipped", "failed"]
    at: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TurnTrace(BaseModel):
    """
    功能：串联 API、主循环、Agent 与外环投递的请求级追踪。
    入参：trace_id（str）：请求级追踪号；request/session/turn 字段用于定位；
        stages/errors：阶段记录和异常记录。
    出参：TurnTrace。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    trace_id: str
    request_id: str | None = None
    session_id: str | None = None
    session_turn_id: int | None = None
    runtime_turn_id: int | None = None
    stages: list[TurnTraceStage] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class TurnRequestContext(BaseModel):
    """
    功能：封装 Web 请求传给主循环的上下文，避免回合 ID 和 trace ID 混用。
    入参：trace_id/request_id/session_id（str）：请求元数据；character_id（str）：角色；
        sandbox_mode（bool）：是否使用 Shadow；recent_memory（str）：会话记忆摘要。
    出参：TurnRequestContext。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    trace_id: str
    request_id: str
    session_id: str
    character_id: str
    sandbox_mode: bool = False
    recent_memory: str = ""


class RuntimeTurnResult(BaseModel):
    """
    功能：主循环返回的运行期回合结果，不包含 Web 会话内回合号。
    入参：runtime_turn_id（int）：全局运行回合号；payload（dict）：主循环原始状态；
        trace（TurnTrace）：追踪记录。
    出参：RuntimeTurnResult。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    runtime_turn_id: int
    payload: dict[str, Any]
    trace: TurnTrace


class TurnResult(BaseModel):
    """
    功能：Web API 对外回合结果契约。
    入参：包含会话回合号、运行回合号、叙事、场景、行动建议和诊断信息。
    出参：TurnResult。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    session_id: str
    session_turn_id: int
    runtime_turn_id: int
    trace_id: str
    request_id: str
    outcome: Literal["valid_action", "clarification", "invalid"]
    is_valid: bool
    action_intent: dict[str, Any] | None = None
    physics_diff: dict[str, Any] | None = None
    final_response: str
    quick_actions: list[str] = Field(default_factory=list)
    quick_action_candidates: list[dict[str, Any]] = Field(default_factory=list)
    quick_action_groups: dict[str, list[str]] = Field(
        default_factory=lambda: {"current": list[str](), "nearby": list[str]()}
    )
    quick_action_layout: dict[str, Any] = Field(default_factory=dict)
    affordances: list[SceneAffordance] = Field(default_factory=list)
    scene_snapshot: SceneSnapshotV2 | None = None
    active_character: dict[str, Any] = Field(default_factory=dict)
    memory_summary: str = ""
    clarification_question: str = ""
    failure_reason: str = ""
    suggested_next_step: str = ""
    should_advance_turn: bool = False
    should_write_story_memory: bool = False
    debug_trace: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace: TurnTrace | None = None

    @model_validator(mode="after")
    def validate_outcome_side_effect_flags(self) -> TurnResult:
        """
        功能：校验 A1 outcome 与回合推进、剧情记忆写入标志的一致性。
        入参：self（TurnResult）：已完成字段级校验的回合响应契约实例。
        出参：TurnResult，字段组合满足 A1 副作用边界时返回自身。
        异常：当 valid_action 未同时推进和写剧情记忆，或 clarification/invalid
            仍声明副作用时抛出 ValueError；
            Pydantic 会将其包装为 ValidationError，供 API 与测试统一处理。
        """
        # outcome 是副作用边界的唯一来源，避免 API 层或调用方临时推断导致世界误推进。
        if self.outcome == "valid_action":
            if not self.should_advance_turn or not self.should_write_story_memory:
                raise ValueError("valid_action must advance turn and write story memory")
            return self

        # 澄清与非法动作都属于非推进结果，不能写入剧情记忆或推进世界状态。
        if self.should_advance_turn or self.should_write_story_memory:
            raise ValueError(
                "clarification and invalid outcomes must not advance or write story memory"
            )
        return self
