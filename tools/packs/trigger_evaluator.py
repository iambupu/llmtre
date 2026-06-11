"""
触发器评估器 - 根据当前游戏状态评估剧本包触发器，支持 once 语义。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from state.contracts.trigger import TriggerDef, TriggerEvent

logger = logging.getLogger(__name__)
EVENT_TRIGGER_RESERVED_CONDITION_KEYS = frozenset(
    {
        "narrative_text",
        "memory_text",
        "grant_item_id",
        "state_flag",
        "quest_id",
        "target_stage_id",
        "move_entity_id",
        "to_scene_id",
        "match",
    }
)


class TriggerEvaluator:
    """
    功能：根据触发类型与游戏上下文评估剧本包触发器，支持 once 语义防重复触发。
    """

    def __init__(
        self,
        triggers: list[TriggerDef],
        fired_trigger_ids: set[str] | None = None,
    ) -> None:
        """
        功能：初始化触发器列表与当前 session 已触发的 once ID 集合。
        入参：triggers（list[TriggerDef]）：已通过 registry 校验的触发器定义；
            fired_trigger_ids（set[str] | None，默认 None）：历史已触发 once ID。
        出参：None。
        异常：不主动抛异常；传入对象的字段合法性由 TriggerDef 校验阶段保证。
        """
        self.triggers = triggers
        self.fired_ids: set[str] = set(fired_trigger_ids) if fired_trigger_ids else set()

    def evaluate(self, trigger_type: str, context: dict[str, Any]) -> list[TriggerEvent]:
        """
        功能：按触发类型和上下文评估候选触发器，并维护 once 去重状态。
        入参：trigger_type（str）：本次评估的触发类型；
            context（dict[str, Any]）：场景、交互、物品或任务阶段上下文。
        出参：list[TriggerEvent]，按优先级预筛选后实际命中的触发事件；
            narrative_text 与 memory_text 来自已命中触发器的 conditions。
        异常：单个触发器条件评估异常会被捕获并记录 warning，不影响其他触发器。
        """
        fire_candidates: list[TriggerDef] = []
        for td in sorted(self.triggers, key=lambda t: t.priority, reverse=True):
            if td.type != trigger_type:
                continue
            if td.once and td.trigger_id in self.fired_ids:
                continue
            fire_candidates.append(td)
        fired_events: list[TriggerEvent] = []
        for td in fire_candidates:
            try:
                if self._match_conditions(td, context):
                    event_name = (
                        str(context.get("event_name") or context.get("event_type") or "")
                        if td.type == "event"
                        else ""
                    )
                    event = TriggerEvent(
                        trigger_id=td.trigger_id,
                        type=td.type,
                        label=td.label,
                        description=td.description,
                        effects=list(td.effects),
                        narrative_text=str(td.conditions.get("narrative_text") or ""),
                        memory_text=str(td.conditions.get("memory_text") or ""),
                        event_name=event_name,
                    )
                    fired_events.append(event)
                    self.fired_ids.add(td.trigger_id)
                    logger.debug("Trigger fired: %s (type=%s)", td.trigger_id, td.type)
            except Exception as exc:
                logger.warning("Error evaluating trigger %s: %s", td.trigger_id, exc)
        return fired_events

    def _match_conditions(self, trigger: TriggerDef, context: dict[str, Any]) -> bool:
        """
        功能：判断一个触发器的条件是否匹配当前上下文。
        入参：trigger（TriggerDef）：待判断触发器；
            context（dict[str, Any]）：由调用方按触发类型组装的最小上下文。
        出参：bool，True 表示命中，False 表示未命中或触发类型不支持。
        异常：条件读取或比较异常会被捕获并记录 warning，随后降级为 False。
        """
        conds = trigger.conditions
        if not conds:
            return True
        try:
            _type = trigger.type
            if _type == "enter_scene":
                if "scene_id" in conds and context.get("scene_id") != conds["scene_id"]:
                    return False
                return True
            if _type in ("observe", "talk", "inspect"):
                if "scene_id" in conds and context.get("scene_id") != conds["scene_id"]:
                    return False
                ctx_id = context.get("interaction_id")
                if "interaction_id" in conds and ctx_id != conds["interaction_id"]:
                    return False
                return True
            if _type == "item_owned":
                if "item_id" in conds and context.get("item_id") != conds["item_id"]:
                    return False
                return True
            if _type == "quest_stage":
                if "quest_id" in conds and context.get("quest_id") != conds["quest_id"]:
                    return False
                if "quest_stage" in conds and context.get("quest_stage") != conds["quest_stage"]:
                    return False
                return True
            if _type == "event":
                return self._match_event_conditions(conds, context)
            logger.warning("Unknown trigger type %s for %s, skipping", _type, trigger.trigger_id)
            return False
        except Exception as exc:
            logger.warning("Condition match error for %s: %s", trigger.trigger_id, exc)
            return False

    def _match_event_conditions(
        self,
        conditions: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """
        功能：匹配 event 触发器的结构化事件上下文。
        入参：conditions（dict[str, Any]）：TriggerDef.conditions；
            context（dict[str, Any]）：运行时生成的事件上下文。
        出参：bool，所有非保留条件均按标量精确匹配时返回 True。
        异常：不抛异常；复杂条件或缺失字段按不匹配处理。
        """
        raw_match = conditions.get("match")
        if raw_match is not None and not isinstance(raw_match, dict):
            logger.warning("event trigger match condition must be an object")
            return False
        has_match_block = isinstance(raw_match, dict)
        # event 触发器的根级 conditions 仍承载 effect 参数；match 块内字段才完整参与事件匹配。
        match_conditions: dict[str, Any]
        if has_match_block:
            match_conditions = cast(dict[str, Any], raw_match)
        else:
            match_conditions = conditions
        expected_event_name = match_conditions.get("event_name")
        if expected_event_name is None:
            expected_event_name = match_conditions.get("event_type")
        if expected_event_name is None:
            expected_event_name = conditions.get("event_name")
        if expected_event_name is None:
            expected_event_name = conditions.get("event_type")
        if expected_event_name is not None:
            actual_event_name = context.get("event_name", context.get("event_type"))
            if str(actual_event_name or "") != str(expected_event_name or ""):
                return False
        for key, expected_value in match_conditions.items():
            if key in {"event_name", "event_type"}:
                continue
            if not has_match_block and key in EVENT_TRIGGER_RESERVED_CONDITION_KEYS:
                continue
            if isinstance(expected_value, dict | list):
                logger.warning("event trigger condition %s uses unsupported complex value", key)
                return False
            if key not in context:
                return False
            actual_value = context.get(key)
            if str(actual_value or "") != str(expected_value or ""):
                return False
        return True

    def get_fired_ids(self) -> set[str]:
        """
        功能：返回当前评估器记录的已触发 once ID。
        入参：无。
        出参：set[str]，内部集合的副本，调用方修改不会影响评估器状态。
        异常：无。
        """
        return set(self.fired_ids)


def load_trigger_defs(pack_root: Path) -> list[TriggerDef]:
    """
    功能：从 Story Pack 的 triggers/ 目录加载触发器定义。
    入参：pack_root（Path）：单个 Story Pack 根目录。
    出参：list[TriggerDef]，成功解析的触发器列表；目录不存在时返回空列表。
    异常：单个文件读取或校验失败会记录 warning 并跳过，不抛出到调用方。
    """
    triggers_dir = pack_root / "triggers"
    if not triggers_dir.is_dir():
        logger.debug("No triggers/ directory in %s, skipping", pack_root)
        return []
    triggers: list[TriggerDef] = []
    for fpath in sorted(triggers_dir.glob("*.json")):
        try:
            raw = json.loads(fpath.read_text(encoding="utf-8"))
            td = TriggerDef.model_validate(raw)
            triggers.append(td)
            logger.debug("Loaded trigger: %s from %s", td.trigger_id, fpath.name)
        except Exception as exc:
            logger.warning("Failed to load trigger %s: %s", fpath, exc)
    return triggers


def evaluate_triggers(
    trigger_defs: list[TriggerDef],
    session_metadata: dict[str, Any],
    current_scene_id: str,
    action_result: dict[str, Any],
    quest_states: list[Any],
    runtime_events: list[dict[str, Any]] | None = None,
    include_enter_scene: bool = True,
) -> list[TriggerEvent]:
    """
    功能：汇总评估当前回合可能命中的 enter/action/item/quest/event 触发器。
    入参：trigger_defs（list[TriggerDef]）：当前 pack 的触发器定义；
        session_metadata（dict[str, Any]）：含 fired_trigger_ids 与角色运行期元数据；
        current_scene_id（str）：当前场景 ID；action_result（dict[str, Any]）：动作结算结果；
        quest_states（list[Any]）：任务运行时状态对象或字典列表；
        runtime_events（list[dict[str, Any]] | None，默认 None）：本回合确定性运行时事件。
        include_enter_scene（bool，默认 True）：是否把 current_scene_id 当作本次进入场景评估；
            主循环普通动作会关闭它，避免离开源场景时误触发源场景 enter once。
    出参：list[TriggerEvent]，按触发器优先级降序排列的命中事件。
    异常：TriggerEvaluator 内部捕获单触发器异常；本函数不直接捕获入参结构错误。
    """
    fired_raw = session_metadata.get("fired_trigger_ids", [])
    fired_ids: set[str] = set(fired_raw) if isinstance(fired_raw, list) else set()
    evaluator = TriggerEvaluator(trigger_defs, fired_ids)
    all_events: list[TriggerEvent] = []
    if include_enter_scene:
        all_events.extend(evaluator.evaluate("enter_scene", {"scene_id": current_scene_id}))
    action = action_result.get("action", "")
    if action in ("observe", "talk", "inspect"):
        ctx: dict[str, str] = {"scene_id": current_scene_id}
        interaction_id = action_result.get("interaction_id") or action_result.get("target_id")
        if interaction_id:
            ctx["interaction_id"] = interaction_id
        all_events.extend(evaluator.evaluate(action, ctx))
    active_char = session_metadata.get("active_character", {})
    inventory = active_char.get("inventory", [])
    for item_id in inventory:
        all_events.extend(evaluator.evaluate("item_owned", {"item_id": item_id}))
    for qs in quest_states:
        if hasattr(qs, "quest_id"):
            qid = qs.quest_id
            stage = qs.current_stage_id
        else:
            qid = qs.get("quest_id", "")
            stage = qs.get("current_stage_id", "")
        if qid and stage:
            ctx_q: dict[str, str] = {"quest_id": qid, "quest_stage": stage}
            all_events.extend(evaluator.evaluate("quest_stage", ctx_q))
    for runtime_event in runtime_events or []:
        if isinstance(runtime_event, dict):
            all_events.extend(evaluator.evaluate("event", runtime_event))
    priority_map = {t.trigger_id: t.priority for t in trigger_defs}
    all_events.sort(key=lambda e: priority_map.get(e.trigger_id, 0), reverse=True)
    return all_events
