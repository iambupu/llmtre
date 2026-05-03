"""
基于 LangGraph 的核心状态机流与中央事件总线 (内环)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, cast

from langgraph.graph import END, StateGraph

from agents.clarifier_agent import ClarifierAgent
from agents.gm_agent import GMAgent
from agents.nlu_agent import NLUAgent
from core.event_bus import EventBus
from core.runtime_logging import ensure_runtime_logging
from game_workflows.async_watchers import (
    NoOpOuterLoopBridge,
    OuterLoopBridge,
    WorkflowOuterLoopBridge,
)
from game_workflows.graph_schema import CharacterState, FlowState, SceneExitState, SceneSnapshot
from game_workflows.main_loop_config import load_main_loop_rules
from game_workflows.main_loop_outer_helpers import (
    emit_outer_events,
    emit_outer_events_background,
    replay_outbox_once,
    schedule_outbox_replay,
)
from game_workflows.main_loop_persistence_helpers import (
    build_write_plan as build_write_plan_helper,
)
from game_workflows.main_loop_persistence_helpers import (
    execute_write_op as execute_write_op_helper,
)
from game_workflows.main_loop_persistence_helpers import (
    update_state_sync as update_state_sync_helper,
)
from game_workflows.main_loop_resolution_helpers import (
    resolve_action_sync as resolve_action_sync_helper,
)
from game_workflows.main_loop_scene_helpers import (
    build_character_state as build_character_state_helper,
)
from game_workflows.main_loop_scene_helpers import (
    build_scene_snapshot as build_scene_snapshot_helper,
)
from game_workflows.main_loop_scene_helpers import (
    normalize_dict_list as normalize_dict_list_helper,
)
from game_workflows.main_loop_scene_helpers import (
    normalize_scene_exits as normalize_scene_exits_helper,
)
from game_workflows.main_loop_validation_helpers import (
    build_move_clarification as build_move_clarification_helper,
)
from game_workflows.main_loop_validation_helpers import (
    build_target_clarification as build_target_clarification_helper,
)
from game_workflows.main_loop_validation_helpers import (
    clarification_result as clarification_result_helper,
)
from game_workflows.main_loop_validation_helpers import (
    clarify_with_agent as clarify_with_agent_helper,
)
from game_workflows.main_loop_validation_helpers import (
    invalid_result as invalid_result_helper,
)
from game_workflows.main_loop_validation_helpers import (
    is_reachable_location as is_reachable_location_helper,
)
from game_workflows.main_loop_validation_helpers import (
    validate_action_sync as validate_action_sync_helper,
)
from game_workflows.rag_readonly_bridge import RAGReadOnlyBridge
from state.contracts.turn import TurnRequestContext
from tools.entity.entity_probes import EntityProbes
from tools.sqlite_db.db_updater import DBUpdater

ensure_runtime_logging()
logger = logging.getLogger("Workflow.MainLoop")
_NARRATIVE_STREAM_CALLBACK: ContextVar[Callable[[str], None] | None] = ContextVar(
    "narrative_stream_callback",
    default=None,
)


class MainEventLoop:
    """
    主事件循环 (内环)：负责强一致性的主线流程推进。
    """

    def __init__(
        self,
        event_bus: EventBus,
        rag_bridge: RAGReadOnlyBridge | None = None,
        outer_bridge: OuterLoopBridge | None = None,
        db_updater: DBUpdater | None = None,
        entity_probes: EntityProbes | None = None,
    ):
        """
        功能：初始化对象状态与依赖。
        入参：event_bus；rag_bridge；outer_bridge；db_updater；entity_probes。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        self.event_bus = event_bus
        self.rules = load_main_loop_rules()
        self.nlu_agent = NLUAgent(rules=self.rules)
        self.clarifier_agent = ClarifierAgent()
        self.gm_agent = GMAgent(event_bus=event_bus, rules=self.rules)
        self.db_updater = db_updater or DBUpdater()
        self.entity_probes = entity_probes or EntityProbes(db_path=self.db_updater.db_path)
        rag_rules = self.rules.get("rag", {})
        self.rag_bridge = rag_bridge or RAGReadOnlyBridge(
            enabled=bool(rag_rules.get("read_only_enabled", True)),
            auto_initialize=bool(rag_rules.get("auto_initialize", True)),
        )
        self.rag_query_template = str(
            rag_rules.get("query_template", "玩家[{active_character_id}]输入：{user_input}")
        )
        outer_rules = self.rules.get("outer_loop", {})
        self.outer_emit_world_evolution = bool(outer_rules.get("emit_world_evolution", True))
        self.outer_world_minutes_per_turn = int(
            outer_rules.get("world_evolution_minutes_per_turn", 10)
        )
        self.outer_emit_timeout_seconds = float(outer_rules.get("emit_timeout_seconds", 8.0))
        self.outer_max_pending_tasks = int(outer_rules.get("max_pending_tasks", 64))
        self.outer_outbox_replay_limit = int(outer_rules.get("outbox_replay_limit", 10))
        self.outer_outbox_max_attempts = int(outer_rules.get("outbox_max_attempts", 5))
        self.outer_outbox_backoff_seconds = int(outer_rules.get("outbox_backoff_seconds", 5))
        self.outer_outbox_processing_timeout_seconds = int(
            outer_rules.get("outbox_processing_timeout_seconds", 30)
        )
        self.outer_outbox_replay_interval_seconds = float(
            outer_rules.get("outbox_replay_interval_seconds", 2.0)
        )
        if outer_bridge is not None:
            self.outer_bridge = outer_bridge
        else:
            default_bridge = str(outer_rules.get("default_bridge", "workflow")).lower()
            self.outer_bridge = (
                NoOpOuterLoopBridge() if default_bridge == "noop" else WorkflowOuterLoopBridge()
            )
        self._outer_emit_tasks: set[asyncio.Task[Any]] = set()
        self._outer_replay_task: asyncio.Task[Any] | None = None
        self._last_outbox_replay_ts = 0.0
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        """
        功能：构建核心状态图拓扑。
        入参：无。
        出参：无显式返回值约束（见调用方约定）。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        workflow = StateGraph(FlowState)
        workflow.add_node("parse_input", self.parse_input)
        workflow.add_node("validate_action", self.validate_action)
        workflow.add_node("resolve_action", self.resolve_action)
        workflow.add_node("retrieve_context", self.retrieve_context)
        workflow.add_node("update_state", self.update_state)
        workflow.add_node("generate_response", self.generate_response)

        workflow.set_entry_point("parse_input")
        workflow.add_edge("parse_input", "validate_action")
        workflow.add_conditional_edges(
            "validate_action",
            self._route_after_validation,
            {"valid": "resolve_action", "invalid": "generate_response"},
        )
        workflow.add_edge("resolve_action", "retrieve_context")
        workflow.add_edge("retrieve_context", "update_state")
        workflow.add_edge("update_state", "generate_response")
        workflow.add_edge("generate_response", END)
        return workflow.compile()

    def _route_after_validation(self, state: FlowState) -> str:
        """
        功能：根据校验结果决定是否进入动作结算；澄清和失败都直接生成响应。
        入参：state（FlowState）：包含 is_valid 与 turn_outcome。
        出参：str，`valid` 进入结算，`invalid` 直接响应。
        异常：不抛异常；缺失字段按 invalid 降级。
        """
        return "valid" if state.get("is_valid", False) else "invalid"

    def _to_int(self, value: Any, default: int = 0) -> int:
        """
        功能：将未知输入安全转换为整数，避免主循环中的 `Any` 参与数值计算。
        入参：value（Any）：待转换值；default（int，默认 0）：转换失败时的降级值。
        出参：int，成功返回转换值，失败返回 default。
        异常：内部捕获 `TypeError/ValueError`，不向上抛出，避免打断回合执行。
        """
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _build_action_rng(self, state: FlowState) -> random.Random:
        """
        功能：为当前动作构造稳定随机源，保证测试可重复。
        入参：state。
        出参：random.Random。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        action = state.get("action_intent") or {}
        seed_material = "|".join(
            [
                state.get("user_input", ""),
                str(state.get("turn_id", 0)),
                str(state.get("active_character_id", "")),
                str(action.get("target_id", "")),
                str(action.get("type", "")),
            ]
        )
        seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
        return random.Random(seed)

    def _resolve_configured_action(
        self,
        action_type: str,
        base_diff: dict[str, Any],
    ) -> dict[str, Any]:
        """
        功能：合并动作配置中的确定性状态变更，支持 HP/MP 与状态标签。
        入参：action_type（str）：动作类型；base_diff（dict[str, Any]）：结算前已确定的增量。
        出参：dict[str, Any]，返回可交给写计划的状态差异。
        异常：配置缺失时按 0/空列表降级；类型转换异常由 int 抛出并暴露配置错误。
        """
        configured = self.rules.get("resolution", {}).get(action_type, {})
        physics_diff = dict(base_diff)
        physics_diff["hp_delta"] = int(configured.get("hp_delta", 0))
        physics_diff["mp_delta"] = int(configured.get("mp_delta", 0))
        flags = configured.get("state_flags_add", [])
        if isinstance(flags, list):
            physics_diff["state_flags_add"] = [flag for flag in flags if isinstance(flag, str)]
        return physics_diff

    def _build_write_plan(self, state: FlowState) -> list[dict[str, Any]]:
        """
        功能：执行 `_build_write_plan` 相关业务逻辑。
        入参：state。
        出参：list[dict[str, Any]]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return build_write_plan_helper(self, state)

    def _execute_write_op(
        self,
        op: dict[str, Any],
        conn: Any | None = None,
    ) -> bool:
        """
        功能：执行 `_execute_write_op` 相关业务逻辑。
        入参：op；conn。
        出参：bool。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return execute_write_op_helper(self, op, conn=conn)

    def parse_input(self, state: FlowState) -> dict[str, Any]:
        """
        功能：[节点 1] 解析输入：将自然语言转为结构化意图，并注入当前场景快照供模糊语义使用。
        入参：state（FlowState）：必须包含 user_input，可选 active_character 与 scene_snapshot。
        出参：dict[str, Any]，包含 action_intent 与 is_valid 初值。
        异常：NLU 规则异常向上抛出；角色缺失时不进入 NLU，保留受控失败路径。
        """
        user_input = state["user_input"]
        logger.info("正在解析玩家输入: %s", user_input)
        active_character = state.get("active_character")
        if active_character is None:
            return {
                "action_intent": None,
                "is_valid": False,
                "validation_errors": state.get(
                    "validation_errors",
                    ["当前角色不存在，无法执行动作"],
                ),
            }
        # TypedDict 转普通 dict，确保 NLU 接口上下文类型与签名一致。
        context = dict(active_character)
        context["scene_snapshot"] = state.get("scene_snapshot")
        action = self.nlu_agent.parse(user_input, context=context)
        return {"action_intent": action, "is_valid": action is not None}

    def _validate_action_sync(self, state: FlowState) -> dict[str, Any]:
        """
        功能：同步执行动作校验逻辑；所有候选动作必须在这里完成确定性合法性确认。
        入参：state（FlowState）：包含候选动作、角色状态、沙盒标记与场景快照。
        出参：dict[str, Any]，包含 is_valid 与 validation_errors。
        异常：数据库只读探针异常向上抛出；校验失败通过 errors 返回，不抛业务异常。
        """
        return validate_action_sync_helper(self, state)

    def _invalid_result(self, message: str) -> dict[str, Any]:
        """
        功能：构造受控失败结果，禁止进入结算和剧情记忆。
        入参：message（str）：失败原因。
        出参：dict[str, Any]，主循环状态补丁。
        异常：不抛异常；输入按字符串原样记录。
        """
        return invalid_result_helper(message)

    def _clarify_with_agent(
        self,
        state: FlowState,
        action: dict[str, Any] | None,
        fallback_question: str,
    ) -> str:
        """
        功能：调用最小 Clarifier 生成澄清问题，失败时使用调用方提供的确定性问题。
        入参：state（FlowState）：当前回合状态；action（dict[str, Any] | None）：候选动作；
            fallback_question（str）：Clarifier 无法生成时的兜底问题。
        出参：str，玩家可见澄清问题。
        异常：内部捕获 Clarifier 异常并降级为 fallback_question，避免阻断回合。
        """
        return clarify_with_agent_helper(self, state, action, fallback_question)

    def _clarification_result(self, question: str) -> dict[str, Any]:
        """
        功能：构造澄清回合结果，返回问题但不推进世界状态。
        入参：question（str）：面向玩家的中文澄清问题。
        出参：dict[str, Any]，主循环状态补丁。
        异常：不抛异常；空问题由调用方提供默认值。
        """
        return clarification_result_helper(question)

    def _build_move_clarification(self, state: FlowState) -> str:
        """
        功能：根据当前出口生成移动澄清问题。
        入参：state（FlowState）：当前场景状态。
        出参：str，面向玩家的问题。
        异常：不抛异常；无出口时返回通用问题。
        """
        return build_move_clarification_helper(state)

    def _build_target_clarification(self, state: FlowState, action_type: Any) -> str:
        """
        功能：根据可见 NPC 生成交谈或攻击目标澄清问题。
        入参：state（FlowState）：当前场景状态；action_type（Any）：候选动作类型。
        出参：str，面向玩家的问题。
        异常：不抛异常；无可见对象时返回通用问题。
        """
        return build_target_clarification_helper(state, action_type)

    def _is_reachable_location(self, state: FlowState, location_id: str) -> bool:
        """
        功能：判断目标地点是否属于当前场景出口，用于阻止 NLU 生成越界移动。
        入参：state（FlowState）：当前回合状态；location_id（str）：候选目标地点。
        出参：bool，目标在出口列表中返回 True。
        异常：不抛异常；场景快照缺失时保守返回 False。
        """
        return is_reachable_location_helper(state, location_id)

    async def validate_action(self, state: FlowState) -> dict[str, Any]:
        """
        功能：[节点 2] 校验动作：在线程池中执行阻塞 I/O，避免阻塞事件循环。
        入参：state。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await asyncio.to_thread(self._validate_action_sync, state)

    def _resolve_action_sync(self, state: FlowState) -> dict[str, Any]:
        """
        功能：同步执行动作结算逻辑；数值变化仅来自确定性规则和骰子工具。
        入参：state（FlowState）：已通过校验的动作状态。
        出参：dict[str, Any]，包含 physics_diff，供写计划消费。
        异常：事件总线钩子或只读查询异常向上抛出，由主循环调用方处理。
        """
        return resolve_action_sync_helper(self, state)

    async def resolve_action(self, state: FlowState) -> dict[str, Any]:
        """
        功能：[节点 3] 解析动作结果：在线程池中执行阻塞 I/O，避免阻塞事件循环。
        入参：state。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await asyncio.to_thread(self._resolve_action_sync, state)

    def _update_state_sync(self, state: FlowState) -> dict[str, Any]:
        """
        功能：同步执行持久化写链。
        入参：state。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return update_state_sync_helper(self, state)

    async def update_state(self, state: FlowState) -> dict[str, Any]:
        """
        功能：[节点 5] 更新状态：在线程池中执行数据库写入，避免阻塞事件循环。
        入参：state。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return await asyncio.to_thread(self._update_state_sync, state)

    async def retrieve_context(self, state: FlowState) -> dict[str, Any]:
        """
        功能：[节点 4] 只读检索：加载叙事上下文，不参与动作判定。
        入参：state。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        query = self.rag_query_template.format(
            active_character_id=state.get("active_character_id", ""),
            user_input=state.get("user_input", ""),
        )
        if hasattr(self.rag_bridge, "build_snapshot_async"):
            snapshot = await self.rag_bridge.build_snapshot_async(query)
        else:
            snapshot = await asyncio.to_thread(self.rag_bridge.build_snapshot, query)
        return {"world_snapshot": snapshot}

    def generate_response(self, state: FlowState) -> dict[str, Any]:
        """
        功能：[节点 6] 生成响应：叙事渲染。
        入参：state。
        出参：dict[str, Any]。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        logger.info("正在生成叙事响应...")
        output = self.gm_agent.render_block(
            dict(state),
            stream_callback=_NARRATIVE_STREAM_CALLBACK.get(),
        )
        return {
            "final_response": output.narrative,
            "quick_actions": output.quick_actions,
            "failure_reason": output.failure_reason,
            "suggested_next_step": output.suggested_next_step,
        }

    def _build_scene_snapshot(
        self,
        active_character: CharacterState | None,
        recent_memory: str = "",
        use_shadow: bool = False,
    ) -> SceneSnapshot | None:
        """
        功能：构造当前回合场景快照，供 NLU、校验、叙事和 Web 可操作提示共享。
        入参：active_character（CharacterState | None）：当前角色快照；
            recent_memory（str，默认空）：会话记忆摘要；
            use_shadow（bool，默认 False）：是否读取 Shadow 世界状态。
        出参：SceneSnapshot | None，角色缺失时返回 None。
        异常：只读数据库异常向上抛出；地点定义缺失时使用配置降级场景，不中断回合。
        """
        return build_scene_snapshot_helper(
            entity_probes=self.entity_probes,
            rules=self.rules,
            active_character=active_character,
            recent_memory=recent_memory,
            use_shadow=use_shadow,
        )

    def _normalize_scene_exits(self, raw_exits: Any) -> list[SceneExitState]:
        """
        功能：把配置或数据库中的出口定义收敛为稳定结构。
        入参：raw_exits（Any）：可能为列表或其他值。
        出参：list[dict[str, Any]]，每项包含 direction、location_id、label、aliases。
        异常：不抛异常；非法项会被跳过。
        """
        return normalize_scene_exits_helper(raw_exits)

    def _normalize_dict_list(self, value: Any) -> list[dict[str, Any]]:
        """
        功能：过滤配置中的对象列表，避免 Web 响应暴露非对象脏数据。
        入参：value（Any）：待过滤值。
        出参：list[dict[str, Any]]，仅保留 dict 项。
        异常：不抛异常；非列表输入返回空列表。
        """
        return normalize_dict_list_helper(value)

    async def _emit_outer_events(self, state: FlowState) -> dict[str, Any]:
        """
        功能：同步投递最小外环事件，并返回本回合外环投递摘要。
        入参：state（FlowState）：当前回合状态快照。
        出参：dict[str, Any]，包含 status/detail，用于 trace 写入真实投递语义。
        异常：内部捕获所有投递异常并降级入 outbox，不向上抛出以免阻断内环。
        """
        return await emit_outer_events(self, state)

    def _emit_outer_events_background(self, state: FlowState) -> dict[str, Any]:
        """
        功能：将外环投递放到后台任务，避免阻塞主回合返回，并返回调度结果。
        入参：state（FlowState）：当前回合状态快照。
        出参：dict[str, Any]，包含 status/detail，用于标记 started/skipped/failed。
        异常：内部仅记录并降级，不向上抛出。
        """
        return emit_outer_events_background(self, state)

    async def _replay_outbox_once(self) -> None:
        """
        功能：执行 `_replay_outbox_once` 相关业务逻辑。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        await replay_outbox_once(self)

    def _schedule_outbox_replay(self) -> None:
        """
        功能：执行 `_schedule_outbox_replay` 相关业务逻辑。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        schedule_outbox_replay(self)

    def _build_character_state(
        self,
        entity_id: str,
        use_shadow: bool = False,
    ) -> CharacterState | None:
        """
        功能：执行 `_build_character_state` 相关业务逻辑。
        入参：entity_id；use_shadow。
        出参：CharacterState | None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        return build_character_state_helper(self.entity_probes, entity_id, use_shadow)

    async def run(
        self,
        user_input: str,
        initial_character_id: str = "player_01",
        is_sandbox_mode: bool = False,
        recent_memory: str = "",
        narrative_stream_callback: Callable[[str], None] | None = None,
        request_context: TurnRequestContext | None = None,
    ) -> FlowState:
        """
        功能：异步运行内环流程，并在初始状态中绑定角色与场景快照。
        入参：user_input（str）：玩家输入；initial_character_id（str，默认 player_01）：角色 ID；
            is_sandbox_mode（bool，默认 False）：是否使用 Shadow 状态；
            recent_memory（str，默认空）：剧情摘要；
            narrative_stream_callback（Callable[[str], None] | None，默认 None）：GM 叙事片段回调；
            request_context（TurnRequestContext | None，默认 None）：A1 请求级上下文。
        出参：FlowState，包含动作、结算、叙事和最新角色/场景状态。
        异常：图执行、数据库读写或事件投递异常按节点策略向上抛出或降级记录。
        """
        if request_context is not None:
            initial_character_id = request_context.character_id
            is_sandbox_mode = request_context.sandbox_mode
            recent_memory = request_context.recent_memory
        active_character = self._build_character_state(
            initial_character_id,
            use_shadow=is_sandbox_mode,
        )
        if active_character is None and is_sandbox_mode:
            active_character = self._build_character_state(initial_character_id, use_shadow=False)
        scene_snapshot = self._build_scene_snapshot(
            active_character,
            recent_memory=recent_memory,
            use_shadow=is_sandbox_mode,
        )
        initial_turn_id = self.db_updater.get_total_turns()
        trace_id = request_context.trace_id if request_context is not None else ""
        request_id = request_context.request_id if request_context is not None else ""
        session_id = request_context.session_id if request_context is not None else ""
        initial_state: FlowState = {
            "user_input": user_input,
            "active_character_id": initial_character_id,
            "action_intent": None,
            "is_valid": active_character is not None,
            "validation_errors": (
                []
                if active_character is not None
                else ["当前角色不存在，无法启动主循环"]
            ),
            "physics_diff": None,
            "turn_id": initial_turn_id,
            "runtime_turn_id": initial_turn_id,
            "trace_id": trace_id,
            "request_id": request_id,
            "session_id": session_id,
            "is_sandbox_mode": is_sandbox_mode,
            "final_response": "",
            "quick_actions": [],
            "write_results": [],
            "failure_reason": "",
            "suggested_next_step": "",
            "world_snapshot": None,
            "scene_snapshot": scene_snapshot,
            "active_character": active_character,
            "turn_outcome": "pending",
            "clarification_question": "",
            "should_advance_turn": True,
            "should_write_story_memory": False,
            "debug_trace": [],
            "outer_emit_result": {"status": "skipped", "detail": {"mode": "not_executed"}},
        }
        stream_token = _NARRATIVE_STREAM_CALLBACK.set(narrative_stream_callback)
        try:
            result_raw = await self.graph.ainvoke(initial_state)
        finally:
            _NARRATIVE_STREAM_CALLBACK.reset(stream_token)
        result = cast(FlowState, result_raw)
        if result.get("active_character_id"):
            latest_character = self._build_character_state(
                result["active_character_id"],
                use_shadow=result.get("is_sandbox_mode", False),
            )
            result["active_character"] = latest_character
            result["scene_snapshot"] = self._build_scene_snapshot(
                latest_character,
                recent_memory=recent_memory,
                use_shadow=result.get("is_sandbox_mode", False),
            )
        result["runtime_turn_id"] = int(result.get("turn_id", 0))
        # A1 可验收要求：outer.emitted 必须反映当前回合可观测结果，
        # 不能依赖 asyncio.run 结束后会被取消的后台任务。
        if isinstance(self.outer_bridge, NoOpOuterLoopBridge):
            result["outer_emit_result"] = {"status": "skipped", "detail": {"mode": "noop"}}
        elif isinstance(self.outer_bridge, WorkflowOuterLoopBridge):
            result["outer_emit_result"] = await self._emit_outer_events(result)
        else:
            result["outer_emit_result"] = await self._emit_outer_events(result)
        return result
