"""
A3 分支后果摘要契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

BranchStateChangeKind = Literal["quest_stage", "state_flag", "item", "scene", "trigger", "memory"]


class BranchStateChange(BaseModel):
    """
    功能：描述一次分支后果中的单条结构化事实变化。
    入参：kind（BranchStateChangeKind）：变化类型；label（str）：玩家可读说明；
        field（str）：结构化字段名；before/after（Any）：变化前后值；
        source（dict[str, Any]，默认空）：触发器、任务或物理差异来源。
    出参：BranchStateChange。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    kind: BranchStateChangeKind
    label: str
    field: str
    before: Any = None
    after: Any = None
    source: dict[str, Any] = Field(default_factory=dict)


class BranchConsequenceSummary(BaseModel):
    """
    功能：把 A3 分支选择造成的结构化变化整理为可展示、可追溯摘要。
    入参：consequence_id（str）：稳定摘要 ID；source_turn_id（int）：会话回合号；
        source_action（str）：触发动作类型；quest_id（str）：关联任务；
        from_stage_id/to_stage_id（str）：任务阶段迁移；branch_path（str）：分支路线；
        state_changes（list[BranchStateChange]）：结构化变化列表；memory_text（str）：可写记忆摘要；
        evidence（dict[str, Any]，默认空）：调试证据索引。
    出参：BranchConsequenceSummary。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    consequence_id: str
    source_turn_id: int
    source_action: str
    quest_id: str
    from_stage_id: str = ""
    to_stage_id: str = ""
    branch_path: str = ""
    state_changes: list[BranchStateChange] = Field(default_factory=list)
    memory_text: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
