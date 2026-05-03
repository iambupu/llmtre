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

from core.event_bus import EventBus
from game_workflows.affordances import build_scene_interaction_model
from game_workflows.async_watchers import NoOpOuterLoopBridge
from game_workflows.main_event_loop import MainEventLoop
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
    context.main_loop = MainEventLoop(event_bus=event_bus, outer_bridge=NoOpOuterLoopBridge())
    app.extensions["tre_api_context"] = context


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
    recent = turns[-max_turns:]
    items: list[dict[str, Any]] = []
    lines: list[str] = []
    for turn in recent:
        turn_id = int(turn.get("session_turn_id", turn.get("turn_id", 0)))
        user_input = str(turn.get("user_input", ""))
        final_response = str(turn.get("final_response", ""))
        line = f"第{turn_id}回合：输入[{user_input}] -> 响应[{final_response}]"
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
    scene_snapshot = play_state.get("scene_snapshot")
    play_state["affordances"] = (
        scene_snapshot.get("affordances", []) if isinstance(scene_snapshot, dict) else []
    )
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
    异常：主循环异常或超过 TURN_TIMEOUT_SECONDS（当前 180 秒）时向上抛出；
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
    trace.stages.append(
        TurnTraceStage(
            stage="gm.rendered",
            status="ok" if bool(str(result.get("final_response", ""))) else "failed",
            at=now_iso(),
            detail={"quick_actions_count": len(result.get("quick_actions", []))},
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
        "quick_actions": result.get("quick_actions", []),
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
