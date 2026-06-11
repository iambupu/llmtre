"""
会话级长期叙事记忆契约。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NarrativeMemoryKind = Literal[
    "scene_beat",
    "discovery",
    "relationship",
    "consequence",
    "quest",
    "unresolved_hook",
    "player_style",
]

NarrativeMemoryScope = Literal["session", "character", "npc", "location", "quest", "item"]

NarrativeMemorySubjectType = Literal[
    "session",
    "character",
    "npc",
    "location",
    "quest",
    "item",
    "world",
    "interaction",
]

NarrativeMemoryStatus = Literal["active", "resolved", "stale"]


class NarrativeMemoryItem(BaseModel):
    """
    功能：描述一条可供 GM 辅助叙事的长期记忆项。
    入参：session_id（str）：所属 Web 会话；kind/scope/subject_type：分类字段；
        subject_id（str）：记忆关联对象；text（str）：给 GM 消费的中文叙事事实；
        evidence_turn_id（int）：证据回合；importance/confidence/status：检索和生命周期控制；
        metadata（dict，默认空）：保存来源动作、触发器或任务的诊断信息；
        created_turn_id/last_seen_turn_id（int）：首次与最近证据回合。
    出参：NarrativeMemoryItem，可序列化为 SQLite 记录。
    异常：字段类型、枚举、重要度或置信度非法时由 Pydantic 抛出 ValidationError。
    """

    memory_id: str = ""
    session_id: str
    scope: NarrativeMemoryScope = "session"
    kind: NarrativeMemoryKind
    subject_type: NarrativeMemorySubjectType
    subject_id: str
    text: str
    evidence_turn_id: int
    importance: int = Field(default=3, ge=1, le=10)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: NarrativeMemoryStatus = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_turn_id: int
    last_seen_turn_id: int
