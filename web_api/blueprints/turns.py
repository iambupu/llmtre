"""
功能：提供普通回合与 SSE 流式回合的 Flask 路由。
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from pydantic import ValidationError

from state.contracts.turn import TurnResult
from web_api.branch_consequences import build_branch_consequence_summaries
from web_api.narrative_memory import build_narrative_memory_items
from web_api.service import (
    DEFAULT_MEMORY_TURNS,
    MAX_MEMORY_TURNS,
    MIN_MEMORY_TURNS,
    TurnExecutionError,
    _merge_trigger_narrative,
    build_memory,
    ensure_character_available,
    error,
    get_runtime_context,
    get_session,
    log_post_body,
    logger,
    new_trace_id,
    now_iso,
    parse_json_body,
    run_turn,
    success,
    sync_session_agent_memory_file,
    validate_character_id,
    validate_request_id,
    validate_session_id,
)
from web_api.session_store import (
    IDEMPOTENCY_STATUS_ERROR,
    IDEMPOTENCY_STATUS_KEY,
    IDEMPOTENCY_STATUS_PENDING,
)

turns_blueprint = Blueprint("turns", __name__, url_prefix="/api/sessions/<session_id>/turns")


@dataclass
class _TurnExecutionPreparation:
    """
    功能：描述会话锁内回合执行前置准备结果，统一普通与 SSE 路由的事务边界。
    入参：由 _prepare_turn_execution_under_lock 构造；字段分别表示会话、幂等缓存、
        前置错误、沙盒锁状态与最终运行角色 ID。
    出参：_TurnExecutionPreparation，供调用方按 HTTP 或 SSE 协议解释。
    异常：数据类本身不抛异常；字段一致性由构造 helper 保证。
    """

    session: dict[str, Any] | None
    runtime_character_id: str
    idempotent_response: dict[str, Any] | None = None
    error_response: tuple[Any, int] | None = None
    acquired_sandbox_lock: bool = False


def _validate_turn_result_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：对外回合响应在出站前执行 A1 契约校验，防止字段漂移。
    入参：response_payload（dict[str, Any]）：准备返回给客户端的回合结果。
    出参：dict[str, Any]，通过契约模型规整后的响应体。
    异常：字段缺失或类型不匹配时抛出 ValidationError，由上层统一转为错误响应。
    """
    # 事务边界：持久化后、返回前执行模型校验；失败即视为服务端契约错误。
    validated = TurnResult.model_validate(response_payload)
    return validated.model_dump(mode="json")


def _append_trace_stage(
    payload: dict[str, Any],
    stage: str,
    status: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """
    功能：向回合 trace 追加阶段记录；trace 缺失时静默降级。
    入参：payload（dict[str, Any]）：回合负载；stage/status（str）：阶段信息；
        detail（dict[str, Any] | None，默认 None）：诊断细节。
    出参：None。
    异常：不抛异常；trace 结构非法时直接返回。
    """
    trace = payload.get("trace")
    if not isinstance(trace, dict):
        return
    stages = trace.get("stages")
    if not isinstance(stages, list):
        return
    stages.append(
        {
            "stage": stage,
            "status": status,
            "at": now_iso(),
            "detail": detail or {},
        }
    )


def _normalize_turn_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：将 run_turn 响应中的 trace 规整为可出站的最小 TurnTrace 结构。
    入参：payload（dict[str, Any]）：已组装的回合响应负载，可能包含缺失或畸形 trace。
    出参：dict[str, Any]，至少包含 trace_id、stages、errors，并在缺少 GM 阶段时补足
        `gm.rendered` 诊断证据。
    异常：不抛异常；字段缺失时按响应负载可见信息降级生成最小追踪。
    """
    trace_id = str(payload.get("trace_id") or new_trace_id())
    trace = payload.get("trace")
    if not isinstance(trace, dict):
        trace = {"trace_id": trace_id, "stages": [], "errors": []}

    trace["trace_id"] = trace_id
    stages = trace.get("stages")
    if not isinstance(stages, list):
        trace["stages"] = []
        stages = trace["stages"]

    errors = trace.get("errors")
    if not isinstance(errors, list):
        trace["errors"] = []

    # 降级路径：测试替身或旧版 worker 可能只返回业务字段，不返回 TurnTrace。
    # 这里基于出站 payload 补齐 GM 渲染阶段，让调试面板仍能看到关键诊断指标。
    if not any(isinstance(item, dict) and item.get("stage") == "gm.rendered" for item in stages):
        active_character = payload.get("active_character")
        character = active_character if isinstance(active_character, dict) else {}
        quick_actions = payload.get("quick_actions")
        quick_action_candidates = payload.get("quick_action_candidates")
        status_effects = character.get("status_effects")
        stages.append(
            {
                "stage": "gm.rendered",
                "status": "ok" if bool(str(payload.get("final_response", ""))) else "failed",
                "at": now_iso(),
                "detail": {
                    "quick_actions_count": (
                        len(quick_actions) if isinstance(quick_actions, list) else 0
                    ),
                    "quick_action_candidates_count": (
                        len(quick_action_candidates)
                        if isinstance(quick_action_candidates, list)
                        else 0
                    ),
                    "status_effects_count": (
                        len(status_effects) if isinstance(status_effects, list) else 0
                    ),
                    "status_summary": str(character.get("status_summary", "")),
                },
            }
        )
    return trace


def _build_post_run_error_payload(
    payload: dict[str, Any] | None,
    stage: str,
    err: Exception,
) -> tuple[str, dict[str, Any]]:
    """
    功能：为 post-run 异常构造可回传的 trace_id/trace，避免普通与 SSE 错误链路断裂。
    入参：payload（dict[str, Any] | None）：run_turn 成功后的负载，可能为空；
        stage（str）：失败阶段（如 api.persisted/api.response_built）；
        err（Exception）：原始异常对象。
    出参：tuple[str, dict[str, Any]]，分别为可回传 trace_id 与最小 trace 结构。
    异常：不抛异常；trace 不可用时降级为最小结构。
    """
    fallback_trace_id = new_trace_id()
    if not isinstance(payload, dict):
        return (
            fallback_trace_id,
            {
                "trace_id": fallback_trace_id,
                "stages": [
                    {
                        "stage": stage,
                        "status": "failed",
                        "at": now_iso(),
                        "detail": {"error": str(err)},
                    }
                ],
                "errors": [{"stage": stage, "error": str(err)}],
            },
        )
    trace_id = str(payload.get("trace_id") or fallback_trace_id)
    trace = payload.get("trace")
    if not isinstance(trace, dict):
        trace = {"trace_id": trace_id, "stages": [], "errors": []}
    trace["trace_id"] = trace_id
    stages = trace.get("stages")
    if not isinstance(stages, list):
        trace["stages"] = []
        stages = trace["stages"]
    stages.append(
        {
            "stage": stage,
            "status": "failed",
            "at": now_iso(),
            "detail": {"error": str(err)},
        }
    )
    errors = trace.get("errors")
    if not isinstance(errors, list):
        trace["errors"] = []
        errors = trace["errors"]
    errors.append({"stage": stage, "error": str(err)})
    return trace_id, trace


def _build_worker_fallback_error_payload(
    trace_id: str,
    stage: str,
    err: Exception,
) -> dict[str, Any]:
    """
    功能：为 SSE worker 全链路兜底异常构造最小错误负载，确保客户端总能收到 error 事件。
    入参：trace_id（str）：本次请求预生成追踪号；stage（str）：失败阶段标识；
        err（Exception）：捕获到的异常对象。
    出参：dict[str, Any]，包含 code/message/trace_id/trace 的 SSE error 负载。
    异常：函数内部不抛出异常；任何构造失败都由调用方外层兜底。
    """
    trace = {
        "trace_id": trace_id,
        "stages": [
            {
                "stage": stage,
                "status": "failed",
                "at": now_iso(),
                "detail": {"error": str(err)},
            }
        ],
        "errors": [{"stage": stage, "error": str(err)}],
    }
    return {
        "code": "INTERNAL_ERROR",
        "message": f"回合执行失败: {err}",
        "trace_id": trace_id,
        "trace": trace,
    }


def _build_api_error_body(
    code: str,
    message: str,
    trace_id: str,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    功能：构造可缓存的 API 错误响应体，供 post-run 幂等失败重放。
    入参：code/message/trace_id（str）：错误码、用户可见消息与追踪号；
        trace（dict[str, Any] | None，默认 None）：可选 TurnTrace。
    出参：dict[str, Any]，与 `error()` 返回体同构的 JSON 对象。
    异常：不抛异常；trace 非 dict 时不写入响应体。
    """
    body: dict[str, Any] = {
        "ok": False,
        "trace_id": trace_id,
        "error": {"code": code, "message": message},
    }
    if isinstance(trace, dict):
        body["trace"] = trace
    return body


def _error_body_to_sse_payload(body: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把普通 API 错误响应体转换为 SSE error 事件 payload。
    入参：body（dict[str, Any]）：缓存的 API 错误响应体。
    出参：dict[str, Any]，至少包含 code/message，可选 trace_id/trace。
    异常：不抛异常；字段缺失时按 INTERNAL_ERROR 降级。
    """
    error_obj = body.get("error")
    error_payload = error_obj if isinstance(error_obj, dict) else {}
    payload: dict[str, Any] = {
        "code": str(error_payload.get("code") or "INTERNAL_ERROR"),
        "message": str(error_payload.get("message") or "回合执行失败"),
    }
    if isinstance(body.get("trace_id"), str):
        payload["trace_id"] = body["trace_id"]
    if isinstance(body.get("trace"), dict):
        payload["trace"] = body["trace"]
    return payload


def _cached_idempotent_http_response(cached: dict[str, Any]) -> tuple[Any, int]:
    """
    功能：把幂等缓存解释为普通 HTTP 响应，区分成功、pending 与 post-run 错误。
    入参：cached（dict[str, Any]）：`WebSessionStore.reserve_idempotent_request` 返回的缓存对象。
    出参：tuple[Any, int]，可直接作为 Flask 路由返回值。
    异常：不抛业务异常；缓存结构异常时按 pending 冲突响应降级，避免误包装为成功。
    """
    status = cached.get(IDEMPOTENCY_STATUS_KEY)
    if status == IDEMPOTENCY_STATUS_ERROR:
        body = cached.get("body")
        response_body = (
            body
            if isinstance(body, dict)
            else _build_api_error_body(
                code="INTERNAL_ERROR",
                message="回合执行失败",
                trace_id=new_trace_id(),
            )
        )
        raw_status_code = cached.get("status_code")
        status_code = raw_status_code if isinstance(raw_status_code, int) else 500
        return jsonify(response_body), status_code
    if status == IDEMPOTENCY_STATUS_PENDING:
        return error("REQUEST_IN_PROGRESS", "同一 request_id 的回合仍在处理，请稍后重试", 409)
    return success(cached)


def _cached_idempotent_sse_event(cached: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    功能：把幂等缓存解释为 SSE 事件，确保错误重放不会伪装成 done。
    入参：cached（dict[str, Any]）：幂等缓存对象。
    出参：tuple[str, dict[str, Any]]，事件名与事件 payload。
    异常：不抛业务异常；异常缓存结构按 REQUEST_IN_PROGRESS 降级。
    """
    status = cached.get(IDEMPOTENCY_STATUS_KEY)
    if status == IDEMPOTENCY_STATUS_ERROR:
        body = cached.get("body")
        response_body = (
            body
            if isinstance(body, dict)
            else _build_api_error_body(
                code="INTERNAL_ERROR",
                message="回合执行失败",
                trace_id=new_trace_id(),
            )
        )
        return "error", _error_body_to_sse_payload(response_body)
    if status == IDEMPOTENCY_STATUS_PENDING:
        return (
            "error",
            {
                "code": "REQUEST_IN_PROGRESS",
                "message": "同一 request_id 的回合仍在处理，请稍后重试",
            },
        )
    return "done", cached


def _cache_post_run_error_response(
    context: Any,
    session_id: str,
    request_id: str,
    response_body: dict[str, Any],
    status_code: int,
) -> None:
    """
    功能：缓存 post-run 错误响应，阻止同一 request_id 重试时再次执行主循环。
    入参：context（Any）：运行时上下文；session_id/request_id（str）：幂等键；
        response_body（dict[str, Any]）：错误响应体；status_code（int）：HTTP 状态码。
    出参：None。
    异常：缓存写入失败时内部记录异常并降级，不覆盖原始 post-run 错误响应。
    """
    try:
        context.session_store.save_idempotent_error_response(
            scope="create_turn",
            session_id=session_id,
            request_id=request_id,
            response_body=response_body,
            status_code=status_code,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "post-run 错误响应缓存失败: route=create_turn session_id=%s request_id=%s",
            session_id,
            request_id,
        )


def _clear_pending_idempotent_request(context: Any, session_id: str, request_id: str) -> None:
    """
    功能：清理由本次请求创建但尚未完成的幂等 pending 占位。
    入参：context（Any）：运行时上下文；session_id/request_id（str）：幂等键。
    出参：None。
    异常：清理失败时内部记录异常并降级，避免覆盖主业务错误。
    """
    try:
        context.session_store.clear_pending_idempotent_request(
            scope="create_turn",
            session_id=session_id,
            request_id=request_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "pending 幂等占位清理失败: route=create_turn session_id=%s request_id=%s",
            session_id,
            request_id,
        )


def _build_turn_response_payload(
    payload: dict[str, Any],
    session_id: str,
    request_id: str,
    memory_summary: str,
    session_turn_id: int,
) -> dict[str, Any]:
    """
    功能：按统一契约组装回合响应，并在出站前完成校验。
    入参：payload（dict[str, Any]）：run_turn 结果；session_id（str）：会话标识；
        request_id（str）：请求标识；memory_summary（str）：会话记忆摘要；
        session_turn_id（int）：持久化后的会话回合号。
    出参：dict[str, Any]，已通过 TurnResult 契约校验的响应体。
    异常：字段缺失或类型非法时抛 ValidationError，交由上层统一转错误响应。
    """
    trigger_events = payload.get("trigger_events", [])
    branch_consequences = build_branch_consequence_summaries(
        payload=payload,
        source_turn_id=session_turn_id,
    )
    final_response = _merge_trigger_narrative(
        str(payload["final_response"]),
        trigger_events,
    )
    response_payload = {
        "session_id": session_id,
        "session_turn_id": session_turn_id,
        "runtime_turn_id": payload["runtime_turn_id"],
        "trace_id": payload["trace_id"],
        "request_id": request_id,
        "is_valid": payload["is_valid"],
        "action_intent": payload["action_intent"],
        "physics_diff": payload["physics_diff"],
        "final_response": final_response,
        "quick_actions": payload["quick_actions"],
        "quick_action_candidates": payload.get("quick_action_candidates", []),
        "quick_action_groups": payload.get("quick_action_groups", {"current": [], "nearby": []}),
        "quick_action_layout": payload.get(
            "quick_action_layout",
            {"common_actions": [], "object_actions": {}},
        ),
        "affordances": payload["affordances"],
        "memory_summary": memory_summary,
        "active_character": payload["active_character"],
        "trace": payload.get("trace"),
        "scene_snapshot": payload["scene_snapshot"],
        "outcome": payload["outcome"],
        "clarification_question": payload["clarification_question"],
        "failure_reason": payload["failure_reason"],
        "suggested_next_step": payload["suggested_next_step"],
        "should_advance_turn": payload["should_advance_turn"],
        "should_write_story_memory": payload["should_write_story_memory"],
        "debug_trace": payload["debug_trace"],
        "errors": payload["errors"],
        "trigger_events": trigger_events,
        "quest_updates": payload.get("quest_updates", []),
        "quest_states": payload.get("quest_states", payload.get("quest_updates", [])),
        "branch_consequences": branch_consequences,
        "pack_runtime_errors": payload.get("pack_runtime_errors", []),
    }
    response_payload["trace"] = _normalize_turn_trace(response_payload)
    _append_trace_stage(
        response_payload,
        stage="api.persisted",
        status="ok",
        detail={"session_turn_id": session_turn_id},
    )
    if isinstance(response_payload.get("trace"), dict):
        response_payload["trace"]["session_turn_id"] = session_turn_id
    return _validate_turn_result_payload(response_payload)


def _build_player_visible_turn_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    功能：把主循环原始回合结果规整为玩家实际看到并应持久化的回合结果。
    入参：payload（dict[str, Any]）：run_turn 返回的原始 payload，需包含 final_response。
    出参：dict[str, Any]，浅拷贝 payload，final_response 已合并触发器叙事。
    异常：缺少 final_response 时抛 KeyError，由 post-run 错误链路统一记录并缓存失败响应。
    """
    visible_payload = dict(payload)
    # 持久化边界：会话历史必须保存玩家可见文本，避免下次加载时丢失触发器追加剧情。
    visible_payload["final_response"] = _merge_trigger_narrative(
        str(payload["final_response"]),
        payload.get("trigger_events", []),
    )
    return visible_payload


def _sse(event: str, payload: dict[str, Any]) -> str:
    """
    功能：编码 SSE 事件帧。
    入参：event（str）：事件名；payload（dict[str, Any]）：事件数据。
    出参：str，符合 text/event-stream 的事件文本。
    异常：JSON 序列化失败时向上抛出，由流式路由错误处理捕获。
    """
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


def _parse_and_validate_turn_request(
    session_id: str,
    route_name: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str, bool] | tuple[tuple[Any, int]]:
    """
    功能：解析并校验回合请求公共参数，供普通与 SSE 路由复用。
    入参：session_id（str）：会话标识；route_name（str）：日志中的路由名称。
    出参：成功返回 (session, body, request_id, user_input, public_character_id,
        runtime_character_id, sandbox_mode)；校验失败返回单元素元组，元素为 error(...) 响应。
    异常：不抛业务异常；全部转换为受控错误响应。
    """
    if not validate_session_id(session_id):
        return (error("INVALID_ARGUMENT", "session_id 格式非法", 400),)
    session = get_session(session_id)
    if session is None:
        return (error("SESSION_NOT_FOUND", "session_id 不存在", 404),)
    body = parse_json_body()
    log_post_body(route_name, body)
    request_id = validate_request_id(body)
    if request_id is None:
        return (error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400),)
    user_input = body.get("user_input")
    if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 500:
        return (error("INVALID_ARGUMENT", "user_input 不能为空且长度需在 1..500", 400),)
    requested_character_id = str(body.get("character_id", session["character_id"]))
    if not validate_character_id(requested_character_id):
        return (error("INVALID_ARGUMENT", "character_id 格式非法", 400),)
    public_character_id = str(session["character_id"])
    runtime_character_id = str(session.get("runtime_character_id") or public_character_id)
    allowed_character_ids = {public_character_id, runtime_character_id}
    if requested_character_id not in allowed_character_ids:
        return (error("TURN_CONFLICT", "character_id 与会话绑定不一致", 409),)
    if not ensure_character_available(runtime_character_id):
        return (error("CHARACTER_NOT_FOUND", "会话绑定角色不存在，无法执行回合", 404),)
    sandbox_mode = bool(body.get("sandbox_mode", session["sandbox_mode"]))
    return (
        session,
        body,
        request_id,
        user_input.strip(),
        public_character_id,
        runtime_character_id,
        sandbox_mode,
    )


def _resolve_memory_policy(
    body: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any] | tuple[Any, int]:
    """
    功能：解析 memory 策略并做边界校验。
    入参：body（dict[str, Any]）：请求体；session（dict[str, Any]）：会话快照。
    出参：合法策略 dict[str, Any]，或错误响应 tuple[Any, int]。
    异常：不抛业务异常；参数错误直接返回 INVALID_ARGUMENT。
    """
    memory_cfg = body.get("memory")
    memory_policy = dict(session["memory_policy"])
    if not isinstance(memory_cfg, dict):
        return memory_policy
    mode = memory_cfg.get("mode", "auto")
    max_turns = memory_cfg.get("max_turns", DEFAULT_MEMORY_TURNS)
    if mode != "auto":
        return error("INVALID_ARGUMENT", "memory.mode 仅支持 auto", 400)
    if not isinstance(max_turns, int) or not (MIN_MEMORY_TURNS <= max_turns <= MAX_MEMORY_TURNS):
        return error("INVALID_ARGUMENT", "memory.max_turns 需在 5..100", 400)
    return {"mode": "auto", "max_turns": max_turns}


def _refresh_session_and_apply_memory_policy(
    context: Any,
    session_id: str,
    body: dict[str, Any],
    character_id: str,
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """
    功能：在会话锁内刷新会话快照，并统一解析/写入本次请求的 memory 策略。
    入参：context（Any）：API 运行时上下文，需提供 session_store；session_id（str）：会话 ID；
        body（dict[str, Any]）：已解析请求体，memory 配置来源；
        character_id（str）：已通过前置校验的请求角色 ID，可为基准或运行角色。
    出参：tuple[dict[str, Any] | None, tuple[Any, int] | None]；成功返回最新 session 与 None，
        失败返回 None 与可直接返回或转 SSE error 的错误响应。
    异常：底层会话读取或策略写入的 SQL 异常不在此捕获，由调用方按普通/SSE 链路降级。
    """
    # 事务边界：调用方已持有 session_lock，避免普通与 SSE 并发回合写出不同 memory_policy。
    fresh_session = get_session(session_id)
    if fresh_session is None:
        return None, error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    public_character_id = str(fresh_session["character_id"])
    runtime_character_id = str(fresh_session.get("runtime_character_id") or public_character_id)
    if character_id not in {public_character_id, runtime_character_id}:
        return None, error("TURN_CONFLICT", "character_id 与会话绑定不一致", 409)
    memory_policy = _resolve_memory_policy(body, fresh_session)
    if isinstance(memory_policy, tuple):
        return None, memory_policy
    if memory_policy != fresh_session["memory_policy"]:
        context.session_store.update_memory_policy(
            session_id=session_id,
            memory_policy=memory_policy,
            now_iso=now_iso(),
        )
        fresh_session["memory_policy"] = memory_policy
    return fresh_session, None


def _build_memory_summary_if_needed(
    context: Any,
    session_id: str,
    session: dict[str, Any],
    user_input: str,
    payload: dict[str, Any],
) -> str:
    """
    功能：按策略生成回合后的记忆摘要；无需写入时复用旧摘要。
    入参：context（Any）：运行时上下文；session_id/session（会话信息）；
        user_input（str）：玩家输入；payload（dict[str, Any]）：回合结果。
    出参：str，最终记忆摘要文本。
    异常：存储层或摘要构建异常向上抛出，由上层统一记录并降级。
    """
    memory_summary = str(session.get("memory_summary", ""))
    if not payload["should_write_story_memory"]:
        return memory_summary
    recent_turns = context.session_store.get_recent_story_turns_for_memory(
        session_id=session_id,
        max_turns=int(session["memory_policy"]["max_turns"]),
    )
    draft_turn_id = int(session["current_turn_id"]) + 1
    draft_turns = recent_turns + [
        {
            "turn_id": draft_turn_id,
            "session_turn_id": draft_turn_id,
            "user_input": user_input,
            "final_response": payload["final_response"],
        }
    ]
    memory_summary, _ = build_memory(
        turns=draft_turns,
        max_turns=int(session["memory_policy"]["max_turns"]),
    )
    return memory_summary


def _ensure_sandbox_lock_preconditions(
    context: Any,
    session: dict[str, Any],
    sandbox_mode: bool,
) -> tuple[dict[str, Any], tuple[Any, int] | None, bool]:
    """
    功能：校验并准备沙盒租约，确保全局仅允许单会话进入沙盒写路径。
    入参：context（Any）：运行时上下文；session（dict[str, Any]）：会话快照；
        sandbox_mode（bool）：本次请求是否声明使用沙盒。
    出参：tuple[dict[str, Any], tuple[Any, int] | None, bool]，依次为最新会话快照、
        错误响应（通过时为 None）、以及是否在本次请求中完成租约抢占。
    异常：函数内部不抛业务异常；底层 DB 异常交由上层统一异常处理。
    """
    fresh_session = get_session(session["session_id"])
    if fresh_session is None:
        return session, error("SESSION_NOT_FOUND", "session_id 不存在", 404), False
    if not sandbox_mode:
        return fresh_session, None, False
    if context.main_loop is None:
        return fresh_session, error("INTERNAL_ERROR", "主循环未初始化", 500), False
    db_updater = context.main_loop.db_updater
    acquired_now = False
    if bool(fresh_session.get("sandbox_mode", False)):
        if not db_updater.is_sandbox_owner(fresh_session["session_id"]):
            return (
                fresh_session,
                error("SANDBOX_OWNER_MISMATCH", "当前会话未持有沙盒租约", 409),
                False,
            )
    else:
        if not db_updater.acquire_sandbox_lock(fresh_session["session_id"]):
            return (
                fresh_session,
                error("SANDBOX_BUSY", "已有其他会话占用沙盒，请稍后重试", 409),
                False,
            )
        acquired_now = True
    return fresh_session, None, acquired_now


def _prepare_turn_execution_under_lock(
    *,
    context: Any,
    session_id: str,
    request_id: str,
    body: dict[str, Any],
    public_character_id: str,
    runtime_character_id: str,
    sandbox_mode: bool,
) -> _TurnExecutionPreparation:
    """
    功能：在会话锁内完成回合执行前置准备，统一普通与 SSE 的状态写入顺序。
    入参：context（Any）：运行时上下文；session_id/request_id（str）：会话与幂等键；
        body（dict[str, Any]）：请求体；public_character_id/runtime_character_id（str）：角色 ID；
        sandbox_mode（bool）：本次回合是否走沙盒写路径。
    出参：_TurnExecutionPreparation，包含可继续执行的会话或需短路的错误/幂等结果。
    异常：底层存储或幂等读取异常不捕获，交由普通/SSE 外层按各自协议降级。
    """
    refreshed_session, policy_error = _refresh_session_and_apply_memory_policy(
        context=context,
        session_id=session_id,
        body=body,
        character_id=public_character_id,
    )
    if policy_error is not None:
        return _TurnExecutionPreparation(
            session=None,
            runtime_character_id=runtime_character_id,
            error_response=policy_error,
        )
    if refreshed_session is None:
        return _TurnExecutionPreparation(
            session=None,
            runtime_character_id=runtime_character_id,
            error_response=error("SESSION_NOT_FOUND", "session_id 不存在", 404),
        )

    runtime_character_id = str(
        refreshed_session.get("runtime_character_id") or runtime_character_id
    )
    existing = context.session_store.reserve_idempotent_request(
        scope="create_turn",
        session_id=session_id,
        request_id=request_id,
    )
    if existing is not None:
        return _TurnExecutionPreparation(
            session=refreshed_session,
            runtime_character_id=runtime_character_id,
            idempotent_response=existing,
        )

    session, precheck_error, acquired_sandbox_lock = _ensure_sandbox_lock_preconditions(
        context=context,
        session=refreshed_session,
        sandbox_mode=sandbox_mode,
    )
    if precheck_error is not None:
        _clear_pending_idempotent_request(context, session_id, request_id)
        return _TurnExecutionPreparation(
            session=session,
            runtime_character_id=runtime_character_id,
            error_response=precheck_error,
        )
    return _TurnExecutionPreparation(
        session=session,
        runtime_character_id=runtime_character_id,
        acquired_sandbox_lock=acquired_sandbox_lock,
    )


def _release_sandbox_lock_if_acquired(
    *,
    context: Any,
    session_id: str,
    acquired_sandbox_lock: bool,
) -> None:
    """
    功能：在本请求确实抢占沙盒锁时释放租约，供成功与异常路径共同调用。
    入参：context（Any）：运行时上下文；session_id（str）：会话 ID；
        acquired_sandbox_lock（bool）：本请求是否新抢占沙盒锁。
    出参：None。
    异常：底层释放异常向上抛出，由调用方按当前回合阶段处理。
    """
    if not acquired_sandbox_lock:
        return
    main_loop = context.main_loop
    if main_loop is not None:
        main_loop.db_updater.release_sandbox_lock(session_id=session_id)


def _run_turn_and_release_sandbox_if_needed(
    *,
    context: Any,
    session: dict[str, Any],
    session_id: str,
    user_input: str,
    runtime_character_id: str,
    sandbox_mode: bool,
    acquired_sandbox_lock: bool,
    trace_id: str,
    request_id: str,
    narrative_stream_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """
    功能：执行 run_turn，并在本请求抢占沙盒锁但结果未进入沙盒时释放租约。
    入参：context/session/session_id/user_input/runtime_character_id/sandbox_mode 为回合参数；
        acquired_sandbox_lock（bool）：是否需负责释放沙盒锁；trace_id/request_id（str）：追踪键；
        narrative_stream_callback（Callable | None）：SSE 增量回调，普通路由为 None。
    出参：dict[str, Any]，run_turn 返回的回合 payload。
    异常：run_turn 或沙盒锁释放异常向上抛出，由调用方清理幂等 pending 并转换响应。
    """
    payload = run_turn(
        session,
        user_input,
        runtime_character_id,
        sandbox_mode,
        narrative_stream_callback=narrative_stream_callback,
        trace_id=trace_id,
        request_id=request_id,
    )
    if acquired_sandbox_lock and not bool(payload.get("is_sandbox_mode", False)):
        _release_sandbox_lock_if_acquired(
            context=context,
            session_id=session_id,
            acquired_sandbox_lock=True,
        )
    return payload


def _persist_turn_result_and_memory(
    *,
    context: Any,
    session_id: str,
    request_id: str,
    session: dict[str, Any],
    user_input: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    功能：生成回合记忆、持久化回合结果、写入叙事记忆项并同步会话 MEMORY 文件。
    入参：context（Any）：运行时上下文；session_id/request_id（str）：会话与幂等键；
        session（dict[str, Any]）：已刷新会话；user_input（str）：玩家输入；
        payload（dict[str, Any]）：run_turn 结果。
    出参：dict[str, Any]，已通过 TurnResult 校验的响应 payload。
    异常：记忆生成、持久化、契约校验或文件同步异常向上抛出，由 post-run 错误链路处理。
    """
    visible_payload = _build_player_visible_turn_payload(payload)
    memory_summary = _build_memory_summary_if_needed(
        context=context,
        session_id=session_id,
        session=session,
        user_input=user_input,
        payload=visible_payload,
    )
    response_payload, _ = context.session_store.persist_turn_result_with_idempotency(
        scope="create_turn",
        session_id=session_id,
        request_id=request_id,
        user_input=user_input,
        turn_result=visible_payload,
        memory_summary=memory_summary,
        now_iso=now_iso(),
        response_builder=lambda persisted_turn_id: _build_turn_response_payload(
            payload=visible_payload,
            session_id=session_id,
            request_id=request_id,
            memory_summary=memory_summary,
            session_turn_id=persisted_turn_id,
        ),
        memory_items_builder=lambda persisted_turn_id: build_narrative_memory_items(
            session_id=session_id,
            session_turn_id=persisted_turn_id,
            user_input=user_input,
            turn_result=visible_payload,
        ),
    )
    sync_session_agent_memory_file(context, session_id, memory_summary)
    return cast(dict[str, Any], response_payload)


def _build_and_cache_post_run_error_response(
    *,
    context: Any,
    session_id: str,
    request_id: str,
    payload: dict[str, Any],
    err: Exception,
) -> tuple[dict[str, Any], int, str]:
    """
    功能：把持久化/响应构建失败转换为可缓存错误响应，阻止幂等重试重复执行主循环。
    入参：context（Any）：运行时上下文；session_id/request_id（str）：会话与幂等键；
        payload（dict[str, Any]）：run_turn 成功返回的负载；err（Exception）：post-run 异常。
    出参：tuple[dict[str, Any], int, str]，错误响应体、HTTP 状态码与失败阶段。
    异常：缓存失败由 _cache_post_run_error_response 内部降级；本函数不抛业务异常。
    """
    stage = "api.response_built" if isinstance(err, ValidationError) else "api.persisted"
    trace_id, trace = _build_post_run_error_payload(payload, stage=stage, err=err)
    response_body = _build_api_error_body(
        code="INTERNAL_ERROR",
        message=f"回合执行失败: {err}",
        trace_id=trace_id,
        trace=trace,
    )
    _cache_post_run_error_response(
        context=context,
        session_id=session_id,
        request_id=request_id,
        response_body=response_body,
        status_code=500,
    )
    return response_body, 500, stage


def _put_sse_error_from_http_response(
    target_queue: queue.Queue[tuple[str, dict[str, Any]]],
    error_response: tuple[Any, int],
) -> None:
    """
    功能：把普通 error(...) 响应转换为 SSE error 事件并写入队列。
    入参：target_queue（queue.Queue）：SSE 事件队列；
        error_response（tuple[Any, int]）：HTTP 错误响应。
    出参：None。
    异常：响应体结构非法时降级为 INTERNAL_ERROR，不向外抛出业务异常。
    """
    body = error_response[0].get_json()
    if isinstance(body, dict):
        target_queue.put(("error", _error_body_to_sse_payload(body)))
        return
    target_queue.put(
        (
            "error",
            {
                "code": "INTERNAL_ERROR",
                "message": "回合执行失败",
            },
        )
    )


@turns_blueprint.post("")
def create_turn(session_id: str) -> tuple[Any, int]:
    """
    功能：执行单回合主循环，并写入会话回合历史与记忆摘要。
    入参：session_id（path）和 JSON（request_id、user_input、character_id、memory 等）。
    出参：tuple[Any, int]，成功返回 200 与回合结果。
    异常：超时转换为 TURN_TIMEOUT 并返回 504；主循环异常转换为 INTERNAL_ERROR；
        参数冲突返回 TURN_CONFLICT。
    """
    parsed = _parse_and_validate_turn_request(session_id=session_id, route_name="create_turn")
    if len(parsed) == 1:
        return parsed[0]
    (
        session,
        body,
        request_id,
        user_input,
        public_character_id,
        runtime_character_id,
        sandbox_mode,
    ) = parsed

    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        preparation = _prepare_turn_execution_under_lock(
            context=context,
            session_id=session_id,
            request_id=request_id,
            body=body,
            public_character_id=public_character_id,
            runtime_character_id=runtime_character_id,
            sandbox_mode=sandbox_mode,
        )
        if preparation.error_response is not None:
            return preparation.error_response
        if preparation.idempotent_response is not None:
            return _cached_idempotent_http_response(preparation.idempotent_response)
        if preparation.session is None:
            return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
        session = preparation.session
        runtime_character_id = preparation.runtime_character_id
        try:
            trace_id = new_trace_id()
            payload = _run_turn_and_release_sandbox_if_needed(
                context=context,
                session=session,
                session_id=session_id,
                user_input=user_input,
                runtime_character_id=runtime_character_id,
                sandbox_mode=sandbox_mode,
                acquired_sandbox_lock=preparation.acquired_sandbox_lock,
                trace_id=trace_id,
                request_id=request_id,
            )
        except TurnExecutionError as err:
            _release_sandbox_lock_if_acquired(
                context=context,
                session_id=session_id,
                acquired_sandbox_lock=preparation.acquired_sandbox_lock,
            )
            _clear_pending_idempotent_request(context, session_id, request_id)
            logger.exception(
                "回合执行失败: route=create_turn session_id=%s request_id=%s",
                session_id,
                request_id,
            )
            message = (
                "回合执行超时：本地模型超过 3 分钟仍未完成，请稍后重试或改用更短的行动描述。"
                if err.error_code == "TURN_TIMEOUT"
                else f"回合执行失败: {err}"
            )
            return error(
                err.error_code,
                message,
                err.status_code,
                trace_id=err.trace_id,
                trace=err.trace,
            )
        except Exception:
            _release_sandbox_lock_if_acquired(
                context=context,
                session_id=session_id,
                acquired_sandbox_lock=preparation.acquired_sandbox_lock,
            )
            _clear_pending_idempotent_request(context, session_id, request_id)
            raise
        try:
            response_payload = _persist_turn_result_and_memory(
                context=context,
                session_id=session_id,
                session=session,
                request_id=request_id,
                user_input=user_input,
                payload=payload,
            )
            return success(response_payload)
        except Exception as err:  # noqa: BLE001
            response_body, status_code, stage = _build_and_cache_post_run_error_response(
                context=context,
                session_id=session_id,
                request_id=request_id,
                payload=payload,
                err=err,
            )
            logger.exception(
                "回合 post-run 失败: route=create_turn stage=%s session_id=%s request_id=%s",
                stage,
                session_id,
                request_id,
            )
            return jsonify(response_body), status_code


def _emit_sse_progress_events(target_queue: queue.Queue[tuple[str, dict[str, Any]]]) -> None:
    """
    功能：向 SSE 队列推送固定阶段进度事件（7 个阶段）。
    事件序列：loading_scene → parsing_nlu → validating_action → resolving_action →
    rendering_gm → evaluating_triggers → resolving_quests。
    入参：target_queue（queue.Queue）：SSE 事件队列。
    出参：None。
    异常：队列写入异常向上抛出，由上层 worker 统一兜底。
    """
    target_queue.put(("loading_scene", {"message": "读取场景快照"}))
    target_queue.put(("parsing_nlu", {"message": "理解玩家意图"}))
    target_queue.put(("validating_action", {"message": "校验动作合法性"}))
    target_queue.put(("resolving_action", {"message": "执行确定性结算"}))
    target_queue.put(("rendering_gm", {"message": "生成叙事响应"}))
    target_queue.put(("evaluating_triggers", {"message": "评估剧本触发器"}))
    target_queue.put(("resolving_quests", {"message": "推进任务状态"}))


def _emit_sse_detail_events(
    target_queue: queue.Queue[tuple[str, dict[str, Any]]],
    payload: dict[str, Any],
) -> None:
    """
    功能：根据回合 payload 发送阶段明细事件（含 evaluating_triggers_detail /
    resolving_quests_detail），供前端展示调试信息。
    入参：target_queue（queue.Queue）：SSE 事件队列；payload（dict[str, Any]）：回合结果。
    出参：None。
    异常：不抛业务异常；字段缺失使用空值降级。
    """
    raw_scene_snapshot = payload.get("scene_snapshot")
    scene_snapshot: dict[str, Any] = (
        raw_scene_snapshot if isinstance(raw_scene_snapshot, dict) else {}
    )
    raw_current_location = scene_snapshot.get("current_location")
    current_location: dict[str, Any] = (
        raw_current_location if isinstance(raw_current_location, dict) else {}
    )
    target_queue.put(
        (
            "loading_scene_detail",
            {
                "message": "场景快照已读取",
                "detail": {
                    "location_id": current_location.get("id"),
                    "location_name": current_location.get("name"),
                    "exits_count": (
                        len(scene_snapshot.get("exits", []))
                        if isinstance(scene_snapshot.get("exits"), list)
                        else 0
                    ),
                    "visible_npcs_count": (
                        len(scene_snapshot.get("visible_npcs", []))
                        if isinstance(scene_snapshot.get("visible_npcs"), list)
                        else 0
                    ),
                    "available_actions": scene_snapshot.get("available_actions", []),
                },
            },
        )
    )
    target_queue.put(
        (
            "parsing_nlu_detail",
            {
                "message": "玩家意图解析完成",
                "detail": {
                    "action_intent": payload.get("action_intent"),
                    "outcome": payload.get("outcome"),
                    "clarification_question": payload.get("clarification_question"),
                },
            },
        )
    )
    target_queue.put(
        (
            "validating_action_detail",
            {
                "message": "动作合法性校验完成",
                "detail": {
                    "is_valid": payload.get("is_valid"),
                    "errors": payload.get("errors", []),
                    "should_advance_turn": payload.get("should_advance_turn"),
                },
            },
        )
    )
    target_queue.put(
        (
            "resolving_action_detail",
            {
                "message": "确定性结算完成",
                "detail": {
                    "physics_diff": payload.get("physics_diff"),
                    "should_write_story_memory": payload.get("should_write_story_memory"),
                },
            },
        )
    )
    trigger_events = payload.get("trigger_events")
    pack_runtime_errors = payload.get("pack_runtime_errors")
    target_queue.put(
        (
            "evaluating_triggers_detail",
            {
                "message": "剧本触发器已评估",
                "detail": {
                    "triggers_fired": (
                        len(trigger_events) if isinstance(trigger_events, list) else 0
                    ),
                    "pack_runtime_errors": (
                        len(pack_runtime_errors) if isinstance(pack_runtime_errors, list) else 0
                    ),
                },
            },
        )
    )
    quest_updates = payload.get("quest_updates")
    target_queue.put(
        (
            "resolving_quests_detail",
            {
                "message": "任务状态已推进",
                "detail": {
                    "quests_updated": (
                        len(quest_updates) if isinstance(quest_updates, list) else 0
                    ),
                },
            },
        )
    )


def _run_turn_stream_with_lock(
    context: Any,
    session_id: str,
    request_id: str,
    body: dict[str, Any],
    session: dict[str, Any],
    user_input: str,
    public_character_id: str,
    runtime_character_id: str,
    sandbox_mode: bool,
    narrative_callback: Callable[[str], None],
    fallback_trace_id: str,
    target_queue: queue.Queue[tuple[str, dict[str, Any]]],
) -> None:
    """
    功能：在会话锁内执行流式回合与持久化，并向队列发送 done/error。
    入参：context/session_id/request_id/body/session/user_input/public_character_id/
        runtime_character_id/sandbox_mode 为执行参数；
        narrative_callback（Callable[[str], None]）：GM 增量回调；
        fallback_trace_id（str）：回退 trace_id；target_queue（queue.Queue）：SSE 队列。
    出参：None。
    异常：业务异常转换为 error 事件，不向外抛出。
    """
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        preparation = _prepare_turn_execution_under_lock(
            context=context,
            session_id=session_id,
            request_id=request_id,
            body=body,
            public_character_id=public_character_id,
            runtime_character_id=runtime_character_id,
            sandbox_mode=sandbox_mode,
        )
        if preparation.error_response is not None:
            _put_sse_error_from_http_response(target_queue, preparation.error_response)
            return
        if preparation.idempotent_response is not None:
            target_queue.put(_cached_idempotent_sse_event(preparation.idempotent_response))
            return
        if preparation.session is None:
            target_queue.put(
                (
                    "error",
                    {"code": "SESSION_NOT_FOUND", "message": "session_id 不存在"},
                )
            )
            return
        session = preparation.session
        runtime_character_id = preparation.runtime_character_id
        _emit_sse_progress_events(target_queue)
        # A2-Plus: trigger/quest stage started events (SSE)
        # 功能：在 run_turn 前推送 trigger_evaluation 与 quest_resolution 的 started 事件
        target_queue.put(("trigger_evaluation", {"status": "started"}))
        target_queue.put(("quest_resolution", {"status": "started"}))
        try:
            payload = _run_turn_and_release_sandbox_if_needed(
                context=context,
                session=session,
                session_id=session_id,
                user_input=user_input,
                runtime_character_id=runtime_character_id,
                sandbox_mode=sandbox_mode,
                acquired_sandbox_lock=preparation.acquired_sandbox_lock,
                narrative_stream_callback=narrative_callback,
                trace_id=fallback_trace_id,
                request_id=request_id,
            )
        except Exception:
            _release_sandbox_lock_if_acquired(
                context=context,
                session_id=session_id,
                acquired_sandbox_lock=preparation.acquired_sandbox_lock,
            )
            _clear_pending_idempotent_request(context, session_id, request_id)
            raise
        _emit_sse_detail_events(target_queue, payload)
        # A2-Plus: trigger/quest stage done events (SSE)
        # 功能：从 run_turn 返回值获取 trigger_events/quest_updates 计数并推送 done 事件
        target_queue.put(
            (
                "trigger_evaluation",
                {
                    "status": "done",
                    "triggers_fired": len(payload.get("trigger_events", [])),
                },
            )
        )
        target_queue.put(
            (
                "quest_resolution",
                {
                    "status": "done",
                    "quests_updated": len(payload.get("quest_updates", [])),
                },
            )
        )
        try:
            response_payload = _persist_turn_result_and_memory(
                context=context,
                session_id=session_id,
                session=session,
                request_id=request_id,
                user_input=user_input,
                payload=payload,
            )
            target_queue.put(("done", response_payload))
        except Exception as err:  # noqa: BLE001
            response_body, _, _stage = _build_and_cache_post_run_error_response(
                context=context,
                session_id=session_id,
                request_id=request_id,
                payload=payload,
                err=err,
            )
            target_queue.put(("error", _error_body_to_sse_payload(response_body)))


def _generate_turn_stream_events(
    app: Any,
    context: Any,
    session_id: str,
    request_id: str,
    body: dict[str, Any],
    session: dict[str, Any],
    user_input: str,
    public_character_id: str,
    runtime_character_id: str,
    sandbox_mode: bool,
) -> Iterator[str]:
    """
    功能：生成 create_turn_stream 的 SSE 事件流。
    入参：app/context 与回合执行参数。
    出参：Iterator[str]，逐条 SSE 事件文本。
    异常：worker 中异常统一转 error 事件，主生成器不抛业务异常。
    """
    yield _sse("received", {"message": "已收到回合输入"})
    event_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
    worker_trace_id = new_trace_id()

    def emit_narrative_delta(delta: str) -> None:
        """
        功能：把 GM 流式叙事片段转投到 SSE 事件队列。
        入参：delta（str）：主循环回调产生的增量文本，空字符串会被忽略。
        出参：None。
        异常：queue.put 失败时向上抛出，由 worker 异常分支转换为 SSE error。
        """
        if delta:
            event_queue.put(("gm_delta", {"delta": delta}))

    def run_turn_worker() -> None:
        """
        功能：在后台线程内持有 Flask app context 并执行单回合流式链路。
        入参：无显式参数；闭包读取当前请求、会话、角色和队列上下文。
        出参：None；终态通过 event_queue 投递 done 或 error 事件。
        异常：TurnExecutionError 转为业务 error 事件；其他异常记录堆栈后转兜底 error。
        """
        try:
            with app.app_context():
                _run_turn_stream_with_lock(
                    context=context,
                    session_id=session_id,
                    request_id=request_id,
                    body=body,
                    session=session,
                    user_input=user_input,
                    public_character_id=public_character_id,
                    runtime_character_id=runtime_character_id,
                    sandbox_mode=sandbox_mode,
                    narrative_callback=emit_narrative_delta,
                    fallback_trace_id=worker_trace_id,
                    target_queue=event_queue,
                )
        except TurnExecutionError as err:
            logger.exception(
                "回合执行失败: route=create_turn_stream session_id=%s request_id=%s",
                session_id,
                request_id,
            )
            message = (
                "回合执行超时：本地模型超过 3 分钟仍未完成，请稍后重试或改用更短的行动描述。"
                if err.error_code == "TURN_TIMEOUT"
                else f"回合执行失败: {err}"
            )
            event_queue.put(
                (
                    "error",
                    {
                        "code": err.error_code,
                        "message": message,
                        "trace_id": err.trace_id,
                        "trace": err.trace,
                    },
                )
            )
        except Exception as err:  # noqa: BLE001
            logger.exception(
                "SSE worker 处理失败: route=create_turn_stream session_id=%s request_id=%s",
                session_id,
                request_id,
            )
            event_queue.put(
                (
                    "error",
                    _build_worker_fallback_error_payload(
                        trace_id=worker_trace_id,
                        stage="api.worker",
                        err=err,
                    ),
                )
            )

    worker = threading.Thread(target=run_turn_worker, daemon=True)
    worker.start()
    while True:
        try:
            event_name, payload = event_queue.get(timeout=0.5)
        except queue.Empty:
            if worker.is_alive():
                continue
            yield _sse(
                "error",
                _build_worker_fallback_error_payload(
                    trace_id=worker_trace_id,
                    stage="api.worker",
                    err=RuntimeError("worker exited without terminal event"),
                ),
            )
            return
        yield _sse(event_name, payload)
        if event_name in {"done", "error"}:
            return


@turns_blueprint.post("/stream")
def create_turn_stream(session_id: str) -> tuple[Any, int] | Response:
    """
    功能：以 SSE 形式执行单回合，向前端持续报告系统运算阶段。
    入参：session_id（path）和 JSON（request_id、user_input、character_id、memory 等）。
    出参：Response(text/event-stream)，最终 `done` 事件携带普通回合响应负载。
    异常：前置参数错误返回普通 JSON 错误；执行中异常通过 SSE `error` 事件返回。
    """
    parsed = _parse_and_validate_turn_request(
        session_id=session_id,
        route_name="create_turn_stream",
    )
    if len(parsed) == 1:
        return parsed[0]
    (
        session,
        body,
        request_id,
        user_input,
        public_character_id,
        runtime_character_id,
        sandbox_mode,
    ) = parsed
    context = get_runtime_context()
    app = cast(Any, current_app)._get_current_object()
    return Response(
        stream_with_context(
            _generate_turn_stream_events(
                app=app,
                context=context,
                session_id=session_id,
                request_id=request_id,
                body=body,
                session=session,
                user_input=user_input,
                public_character_id=public_character_id,
                runtime_character_id=runtime_character_id,
                sandbox_mode=sandbox_mode,
            )
        ),
        mimetype="text/event-stream",
    )


@turns_blueprint.get("")
def list_turns(session_id: str) -> tuple[Any, int]:
    """
    功能：分页查询会话回合摘要。
    入参：session_id（path），page/page_size（query）。
    出参：tuple[Any, int]，返回分页列表。
    异常：分页参数非法返回 INVALID_ARGUMENT。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    try:
        page = int(request.args.get("page", "1"))
        page_size = int(request.args.get("page_size", "20"))
    except ValueError:
        return error("INVALID_ARGUMENT", "page/page_size 必须为整数", 400)
    if page < 1 or page_size < 1 or page_size > 100:
        return error("INVALID_ARGUMENT", "page/page_size 超出范围", 400)

    context = get_runtime_context()
    total, items = context.session_store.list_turns(
        session_id=session_id,
        page=page,
        page_size=page_size,
    )
    return success(
        {
            "session_id": session_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }
    )


@turns_blueprint.get("/<int:session_turn_id>")
def get_turn(session_id: str, session_turn_id: int) -> tuple[Any, int]:
    """
    功能：查询单个回合详情。
    入参：session_id（path），session_turn_id（path）。
    出参：tuple[Any, int]，存在返回 200，不存在返回 404。
    异常：参数非法返回 INVALID_ARGUMENT。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    context = get_runtime_context()
    target = context.session_store.get_turn(
        session_id=session_id,
        session_turn_id=session_turn_id,
    )
    if target is None:
        return error("TURN_NOT_FOUND", "session_turn_id 不存在", 404)
    return success(
        {
            "session_id": session_id,
            "session_turn_id": target["session_turn_id"],
            "created_at": target["created_at"],
            "user_input": target["user_input"],
            "is_valid": target["is_valid"],
            "action_intent": target["action_intent"],
            "physics_diff": target["physics_diff"],
            "trigger_events": target.get("trigger_events", []),
            "quest_updates": target.get("quest_updates", []),
            "quest_states": target.get("quest_states", target.get("quest_updates", [])),
            "branch_consequences": target.get("branch_consequences", []),
            "final_response": target["final_response"],
            "memory_summary": target["memory_summary"],
        }
    )
