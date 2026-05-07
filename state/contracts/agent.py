"""
Agent 进程内消息与 GM 输出契约。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentEnvelope(BaseModel):
    """
    功能：约束 A1 阶段进程内 Agent 消息，避免用散乱 dict 传递关键上下文。
    入参：trace_id（str）：请求级追踪号；turn_id（int | str）：运行回合或会话回合标识；
        sender/recipient/kind（str）：消息路由元数据；payload（dict）：结构化负载；
        ack_required（bool，默认 False）：是否需要确认。
    出参：AgentEnvelope，可通过 model_dump 传递给内部 Agent。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    trace_id: str
    turn_id: int | str
    sender: str
    recipient: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ack_required: bool = False


class GMOutputBlock(BaseModel):
    """
    功能：标准化 GM 输出，分离叙事正文、失败原因、下一步建议和快捷行动。
    入参：narrative（str）：玩家可见叙事；failure_reason（str）：失败原因；
        suggested_next_step（str）：建议下一步；quick_actions（list[str]）：可点击行动；
        quick_action_candidates（list[QuickActionCandidate]）：结构化候选动作。
    出参：GMOutputBlock。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    narrative: str
    failure_reason: str = ""
    suggested_next_step: str = ""
    quick_actions: list[str] = Field(default_factory=list)
    quick_action_candidates: list[QuickActionCandidate] = Field(default_factory=list)


class QuickActionCandidate(BaseModel):
    """
    功能：定义 GM 结构化快捷动作候选，供后端按对象与意图约束安全落桶。
    入参：canonical_intent_key（str）：归一化意图键；
        target_object_hint（str）：对象提示，可为 location/exit/inventory 前缀；
        display_text（str）：前端按钮文案；
        confidence（float | None）：模型置信度；
        reason（str）：模型附带理由。
    出参：QuickActionCandidate。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    canonical_intent_key: str
    target_object_hint: str
    display_text: str
    confidence: float | None = None
    reason: str = ""
