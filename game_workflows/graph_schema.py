"""
TypedDict / Pydantic 状态流转数据结构严格定义
"""

from typing import Any, TypedDict


class StatusEffectState(TypedDict):
    """角色派生状态效果"""
    key: str
    label: str
    kind: str
    severity: str
    description: str


class StatusContextState(TypedDict):
    """面向 Agent 的角色状态上下文"""
    resource_state: str
    flags: list[str]
    prompt_text: str


class CharacterState(TypedDict):
    """角色状态"""
    id: str
    name: str
    hp: int
    max_hp: int
    mp: int
    max_mp: int
    inventory: list[str]
    location: str
    state_flags: list[str]
    status_summary: str
    status_effects: list[StatusEffectState]
    status_context: StatusContextState


class WorldState(TypedDict, total=False):
    """世界状态"""
    current_time_minutes: int
    weather: str
    active_events: list[str]
    rag_enabled: bool
    rag_ready: bool
    rag_query: str
    rag_context: str
    rag_error: str


class SceneExitState(TypedDict):
    """场景出口状态"""
    direction: str
    location_id: str
    label: str
    aliases: list[str]


class SceneSnapshot(TypedDict):
    """回合场景快照"""
    schema_version: str
    current_location: dict[str, Any]
    exits: list[SceneExitState]
    visible_npcs: list[dict[str, Any]]
    visible_items: list[dict[str, Any]]
    active_quests: list[dict[str, Any]]
    recent_memory: str
    available_actions: list[str]
    suggested_actions: list[str]
    scene_objects: list[dict[str, Any]]
    interaction_slots: list[dict[str, Any]]
    affordances: list[dict[str, Any]]
    ui_hints: dict[str, Any]


class FlowState(TypedDict):
    """
    LangGraph 节点间流转的核心数据结构 (内环状态)
    """
    # 1. 玩家原始输入
    user_input: str
    active_character_id: str

    # 2. NLU 解析结果 (符合 ActionSchema)
    action_intent: dict[str, Any] | None

    # 3. 校验状态
    is_valid: bool
    validation_errors: list[str]
    turn_outcome: str
    clarification_question: str
    should_advance_turn: bool
    should_write_story_memory: bool
    debug_trace: list[dict[str, Any]]

    # 4. 物理结算结果 (State Diff)
    physics_diff: dict[str, Any] | None

    # 5. 回合元数据
    turn_id: int
    runtime_turn_id: int
    trace_id: str
    request_id: str
    session_id: str
    is_sandbox_mode: bool

    # 6. 最终输出文本
    final_response: str
    quick_actions: list[str]
    quick_action_candidates: list[dict[str, Any]]
    write_results: list[dict[str, Any]]
    failure_reason: str
    suggested_next_step: str

    # 7. 缓存的世界与角色快照 (用于 RAG 或叙事参考)
    world_snapshot: WorldState | None
    scene_snapshot: SceneSnapshot | None
    active_character: CharacterState | None
    outer_emit_result: dict[str, Any]
