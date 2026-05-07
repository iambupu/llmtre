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
from datetime import UTC, datetime
from typing import Any, Literal, cast

from flask import Flask, current_app, jsonify, request

from config.agent_model_loader import load_agent_model_config
from core.event_bus import EventBus
from game_workflows.affordances import build_scene_interaction_model
from game_workflows.async_watchers import NoOpOuterLoopBridge
from game_workflows.main_event_loop import MainEventLoop
from game_workflows.main_loop_config import load_main_loop_rules
from state.contracts.turn import TurnRequestContext, TurnTrace, TurnTraceStage
from state.tools.db_initializer import DB_PATH, DBInitializer
from state.tools.runtime_schema import ensure_runtime_tables
from web_api.session_store import WebSessionStore

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REGISTRY_PATH = os.path.join(BASE_DIR, "config", "mod_registry.yml")
MODS_ROOT = os.path.join(BASE_DIR, "mods")
ITEMS_DATA_PATH = os.path.join(BASE_DIR, "state", "data", "items.json")
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
                or (
                    bool(target_location_id)
                    and target_location_id != current_location_id
                )
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
    affordances = scene_snapshot.get("affordances", [])
    if not isinstance(affordances, list):
        affordances = []
    interaction_slots = scene_snapshot.get("interaction_slots", [])
    if not isinstance(interaction_slots, list):
        interaction_slots = []
    candidates = _sanitize_quick_action_candidates(quick_action_candidates or [])
    common_actions: list[str] = []
    object_actions: dict[str, list[str]] = {}
    seen_global: set[str] = set()
    seen_per_object: dict[str, set[str]] = {}
    matched_by_slot = 0
    matched_by_text = 0
    unmatched_actions: list[str] = []

    scene_objects = scene_snapshot.get("scene_objects", [])
    valid_object_ids: set[str] = set()
    if isinstance(scene_objects, list):
        for obj in scene_objects:
            if not isinstance(obj, dict):
                continue
            object_id = str(obj.get("object_id") or "").strip()
            if not object_id:
                continue
            valid_object_ids.add(object_id)

    affordance_semantic_to_object: dict[str, str] = {}
    affordance_semantic_to_common_action: dict[str, str] = {}
    affordance_semantic_to_inventory_action: dict[str, str] = {}
    affordance_action_type_to_inventory_action: dict[str, str] = {}
    for item in affordances:
        if not isinstance(item, dict) or not bool(item.get("enabled", False)):
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
    for slot in interaction_slots:
        if not isinstance(slot, dict) or not bool(slot.get("enabled", False)):
            continue
        object_id = str(slot.get("object_id") or "").strip()
        if not object_id:
            continue
        for candidate_text in (slot.get("default_input"), slot.get("label")):
            semantic_key = _normalize_quick_action_semantic_key(str(candidate_text or "").strip())
            if semantic_key:
                slot_semantic_to_object[semantic_key] = object_id

    def _append_object_action(object_id: str, action_text: str, semantic_key: str) -> None:
        if object_id not in object_actions:
            object_actions[object_id] = []
            seen_per_object[object_id] = set()
        if semantic_key in seen_per_object[object_id]:
            return
        object_actions[object_id].append(action_text)
        seen_per_object[object_id].add(semantic_key)
        seen_global.add(semantic_key)

    def _append_common_action(action_text: str, semantic_key: str) -> None:
        if semantic_key in seen_global:
            return
        common_actions.append(action_text)
        seen_global.add(semantic_key)

    # 结构化候选优先：先用 canonical + target_hint 定位，再由 affordance/slot 约束是否可执行。
    for candidate in candidates:
        action_text = str(candidate.get("display_text") or "").strip()
        semantic_key = _normalize_quick_action_semantic_key(action_text)
        if not action_text or not semantic_key or semantic_key in seen_global:
            continue
        canonical_key = str(candidate.get("canonical_intent_key") or "").strip()
        target_hint = str(candidate.get("target_object_hint") or "").strip()
        if target_hint.startswith("location:") or target_hint.startswith("exit:"):
            if target_hint in valid_object_ids:
                _append_object_action(target_hint, action_text, semantic_key)
                matched_by_slot += 1
                continue
        if target_hint.startswith("inventory:"):
            mapped_inventory_action = affordance_semantic_to_inventory_action.get(semantic_key)
            if not mapped_inventory_action:
                unmatched_actions.append(action_text)
                continue
            _append_common_action(mapped_inventory_action, semantic_key)
            matched_by_slot += 1
            continue
        if canonical_key == "use_inventory_item":
            inventory_action = affordance_action_type_to_inventory_action.get("use_item", "")
            if inventory_action:
                mapped_semantic = _normalize_quick_action_semantic_key(inventory_action)
                if mapped_semantic:
                    _append_common_action(inventory_action, mapped_semantic)
                    matched_by_slot += 1
                    continue
        common_action = affordance_semantic_to_common_action.get(semantic_key, "")
        if common_action:
            _append_common_action(common_action, semantic_key)
            matched_by_slot += 1
            continue
        unmatched_actions.append(action_text)

    # 降级路径：quick_actions 只能映射回 enabled affordance/slot；未匹配文本只进入诊断。
    for raw_action in quick_actions:
        action_text = str(raw_action).strip()
        semantic_key = _normalize_quick_action_semantic_key(action_text)
        if not action_text or not semantic_key or semantic_key in seen_global:
            continue
        matched_object_id = affordance_semantic_to_object.get(semantic_key, "")
        if not matched_object_id:
            matched_object_id = slot_semantic_to_object.get(semantic_key, "")
        if matched_object_id:
            _append_object_action(matched_object_id, action_text, semantic_key)
            matched_by_slot += 1
            continue
        common_action = affordance_semantic_to_common_action.get(semantic_key, "")
        if common_action:
            _append_common_action(common_action, semantic_key)
            matched_by_slot += 1
            continue
        inventory_action = affordance_semantic_to_inventory_action.get(semantic_key, "")
        if inventory_action:
            mapped_semantic = _normalize_quick_action_semantic_key(inventory_action)
            if mapped_semantic:
                _append_common_action(inventory_action, mapped_semantic)
                matched_by_slot += 1
                continue
        unmatched_actions.append(action_text)

    unmapped_actions = list(dict.fromkeys(unmatched_actions))
    return {
        "common_actions": common_actions,
        "object_actions": object_actions,
        "diagnostics": {
            "matched_by_slot": matched_by_slot,
            "matched_by_text": matched_by_text,
            "unmatched_to_common": 0,
            "unmapped_actions": unmapped_actions,
        },
    }


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
        功能：初始化会话存储与会话级串行锁容器。
        入参：无。
        出参：None。
        异常：无显式异常；内存分配异常向上抛出。
        """
        self.main_loop: MainEventLoop | None = None
        self.session_store = WebSessionStore(DB_PATH)
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
    # 配置来源：MainEventLoop 会读取 config/main_loop_rules.json 的 outer_loop.default_bridge；
    # Web 层不显式覆盖外环桥，避免 API 路径把 state_changed/turn_ended 投递降级为 noop。
    context.main_loop = MainEventLoop(event_bus=event_bus)
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


def log_post_body(route_name: str, body: dict[str, Any]) -> None:
    """
    功能：记录 POST 请求体快照，帮助定位 500 错误对应的入参。
    入参：route_name（str）：业务路由名称，用于区分普通/流式回合；
        body（dict[str, Any]）：已解析的 JSON 请求体，来源于 `parse_json_body`。
    出参：None。
    异常：JSON 序列化失败时内部降级为 `repr`；日志写入异常由 logging 内部处理。
    """
    try:
        body_text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        body_text = repr(body)
    logger.info("POST 请求体: route=%s body=%s", route_name, body_text[:2000])


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
        summary_step_raw
        if isinstance(summary_step_raw, int) and summary_step_raw >= 2
        else 0
    )
    recent = turns[-min(max_turns, context_window) :]
    items: list[dict[str, Any]] = []
    lines: list[str] = []

    def _to_line(turn: dict[str, Any]) -> tuple[int, str]:
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
                str(turn.get("user_input", ""))
                for turn in chunk
                if str(turn.get("user_input", ""))
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


def get_session(session_id: str) -> dict[str, Any] | None:
    """
    功能：读取指定 session_id 的会话对象。
    入参：session_id（str）：会话标识。
    出参：dict[str, Any] | None，会话存在时返回对象，不存在返回 None。
    异常：SQL 异常向上抛出；会话不存在时返回 None。
    """
    context = get_runtime_context()
    return context.session_store.get_session(session_id)


def get_play_state(
    character_id: str,
    sandbox_mode: bool,
    recent_memory: str = "",
) -> dict[str, Any]:
    """
    功能：读取 Web 展示所需的角色状态与场景快照，不推进回合。
    入参：character_id（str）：角色 ID；sandbox_mode（bool）：是否读取 Shadow 状态；
        recent_memory（str，默认空）：会话剧情摘要。
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
    scene_snapshot = context.main_loop._build_scene_snapshot(  # noqa: SLF001
        cast(Any, active_character),
        recent_memory=recent_memory,
        use_shadow=sandbox_mode,
    )
    return {"active_character": active_character, "scene_snapshot": scene_snapshot}


def build_initial_turn_payload(
    character_id: str,
    sandbox_mode: bool,
    recent_memory: str = "",
) -> dict[str, Any]:
    """
    功能：为新会话生成第 0 回合开场叙事和可点击行动，保证首屏选项也来自 GM 输出。
    入参：character_id（str）：角色 ID；sandbox_mode（bool）：是否读取 Shadow 状态；
        recent_memory（str，默认空）：会话记忆摘要，首回合通常为空。
    出参：dict[str, Any]，包含 active_character、scene_snapshot、final_response、quick_actions。
    异常：主循环未初始化时抛 RuntimeError；GM LLM 失败由 GMAgent 内部降级为模板/场景建议。
    """
    context = get_runtime_context()
    if context.main_loop is None:
        raise RuntimeError("主循环未初始化")
    play_state = get_play_state(character_id, sandbox_mode, recent_memory=recent_memory)
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
    except (OSError, json.JSONDecodeError):
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
    if context.main_loop is None:
        raise RuntimeError("主循环未初始化")
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
    request_context = TurnRequestContext(
        trace_id=effective_trace_id,
        request_id=request_id,
        session_id=str(session["session_id"]),
        character_id=character_id,
        sandbox_mode=sandbox_mode,
        recent_memory=str(session.get("memory_summary", "")),
    )
    try:
        result = asyncio.run(
            asyncio.wait_for(
                context.main_loop.run(
                    user_input=user_input,
                    initial_character_id=character_id,
                    is_sandbox_mode=sandbox_mode,
                    recent_memory=str(session.get("memory_summary", "")),
                    narrative_stream_callback=narrative_stream_callback,
                    request_context=request_context,
                ),
                timeout=TURN_TIMEOUT_SECONDS,
            )
        )
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
        logger.error("TurnTrace[%s] run_turn_timeout: %s", effective_trace_id, str(error))
        raise TurnExecutionError(
            message="回合执行超时",
            trace_id=effective_trace_id,
            trace=trace.model_dump(mode="json"),
            error_code="TURN_TIMEOUT",
            status_code=504,
        ) from error
    except Exception as error:  # noqa: BLE001
        trace.stages.append(
            TurnTraceStage(
                stage="run_turn",
                status="failed",
                at=now_iso(),
                detail={"error": str(error)},
            )
        )
        trace.errors.append({"stage": "run_turn", "error": str(error)})
        logger.error("TurnTrace[%s] run_turn_failed: %s", effective_trace_id, str(error))
        raise TurnExecutionError(
            message=str(error),
            trace_id=effective_trace_id,
            trace=trace.model_dump(mode="json"),
            error_code="INTERNAL_ERROR",
            status_code=500,
        ) from error
    raw_character_obj = result.get("active_character")
    raw_character: dict[str, Any] = (
        dict(raw_character_obj) if isinstance(raw_character_obj, dict) else {}
    )
    active_character = _enrich_character_inventory(raw_character)
    state_flags = active_character.get("state_flags", [])
    status_effects = active_character.get("status_effects", [])
    raw_scene_snapshot = result.get("scene_snapshot")
    scene_snapshot = dict(raw_scene_snapshot) if isinstance(raw_scene_snapshot, dict) else {}
    trace.stages.append(
        TurnTraceStage(
            stage="scene.loaded",
            status="ok" if bool(scene_snapshot) else "failed",
            at=now_iso(),
            detail={
                "has_scene_snapshot": bool(scene_snapshot),
                "schema_version": scene_snapshot.get("schema_version"),
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
            status=(
                "ok"
                if bool(result.get("is_valid", False))
                else "skipped"
            ),
            at=now_iso(),
            detail={"physics_diff": result.get("physics_diff")},
        )
    )
    trace.stages.append(
        TurnTraceStage(
            stage="state.updated",
            status=(
                "ok"
                if bool(result.get("should_advance_turn", False))
                else "skipped"
            ),
            at=now_iso(),
            detail={
                "should_advance_turn": bool(result.get("should_advance_turn", False)),
                "runtime_turn_id": int(result.get("runtime_turn_id", result.get("turn_id", 0))),
            },
        )
    )
    scene_snapshot.update(build_scene_interaction_model(scene_snapshot, active_character))
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
    layout_diagnostics = (
        quick_action_layout.get("diagnostics", {})
        if isinstance(quick_action_layout, dict)
        else {}
    )
    trace.stages.append(
        TurnTraceStage(
            stage="gm.rendered",
            status="ok" if bool(str(result.get("final_response", ""))) else "failed",
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
    trace.runtime_turn_id = int(result.get("runtime_turn_id", result.get("turn_id", 0)))
    logger.info(
        "TurnTrace[%s] stages=%s",
        effective_trace_id,
        [item.stage for item in trace.stages],
    )
    return {
        "session_id": session["session_id"],
        "runtime_turn_id": int(result.get("runtime_turn_id", result.get("turn_id", 0))),
        "trace_id": effective_trace_id,
        "request_id": request_id,
        "is_valid": bool(result.get("is_valid", False)),
        "action_intent": result.get("action_intent"),
        "physics_diff": result.get("physics_diff"),
        "final_response": str(result.get("final_response", "")),
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
    }
