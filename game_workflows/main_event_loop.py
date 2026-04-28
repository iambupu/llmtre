"""
基于 LangGraph 的核心状态机流与中央事件总线 (内环)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, cast

from langgraph.graph import END, StateGraph

from agents.gm_agent import GMAgent
from agents.nlu_agent import NLUAgent
from core.event_bus import EventBus
from core.runtime_logging import ensure_runtime_logging
from game_workflows.async_watchers import (
    NoOpOuterLoopBridge,
    OuterLoopBridge,
    WorkflowOuterLoopBridge,
)
from game_workflows.event_schemas import StateChangedEvent, TurnEndedEvent, WorldEvolutionEvent
from game_workflows.graph_schema import CharacterState, FlowState, SceneExitState, SceneSnapshot
from game_workflows.main_loop_config import load_main_loop_rules
from game_workflows.rag_readonly_bridge import RAGReadOnlyBridge
from tools.entity.entity_probes import EntityProbes
from tools.roll.dice_roller import check_success, roll_d20, roll_dice
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
        action = state.get("action_intent") or {}
        action_type = str(action.get("type", ""))
        diff = state.get("physics_diff") or {}
        is_sandbox = state.get("is_sandbox_mode", False)
        entity_id = state["active_character_id"]
        write_plan: list[dict[str, Any]] = []

        if action_type == "commit_sandbox":
            write_plan.append({"type": "merge_shadow"})
            write_plan.append({"type": "drop_shadow"})
            write_plan.append({"type": "advance_turn", "turns": 1})
            return write_plan

        if action_type == "discard_sandbox":
            write_plan.append({"type": "drop_shadow"})
            write_plan.append({"type": "advance_turn", "turns": 1})
            return write_plan

        if is_sandbox and not self.db_updater.has_shadow_state():
            write_plan.append({"type": "fork_shadow"})

        if diff:
            write_plan.append(
                {
                    "type": "apply_diff",
                    "entity_id": entity_id,
                    "diff": diff,
                    "use_shadow": is_sandbox,
                }
            )

        consumed_item_id = diff.get("consumed_item_id")
        if isinstance(consumed_item_id, str):
            write_plan.append(
                {
                    "type": "consume_item",
                    "owner_id": entity_id,
                    "item_id": consumed_item_id,
                    "use_shadow": is_sandbox,
                }
            )

        target_id = action.get("target_id")
        target_hp_delta = diff.get("target_hp_delta")
        if target_id and isinstance(target_hp_delta, int):
            write_plan.append(
                {
                    "type": "apply_diff",
                    "entity_id": str(target_id),
                    "diff": {"hp_delta": target_hp_delta},
                    "use_shadow": is_sandbox,
                }
            )

        write_plan.append({"type": "advance_turn", "turns": 1})
        return write_plan

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
        op_type = str(op.get("type", ""))
        if op_type == "fork_shadow":
            return self.db_updater.fork_shadow_state(conn=conn)
        if op_type == "merge_shadow":
            return self.db_updater.merge_shadow_state(conn=conn)
        if op_type == "drop_shadow":
            return self.db_updater.drop_shadow_state(conn=conn)
        if op_type == "apply_diff":
            return self.db_updater.apply_diff(
                entity_id=str(op.get("entity_id", "")),
                diff=dict(op.get("diff", {})),
                use_shadow=bool(op.get("use_shadow", False)),
                conn=conn,
            )
        if op_type == "consume_item":
            return self.db_updater.consume_item(
                owner_id=str(op.get("owner_id", "")),
                item_id=str(op.get("item_id", "")),
                quantity=int(op.get("quantity", 1)),
                use_shadow=bool(op.get("use_shadow", False)),
                conn=conn,
            )
        if op_type == "advance_turn":
            return self.db_updater.advance_turn(turns=int(op.get("turns", 1)), conn=conn)
        return False

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
        action = state.get("action_intent")
        active_character = state.get("active_character")
        if not active_character:
            return self._invalid_result("当前角色不存在，无法执行动作")

        if not action:
            return self._clarification_result(
                "我还没有理解你的行动，你想观察、移动、交谈，还是休息？"
            )

        if bool(action.get("needs_clarification", False)):
            question = str(action.get("clarification_question") or "").strip()
            return self._clarification_result(question or "你能再具体说明目标或方向吗？")

        errors: list[str] = []
        action_type = action.get("type")
        supported_actions = {
            "attack",
            "talk",
            "move",
            "observe",
            "wait",
            "rest",
            "inspect",
            "use_item",
            "interact",
            "commit_sandbox",
            "discard_sandbox",
        }
        if action_type not in supported_actions:
            errors.append("动作类型暂不支持")

        if action_type in {"attack", "talk"} and not action.get("target_id"):
            return self._clarification_result(self._build_target_clarification(state, action_type))

        if action_type == "attack":
            target = self.entity_probes.get_character_stats(str(action["target_id"]))
            if target is None:
                errors.append("攻击目标不存在")

        if action_type == "move":
            location_id = action.get("parameters", {}).get("location_id")
            if not location_id or location_id == "unknown":
                return self._clarification_result(self._build_move_clarification(state))
            elif not self._is_reachable_location(state, str(location_id)):
                errors.append("目标地点不在当前场景出口中")

        if (
            action_type in {"commit_sandbox", "discard_sandbox"}
            and not state.get("is_sandbox_mode")
        ):
            errors.append("当前不在沙盒模式，无法执行沙盒控制动作")

        if action_type == "use_item":
            item_id = action.get("parameters", {}).get("item_id")
            if not item_id:
                return self._clarification_result("你想使用哪个物品？")
            else:
                inventory_item = self.entity_probes.get_inventory_item(
                    active_character["id"],
                    str(item_id),
                    use_shadow=state.get("is_sandbox_mode", False),
                )
                if inventory_item is None or int(inventory_item.get("quantity", 0)) <= 0:
                    errors.append("背包中不存在该物品")
                elif self.entity_probes.get_item_definition(str(item_id)) is None:
                    errors.append("该物品缺少可用定义")

        if errors:
            return self._invalid_result("；".join(errors))
        return {
            "is_valid": True,
            "validation_errors": [],
            "turn_outcome": "valid_action",
            "clarification_question": "",
            "should_advance_turn": True,
            "should_write_story_memory": True,
            "debug_trace": [
                {
                    "stage": "validate_action",
                    "status": "valid",
                    "action_type": str(action_type),
                }
            ],
        }

    def _invalid_result(self, message: str) -> dict[str, Any]:
        """
        功能：构造受控失败结果，禁止进入结算和剧情记忆。
        入参：message（str）：失败原因。
        出参：dict[str, Any]，主循环状态补丁。
        异常：不抛异常；输入按字符串原样记录。
        """
        return {
            "is_valid": False,
            "validation_errors": [message],
            "turn_outcome": "invalid",
            "clarification_question": "",
            "should_advance_turn": False,
            "should_write_story_memory": False,
            "debug_trace": [{"stage": "validate_action", "status": "invalid", "message": message}],
        }

    def _clarification_result(self, question: str) -> dict[str, Any]:
        """
        功能：构造澄清回合结果，返回问题但不推进世界状态。
        入参：question（str）：面向玩家的中文澄清问题。
        出参：dict[str, Any]，主循环状态补丁。
        异常：不抛异常；空问题由调用方提供默认值。
        """
        return {
            "is_valid": False,
            "validation_errors": [],
            "turn_outcome": "clarification",
            "clarification_question": question,
            "should_advance_turn": False,
            "should_write_story_memory": False,
            "debug_trace": [
                {"stage": "validate_action", "status": "clarification", "question": question}
            ],
        }

    def _build_move_clarification(self, state: FlowState) -> str:
        """
        功能：根据当前出口生成移动澄清问题。
        入参：state（FlowState）：当前场景状态。
        出参：str，面向玩家的问题。
        异常：不抛异常；无出口时返回通用问题。
        """
        scene_snapshot = state.get("scene_snapshot")
        exits = scene_snapshot["exits"] if scene_snapshot else []
        if not exits:
            return "这里暂时没有明确出口，你想先观察周围吗？"
        labels = "、".join(exit_info["label"] for exit_info in exits)
        return f"你想往哪个方向走？当前可选出口：{labels}。"

    def _build_target_clarification(self, state: FlowState, action_type: Any) -> str:
        """
        功能：根据可见 NPC 生成交谈或攻击目标澄清问题。
        入参：state（FlowState）：当前场景状态；action_type（Any）：候选动作类型。
        出参：str，面向玩家的问题。
        异常：不抛异常；无可见对象时返回通用问题。
        """
        scene_snapshot = state.get("scene_snapshot")
        npcs = scene_snapshot["visible_npcs"] if scene_snapshot else []
        verb = "攻击" if action_type == "attack" else "交谈"
        if not npcs:
            return f"你想和谁{verb}？当前没有明确可见目标。"
        labels = "、".join(str(npc.get("name") or npc.get("entity_id")) for npc in npcs)
        return f"你想{verb}哪个目标？当前可见目标：{labels}。"

    def _is_reachable_location(self, state: FlowState, location_id: str) -> bool:
        """
        功能：判断目标地点是否属于当前场景出口，用于阻止 NLU 生成越界移动。
        入参：state（FlowState）：当前回合状态；location_id（str）：候选目标地点。
        出参：bool，目标在出口列表中返回 True。
        异常：不抛异常；场景快照缺失时保守返回 False。
        """
        scene_snapshot = state.get("scene_snapshot")
        if not scene_snapshot:
            return False
        return any(exit_info["location_id"] == location_id for exit_info in scene_snapshot["exits"])

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
        hooked_state = self.event_bus.emit("on_action_pre", dict(state))
        action = cast(dict[str, Any], hooked_state["action_intent"])
        action_type = action["type"]

        physics_diff: dict[str, Any] = {}
        if action_type == "attack":
            attacker: dict[str, Any] = dict(state.get("active_character") or {})
            target = self.entity_probes.get_character_stats(str(action["target_id"]))
            rng = self._build_action_rng(state)
            attack_rules = self.rules.get("resolution", {}).get("attack", {})
            attacker_strength = self._to_int(attacker.get("strength", 10), 10)
            target_agility = self._to_int(target.get("agility", 10), 10) if target else 10
            attack_roll = roll_d20(modifier=attacker_strength, rng=rng)
            base_dc = self._to_int(attack_rules.get("base_dc", 10), 10)
            agility_divisor = max(1, self._to_int(attack_rules.get("agility_divisor", 2), 2))
            attack_dc = base_dc + target_agility // agility_divisor
            attack_hit = check_success(attack_roll, attack_dc)
            physics_diff = {
                "attack_roll": attack_roll,
                "attack_dc": attack_dc,
                "attack_hit": attack_hit,
            }
            if attack_hit:
                damage_dice = str(attack_rules.get("damage_dice", "d6"))
                damage_roll = roll_dice(damage_dice, rng=rng)[0]
                strength_divisor = max(
                    1,
                    self._to_int(attack_rules.get("strength_damage_divisor", 3), 3),
                )
                min_damage = max(1, self._to_int(attack_rules.get("min_damage", 1), 1))
                damage = max(min_damage, damage_roll + attacker_strength // strength_divisor)
                physics_diff["damage_roll"] = damage_roll
                physics_diff["target_hp_delta"] = -damage
        elif action_type == "move":
            location_id = action.get("parameters", {}).get("location_id", "unknown")
            physics_diff = self._resolve_configured_action("move", {"location_id": location_id})
        elif action_type == "use_item":
            item_id = action.get("parameters", {}).get("item_id")
            item_definition = self.entity_probes.get_item_definition(str(item_id))
            if item_definition:
                for effect in item_definition.get("effects", []):
                    if not isinstance(effect, dict):
                        continue
                    target_attribute = effect.get("target_attribute")
                    value = self._to_int(effect.get("value", 0))
                    if target_attribute == "hp":
                        physics_diff["hp_delta"] = physics_diff.get("hp_delta", 0) + value
                    elif target_attribute == "mp":
                        physics_diff["mp_delta"] = physics_diff.get("mp_delta", 0) + value
                physics_diff["consumed_item_id"] = str(item_id)
        elif action_type == "talk":
            physics_diff = self._resolve_configured_action("talk", {})
        elif action_type in {"observe", "wait", "rest", "inspect", "interact"}:
            physics_diff = self._resolve_configured_action(action_type, {})
        elif action_type in {"commit_sandbox", "discard_sandbox"}:
            physics_diff = self._resolve_configured_action(action_type, {})

        logger.info("物理结算完成，结果: %s", physics_diff)
        return {"physics_diff": physics_diff}

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
        write_plan = self._build_write_plan(state)
        # 事务句柄通过闭包在 begin/execute/commit/rollback 之间共享。
        txn: dict[str, Any] = {"conn": None}

        def _begin() -> None:
            """
            功能：执行 `_begin` 相关业务逻辑。
            入参：无。
            出参：None。
            异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
            """
            txn["conn"] = self.db_updater.begin_transaction()

        def _execute(op: dict[str, Any]) -> bool:
            """
            功能：执行 `_execute` 相关业务逻辑。
            入参：op。
            出参：bool。
            异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
            """
            return self._execute_write_op(op, conn=txn["conn"])

        def _commit() -> None:
            """
            功能：执行 `_commit` 相关业务逻辑。
            入参：无。
            出参：None。
            异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
            """
            if txn["conn"] is None:
                return
            self.db_updater.commit_transaction(txn["conn"])
            txn["conn"] = None

        def _rollback() -> None:
            """
            功能：执行 `_rollback` 相关业务逻辑。
            入参：无。
            出参：None。
            异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
            """
            if txn["conn"] is None:
                return
            self.db_updater.rollback_transaction(txn["conn"])
            txn["conn"] = None

        write_result = self.event_bus.apply_write_plan(
            dict(state),
            write_plan,
            _execute,
            begin=_begin,
            commit=_commit,
            rollback=_rollback,
        )
        self.event_bus.emit("on_action_post", dict(state))
        logger.info("数据库更新已提交")
        # turn_id 以 timeline 真值为准，避免回合号被请求级初始值污染。
        current_turn = self.db_updater.get_total_turns()
        action_intent = state.get("action_intent")
        action_type = action_intent.get("type") if isinstance(action_intent, dict) else None
        write_results_raw = write_result.get("results", [])
        write_results = write_results_raw if isinstance(write_results_raw, list) else []
        if action_type in {"commit_sandbox", "discard_sandbox"}:
            return {
                "turn_id": current_turn,
                "is_sandbox_mode": False,
                "write_results": write_results,
            }
        return {
            "turn_id": current_turn,
            "write_results": write_results,
        }

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
        response = self.gm_agent.render(
            dict(state),
            stream_callback=_NARRATIVE_STREAM_CALLBACK.get(),
        )
        quick_actions = self.gm_agent.suggest_quick_actions(dict(state), response)
        return {"final_response": response, "quick_actions": quick_actions}

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
        if active_character is None:
            return None
        location_id = active_character.get("location", "unknown")
        location_info = self.entity_probes.get_location_info(location_id, use_shadow=use_shadow)
        scene_defaults = self.rules.get("scene_defaults", {})
        fallback_locations = scene_defaults.get("locations", {})
        if location_info is None and isinstance(fallback_locations, dict):
            fallback = (
                fallback_locations.get(location_id)
                or fallback_locations.get("unknown")
                or {}
            )
            location_info = dict(fallback) if isinstance(fallback, dict) else {}
        location_info = location_info or {"id": location_id, "name": location_id, "description": ""}

        exits = self._normalize_scene_exits(location_info.get("exits", []))
        nearby_entities = self.entity_probes.list_nearby_entities(
            location_id,
            use_shadow=use_shadow,
        )
        visible_npcs = [
            entity for entity in nearby_entities
            if entity.get("entity_id") != active_character["id"]
        ]
        available_actions = scene_defaults.get("available_actions", [])
        suggested_actions = scene_defaults.get("suggested_actions", [])
        return {
            "current_location": {
                "id": str(location_info.get("id", location_id)),
                "name": str(location_info.get("name", location_id)),
                "description": str(location_info.get("description", "")),
            },
            "exits": exits,
            "visible_npcs": visible_npcs,
            "visible_items": self._normalize_dict_list(location_info.get("visible_items", [])),
            "active_quests": self._normalize_dict_list(location_info.get("active_quests", [])),
            "recent_memory": recent_memory,
            "available_actions": [
                str(action) for action in available_actions if isinstance(action, str)
            ],
            "suggested_actions": [
                str(action) for action in suggested_actions if isinstance(action, str)
            ],
        }

    def _normalize_scene_exits(self, raw_exits: Any) -> list[SceneExitState]:
        """
        功能：把配置或数据库中的出口定义收敛为稳定结构。
        入参：raw_exits（Any）：可能为列表或其他值。
        出参：list[dict[str, Any]]，每项包含 direction、location_id、label、aliases。
        异常：不抛异常；非法项会被跳过。
        """
        if not isinstance(raw_exits, list):
            return []
        exits: list[SceneExitState] = []
        for raw_exit in raw_exits:
            if not isinstance(raw_exit, dict):
                continue
            location_id = raw_exit.get("location_id")
            if not isinstance(location_id, str) or not location_id:
                continue
            aliases = raw_exit.get("aliases", [])
            exits.append(
                {
                    "direction": str(raw_exit.get("direction", "")),
                    "location_id": location_id,
                    "label": str(raw_exit.get("label", location_id)),
                    "aliases": (
                        [str(alias) for alias in aliases if isinstance(alias, str)]
                        if isinstance(aliases, list)
                        else []
                    ),
                }
            )
        return exits

    def _normalize_dict_list(self, value: Any) -> list[dict[str, Any]]:
        """
        功能：过滤配置中的对象列表，避免 Web 响应暴露非对象脏数据。
        入参：value（Any）：待过滤值。
        出参：list[dict[str, Any]]，仅保留 dict 项。
        异常：不抛异常；非列表输入返回空列表。
        """
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    async def _emit_outer_events(self, state: FlowState) -> None:
        """
        功能：投递最小外环事件，失败不影响内环。
        入参：state。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if state.get("is_valid") and state.get("physics_diff"):
            try:
                await asyncio.wait_for(
                    self.outer_bridge.emit_state_changed(
                        StateChangedEvent(
                            entity_id=str(state.get("active_character_id", "")),
                            diff=dict(state.get("physics_diff") or {}),
                            is_sandbox=bool(state.get("is_sandbox_mode", False)),
                        )
                    ),
                    timeout=self.outer_emit_timeout_seconds,
                )
            except Exception as error:  # noqa: BLE001
                logger.warning("外环事件投递失败[event=state_changed]，已降级忽略: %s", error)
                self.db_updater.enqueue_outer_event(
                    "state_changed",
                    StateChangedEvent(
                        entity_id=str(state.get("active_character_id", "")),
                        diff=dict(state.get("physics_diff") or {}),
                        is_sandbox=bool(state.get("is_sandbox_mode", False)),
                    ).model_dump(),
                    str(error),
                )

        try:
            await asyncio.wait_for(
                self.outer_bridge.emit_turn_ended(
                    TurnEndedEvent(
                        turn_id=int(state.get("turn_id", 0)),
                        user_input=str(state.get("user_input", "")),
                        final_response=str(state.get("final_response", "")),
                    )
                ),
                timeout=self.outer_emit_timeout_seconds,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning("外环事件投递失败[event=turn_ended]，已降级忽略: %s", error)
            self.db_updater.enqueue_outer_event(
                "turn_ended",
                TurnEndedEvent(
                    turn_id=int(state.get("turn_id", 0)),
                    user_input=str(state.get("user_input", "")),
                    final_response=str(state.get("final_response", "")),
                ).model_dump(),
                str(error),
            )

        if self.outer_emit_world_evolution and state.get("should_advance_turn", True):
            active_character: dict[str, Any] = dict(state.get("active_character") or {})
            try:
                await asyncio.wait_for(
                    self.outer_bridge.emit_world_evolution(
                        WorldEvolutionEvent(
                            time_passed_minutes=self.outer_world_minutes_per_turn,
                            location_id=str(active_character.get("location", "unknown")),
                        )
                    ),
                    timeout=self.outer_emit_timeout_seconds,
                )
            except Exception as error:  # noqa: BLE001
                logger.warning("外环事件投递失败[event=world_evolution]，已降级忽略: %s", error)
                self.db_updater.enqueue_outer_event(
                    "world_evolution",
                    WorldEvolutionEvent(
                        time_passed_minutes=self.outer_world_minutes_per_turn,
                        location_id=str(active_character.get("location", "unknown")),
                    ).model_dump(),
                    str(error),
                )

    def _emit_outer_events_background(self, state: FlowState) -> None:
        """
        功能：将外环投递放到后台任务，避免阻塞主回合返回。
        入参：state。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        if len(self._outer_emit_tasks) >= self.outer_max_pending_tasks:
            logger.warning(
                "外环后台任务达到上限，当前回合事件写入补偿队列: pending=%s",
                len(self._outer_emit_tasks),
            )
            # 溢出降级时只写入“可重放事件类型”，避免补偿队列出现不可消费事件。
            if state.get("is_valid") and state.get("physics_diff"):
                self.db_updater.enqueue_outer_event(
                    "state_changed",
                    StateChangedEvent(
                        entity_id=str(state.get("active_character_id", "")),
                        diff=dict(state.get("physics_diff") or {}),
                        is_sandbox=bool(state.get("is_sandbox_mode", False)),
                    ).model_dump(),
                    "pending tasks overflow",
                )
            self.db_updater.enqueue_outer_event(
                "turn_ended",
                TurnEndedEvent(
                    turn_id=int(state.get("turn_id", 0)),
                    user_input=str(state.get("user_input", "")),
                    final_response=str(state.get("final_response", "")),
                ).model_dump(),
                "pending tasks overflow",
            )
            if self.outer_emit_world_evolution:
                active_character: dict[str, Any] = dict(state.get("active_character") or {})
                self.db_updater.enqueue_outer_event(
                    "world_evolution",
                    WorldEvolutionEvent(
                        time_passed_minutes=self.outer_world_minutes_per_turn,
                        location_id=str(active_character.get("location", "unknown")),
                    ).model_dump(),
                    "pending tasks overflow",
                )
            logger.warning(
                "外环事件已拆分入补偿队列: actor=%s turn=%s",
                str(state.get("active_character_id", "")),
                int(state.get("turn_id", 0)),
            )
            return
        task = asyncio.create_task(self._emit_outer_events(state))
        self._outer_emit_tasks.add(task)

        def _on_done(done_task: asyncio.Task[Any]) -> None:
            """
            功能：执行 `_on_done` 相关业务逻辑。
            入参：done_task。
            出参：None。
            异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
            """
            self._outer_emit_tasks.discard(done_task)
            if done_task.cancelled():
                logger.warning("外环事件投递后台任务被取消。")
                return
            error = done_task.exception()
            if error is not None:
                logger.warning("外环事件投递后台任务失败: %s", error)

        task.add_done_callback(_on_done)

    async def _replay_outbox_once(self) -> None:
        """
        功能：执行 `_replay_outbox_once` 相关业务逻辑。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        rows = self.db_updater.reserve_pending_outer_events(
            limit=self.outer_outbox_replay_limit,
            processing_timeout_seconds=self.outer_outbox_processing_timeout_seconds,
        )
        for row in rows:
            event_id = self._to_int(row.get("id", 0), 0)
            event_name = str(row.get("event_name", ""))
            payload_raw = row.get("payload", {})
            payload = cast(dict[str, Any], payload_raw) if isinstance(payload_raw, dict) else {}
            try:
                if event_name == "state_changed":
                    await asyncio.wait_for(
                        self.outer_bridge.emit_state_changed(StateChangedEvent(**payload)),
                        timeout=self.outer_emit_timeout_seconds,
                    )
                elif event_name == "turn_ended":
                    await asyncio.wait_for(
                        self.outer_bridge.emit_turn_ended(TurnEndedEvent(**payload)),
                        timeout=self.outer_emit_timeout_seconds,
                    )
                elif event_name == "world_evolution":
                    await asyncio.wait_for(
                        self.outer_bridge.emit_world_evolution(WorldEvolutionEvent(**payload)),
                        timeout=self.outer_emit_timeout_seconds,
                    )
                else:
                    raise ValueError(f"unsupported outbox event: {event_name}")
                self.db_updater.mark_outer_event_delivered(event_id)
            except Exception as error:  # noqa: BLE001
                self.db_updater.mark_outer_event_failed(
                    event_id=event_id,
                    error=str(error),
                    max_attempts=self.outer_outbox_max_attempts,
                    base_backoff_seconds=self.outer_outbox_backoff_seconds,
                )
                logger.warning(
                    "外环补偿重放失败[event=%s id=%s]，已回写重试状态: %s",
                    event_name,
                    event_id,
                    error,
                )

    def _schedule_outbox_replay(self) -> None:
        """
        功能：执行 `_schedule_outbox_replay` 相关业务逻辑。
        入参：无。
        出参：None。
        异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
        """
        now = time.monotonic()
        if now - self._last_outbox_replay_ts < self.outer_outbox_replay_interval_seconds:
            return
        if self._outer_replay_task is not None and not self._outer_replay_task.done():
            return
        self._last_outbox_replay_ts = now
        self._outer_replay_task = asyncio.create_task(self._replay_outbox_once())

        def _on_done(done_task: asyncio.Task[Any]) -> None:
            """
            功能：执行 `_on_done` 相关业务逻辑。
            入参：done_task。
            出参：None。
            异常：无显式捕获时向上抛出；如函数内有捕获，则按函数内降级策略处理。
            """
            if done_task.cancelled():
                logger.warning("外环补偿重放任务被取消。")
                return
            error = done_task.exception()
            if error is not None:
                logger.warning("外环补偿重放任务失败: %s", error)

        self._outer_replay_task.add_done_callback(_on_done)

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
        snapshot = self.entity_probes.get_character_stats(entity_id, use_shadow=use_shadow)
        if snapshot is None:
            return None

        inventory_rows = self.entity_probes.check_inventory(entity_id, use_shadow=use_shadow)
        return {
            "id": snapshot["entity_id"],
            "name": snapshot["name"],
            "hp": snapshot["hp"],
            "max_hp": snapshot["max_hp"],
            "mp": snapshot["mp"],
            "max_mp": snapshot["max_mp"],
            "inventory": [row["item_id"] for row in inventory_rows],
            "location": snapshot.get("current_location_id") or "unknown",
        }

    async def run(
        self,
        user_input: str,
        initial_character_id: str = "player_01",
        is_sandbox_mode: bool = False,
        recent_memory: str = "",
        narrative_stream_callback: Callable[[str], None] | None = None,
    ) -> FlowState:
        """
        功能：异步运行内环流程，并在初始状态中绑定角色与场景快照。
        入参：user_input（str）：玩家输入；initial_character_id（str，默认 player_01）：角色 ID；
            is_sandbox_mode（bool，默认 False）：是否使用 Shadow 状态；
            recent_memory（str，默认空）：剧情摘要；
            narrative_stream_callback（Callable[[str], None] | None，默认 None）：GM 叙事片段回调。
        出参：FlowState，包含动作、结算、叙事和最新角色/场景状态。
        异常：图执行、数据库读写或事件投递异常按节点策略向上抛出或降级记录。
        """
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
            "is_sandbox_mode": is_sandbox_mode,
            "final_response": "",
            "quick_actions": [],
            "write_results": [],
            "world_snapshot": None,
            "scene_snapshot": scene_snapshot,
            "active_character": active_character,
            "turn_outcome": "pending",
            "clarification_question": "",
            "should_advance_turn": True,
            "should_write_story_memory": False,
            "debug_trace": [],
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
        if isinstance(self.outer_bridge, WorkflowOuterLoopBridge):
            self._emit_outer_events_background(result)
            self._schedule_outbox_replay()
        else:
            await self._emit_outer_events(result)
        return result
