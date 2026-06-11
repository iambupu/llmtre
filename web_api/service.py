"""
功能：提供 Web API 层复用的应用工厂、校验和业务编排服务。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, NoReturn, cast

from flask import Flask, current_app, jsonify, request

from agents.agent_context import write_session_agent_memory
from config.agent_model_loader import load_agent_model_config
from core.event_bus import EventBus
from game_workflows.affordances import build_scene_interaction_model
from game_workflows.async_watchers import NoOpOuterLoopBridge
from game_workflows.main_event_loop import MainEventLoop
from game_workflows.main_loop_config import load_main_loop_rules
from game_workflows.main_loop_scene_helpers import derive_character_status
from game_workflows.quest_state_helpers import normalize_quest_states, quest_def_from_raw
from state.contracts.turn import TurnRequestContext, TurnTrace, TurnTraceStage
from state.tools.db_initializer import DB_PATH, DBInitializer
from state.tools.runtime_schema import ensure_runtime_tables
from tools.packs.registry import StoryPackRegistry
from web_api.narrative_memory import build_narrative_memory_relevance
from web_api.scene_assets import enrich_scene_snapshot_pack_assets
from web_api.session_store import WebSessionStore

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REGISTRY_PATH = os.path.join(BASE_DIR, "config", "mod_registry.yml")
MODS_ROOT = os.path.join(BASE_DIR, "mods")
ITEMS_DATA_PATH = os.path.join(BASE_DIR, "state", "data", "items.json")
STORY_PACKS_ROOT = os.path.join(BASE_DIR, "story_packs")
VECTOR_DOCSTORE_PATH = os.path.join(
    BASE_DIR,
    "knowledge_base",
    "indices",
    "vector",
    "docstore.json",
)
REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
CHARACTER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
TURN_TIMEOUT_SECONDS = 180
DEFAULT_MEMORY_TURNS = 20
MIN_MEMORY_TURNS = 5
MAX_MEMORY_TURNS = 100
logger = logging.getLogger("WebAPI.Runtime")
SENSITIVE_LOG_KEYS = {
    "user_input",
    "background",
    "persona_profile",
    "asset_files",
    "content_base64",
    "base64",
    "prompt",
    "description",
    "memory_summary",
    "long_term_memory",
}
MAX_LOG_STRING_CHARS = 160
MAX_LOG_COLLECTION_ITEMS = 8


@dataclass
class _QuickActionLayoutIndex:
    """
    功能：保存快捷动作布局所需的可执行索引，隔离 scene_snapshot 字段解析细节。
    入参：由 _build_quick_action_layout_index 构造，字段均为已归一化映射。
    出参：_QuickActionLayoutIndex，用于候选动作和 fallback 动作落桶。
    异常：数据类本身不抛异常；字段一致性由构造函数保证。
    """

    valid_object_ids: set[str]
    affordance_semantic_to_object: dict[str, str]
    affordance_semantic_to_common_action: dict[str, str]
    affordance_semantic_to_inventory_action: dict[str, str]
    affordance_action_type_to_inventory_action: dict[str, str]
    slot_semantic_to_object: dict[str, str]


@dataclass
class _QuickActionLayoutAccumulator:
    """
    功能：记录快捷动作布局归类过程中的输出与诊断计数。
    入参：无；默认创建空布局状态。
    出参：_QuickActionLayoutAccumulator，供归类 helper 原地累积结果。
    异常：不抛业务异常；集合与列表扩容失败时由运行时抛出系统异常。
    """

    common_actions: list[str] = field(default_factory=list)
    object_actions: dict[str, list[str]] = field(default_factory=dict)
    seen_global: set[str] = field(default_factory=set)
    seen_per_object: dict[str, set[str]] = field(default_factory=dict)
    matched_by_slot: int = 0
    matched_by_text: int = 0
    unmatched_actions: list[str] = field(default_factory=list)


def _normalize_quick_action_semantic_key(action: str) -> str:
    """
    功能：将快捷动作归一化为语义键，用于合并“检查四周/观察周围”等同义动作。
    入参：action（str）：原始快捷动作文本。
    出参：str，语义去重键；空字符串表示不可用动作。
    异常：不抛异常；任何文本都会降级为稳定字符串键。
    """
    normalized = "".join(action.split()).strip()
    if not normalized:
        return ""
    # TODO(A1-quick-action-intent): 这里是规则词表兜底；后续改为 LLM 约束后的意图归一化，
    # 由模型输出 canonical_intent_key，再回落本地规则，降低同义短句漏判与误判。
    compact = (
        normalized.replace("一下", "")
        .replace("一会", "")
        .replace("一下子", "")
        .replace("一下儿", "")
    )
    if re.search(
        r"(检查|观察|查看|看看|环顾|打量|侦查|探查|巡视).*(周围|四周|附近|这里|周遭)",
        compact,
    ):
        return "inspect-surroundings"
    return compact


CANONICAL_INTENT_KEY_WHITELIST: set[str] = {
    "inspect_local",
    "observe_local",
    "wait_local",
    "rest_local",
    "move_to_exit",
    "use_inventory_item",
    "talk_to_npc",
    "attack_target",
    "inspect_object",
    "generic_action",
}


def _sanitize_quick_action_candidates(raw_candidates: Any) -> list[dict[str, Any]]:
    """
    功能：清洗 quick_action_candidates 原始数组，确保结构化字段完整且可用于落桶。
    入参：raw_candidates（Any）：GM 输出候选，期望为对象列表。
    出参：list[dict[str, Any]]，每项至少包含 canonical_intent_key/target_object_hint/display_text。
    异常：不抛异常；字段非法时丢弃单条并继续。
    """
    if not isinstance(raw_candidates, list):
        return []
    sanitized: list[dict[str, Any]] = []
    seen_display: set[str] = set()
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        canonical_key = str(item.get("canonical_intent_key") or "").strip()
        display_text = str(item.get("display_text") or "").strip()
        target_hint = str(item.get("target_object_hint") or "").strip()
        if not canonical_key or not display_text:
            continue
        if canonical_key not in CANONICAL_INTENT_KEY_WHITELIST:
            canonical_key = "generic_action"
        if display_text in seen_display:
            continue
        seen_display.add(display_text)
        sanitized.append(
            {
                "canonical_intent_key": canonical_key,
                "target_object_hint": target_hint,
                "display_text": display_text[:40],
                "confidence": item.get("confidence"),
                "reason": str(item.get("reason") or "")[:120],
            }
        )
        if len(sanitized) >= 8:
            break
    return sanitized


def _build_quick_action_groups(
    scene_snapshot: dict[str, Any],
    quick_actions: list[str],
) -> dict[str, list[str]]:
    """
    功能：基于场景 affordance 生成“当前场景/临近场景”快捷动作分组，并执行语义去重。
    入参：scene_snapshot（dict[str, Any]）：当前回合场景快照；
        quick_actions（list[str]）：GM 输出的回合快捷动作。
    出参：dict[str, list[str]]，固定包含 current 与 nearby 两组动作。
    异常：不抛异常；字段缺失时降级为空分组。
    """
    current_location = scene_snapshot.get("current_location")
    current_location_id = (
        str(current_location.get("id"))
        if isinstance(current_location, dict) and current_location.get("id") is not None
        else ""
    )
    affordances = scene_snapshot.get("affordances", [])
    action_bucket_by_text: dict[str, str] = {}
    if isinstance(affordances, list):
        for item in affordances:
            if not isinstance(item, dict) or not bool(item.get("enabled", False)):
                continue
            action_text = str(item.get("user_input") or item.get("label") or "").strip()
            if not action_text:
                continue
            target_location_id = str(item.get("location_id") or "").strip()
            object_id = str(item.get("object_id") or "").strip()
            is_nearby = (
                object_id.startswith("exit:")
                or str(item.get("action_type") or "") == "move"
                or (bool(target_location_id) and target_location_id != current_location_id)
            )
            action_bucket_by_text[action_text] = "nearby" if is_nearby else "current"

    groups: dict[str, list[str]] = {"current": [], "nearby": []}
    seen_keys: dict[str, set[str]] = {"current": set(), "nearby": set()}
    # 事务边界：分组只接受 enabled affordance 中出现过的动作，未授权文本不进入可点击入口。
    for raw_action in quick_actions:
        action_text = str(raw_action).strip()
        if not action_text:
            continue
        bucket = action_bucket_by_text.get(action_text)
        if bucket is None:
            continue
        semantic_key = _normalize_quick_action_semantic_key(action_text)
        if not semantic_key or semantic_key in seen_keys[bucket]:
            continue
        seen_keys[bucket].add(semantic_key)
        groups[bucket].append(action_text)
    return groups


def _as_mapping_list(raw_items: Any) -> list[dict[str, Any]]:
    """
    功能：把任意输入安全转换为对象列表，供 scene_snapshot 子字段解析复用。
    入参：raw_items（Any）：期望为 list[dict] 的原始字段。
    出参：list[dict[str, Any]]，仅保留字典项；非列表降级为空列表。
    异常：不抛异常；非法条目会被静默丢弃。
    """
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _collect_layout_valid_object_ids(scene_snapshot: dict[str, Any]) -> set[str]:
    """
    功能：收集 scene_objects 中可被结构化候选直接指向的对象 ID。
    入参：scene_snapshot（dict[str, Any]）：当前场景快照。
    出参：set[str]，不含空 ID；只代表 scene_objects 中显式存在的对象。
    异常：不抛异常；scene_objects 缺失或非法时返回空集合。
    """
    valid_object_ids: set[str] = set()
    for obj in _as_mapping_list(scene_snapshot.get("scene_objects", [])):
        object_id = str(obj.get("object_id") or "").strip()
        if object_id:
            valid_object_ids.add(object_id)
    return valid_object_ids


def _build_quick_action_layout_index(scene_snapshot: dict[str, Any]) -> _QuickActionLayoutIndex:
    """
    功能：从场景快照构建快捷动作布局索引，统一封装 affordance/slot 的授权判断。
    入参：scene_snapshot（dict[str, Any]）：当前场景快照。
    出参：_QuickActionLayoutIndex，包含对象动作、公共动作、背包动作与 slot 映射。
    异常：不抛异常；字段缺失、类型非法或 disabled 项都会降级为不可匹配。
    """
    affordance_semantic_to_object: dict[str, str] = {}
    affordance_semantic_to_common_action: dict[str, str] = {}
    affordance_semantic_to_inventory_action: dict[str, str] = {}
    affordance_action_type_to_inventory_action: dict[str, str] = {}

    for item in _as_mapping_list(scene_snapshot.get("affordances", [])):
        if not bool(item.get("enabled", False)):
            continue
        object_id = str(item.get("object_id") or "").strip()
        action_text = str(item.get("user_input") or item.get("label") or "").strip()
        action_type = str(item.get("action_type") or "").strip()
        if not action_text:
            continue
        semantic_key = _normalize_quick_action_semantic_key(action_text)
        if object_id.startswith("inventory:"):
            if semantic_key:
                affordance_semantic_to_inventory_action[semantic_key] = action_text
            if action_type and action_type not in affordance_action_type_to_inventory_action:
                affordance_action_type_to_inventory_action[action_type] = action_text
            continue
        if (object_id.startswith("location:") or object_id.startswith("exit:")) and semantic_key:
            affordance_semantic_to_object[semantic_key] = object_id
            continue
        if semantic_key:
            affordance_semantic_to_common_action[semantic_key] = action_text

    slot_semantic_to_object: dict[str, str] = {}
    for slot in _as_mapping_list(scene_snapshot.get("interaction_slots", [])):
        if not bool(slot.get("enabled", False)):
            continue
        object_id = str(slot.get("object_id") or "").strip()
        if not object_id:
            continue
        for candidate_text in (slot.get("default_input"), slot.get("label")):
            semantic_key = _normalize_quick_action_semantic_key(str(candidate_text or "").strip())
            if semantic_key:
                slot_semantic_to_object[semantic_key] = object_id

    return _QuickActionLayoutIndex(
        valid_object_ids=_collect_layout_valid_object_ids(scene_snapshot),
        affordance_semantic_to_object=affordance_semantic_to_object,
        affordance_semantic_to_common_action=affordance_semantic_to_common_action,
        affordance_semantic_to_inventory_action=affordance_semantic_to_inventory_action,
        affordance_action_type_to_inventory_action=affordance_action_type_to_inventory_action,
        slot_semantic_to_object=slot_semantic_to_object,
    )


def _append_layout_object_action(
    accumulator: _QuickActionLayoutAccumulator,
    object_id: str,
    action_text: str,
    semantic_key: str,
) -> None:
    """
    功能：把动作加入对象快捷操作桶，并维护对象级与全局语义去重集合。
    入参：accumulator（_QuickActionLayoutAccumulator）：布局累加器；
        object_id（str）：目标对象 ID；action_text（str）：展示动作文本；
        semantic_key（str）：归一化语义键，必须非空。
    出参：None，原地更新 accumulator。
    异常：不抛业务异常；传入空 object_id 时仍按调用方意图创建桶。
    """
    if object_id not in accumulator.object_actions:
        accumulator.object_actions[object_id] = []
        accumulator.seen_per_object[object_id] = set()
    if semantic_key in accumulator.seen_per_object[object_id]:
        return
    accumulator.object_actions[object_id].append(action_text)
    accumulator.seen_per_object[object_id].add(semantic_key)
    accumulator.seen_global.add(semantic_key)


def _append_layout_common_action(
    accumulator: _QuickActionLayoutAccumulator,
    action_text: str,
    semantic_key: str,
) -> None:
    """
    功能：把动作加入公共快捷操作桶，并维护全局语义去重集合。
    入参：accumulator（_QuickActionLayoutAccumulator）：布局累加器；
        action_text（str）：展示动作文本；semantic_key（str）：归一化语义键，必须非空。
    出参：None，原地更新 accumulator。
    异常：不抛异常；重复语义键会直接忽略。
    """
    if semantic_key in accumulator.seen_global:
        return
    accumulator.common_actions.append(action_text)
    accumulator.seen_global.add(semantic_key)


def _place_structured_quick_action_candidate(
    candidate: dict[str, Any],
    index: _QuickActionLayoutIndex,
    accumulator: _QuickActionLayoutAccumulator,
) -> None:
    """
    功能：按结构化候选的 canonical/target_hint 将动作归入对象桶或公共桶。
    入参：candidate（dict[str, Any]）：已清洗的候选动作；
        index（_QuickActionLayoutIndex）：授权动作索引；
        accumulator（_QuickActionLayoutAccumulator）：布局累加器。
    出参：None，原地更新 accumulator。
    异常：不抛异常；无法映射到 enabled affordance/slot 的候选记入 unmapped。
    """
    action_text = str(candidate.get("display_text") or "").strip()
    semantic_key = _normalize_quick_action_semantic_key(action_text)
    if not action_text or not semantic_key or semantic_key in accumulator.seen_global:
        return

    canonical_key = str(candidate.get("canonical_intent_key") or "").strip()
    target_hint = str(candidate.get("target_object_hint") or "").strip()
    if target_hint.startswith("location:") or target_hint.startswith("exit:"):
        if target_hint in index.valid_object_ids:
            _append_layout_object_action(accumulator, target_hint, action_text, semantic_key)
            accumulator.matched_by_slot += 1
            return

    if target_hint.startswith("inventory:"):
        mapped_inventory_action = index.affordance_semantic_to_inventory_action.get(semantic_key)
        if not mapped_inventory_action:
            accumulator.unmatched_actions.append(action_text)
            return
        _append_layout_common_action(accumulator, mapped_inventory_action, semantic_key)
        accumulator.matched_by_slot += 1
        return

    if canonical_key == "use_inventory_item":
        inventory_action = index.affordance_action_type_to_inventory_action.get("use_item", "")
        if inventory_action:
            mapped_semantic = _normalize_quick_action_semantic_key(inventory_action)
            if mapped_semantic:
                _append_layout_common_action(accumulator, inventory_action, mapped_semantic)
                accumulator.matched_by_slot += 1
                return

    common_action = index.affordance_semantic_to_common_action.get(semantic_key, "")
    if common_action:
        _append_layout_common_action(accumulator, common_action, semantic_key)
        accumulator.matched_by_slot += 1
        return
    accumulator.unmatched_actions.append(action_text)


def _place_fallback_quick_action(
    raw_action: str,
    index: _QuickActionLayoutIndex,
    accumulator: _QuickActionLayoutAccumulator,
) -> None:
    """
    功能：在结构化候选之后，用 GM quick_actions 文本按授权索引进行保守落桶。
    入参：raw_action（str）：原始动作文本；index（_QuickActionLayoutIndex）：授权索引；
        accumulator（_QuickActionLayoutAccumulator）：布局累加器。
    出参：None，原地更新 accumulator。
    异常：不抛异常；无法映射到 enabled affordance/slot 的文本记入 unmapped。
    """
    action_text = str(raw_action).strip()
    semantic_key = _normalize_quick_action_semantic_key(action_text)
    if not action_text or not semantic_key or semantic_key in accumulator.seen_global:
        return

    matched_object_id = index.affordance_semantic_to_object.get(semantic_key, "")
    if not matched_object_id:
        matched_object_id = index.slot_semantic_to_object.get(semantic_key, "")
    if matched_object_id:
        _append_layout_object_action(accumulator, matched_object_id, action_text, semantic_key)
        accumulator.matched_by_slot += 1
        return

    common_action = index.affordance_semantic_to_common_action.get(semantic_key, "")
    if common_action:
        _append_layout_common_action(accumulator, common_action, semantic_key)
        accumulator.matched_by_slot += 1
        return

    inventory_action = index.affordance_semantic_to_inventory_action.get(semantic_key, "")
    if inventory_action:
        mapped_semantic = _normalize_quick_action_semantic_key(inventory_action)
        if mapped_semantic:
            _append_layout_common_action(accumulator, inventory_action, mapped_semantic)
            accumulator.matched_by_slot += 1
            return
    accumulator.unmatched_actions.append(action_text)


def _finalize_quick_action_layout(accumulator: _QuickActionLayoutAccumulator) -> dict[str, Any]:
    """
    功能：把布局累加器转换为 API payload，保持既有 diagnostics 契约。
    入参：accumulator（_QuickActionLayoutAccumulator）：已归类完成的布局状态。
    出参：dict[str, Any]，包含 common_actions、object_actions、diagnostics。
    异常：不抛异常；仅执行去重和字典组装。
    """
    unmapped_actions = list(dict.fromkeys(accumulator.unmatched_actions))
    return {
        "common_actions": accumulator.common_actions,
        "object_actions": accumulator.object_actions,
        "diagnostics": {
            "matched_by_slot": accumulator.matched_by_slot,
            "matched_by_text": accumulator.matched_by_text,
            "unmatched_to_common": 0,
            "unmapped_actions": unmapped_actions,
        },
    }


def _build_quick_action_layout(
    scene_snapshot: dict[str, Any],
    quick_actions: list[str],
    quick_action_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    功能：构造场景快捷操作布局，优先使用结构化候选并由 affordance/slot 约束落桶。
    入参：scene_snapshot（dict[str, Any]）：当前回合场景快照；
        quick_actions（list[str]）：GM 返回的回合快捷动作；
        quick_action_candidates（list[dict[str, Any]] | None）：GM 结构化候选动作。
    出参：dict[str, Any]，字段为 common_actions、object_actions、diagnostics。
    异常：不抛异常；字段缺失时返回空布局。
    """
    candidates = _sanitize_quick_action_candidates(quick_action_candidates or [])
    index = _build_quick_action_layout_index(scene_snapshot)
    accumulator = _QuickActionLayoutAccumulator()

    # 结构化候选优先：先用 canonical + target_hint 定位，再由 affordance/slot 约束是否可执行。
    for candidate in candidates:
        _place_structured_quick_action_candidate(candidate, index, accumulator)

    # 降级路径：场景布局只接受 enabled affordance/slot；
    # 回合内动态 quick_actions 留在旁白消息按钮，不写入公共快捷操作。
    for raw_action in quick_actions:
        _place_fallback_quick_action(raw_action, index, accumulator)

    return _finalize_quick_action_layout(accumulator)


def _merge_trigger_narrative(final_response: str, trigger_events: Any) -> str:
    """
    功能：把已触发 Story Pack 的叙事文本合入最终旁白，避免关键线索只显示为事件计数。
    入参：final_response（str）：GM 已生成或确定性降级的基础叙事；
        trigger_events（Any）：主循环返回的触发事件列表，可能含 narrative_text。
    出参：str，去重后的玩家可读叙事。
    异常：不抛异常；非列表或字段缺失时保留原始 final_response。
    """
    base = final_response.strip()
    if not isinstance(trigger_events, list):
        return base
    narrative_parts: list[str] = []
    seen = {base} if base else set()
    for event in trigger_events:
        if not isinstance(event, dict):
            continue
        if "narrative" not in event.get("effects", []):
            continue
        text = str(event.get("narrative_text") or "").strip()
        if not text or text in seen or (base and text in base):
            continue
        narrative_parts.append(text)
        seen.add(text)
    if not narrative_parts:
        return base
    if not base:
        return " ".join(narrative_parts)
    return f"{base} {' '.join(narrative_parts)}"


def _load_turn_timeout_seconds() -> int:
    """
    功能：读取 Web 回合超时配置；缺失或非法时降级到默认值 180 秒。
    入参：无。
    出参：int，回合超时秒数，约束在 30..600 之间。
    异常：配置读取异常时内部捕获并降级，不阻断服务启动。
    """
    try:
        config = load_agent_model_config()
    except Exception as error:  # noqa: BLE001
        logger.warning("读取 agent_model_config.yml 失败，回合超时降级默认值: %s", str(error))
        return TURN_TIMEOUT_SECONDS
    web_api_cfg = config.get("web_api", {}) if isinstance(config, dict) else {}
    timeout_raw = web_api_cfg.get("turn_timeout_seconds", TURN_TIMEOUT_SECONDS)
    if not isinstance(timeout_raw, int):
        return TURN_TIMEOUT_SECONDS
    if 30 <= timeout_raw <= 600:
        return timeout_raw
    logger.warning(
        "web_api.turn_timeout_seconds 超出范围，已降级默认值: value=%s",
        timeout_raw,
    )
    return TURN_TIMEOUT_SECONDS


class TurnExecutionError(RuntimeError):
    """
    功能：封装回合执行失败时的 trace 上下文，供路由层返回同一 trace_id。
    入参：message（str）：错误说明；trace_id（str）：请求级追踪号；
        trace（dict[str, Any]）：阶段记录。
    出参：TurnExecutionError。
    异常：构造本身不做额外校验；字段类型错误由调用方保证。
    """

    def __init__(
        self,
        message: str,
        trace_id: str,
        trace: dict[str, Any],
        error_code: str = "INTERNAL_ERROR",
        status_code: int = 500,
    ) -> None:
        """
        功能：保存失败 trace 关联信息，避免路由层重新生成 trace_id。
        入参：message（str）：错误说明；trace_id（str）：请求追踪号；
            trace（dict[str, Any]）：追踪负载。
        出参：None。
        异常：无显式异常；内存分配失败时系统异常向上抛出。
        """
        super().__init__(message)
        self.trace_id = trace_id
        self.trace = trace
        self.error_code = error_code
        self.status_code = status_code


class ApiRuntimeContext:
    """
    功能：承载 Flask 契约 API 的运行时状态与主循环依赖。
    入参：无；实例构造后由 `initialize_runtime` 注入事件总线与主循环。
    出参：ApiRuntimeContext，对外提供会话存储、幂等缓存与并发锁。
    异常：构造函数不抛业务异常；后续字段由初始化流程保证完整性。
    """

    def __init__(self) -> None:
        """
        功能：初始化会话存储、剧本包注册表、Agent 上下文目录与会话级串行锁容器。
        入参：无。
        出参：None。
        异常：无显式异常；内存分配异常向上抛出。
        """
        self.main_loop: MainEventLoop | None = None
        self.session_store = WebSessionStore(DB_PATH)
        self.story_pack_registry = StoryPackRegistry(STORY_PACKS_ROOT)
        self.agent_context_dir: str | None = None
        self.session_locks: dict[str, Any] = {}
        self.session_locks_guard = threading.Lock()

    def get_session_lock(self, session_id: str) -> Any:
        """
        功能：获取会话级串行锁；不存在时延迟创建。
        入参：session_id（str）：会话标识。
        出参：Any，线程锁对象。
        异常：无显式异常；锁创建失败时向上抛出。
        """
        with self.session_locks_guard:
            if session_id not in self.session_locks:
                self.session_locks[session_id] = threading.Lock()
            return self.session_locks[session_id]


def _ensure_runtime_ready() -> None:
    """
    功能：确保 Flask 运行时依赖就绪，包含 SQLite 与向量库索引。
    入参：无。
    出参：None。
    异常：数据库初始化失败时异常向上抛出；向量库失败仅记录告警并降级。
    """
    initializer = DBInitializer()
    if not initializer.is_db_initialized():
        initializer.initialize_db()
        logger.info("检测到 SQLite 缺失，已自动初始化数据库。")
    _ensure_runtime_schema_ready(initializer.db_path)
    _ensure_vector_index_ready()


def _ensure_runtime_schema_ready(db_path: str) -> None:
    """
    功能：对已有数据库执行运行期表结构补齐迁移。
    入参：db_path（str）：SQLite 文件路径。
    出参：None。
    异常：迁移失败时抛出 sqlite3.Error。
    """
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.cursor()
        ensure_runtime_tables(cursor)
        connection.commit()
    finally:
        connection.close()


def _ensure_vector_index_ready() -> None:
    """
    功能：校验向量库索引是否存在，缺失时触发一次 RAG 索引初始化（失败可降级）。
    入参：无。
    出参：None。
    异常：函数内部不抛出异常；初始化失败时记录 warning 并维持 Web 可启动。
    """
    if os.path.exists(VECTOR_DOCSTORE_PATH):
        return
    rag_cfg = load_main_loop_rules().get("rag", {})
    auto_initialize = bool(rag_cfg.get("auto_initialize", True))
    if not auto_initialize:
        logger.info(
            "检测到向量库缺失，但 rag.auto_initialize=false，"
            "跳过自动索引初始化并降级为无 RAG 上下文。"
        )
        return
    logger.warning("检测到向量库缺失，开始自动初始化 RAG 索引。")
    try:
        from tools.rag import RAGManager

        manager = RAGManager()
        manager.update_index()
    except Exception as error:  # noqa: BLE001
        logger.warning("向量库初始化失败，已降级为无 RAG 只读上下文: %s", str(error))
        return
    if not os.path.exists(VECTOR_DOCSTORE_PATH):
        logger.warning(
            "向量库初始化后仍未生成 docstore.json，"
            "已降级为无 RAG 只读上下文；请检查 docs/ 与 config/rag_import_rules.json。"
        )
        return
    logger.info("向量库初始化完成。")


def initialize_runtime(app: Flask) -> None:
    """
    功能：为 Flask 应用构建并挂载 API 运行时上下文。
    入参：app（Flask）：待挂载上下文的应用实例。
    出参：None。
    异常：数据库初始化或主循环构建失败时异常向上抛出。
    """
    _ensure_runtime_ready()
    event_bus = EventBus(registry_path=REGISTRY_PATH, mods_root=MODS_ROOT)
    context = ApiRuntimeContext()
    context.story_pack_registry.refresh()
    logger.info(
        "A2 Story Pack registry 加载完成: valid=%s invalid=%s",
        len(context.story_pack_registry.list_summaries()),
        len(context.story_pack_registry.diagnostics()),
    )
    # 配置来源：MainEventLoop 会读取 config/main_loop_rules.json 的 outer_loop.default_bridge；
    # Web 层不显式覆盖外环桥，避免 API 路径把 state_changed/turn_ended 投递降级为 noop。
    context.main_loop = MainEventLoop(
        event_bus=event_bus,
        agent_context_dir=context.agent_context_dir,
        story_pack_registry=context.story_pack_registry,
    )
    app.extensions["tre_api_context"] = context
    global TURN_TIMEOUT_SECONDS
    TURN_TIMEOUT_SECONDS = _load_turn_timeout_seconds()
    logger.info("Web API 回合超时配置生效: turn_timeout_seconds=%s", TURN_TIMEOUT_SECONDS)


def get_runtime_context() -> ApiRuntimeContext:
    """
    功能：从当前 Flask 应用上下文中获取 API 运行时对象。
    入参：无（依赖 Flask `request` 隐式上下文）。
    出参：ApiRuntimeContext，可用于访问会话状态与主循环。
    异常：上下文缺失时抛出 RuntimeError；调用方应将其转换为 500。
    """
    runtime = current_app.extensions.get("tre_api_context")
    if not isinstance(runtime, ApiRuntimeContext):
        raise RuntimeError("API 运行时上下文未初始化")
    return runtime


def now_iso() -> str:
    """
    功能：生成统一 UTC ISO8601 时间字符串。
    入参：无。
    出参：str，格式为 `YYYY-MM-DDTHH:MM:SS.sssZ`。
    异常：时间系统调用异常向上抛出。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_trace_id() -> str:
    """
    功能：生成请求级追踪标识。
    入参：无。
    出参：str，格式为 `trc_<随机串>`。
    异常：UUID 生成异常向上抛出。
    """
    return f"trc_{uuid.uuid4().hex[:16]}"


def new_session_id() -> str:
    """
    功能：生成会话标识。
    入参：无。
    出参：str，格式为 `sess_<随机串>`。
    异常：UUID 生成异常向上抛出。
    """
    return f"sess_{uuid.uuid4().hex[:16]}"


def success(payload: dict[str, Any], status_code: int = 200) -> tuple[Any, int]:
    """
    功能：返回统一成功响应体。
    入参：payload（dict[str, Any]）：业务数据。status_code（int）：HTTP 状态码，默认 200。
    出参：tuple[Any, int]，可直接作为 Flask 路由返回值。
    异常：响应序列化失败时由 Flask 抛出异常。
    """
    body = {"ok": True, "trace_id": new_trace_id()}
    body.update(payload)
    return jsonify(body), status_code


def error(
    code: str,
    message: str,
    status_code: int,
    trace_id: str | None = None,
    trace: dict[str, Any] | None = None,
) -> tuple[Any, int]:
    """
    功能：返回统一错误响应体。
    入参：code（str）：错误码。message（str）：错误描述。status_code（int）：HTTP 状态码；
        trace_id（str | None，默认 None）：指定追踪号；trace（dict[str, Any] | None，默认 None）：
        可选阶段追踪负载。
    出参：tuple[Any, int]，可直接作为 Flask 路由返回值。
    异常：响应序列化失败时由 Flask 抛出异常。
    """
    body = {
        "ok": False,
        "trace_id": trace_id or new_trace_id(),
        "error": {"code": code, "message": message},
    }
    if isinstance(trace, dict):
        body["trace"] = trace
    return jsonify(body), status_code


def parse_json_body() -> dict[str, Any]:
    """
    功能：读取并解析 JSON 请求体；失败时返回空字典。
    入参：无。
    出参：dict[str, Any]，解析成功返回原始对象，失败返回 `{}`。
    异常：内部采用静默解析；不抛异常，统一降级为空字典。
    """
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _summarize_log_value(key: str, value: Any, depth: int = 0) -> Any:
    """
    功能：将请求体字段转换为安全日志摘要，避免玩家文本、persona 与资产内容明文落盘。
    入参：key（str）：当前字段名；value（Any）：字段值；depth（int，默认 0）：递归深度。
    出参：Any，可 JSON 序列化的摘要值；敏感字段只保留类型和长度。
    异常：不抛异常；不可序列化对象按类型名与 repr 长度降级。
    """
    normalized_key = key.lower()
    if normalized_key in SENSITIVE_LOG_KEYS:
        length = len(value) if isinstance(value, str | list | dict) else None
        return {"redacted": True, "type": type(value).__name__, "length": length}
    if isinstance(value, str):
        if len(value) <= MAX_LOG_STRING_CHARS:
            return value
        return {
            "truncated": True,
            "prefix": value[:MAX_LOG_STRING_CHARS],
            "length": len(value),
        }
    if isinstance(value, dict):
        if depth >= 2:
            return {"type": "dict", "keys": sorted(str(item) for item in value.keys())}
        return {
            str(item_key): _summarize_log_value(str(item_key), item_value, depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        if depth >= 2:
            return {"type": "list", "length": len(value)}
        return {
            "type": "list",
            "length": len(value),
            "items": [
                _summarize_log_value(key, item, depth + 1)
                for item in value[:MAX_LOG_COLLECTION_ITEMS]
            ],
        }
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError, ValueError:
        return {"type": type(value).__name__, "repr_length": len(repr(value))}


def _redact_post_body_for_log(body: dict[str, Any]) -> dict[str, Any]:
    """
    功能：生成 POST 请求体日志摘要，保留诊断字段但移除敏感或过长内容。
    入参：body（dict[str, Any]）：已解析 JSON 请求体。
    出参：dict[str, Any]，可安全写入日志的摘要对象。
    异常：不抛异常；单字段异常按 `_summarize_log_value` 的降级策略处理。
    """
    return {str(key): _summarize_log_value(str(key), value) for key, value in body.items()}


def log_post_body(route_name: str, body: dict[str, Any]) -> None:
    """
    功能：记录 POST 请求体脱敏摘要，帮助定位 500 错误对应的入参结构。
    入参：route_name（str）：业务路由名称，用于区分普通/流式回合；
        body（dict[str, Any]）：已解析的 JSON 请求体，来源于 `parse_json_body`。
    出参：None。
    异常：摘要 JSON 序列化失败时内部降级为 `repr`；日志写入异常由 logging 内部处理。
    """
    safe_body = _redact_post_body_for_log(body)
    try:
        body_text = json.dumps(safe_body, ensure_ascii=False, sort_keys=True)
    except TypeError, ValueError:
        body_text = repr(safe_body)
    logger.info("POST 请求体摘要: route=%s body=%s", route_name, body_text[:2000])


def validate_request_id(body: dict[str, Any]) -> str | None:
    """
    功能：校验 request_id 字段格式。
    入参：body（dict[str, Any]）：请求体对象。
    出参：str | None，合法返回 request_id，非法返回 None。
    异常：无显式异常；类型或格式不符合时走降级返回 None。
    """
    request_id = body.get("request_id")
    if not isinstance(request_id, str):
        return None
    if not REQUEST_ID_PATTERN.match(request_id):
        return None
    return request_id


def validate_session_id(session_id: str) -> bool:
    """
    功能：校验 session_id 路径参数格式。
    入参：session_id（str）：会话标识。
    出参：bool，合法返回 True。
    异常：无显式异常；非法输入返回 False。
    """
    return bool(SESSION_ID_PATTERN.match(session_id))


def validate_character_id(character_id: str) -> bool:
    """
    功能：校验 character_id 参数格式。
    入参：character_id（str）：角色标识。
    出参：bool，合法返回 True。
    异常：无显式异常；非法输入返回 False。
    """
    return bool(CHARACTER_ID_PATTERN.match(character_id))


def ensure_character_available(character_id: str) -> bool:
    """
    功能：确认角色存在；默认玩家缺失时执行一次种子初始化自愈，避免旧会话绑定空角色。
    入参：character_id（str）：待校验角色 ID，需已通过格式校验。
    出参：bool，角色存在或自愈成功返回 True，否则返回 False。
    异常：SQLite 查询或初始化失败时向上抛出，由路由转换为 500 或启动失败。
    """
    if _character_exists(character_id):
        return True
    if character_id != "player_01":
        return False
    logger.warning("默认角色 player_01 缺失，尝试重新导入种子数据自愈。")
    DBInitializer().initialize_db()
    return _character_exists(character_id)


def _character_exists(character_id: str) -> bool:
    """
    功能：从 Active 实体表检查角色是否存在。
    入参：character_id（str）：角色 ID。
    出参：bool，存在返回 True。
    异常：SQLite 访问异常向上抛出；调用方负责统一错误处理。
    """
    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute(
            "SELECT 1 FROM entities_active WHERE entity_id = ? LIMIT 1",
            (character_id,),
        ).fetchone()
    return row is not None


def build_memory(turns: list[dict[str, Any]], max_turns: int) -> tuple[str, list[dict[str, Any]]]:
    """
    功能：按最近有效剧情回合生成记忆摘要与可回放片段。
    入参：turns（list[dict[str, Any]]）：已由调用方过滤后的剧情回合列表。
        max_turns（int）：摘要窗口大小。
    出参：tuple[str, list[dict[str, Any]]]，分别为摘要文本和结构化片段。
    异常：无显式异常；字段缺失时按空值降级。
    """
    memory_cfg = load_main_loop_rules().get("memory", {})
    context_window_raw = memory_cfg.get("summary_context_size", max_turns)
    summary_step_raw = memory_cfg.get("summary_step", 0)
    context_window = (
        context_window_raw
        if isinstance(context_window_raw, int) and context_window_raw > 0
        else max_turns
    )
    summary_step = (
        summary_step_raw if isinstance(summary_step_raw, int) and summary_step_raw >= 2 else 0
    )
    recent = turns[-min(max_turns, context_window) :]
    items: list[dict[str, Any]] = []
    lines: list[str] = []

    def _to_line(turn: dict[str, Any]) -> tuple[int, str]:
        """
        功能：把单条回合记录转换为记忆摘要行和可回放回合 ID。
        入参：turn（dict[str, Any]）：已过滤后的回合字典，允许字段缺失。
        出参：tuple[int, str]，分别为回合序号和中文摘要行。
        异常：session_turn_id/turn_id 无法转为 int 时向上抛出，由外层摘要降级分支处理。
        """
        turn_id = int(turn.get("session_turn_id", turn.get("turn_id", 0)))
        user_input = str(turn.get("user_input", ""))
        final_response = str(turn.get("final_response", ""))
        line = f"第{turn_id}回合：输入[{user_input}] -> 响应[{final_response}]"
        return turn_id, line

    if summary_step == 0:
        for turn in recent:
            turn_id, line = _to_line(turn)
            lines.append(line)
            items.append({"session_turn_id": turn_id, "text": line})
        return "\n".join(lines), items

    # 事务边界：仅做只读拼接；不依赖外部状态写入，失败可降级到逐条拼接。
    try:
        total = len(recent)
        summarized_tail_start = total - (total % summary_step)
        for start in range(0, summarized_tail_start, summary_step):
            chunk = recent[start : start + summary_step]
            if not chunk:
                continue
            chunk_start_turn = int(chunk[0].get("session_turn_id", chunk[0].get("turn_id", 0)))
            chunk_end_turn = int(chunk[-1].get("session_turn_id", chunk[-1].get("turn_id", 0)))
            inputs = [
                str(turn.get("user_input", "")) for turn in chunk if str(turn.get("user_input", ""))
            ]
            responses = [
                str(turn.get("final_response", ""))
                for turn in chunk
                if str(turn.get("final_response", ""))
            ]
            compact_inputs = "；".join(inputs[:3])
            compact_responses = "；".join(responses[:2])
            summary_line = (
                f"第{chunk_start_turn}-{chunk_end_turn}回合阶段摘要："
                f"玩家动作[{compact_inputs}]；系统反馈[{compact_responses}]"
            )
            lines.append(summary_line)
            items.append({"session_turn_id": chunk_end_turn, "text": summary_line})
        # 不足一个步长的尾部保留原始逐条细节，避免最新上下文过度压缩。
        for turn in recent[summarized_tail_start:]:
            turn_id, line = _to_line(turn)
            lines.append(line)
            items.append({"session_turn_id": turn_id, "text": line})
    except Exception:  # noqa: BLE001
        for turn in recent:
            turn_id, line = _to_line(turn)
            lines.append(line)
            items.append({"session_turn_id": turn_id, "text": line})
    return "\n".join(lines), items


def sync_session_agent_memory_file(
    context: Any,
    session_id: str,
    memory_summary: str,
) -> None:
    """
    功能：把数据库中的长期叙事记忆导出到会话级 `.agent_context` 文件镜像。
    入参：context（Any）：运行时上下文，需提供 session_store 与 agent_context_dir；
        session_id（str）：会话标识；memory_summary（str）：兼容旧摘要镜像的短期摘要。
    出参：None。
    异常：内部捕获长期记忆读取和文件写入降级路径；不阻断已成功的 DB 回合持久化。
    """
    long_term_memory = ""
    try:
        long_term_memory = context.session_store.build_narrative_memory_context(
            session_id=session_id,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "长期叙事记忆导出读取失败，已仅同步空长期记忆镜像: session_id=%s error=%s",
            session_id,
            error,
        )
    context_dir = getattr(context, "agent_context_dir", None)
    if context_dir is None:
        logger.warning(
            "Agent 上下文目录缺失，已跳过会话长期记忆文件导出: session_id=%s",
            session_id,
        )
        return
    write_session_agent_memory(
        session_id=session_id,
        memory_summary=memory_summary,
        context_dir=context_dir,
        long_term_memory=long_term_memory,
    )


def build_turn_long_term_memory_context(
    context: Any,
    session: dict[str, Any],
    character_id: str,
    sandbox_mode: bool,
) -> str:
    """
    功能：按当前地点/NPC/任务过滤并构建本回合 GM 长期叙事记忆上下文。
    入参：context（Any）：运行时上下文，需提供 session_store；session（dict）：Web 会话快照；
        character_id（str）：当前角色；sandbox_mode（bool）：是否读取 Shadow 状态。
    出参：str，过滤后的长期叙事记忆上下文；读取失败时返回空字符串。
    异常：内部捕获场景快照和记忆读取异常，按空长期记忆降级。
    """
    scene_snapshot: dict[str, Any] | None = None
    try:
        play_state = get_play_state(
            character_id=character_id,
            sandbox_mode=sandbox_mode,
            recent_memory=str(session.get("memory_summary", "")),
            pack_id=str(session.get("pack_id", "")) or None,
        )
        raw_scene = play_state.get("scene_snapshot")
        scene_snapshot = dict(raw_scene) if isinstance(raw_scene, dict) else None
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "长期叙事记忆相关场景构建失败，已按会话级记忆降级: session_id=%s error=%s",
            session.get("session_id"),
            error,
        )

    try:
        relevance = build_narrative_memory_relevance(scene_snapshot)
        memory_context = context.session_store.build_narrative_memory_context(
            session_id=str(session["session_id"]),
            relevance=relevance,
        )
        return str(memory_context or "")
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "长期叙事记忆读取失败，已按空长期记忆继续: session_id=%s error=%s",
            session.get("session_id"),
            error,
        )
        return ""


def get_session(session_id: str) -> dict[str, Any] | None:
    """
    功能：从 session store 读取会话。
    入参：session_id（str）：会话标识。
    出参：dict[str, Any] | None，会话数据或 None。
    异常：无，委托给 session_store 处理。
    """
    context = get_runtime_context()
    return context.session_store.get_session(session_id)


def _pack_quests_json(session: dict[str, Any]) -> list[dict[str, Any]]:
    """
    功能：按 session 绑定的 pack_id 从 registry 查询任务定义，序列化为 JSON。
    入参：session（dict[str, Any]）：会话数据，需含 pack_id 字段。
    出参：list[dict[str, Any]]，打包的任务定义列表，无 pack 时返回空列表。
    异常：不抛异常；registry 未初始化或 pack 不存在时安全降级返回空列表。
    """
    context = get_runtime_context()
    pack_id = session.get("pack_id")
    if not pack_id or context.story_pack_registry is None:
        return []
    bundle = context.story_pack_registry.get(str(pack_id))
    if bundle is None:
        return []
    return [q.model_dump(mode="json") for q in bundle.quests.values()]


def _pack_triggers_json(session: dict[str, Any]) -> list[dict[str, Any]]:
    """
    功能：按 session 绑定的 pack_id 从 registry 查询触发器定义，序列化为 JSON。
    入参：session（dict[str, Any]）：会话数据，需含 pack_id 字段。
    出参：list[dict[str, Any]]，打包的触发器定义列表，无 pack 时返回空列表。
    异常：不抛异常；registry 未初始化或 pack 不存在时安全降级返回空列表。
    """
    context = get_runtime_context()
    pack_id = session.get("pack_id")
    if not pack_id or context.story_pack_registry is None:
        return []
    bundle = context.story_pack_registry.get(str(pack_id))
    if bundle is None:
        return []
    return [t.model_dump(mode="json") for t in bundle.triggers.values()]


def _quest_status_label(status: str) -> str:
    """
    功能：把任务运行态状态码转换为玩家可读中文标签。
    入参：status（str）：QuestRuntimeState.status 原始值。
    出参：str，中文状态标签。
    异常：不抛异常；未知状态按“未知”降级。
    """
    return {
        "locked": "未开始",
        "active": "进行中",
        "completed": "已完成",
        "failed": "已失败",
    }.get(status, "未知")


def _pack_active_quests_json(
    context: ApiRuntimeContext,
    pack_id: str | None,
    quest_states: Any,
) -> list[dict[str, Any]]:
    """
    功能：合并剧本包任务定义与会话运行态，生成前端可直接展示的当前任务列表。
    入参：context（ApiRuntimeContext）：API 运行时；pack_id（str | None）：当前会话绑定剧本；
        quest_states（Any）：会话元数据或主循环返回的任务运行态列表。
    出参：list[dict[str, Any]]，每项包含任务标题、状态、当前阶段与进度文案。
    异常：不抛业务异常；registry 缺失、pack 不存在或单个任务定义非法时返回可用子集。
    """
    if not pack_id or context.story_pack_registry is None:
        return []
    bundle = context.story_pack_registry.get(str(pack_id))
    if bundle is None:
        return []
    quest_defs = {
        quest_id: quest_def
        for quest_id, raw_quest in bundle.quests.items()
        if (quest_def := quest_def_from_raw(quest_id, raw_quest)) is not None
    }
    if not quest_defs:
        return []
    raw_states = quest_states if isinstance(quest_states, list) else []
    runtime_states = normalize_quest_states(raw_states, quest_defs)
    active_quests: list[dict[str, Any]] = []
    for quest_state in runtime_states:
        quest_def = quest_defs.get(quest_state.quest_id)
        if quest_def is None:
            continue
        stage_ids = [stage.stage_id for stage in quest_def.stages]
        stage_by_id = {stage.stage_id: stage for stage in quest_def.stages}
        current_stage = stage_by_id.get(quest_state.current_stage_id)
        stage_index = (
            stage_ids.index(quest_state.current_stage_id) + 1
            if quest_state.current_stage_id in stage_ids
            else 0
        )
        stage_count = len(stage_ids)
        payload = {
            "quest_id": quest_state.quest_id,
            "title": quest_def.title,
            "name": quest_def.title,
            "description": quest_def.description,
            "status": quest_state.status,
            "status_label": _quest_status_label(quest_state.status),
            "current_stage_id": quest_state.current_stage_id,
            "stage_index": stage_index,
            "stage_count": stage_count,
            "progress_label": f"{stage_index}/{stage_count}" if stage_index else "",
            "data": dict(quest_state.data),
            "started_at": quest_state.started_at,
            "updated_at": quest_state.updated_at,
        }
        if current_stage is not None:
            payload["stage_label"] = current_stage.label
            payload["stage_description"] = current_stage.description
        active_quests.append(payload)
    return active_quests


def _resolve_pack_scene_for_play_state(
    context: ApiRuntimeContext,
    pack_id: str | None,
    active_character: dict[str, Any],
) -> Any | None:
    """
    功能：为 Web 首屏/详情展示解析当前 pack 场景，避免 pack 会话展示旧 DB 场景。
    入参：context（ApiRuntimeContext）：当前 API 运行时；
        pack_id（str | None）：会话绑定的剧本包 ID，缺失时不注入 pack 场景；
        active_character（dict[str, Any]）：角色结构化状态，用于尝试匹配当前 location。
    出参：Any | None，返回 StoryPackSceneDef 兼容对象；无 pack 或未命中时返回 None。
    异常：不抛业务异常；registry 未初始化、pack 缺失或起始场景缺失时安全降级为 None。
    """
    if not pack_id or context.story_pack_registry is None:
        return None
    bundle = context.story_pack_registry.get(str(pack_id))
    if bundle is None:
        return None
    location_id = str(active_character.get("location") or "").strip()
    if location_id in bundle.scenes:
        return bundle.scenes[location_id]
    start_scene = bundle.scenes.get(bundle.manifest.start_scene_id)
    if start_scene is not None and location_id and location_id != "unknown":
        # 降级路径：新建 pack 会话可能遇到角色 DB 中残留的非 pack location；
        # 首屏展示回退到 manifest.start_scene_id，不直接写结构化角色位置。
        logger.info(
            "Web play_state 使用 pack 起始场景: pack_id=%s location=%s start_scene_id=%s",
            pack_id,
            location_id,
            bundle.manifest.start_scene_id,
        )
    return start_scene


def get_play_state(
    character_id: str,
    sandbox_mode: bool,
    recent_memory: str = "",
    pack_id: str | None = None,
    session_metadata: dict[str, Any] | None = None,
    initial_location_id: str | None = None,
) -> dict[str, Any]:
    """
    功能：读取 Web 展示所需的角色状态与场景快照，不推进回合。
    入参：character_id（str）：角色 ID；sandbox_mode（bool）：是否读取 Shadow 状态；
        recent_memory（str，默认空）：会话剧情摘要；
        pack_id（str | None，默认 None）：会话绑定的剧本包 ID，用于首屏/详情展示 pack 场景；
        session_metadata（dict[str, Any] | None，默认 None）：会话持久化任务进度来源。
        initial_location_id（str | None，默认 None）：新建/重置会话时强制展示的 pack 起点。
    出参：dict[str, Any]，包含 active_character 与 scene_snapshot。
    异常：主循环未初始化时抛 RuntimeError；数据库读取异常向上抛出。
    """
    context = get_runtime_context()
    if context.main_loop is None:
        raise RuntimeError("主循环未初始化")
    character_state = context.main_loop._build_character_state(  # noqa: SLF001
        character_id,
        use_shadow=sandbox_mode,
    )
    if character_state is None:
        raise RuntimeError(f"角色状态不存在: character_id={character_id}")
    active_character = _enrich_character_inventory(cast(dict[str, Any], character_state))
    normalized_initial_location_id = str(initial_location_id or "").strip()
    if normalized_initial_location_id:
        # 首屏边界：持久层稍后会把同一位置写入运行角色；
        # 这里先覆盖展示快照，避免 create_session 响应短暂显示上一局位置。
        active_character["location"] = normalized_initial_location_id
        active_character["current_location_id"] = normalized_initial_location_id
        _reset_initial_play_character(active_character, context.main_loop.rules)
    pack_scene = _resolve_pack_scene_for_play_state(context, pack_id, active_character)
    scene_snapshot_raw = context.main_loop._build_scene_snapshot(  # noqa: SLF001
        cast(Any, active_character),
        recent_memory=recent_memory,
        use_shadow=sandbox_mode,
        pack_scene=pack_scene,
    )
    scene_snapshot = (
        enrich_scene_snapshot_pack_assets(context, dict(scene_snapshot_raw), pack_id)
        if isinstance(scene_snapshot_raw, dict)
        else scene_snapshot_raw
    )
    if isinstance(scene_snapshot, dict):
        # 展示边界：任务进度只从后端 pack 定义与会话元数据合成，
        # 前端不推断阶段，确保加载会话和首屏显示与运行时状态一致。
        metadata = session_metadata if isinstance(session_metadata, dict) else {}
        scene_snapshot["active_quests"] = _pack_active_quests_json(
            context,
            pack_id,
            metadata.get("quest_states", []),
        )
    return {"active_character": active_character, "scene_snapshot": scene_snapshot}


def _coerce_optional_int(value: Any) -> int | None:
    """
    功能：把 Web 展示层读取到的可选数值收敛为整数。
    入参：value（Any）：可能来自 SQLite、Pydantic 或测试替身的任意值。
    出参：int | None，成功转换时返回整数，空值或非法值返回 None。
    异常：内部捕获 TypeError/ValueError，避免脏角色快照阻断建会话首屏。
    """
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _reset_initial_play_character(
    active_character: dict[str, Any],
    rules: dict[str, Any] | None,
) -> None:
    """
    功能：把新建/重置会话首屏的角色展示状态对齐到持久层即将写入的干净运行角色。
    入参：active_character（dict[str, Any]）：待返回前端的角色快照；
        rules（dict[str, Any] | None）：主循环规则，用于重新派生状态文案。
    出参：None，原地更新 active_character。
    异常：不抛异常；缺失 max_hp/max_mp 时保留原始资源值，仅清空临时状态标签。
    """
    max_hp = _coerce_optional_int(active_character.get("max_hp"))
    max_mp = _coerce_optional_int(active_character.get("max_mp"))
    if max_hp is not None:
        active_character["hp"] = max_hp
    if max_mp is not None:
        active_character["mp"] = max_mp
    active_character["state_flags"] = []
    status_summary, status_effects, status_context = derive_character_status(
        active_character,
        [],
        rules,
    )
    active_character["status_summary"] = status_summary
    active_character["status_effects"] = status_effects
    active_character["status_context"] = status_context


def build_initial_turn_payload(
    character_id: str,
    sandbox_mode: bool,
    recent_memory: str = "",
    pack_id: str | None = None,
    initial_location_id: str | None = None,
) -> dict[str, Any]:
    """
    功能：为新会话生成第 0 回合开场叙事和可点击行动，保证首屏选项也来自 GM 输出。
    入参：character_id（str）：角色 ID；sandbox_mode（bool）：是否读取 Shadow 状态；
        recent_memory（str，默认空）：会话记忆摘要，首回合通常为空；
        pack_id（str | None，默认 None）：会话绑定的剧本包 ID，用于生成 pack 首屏；
        initial_location_id（str | None，默认 None）：强制首屏使用的 pack 起点。
    出参：dict[str, Any]，包含 active_character、scene_snapshot、final_response、quick_actions。
    异常：主循环未初始化时抛 RuntimeError；GM LLM 失败由 GMAgent 内部降级为模板/场景建议。
    """
    context = get_runtime_context()
    if context.main_loop is None:
        raise RuntimeError("主循环未初始化")
    play_state = get_play_state(
        character_id,
        sandbox_mode,
        recent_memory=recent_memory,
        pack_id=pack_id,
        initial_location_id=initial_location_id,
    )
    gm_state = {
        "is_valid": True,
        "turn_outcome": "initial_scene",
        "clarification_question": "",
        "validation_errors": [],
        "action_intent": {"type": "observe", "parameters": {"initial": True}},
        "physics_diff": {},
        "active_character": play_state["active_character"],
        "scene_snapshot": play_state["scene_snapshot"],
        "rag_context": "",
    }
    # 首回合不推进持久化回合号，只借用 GM 渲染链路生成开场叙事和本轮选项。
    final_response = context.main_loop.gm_agent.render(gm_state)
    play_state["final_response"] = final_response
    play_state["quick_actions"] = context.main_loop.gm_agent.suggest_quick_actions(
        gm_state,
        final_response,
    )
    play_state["quick_action_candidates"] = [
        candidate.model_dump(mode="json")
        for candidate in context.main_loop.gm_agent.suggest_quick_action_candidates(
            gm_state,
            final_response,
            play_state["quick_actions"] if isinstance(play_state["quick_actions"], list) else [],
        )
    ]
    scene_snapshot = play_state.get("scene_snapshot")
    quick_actions = (
        play_state["quick_actions"] if isinstance(play_state["quick_actions"], list) else []
    )
    play_state["affordances"] = (
        scene_snapshot.get("affordances", []) if isinstance(scene_snapshot, dict) else []
    )
    quick_action_candidates = (
        play_state["quick_action_candidates"]
        if isinstance(play_state.get("quick_action_candidates"), list)
        else []
    )
    if isinstance(scene_snapshot, dict):
        play_state["quick_action_groups"] = _build_quick_action_groups(
            scene_snapshot,
            quick_actions,
        )
        play_state["quick_action_layout"] = _build_quick_action_layout(
            scene_snapshot,
            quick_actions,
            quick_action_candidates,
        )
    else:
        play_state["quick_action_groups"] = {"current": [], "nearby": []}
        play_state["quick_action_layout"] = {
            "common_actions": [],
            "object_actions": {},
            "diagnostics": {
                "matched_by_slot": 0,
                "matched_by_text": 0,
                "unmatched_to_common": 0,
                "unmapped_actions": [],
            },
        }
    play_state["failure_reason"] = ""
    play_state["suggested_next_step"] = (
        play_state["quick_actions"][0] if play_state["quick_actions"] else "观察周围"
    )
    play_state["outcome"] = "initial_scene"
    return play_state


def _load_item_catalog() -> dict[str, dict[str, Any]]:
    """
    功能：读取静态物品目录，供 Web 展示层把背包 ID 转换为可读名称。
    入参：无，数据来源固定为 state/data/items.json。
    出参：dict[str, dict[str, Any]]，键为 item_id，值为物品定义。
    异常：文件缺失、JSON 非法或字段缺失时内部降级为空目录，避免阻断试玩。
    """
    try:
        with open(ITEMS_DATA_PATH, encoding="utf-8") as file:
            items_raw = json.load(file)
    except OSError, json.JSONDecodeError:
        logger.exception("物品目录读取失败，背包展示降级为物品 ID。")
        return {}
    if not isinstance(items_raw, list):
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        item_id = item.get("item_id")
        if isinstance(item_id, str) and item_id:
            catalog[item_id] = item
    return catalog


def _enrich_character_inventory(active_character: dict[str, Any]) -> dict[str, Any]:
    """
    功能：补全角色背包快照中的物品展示信息。
    入参：active_character（dict[str, Any]）：主循环返回的角色状态，inventory 可为 ID 列表。
    出参：dict[str, Any]，保留原字段并新增 inventory_items 展示列表。
    异常：物品目录异常由 _load_item_catalog 捕获；未知物品按 ID 降级展示。
    """
    inventory = active_character.get("inventory", [])
    if not isinstance(inventory, list):
        inventory = []
    catalog = _load_item_catalog()
    inventory_items: list[dict[str, Any]] = []
    for raw_item in inventory:
        item_id = str(raw_item)
        item_def = catalog.get(item_id, {})
        inventory_items.append(
            {
                "item_id": item_id,
                "name": str(item_def.get("name") or item_id),
                "description": str(item_def.get("description") or "暂无物品描述。"),
                "item_type": str(item_def.get("item_type") or "unknown"),
                "effects": item_def.get("effects", []),
            }
        )
    enriched = dict(active_character)
    enriched["inventory_items"] = inventory_items
    return enriched


def _build_turn_trace(
    session: dict[str, Any],
    character_id: str,
    sandbox_mode: bool,
    trace_id: str | None,
    request_id: str,
) -> tuple[str, TurnTrace]:
    """
    功能：创建回合级 trace 并写入 API 接收阶段。
    入参：session（dict[str, Any]）：目标会话；character_id（str）：运行态角色 ID；
        sandbox_mode（bool）：是否沙盒；trace_id（str | None）：外部传入追踪号；
        request_id（str）：幂等请求号。
    出参：tuple[str, TurnTrace]，分别为最终 trace_id 与可继续追加阶段的 trace 对象。
    异常：session 缺少 session_id 时抛 KeyError，表示调用方契约破坏。
    """
    effective_trace_id = trace_id or new_trace_id()
    trace = TurnTrace(
        trace_id=effective_trace_id,
        request_id=request_id,
        session_id=str(session["session_id"]),
    )
    trace.stages.append(
        TurnTraceStage(
            stage="api.received",
            status="ok",
            at=now_iso(),
            detail={
                "character_id": character_id,
                "sandbox_mode": sandbox_mode,
            },
        )
    )
    return effective_trace_id, trace


def _build_turn_request_context(
    context: ApiRuntimeContext,
    session: dict[str, Any],
    character_id: str,
    sandbox_mode: bool,
    trace_id: str,
    request_id: str,
) -> TurnRequestContext:
    """
    功能：把 Web 会话快照转换成主循环请求上下文。
    入参：context（ApiRuntimeContext）：API 运行时；session（dict[str, Any]）：会话快照；
        character_id（str）：运行态角色 ID；sandbox_mode（bool）：是否沙盒；
        trace_id（str）：本回合追踪号；request_id（str）：幂等请求号。
    出参：TurnRequestContext，供 MainEventLoop 读取会话、记忆与剧本包元数据。
    异常：长期记忆构造异常向上抛出，由 run_turn 统一转 TurnExecutionError。
    """
    long_term_memory = build_turn_long_term_memory_context(
        context=context,
        session=session,
        character_id=character_id,
        sandbox_mode=sandbox_mode,
    )
    return TurnRequestContext(
        trace_id=trace_id,
        request_id=request_id,
        session_id=str(session["session_id"]),
        character_id=character_id,
        sandbox_mode=sandbox_mode,
        recent_memory=str(session.get("memory_summary", "")),
        long_term_memory=long_term_memory,
        pack_id=str(session.get("pack_id", "")) or None,
        session_metadata=dict(session.get("session_metadata", {})),
    )


def _raise_turn_execution_error(
    trace: TurnTrace,
    trace_id: str,
    stage_error: str,
    message: str,
    error_code: str,
    status_code: int,
    original_error: Exception,
) -> NoReturn:
    """
    功能：把主循环异常写入 trace 并转换为路由层可识别的 TurnExecutionError。
    入参：trace（TurnTrace）：当前追踪对象；trace_id（str）：追踪号；
        stage_error（str）：写入 trace.errors 的错误摘要；message（str）：对外错误消息；
        error_code（str）：API 错误码；status_code（int）：HTTP 状态码；
        original_error（Exception）：原始异常。
    出参：None；本函数总是抛出 TurnExecutionError。
    异常：固定抛出 TurnExecutionError，并保留 original_error 的异常链。
    """
    trace.stages.append(
        TurnTraceStage(
            stage="run_turn",
            status="failed",
            at=now_iso(),
            detail={"error": stage_error},
        )
    )
    trace.errors.append({"stage": "run_turn", "error": stage_error})
    logger.error("TurnTrace[%s] run_turn_failed: %s", trace_id, str(original_error))
    raise TurnExecutionError(
        message=message,
        trace_id=trace_id,
        trace=trace.model_dump(mode="json"),
        error_code=error_code,
        status_code=status_code,
    ) from original_error


def _execute_main_loop_turn(
    context: ApiRuntimeContext,
    request_context: TurnRequestContext,
    user_input: str,
    character_id: str,
    sandbox_mode: bool,
    narrative_stream_callback: Callable[[str], None] | None,
    trace: TurnTrace,
) -> dict[str, Any]:
    """
    功能：同步调用异步主循环，并统一处理超时与内部异常。
    入参：context（ApiRuntimeContext）：API 运行时；
        request_context（TurnRequestContext）：主循环上下文；
        user_input（str）：玩家输入；character_id（str）：运行态角色 ID；
        sandbox_mode（bool）：是否沙盒；narrative_stream_callback（Callable | None）：流式叙事回调；
        trace（TurnTrace）：当前 trace。
    出参：dict[str, Any]，MainEventLoop 返回的 FlowState 字典。
    异常：主循环超时或异常时抛 TurnExecutionError；main_loop 缺失时抛 RuntimeError。
    """
    if context.main_loop is None:
        raise RuntimeError("主循环未初始化")
    try:
        result = asyncio.run(
            asyncio.wait_for(
                context.main_loop.run(
                    user_input=user_input,
                    initial_character_id=character_id,
                    is_sandbox_mode=sandbox_mode,
                    recent_memory=request_context.recent_memory,
                    narrative_stream_callback=narrative_stream_callback,
                    request_context=request_context,
                ),
                timeout=TURN_TIMEOUT_SECONDS,
            )
        )
        return cast(dict[str, Any], result)
    except TimeoutError as error:
        trace.stages.append(
            TurnTraceStage(
                stage="run_turn",
                status="failed",
                at=now_iso(),
                detail={"error": "timeout"},
            )
        )
        trace.errors.append({"stage": "run_turn", "error": "timeout"})
        logger.error("TurnTrace[%s] run_turn_timeout: %s", request_context.trace_id, str(error))
        raise TurnExecutionError(
            message="回合执行超时",
            trace_id=request_context.trace_id,
            trace=trace.model_dump(mode="json"),
            error_code="TURN_TIMEOUT",
            status_code=504,
        ) from error
    except Exception as error:  # noqa: BLE001
        _raise_turn_execution_error(
            trace=trace,
            trace_id=request_context.trace_id,
            stage_error=str(error),
            message=str(error),
            error_code="INTERNAL_ERROR",
            status_code=500,
            original_error=error,
        )


def _normalize_turn_result_character(result: dict[str, Any]) -> dict[str, Any]:
    """
    功能：从主循环结果中提取角色快照并补齐背包展示字段。
    入参：result（dict[str, Any]）：MainEventLoop 返回结果。
    出参：dict[str, Any]，可直接返回前端的 active_character。
    异常：物品目录异常由 _enrich_character_inventory 内部降级；非法角色结构按空对象处理。
    """
    raw_character_obj = result.get("active_character")
    raw_character: dict[str, Any] = (
        dict(raw_character_obj) if isinstance(raw_character_obj, dict) else {}
    )
    return _enrich_character_inventory(raw_character)


def _normalize_turn_scene_snapshot(
    context: ApiRuntimeContext,
    session: dict[str, Any],
    result: dict[str, Any],
    active_character: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：提取并补齐回合场景快照，包括交互模型与剧本包资源。
    入参：context（ApiRuntimeContext）：API 运行时；session（dict[str, Any]）：会话快照；
        result（dict[str, Any]）：主循环结果；active_character（dict[str, Any]）：角色快照。
    出参：dict[str, Any]，可直接返回前端的 scene_snapshot。
    异常：资源补齐异常向上抛出，由调用方暴露为回合执行失败。
    """
    raw_scene_snapshot = result.get("scene_snapshot")
    scene_snapshot = dict(raw_scene_snapshot) if isinstance(raw_scene_snapshot, dict) else {}
    pack_id = str(session.get("pack_id", "")) or None
    # 回合响应边界：主循环返回完整 quest_states；这里把它与 pack 定义合成可读任务，
    # 让场景快照成为前端“当前任务”的单一事实来源。
    scene_snapshot["active_quests"] = _pack_active_quests_json(
        context,
        pack_id,
        result.get("quest_states", result.get("quest_updates", [])),
    )
    scene_snapshot.update(build_scene_interaction_model(scene_snapshot, active_character))
    return enrich_scene_snapshot_pack_assets(
        context,
        scene_snapshot,
        pack_id,
    )


def _append_core_turn_trace(
    trace: TurnTrace,
    result: dict[str, Any],
    scene_snapshot: dict[str, Any],
    scene_snapshot_loaded: bool,
    scene_schema_version: Any,
) -> list[Any]:
    """
    功能：追加场景、NLU、动作校验、结算和 pack runtime 的 trace 阶段。
    入参：trace（TurnTrace）：当前 trace；result（dict[str, Any]）：主循环结果；
        scene_snapshot（dict[str, Any]）：标准化后的场景快照；
        scene_snapshot_loaded（bool）：主循环原始场景是否有效；
        scene_schema_version（Any）：主循环原始场景 schema_version。
    出参：list[Any]，清洗后的 pack runtime 错误列表。
    异常：不抛异常；非法列表字段按空列表降级。
    """
    trace.stages.append(
        TurnTraceStage(
            stage="scene.loaded",
            status="ok" if scene_snapshot_loaded else "failed",
            at=now_iso(),
            detail={
                "has_scene_snapshot": scene_snapshot_loaded,
                "schema_version": scene_schema_version,
            },
        )
    )
    trace.stages.append(
        TurnTraceStage(
            stage="nlu.parsed",
            status="ok" if result.get("action_intent") is not None else "failed",
            at=now_iso(),
            detail={
                "has_action_intent": result.get("action_intent") is not None,
                "turn_outcome": str(result.get("turn_outcome", "")),
            },
        )
    )
    trace.stages.append(
        TurnTraceStage(
            stage="action.validated",
            status="ok",
            at=now_iso(),
            detail={
                "is_valid": bool(result.get("is_valid", False)),
                "errors": result.get("validation_errors", []),
            },
        )
    )
    trace.stages.append(
        TurnTraceStage(
            stage="action.resolved",
            status=("ok" if bool(result.get("is_valid", False)) else "skipped"),
            at=now_iso(),
            detail={"physics_diff": result.get("physics_diff")},
        )
    )
    pack_runtime_errors_raw = result.get("pack_runtime_errors", [])
    pack_runtime_errors = (
        pack_runtime_errors_raw if isinstance(pack_runtime_errors_raw, list) else []
    )
    if pack_runtime_errors:
        for error_item in pack_runtime_errors:
            trace.errors.append({"stage": "pack.runtime", "error": str(error_item)})
    trace.stages.append(
        TurnTraceStage(
            stage="pack.runtime",
            status=("failed" if pack_runtime_errors else "ok"),
            at=now_iso(),
            detail={
                "trigger_events_count": (
                    len(result.get("trigger_events", []))
                    if isinstance(result.get("trigger_events"), list)
                    else 0
                ),
                "quest_updates_count": (
                    len(result.get("quest_updates", []))
                    if isinstance(result.get("quest_updates"), list)
                    else 0
                ),
                "errors": pack_runtime_errors,
            },
        )
    )
    trace.stages.append(
        TurnTraceStage(
            stage="state.updated",
            status=("ok" if bool(result.get("should_advance_turn", False)) else "skipped"),
            at=now_iso(),
            detail={
                "should_advance_turn": bool(result.get("should_advance_turn", False)),
                "runtime_turn_id": int(result.get("runtime_turn_id", result.get("turn_id", 0))),
            },
        )
    )
    return pack_runtime_errors


def _append_render_turn_trace(
    trace: TurnTrace,
    active_character: dict[str, Any],
    quick_actions: list[str],
    quick_action_candidates: list[dict[str, Any]],
    quick_action_groups: dict[str, list[str]],
    quick_action_layout: dict[str, Any],
    final_response: str,
) -> None:
    """
    功能：追加 GM 渲染阶段 trace，记录快捷动作、布局和角色状态摘要。
    入参：trace（TurnTrace）：当前 trace；active_character（dict[str, Any]）：角色快照；
        quick_actions（list[str]）：快捷动作；
        quick_action_candidates（list[dict[str, Any]]）：候选动作；
        quick_action_groups（dict[str, list[str]]）：动作分组；
        quick_action_layout（dict[str, Any]）：布局结果；
        final_response（str）：最终叙事文本。
    出参：None。
    异常：不抛异常；非法列表字段按 0 计数。
    """
    state_flags = active_character.get("state_flags", [])
    status_effects = active_character.get("status_effects", [])
    layout_diagnostics = (
        quick_action_layout.get("diagnostics", {}) if isinstance(quick_action_layout, dict) else {}
    )
    trace.stages.append(
        TurnTraceStage(
            stage="gm.rendered",
            status="ok" if bool(final_response) else "failed",
            at=now_iso(),
            detail={
                "quick_actions_count": len(quick_actions),
                "quick_action_candidates_count": len(quick_action_candidates),
                "quick_actions_current_count": len(quick_action_groups["current"]),
                "quick_actions_nearby_count": len(quick_action_groups["nearby"]),
                "layout.matched_by_slot": int(layout_diagnostics.get("matched_by_slot", 0)),
                "layout.matched_by_text": int(layout_diagnostics.get("matched_by_text", 0)),
                "layout.unmatched_to_common": int(layout_diagnostics.get("unmatched_to_common", 0)),
                "state_flags_count": len(state_flags) if isinstance(state_flags, list) else 0,
                "status_effects_count": (
                    len(status_effects) if isinstance(status_effects, list) else 0
                ),
                "status_summary": str(active_character.get("status_summary", "")),
            },
        )
    )


def _append_outer_turn_trace(
    context: ApiRuntimeContext,
    trace: TurnTrace,
    result: dict[str, Any],
) -> None:
    """
    功能：追加外环投递阶段 trace，并保留 noop/skipped/failed 语义。
    入参：context（ApiRuntimeContext）：API 运行时；trace（TurnTrace）：当前 trace；
        result（dict[str, Any]）：主循环结果。
    出参：None。
    异常：main_loop 缺失时抛 RuntimeError，表示运行时初始化错误。
    """
    if context.main_loop is None:
        raise RuntimeError("主循环未初始化")
    outer_emit_result = result.get("outer_emit_result")
    outer_status: Literal["ok", "failed", "skipped"] = "skipped"
    outer_detail: dict[str, Any] = {"mode": "unknown"}
    if isinstance(context.main_loop.outer_bridge, NoOpOuterLoopBridge):
        outer_status = "skipped"
        outer_detail = {"mode": "noop"}
    elif isinstance(outer_emit_result, dict):
        candidate_status = str(outer_emit_result.get("status", "skipped"))
        if candidate_status in {"ok", "failed", "skipped"}:
            outer_status = cast(Literal["ok", "failed", "skipped"], candidate_status)
        detail = outer_emit_result.get("detail")
        if isinstance(detail, dict):
            outer_detail = detail
    trace.stages.append(
        TurnTraceStage(
            stage="outer.emitted",
            status=outer_status,
            at=now_iso(),
            detail=outer_detail,
        )
    )


def _build_turn_response_payload(
    context: ApiRuntimeContext,
    session: dict[str, Any],
    request_id: str,
    trace_id: str,
    trace: TurnTrace,
    sandbox_mode: bool,
    result: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：把主循环 FlowState 转为 Web API 回合响应负载。
    入参：context（ApiRuntimeContext）：API 运行时；session（dict[str, Any]）：会话快照；
        request_id（str）：幂等请求号；trace_id（str）：追踪号；trace（TurnTrace）：完整 trace；
        sandbox_mode（bool）：调用方请求的沙盒标记；result（dict[str, Any]）：主循环结果。
    出参：dict[str, Any]，供普通路由、SSE 路由和沙盒路由复用。
    异常：资源补齐或 trace 追加异常向上抛出，由 run_turn 转换为 TurnExecutionError。
    """
    active_character = _normalize_turn_result_character(result)
    raw_scene_snapshot = result.get("scene_snapshot")
    raw_scene_for_trace = dict(raw_scene_snapshot) if isinstance(raw_scene_snapshot, dict) else {}
    scene_snapshot = _normalize_turn_scene_snapshot(context, session, result, active_character)
    pack_runtime_errors = _append_core_turn_trace(
        trace,
        result,
        scene_snapshot,
        bool(raw_scene_for_trace),
        raw_scene_for_trace.get("schema_version"),
    )
    affordances = scene_snapshot.get("affordances", [])
    quick_actions_raw = result.get("quick_actions", [])
    quick_actions = quick_actions_raw if isinstance(quick_actions_raw, list) else []
    quick_action_candidates = _sanitize_quick_action_candidates(
        result.get("quick_action_candidates", [])
    )
    quick_action_groups = _build_quick_action_groups(scene_snapshot, quick_actions)
    quick_action_layout = _build_quick_action_layout(
        scene_snapshot,
        quick_actions,
        quick_action_candidates,
    )
    final_response = _merge_trigger_narrative(
        str(result.get("final_response", "")),
        result.get("trigger_events", []),
    )
    _append_render_turn_trace(
        trace=trace,
        active_character=active_character,
        quick_actions=quick_actions,
        quick_action_candidates=quick_action_candidates,
        quick_action_groups=quick_action_groups,
        quick_action_layout=quick_action_layout,
        final_response=final_response,
    )
    _append_outer_turn_trace(context, trace, result)
    trace.runtime_turn_id = int(result.get("runtime_turn_id", result.get("turn_id", 0)))
    logger.info(
        "TurnTrace[%s] stages=%s",
        trace_id,
        [item.stage for item in trace.stages],
    )
    return {
        "session_id": session["session_id"],
        "runtime_turn_id": int(result.get("runtime_turn_id", result.get("turn_id", 0))),
        "trace_id": trace_id,
        "request_id": request_id,
        "is_valid": bool(result.get("is_valid", False)),
        "action_intent": result.get("action_intent"),
        "physics_diff": result.get("physics_diff"),
        "final_response": final_response,
        "quick_actions": quick_actions,
        "quick_action_candidates": quick_action_candidates,
        "quick_action_groups": quick_action_groups,
        "quick_action_layout": quick_action_layout,
        "affordances": affordances if isinstance(affordances, list) else [],
        "is_sandbox_mode": bool(result.get("is_sandbox_mode", sandbox_mode)),
        "active_character": active_character,
        "scene_snapshot": scene_snapshot,
        "outcome": str(result.get("turn_outcome", "invalid")),
        "clarification_question": str(result.get("clarification_question", "")),
        "failure_reason": str(result.get("failure_reason", "")),
        "suggested_next_step": str(result.get("suggested_next_step", "")),
        "should_advance_turn": bool(result.get("should_advance_turn", False)),
        "should_write_story_memory": bool(result.get("should_write_story_memory", False)),
        "debug_trace": result.get("debug_trace", []),
        "errors": result.get("validation_errors", []),
        "trace": trace.model_dump(mode="json"),
        # A2-Plus Quest/Trigger 数据（从 pack registry 查询）
        "trigger_events": result.get("trigger_events", []),
        "quest_updates": result.get("quest_updates", []),
        "quest_states": result.get("quest_states", result.get("quest_updates", [])),
        "fired_trigger_ids": result.get("fired_trigger_ids", []),
        "pack_runtime_errors": pack_runtime_errors,
        "session_metadata": result.get("session_metadata", {}),
        "pack_quests": _pack_quests_json(session),
        "pack_triggers": _pack_triggers_json(session),
    }


def run_turn(
    session: dict[str, Any],
    user_input: str,
    character_id: str,
    sandbox_mode: bool,
    narrative_stream_callback: Callable[[str], None] | None = None,
    trace_id: str | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """
    功能：执行一次主循环回合并返回标准化结果（不直接持久化）。
    入参：session（dict[str, Any]）：目标会话。user_input（str）：玩家输入。
        character_id（str）：角色标识。sandbox_mode（bool）：是否沙盒。
        narrative_stream_callback（Callable[[str], None] | None，默认 None）：GM 叙事片段回调；
        trace_id（str | None，默认 None）：请求级追踪号；request_id（str，默认空）：幂等键。
    出参：dict[str, Any]，包含回合号、动作与叙事结果的负载。
    异常：主循环异常或超过 TURN_TIMEOUT_SECONDS（来自 `agent_model_config.yml`）时向上抛出；
        调用方负责转换为玩家可见的 API 或 SSE 错误响应。
    """
    context = get_runtime_context()
    effective_trace_id, trace = _build_turn_trace(
        session=session,
        character_id=character_id,
        sandbox_mode=sandbox_mode,
        trace_id=trace_id,
        request_id=request_id,
    )
    try:
        request_context = _build_turn_request_context(
            context=context,
            session=session,
            character_id=character_id,
            sandbox_mode=sandbox_mode,
            trace_id=effective_trace_id,
            request_id=request_id,
        )
        result = _execute_main_loop_turn(
            context=context,
            request_context=request_context,
            user_input=user_input,
            character_id=character_id,
            sandbox_mode=sandbox_mode,
            narrative_stream_callback=narrative_stream_callback,
            trace=trace,
        )
        return _build_turn_response_payload(
            context=context,
            session=session,
            request_id=request_id,
            trace_id=effective_trace_id,
            trace=trace,
            sandbox_mode=sandbox_mode,
            result=result,
        )
    except Exception as error:  # noqa: BLE001
        if isinstance(error, TurnExecutionError):
            raise
        _raise_turn_execution_error(
            trace=trace,
            trace_id=effective_trace_id,
            stage_error=str(error),
            message=str(error),
            error_code="INTERNAL_ERROR",
            status_code=500,
            original_error=error,
        )
