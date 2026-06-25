"""
A3 沙盒差异摘要契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SandboxDiffMode = Literal["preview", "committed", "discarded"]


class SandboxFieldChange(BaseModel):
    """
    功能：描述 Active/Shadow 对比中的单项字段变化。
    入参：subject_id（str）：变化对象 ID；field（str）：字段名；
        before/after（Any）：Active 与 Shadow 中的值；label（str）：玩家可读说明；
        source（dict[str, Any]，默认空）：表名、键名等调试来源。
    出参：SandboxFieldChange。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    subject_id: str
    field: str
    before: Any = None
    after: Any = None
    label: str = ""
    source: dict[str, Any] = Field(default_factory=dict)


class SandboxDiffSummary(BaseModel):
    """
    功能：描述一次沙盒预览、并入或回滚的 Active/Shadow 差异摘要。
    入参：session_id（str）：会话 ID；trace_id（str）：请求追踪；
        mode（SandboxDiffMode）：preview/committed/discarded；has_changes（bool）：是否存在变化；
        character_changes/inventory_changes/task_changes/world_changes/memory_changes：分组差异；
        diagnostics（list[str]）：无法比较或降级说明。
    出参：SandboxDiffSummary。
    异常：字段类型非法时由 Pydantic 抛出 ValidationError。
    """

    session_id: str
    trace_id: str
    mode: SandboxDiffMode
    has_changes: bool
    character_changes: list[SandboxFieldChange] = Field(default_factory=list)
    inventory_changes: list[SandboxFieldChange] = Field(default_factory=list)
    task_changes: list[SandboxFieldChange] = Field(default_factory=list)
    world_changes: list[SandboxFieldChange] = Field(default_factory=list)
    memory_changes: list[SandboxFieldChange] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
