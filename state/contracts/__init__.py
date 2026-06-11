"""
A1 稳态闭环跨模块契约。
"""

from state.contracts.agent import AgentEnvelope, GMOutputBlock
from state.contracts.memory import NarrativeMemoryItem
from state.contracts.quest import QuestDef, QuestRuntimeState, QuestStage, QuestStatus
from state.contracts.scene import (
    InteractionSlot,
    SceneAffordance,
    SceneObjectRef,
    SceneSnapshotV2,
)
from state.contracts.trigger import TriggerDef, TriggerEffect, TriggerEvent, TriggerType
from state.contracts.turn import RuntimeTurnResult, TurnRequestContext, TurnResult, TurnTrace

__all__ = [
    "AgentEnvelope",
    "GMOutputBlock",
    "InteractionSlot",
    "NarrativeMemoryItem",
    "QuestDef",
    "QuestRuntimeState",
    "QuestStage",
    "QuestStatus",
    "RuntimeTurnResult",
    "SceneAffordance",
    "SceneObjectRef",
    "SceneSnapshotV2",
    "TriggerDef",
    "TriggerEvent",
    "TriggerEffect",
    "TriggerType",
    "TurnRequestContext",
    "TurnResult",
    "TurnTrace",
]
