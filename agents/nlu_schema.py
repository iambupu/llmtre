"""
NLU 候选动作结构化契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

ActionType = Literal[
    "observe",
    "wait",
    "rest",
    "move",
    "talk",
    "inspect",
    "use_item",
    "attack",
    "commit_sandbox",
    "discard_sandbox",
]


class NLUActionCandidate(BaseModel):
    """
    功能：约束 NLU 输出的候选动作，保证 LLM 只产生可校验的结构化数据。
    入参：BaseModel 字段来自规则 NLU 或 LLM JSON。
    出参：NLUActionCandidate，可通过 `model_dump()` 转为主循环动作字典。
    异常：字段类型、动作类型或置信度非法时抛出 ValidationError。
    """

    type: ActionType
    actor_id: str | None = None
    target_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str = ""
    raw_input: str = ""

    @field_validator("clarification_question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        """
        功能：清理澄清问题两端空白，避免 Web 展示出现格式噪声。
        入参：value（str）：模型或规则层给出的澄清问题。
        出参：str，去除首尾空白后的文本。
        异常：类型非法时由 Pydantic 在进入校验器前抛出 ValidationError。
        """
        return value.strip()


def normalize_action_candidate(
    payload: dict[str, Any] | None,
    *,
    raw_input: str,
    actor_id: str | None,
) -> dict[str, Any] | None:
    """
    功能：把规则或 LLM 输出强校验为主循环可消费的动作字典。
    入参：payload（dict[str, Any] | None）：候选动作；raw_input（str）：玩家原文；
        actor_id（str | None）：当前角色 ID，用于补齐缺省 actor。
    出参：dict[str, Any] | None，校验成功返回动作字典，失败返回 None。
    异常：内部捕获 ValidationError 并降级为 None；不向主循环抛出。
    """
    if payload is None:
        return None
    prepared = dict(payload)
    prepared.setdefault("raw_input", raw_input)
    prepared.setdefault("actor_id", actor_id)
    prepared.setdefault("parameters", {})
    prepared.setdefault("confidence", 1.0)
    prepared.setdefault("needs_clarification", False)
    prepared.setdefault("clarification_question", "")
    try:
        candidate = NLUActionCandidate.model_validate(prepared)
    except ValidationError:
        return None
    return candidate.model_dump()
