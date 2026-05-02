"""
A1 稳态闭环跨模块契约。
"""

from state.contracts.agent import AgentEnvelope, GMOutputBlock
from state.contracts.scene import (
    InteractionSlot,
    SceneAffordance,
    SceneObjectRef,
    SceneSnapshotV2,
)
from state.contracts.turn import RuntimeTurnResult, TurnRequestContext, TurnResult, TurnTrace

__all__ = [
    "AgentEnvelope",
    "GMOutputBlock",
    "InteractionSlot",
    "RuntimeTurnResult",
    "SceneAffordance",
    "SceneObjectRef",
    "SceneSnapshotV2",
    "TurnRequestContext",
    "TurnResult",
    "TurnTrace",
]
