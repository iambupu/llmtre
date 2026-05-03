from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any, cast

from flask import Blueprint, Response, current_app, request, stream_with_context
from pydantic import ValidationError

from state.contracts.turn import TurnResult
from web_api.service import (
    DEFAULT_MEMORY_TURNS,
    MAX_MEMORY_TURNS,
    MIN_MEMORY_TURNS,
    TurnExecutionError,
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
    validate_character_id,
    validate_request_id,
    validate_session_id,
)

turns_blueprint = Blueprint("turns", __name__, url_prefix="/api/sessions/<session_id>/turns")


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
    response_payload = {
        "session_id": session_id,
        "session_turn_id": session_turn_id,
        "runtime_turn_id": payload["runtime_turn_id"],
        "trace_id": payload["trace_id"],
        "request_id": request_id,
        "is_valid": payload["is_valid"],
        "action_intent": payload["action_intent"],
        "physics_diff": payload["physics_diff"],
        "final_response": payload["final_response"],
        "quick_actions": payload["quick_actions"],
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
    }
    _append_trace_stage(
        response_payload,
        stage="api.persisted",
        status="ok",
        detail={"session_turn_id": session_turn_id},
    )
    if isinstance(response_payload.get("trace"), dict):
        response_payload["trace"]["session_turn_id"] = session_turn_id
    return _validate_turn_result_payload(response_payload)


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
) -> tuple[dict[str, Any], dict[str, Any], str, str, str, bool] | tuple[tuple[Any, int]]:
    """
    功能：解析并校验回合请求公共参数，供普通与 SSE 路由复用。
    入参：session_id（str）：会话标识；route_name（str）：日志中的路由名称。
    出参：成功返回 (session, body, request_id, user_input, character_id, sandbox_mode)；
        校验失败返回单元素元组，元素为 error(...) 响应。
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
    character_id = str(body.get("character_id", session["character_id"]))
    if not validate_character_id(character_id):
        return (error("INVALID_ARGUMENT", "character_id 格式非法", 400),)
    if character_id != session["character_id"]:
        return (error("TURN_CONFLICT", "character_id 与会话绑定不一致", 409),)
    if not ensure_character_available(character_id):
        return (error("CHARACTER_NOT_FOUND", "会话绑定角色不存在，无法执行回合", 404),)
    sandbox_mode = bool(body.get("sandbox_mode", session["sandbox_mode"]))
    return session, body, request_id, user_input.strip(), character_id, sandbox_mode


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
    session, body, request_id, user_input, character_id, sandbox_mode = parsed
    memory_policy = _resolve_memory_policy(body, session)
    if isinstance(memory_policy, tuple):
        return memory_policy

    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        existing = context.session_store.get_idempotent_response(
            scope="create_turn",
            session_id=session_id,
            request_id=request_id,
        )
        if existing is not None:
            return success(existing)
        if memory_policy != session["memory_policy"]:
            context.session_store.update_memory_policy(
                session_id=session_id,
                memory_policy=memory_policy,
                now_iso=now_iso(),
            )
            session["memory_policy"] = memory_policy
        try:
            trace_id = new_trace_id()
            payload = run_turn(
                session,
                user_input,
                character_id,
                sandbox_mode,
                trace_id=trace_id,
                request_id=request_id,
            )
        except TurnExecutionError as err:
            logger.exception(
                "回合执行失败: route=create_turn session_id=%s request_body=%s",
                session_id,
                body,
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
        try:
            memory_summary = _build_memory_summary_if_needed(
                context=context,
                session_id=session_id,
                session=session,
                user_input=user_input,
                payload=payload,
            )
            response_payload, _ = context.session_store.persist_turn_result_with_idempotency(
                scope="create_turn",
                session_id=session_id,
                request_id=request_id,
                user_input=user_input,
                turn_result=payload,
                memory_summary=memory_summary,
                now_iso=now_iso(),
                response_builder=lambda persisted_turn_id: _build_turn_response_payload(
                    payload=payload,
                    session_id=session_id,
                    request_id=request_id,
                    memory_summary=memory_summary,
                    session_turn_id=persisted_turn_id,
                ),
            )
            return success(response_payload)
        except Exception as err:  # noqa: BLE001
            stage = "api.response_built" if isinstance(err, ValidationError) else "api.persisted"
            trace_id, trace = _build_post_run_error_payload(payload, stage=stage, err=err)
            logger.exception(
                "回合 post-run 失败: route=create_turn stage=%s session_id=%s request_id=%s",
                stage,
                session_id,
                request_id,
            )
            return error(
                "INTERNAL_ERROR",
                f"回合执行失败: {err}",
                500,
                trace_id=trace_id,
                trace=trace,
            )


def _emit_sse_progress_events(target_queue: queue.Queue[tuple[str, dict[str, Any]]]) -> None:
    """
    功能：向 SSE 队列推送固定阶段进度事件。
    入参：target_queue（queue.Queue）：SSE 事件队列。
    出参：None。
    异常：队列写入异常向上抛出，由上层 worker 统一兜底。
    """
    target_queue.put(("loading_scene", {"message": "读取场景快照"}))
    target_queue.put(("parsing_nlu", {"message": "理解玩家意图"}))
    target_queue.put(("validating_action", {"message": "校验动作合法性"}))
    target_queue.put(("resolving_action", {"message": "执行确定性结算"}))
    target_queue.put(("rendering_gm", {"message": "生成叙事响应"}))


def _emit_sse_detail_events(
    target_queue: queue.Queue[tuple[str, dict[str, Any]]],
    payload: dict[str, Any],
) -> None:
    """
    功能：根据回合 payload 发送阶段明细事件，供前端展示调试信息。
    入参：target_queue（queue.Queue）：SSE 事件队列；payload（dict[str, Any]）：回合结果。
    出参：None。
    异常：不抛业务异常；字段缺失使用空值降级。
    """
    scene_snapshot = payload.get("scene_snapshot") if isinstance(payload.get("scene_snapshot"), dict) else {}
    current_location = (
        scene_snapshot.get("current_location")
        if isinstance(scene_snapshot.get("current_location"), dict)
        else {}
    )
    target_queue.put(("loading_scene_detail", {"message": "场景快照已读取", "detail": {"location_id": current_location.get("id"), "location_name": current_location.get("name"), "exits_count": len(scene_snapshot.get("exits", [])) if isinstance(scene_snapshot.get("exits"), list) else 0, "visible_npcs_count": len(scene_snapshot.get("visible_npcs", [])) if isinstance(scene_snapshot.get("visible_npcs"), list) else 0, "available_actions": scene_snapshot.get("available_actions", [])}}))
    target_queue.put(("parsing_nlu_detail", {"message": "玩家意图解析完成", "detail": {"action_intent": payload.get("action_intent"), "outcome": payload.get("outcome"), "clarification_question": payload.get("clarification_question")}}))
    target_queue.put(("validating_action_detail", {"message": "动作合法性校验完成", "detail": {"is_valid": payload.get("is_valid"), "errors": payload.get("errors", []), "should_advance_turn": payload.get("should_advance_turn")}}))
    target_queue.put(("resolving_action_detail", {"message": "确定性结算完成", "detail": {"physics_diff": payload.get("physics_diff"), "should_write_story_memory": payload.get("should_write_story_memory")}}))


def _run_turn_stream_with_lock(
    context: Any,
    session_id: str,
    request_id: str,
    session: dict[str, Any],
    user_input: str,
    character_id: str,
    sandbox_mode: bool,
    narrative_callback: Callable[[str], None],
    fallback_trace_id: str,
    target_queue: queue.Queue[tuple[str, dict[str, Any]]],
) -> None:
    """
    功能：在会话锁内执行流式回合与持久化，并向队列发送 done/error。
    入参：context/session_id/request_id/session/user_input/character_id/sandbox_mode 为执行参数；
        narrative_callback（Callable[[str], None]）：GM 增量回调；
        fallback_trace_id（str）：回退 trace_id；target_queue（queue.Queue）：SSE 队列。
    出参：None。
    异常：业务异常转换为 error 事件，不向外抛出。
    """
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        existing = context.session_store.get_idempotent_response(
            scope="create_turn",
            session_id=session_id,
            request_id=request_id,
        )
        if existing is not None:
            target_queue.put(("done", existing))
            return
        _emit_sse_progress_events(target_queue)
        payload = run_turn(
            session,
            user_input,
            character_id,
            sandbox_mode,
            narrative_stream_callback=narrative_callback,
            trace_id=fallback_trace_id,
            request_id=request_id,
        )
        _emit_sse_detail_events(target_queue, payload)
        try:
            memory_summary = _build_memory_summary_if_needed(
                context=context,
                session_id=session_id,
                session=session,
                user_input=user_input,
                payload=payload,
            )
            response_payload, _ = context.session_store.persist_turn_result_with_idempotency(
                scope="create_turn",
                session_id=session_id,
                request_id=request_id,
                user_input=user_input,
                turn_result=payload,
                memory_summary=memory_summary,
                now_iso=now_iso(),
                response_builder=lambda persisted_turn_id: _build_turn_response_payload(
                    payload=payload,
                    session_id=session_id,
                    request_id=request_id,
                    memory_summary=memory_summary,
                    session_turn_id=persisted_turn_id,
                ),
            )
            target_queue.put(("done", response_payload))
        except Exception as err:  # noqa: BLE001
            stage = "api.response_built" if isinstance(err, ValidationError) else "api.persisted"
            trace_id, trace = _build_post_run_error_payload(payload, stage=stage, err=err)
            target_queue.put(("error", {"code": "INTERNAL_ERROR", "message": f"回合执行失败: {err}", "trace_id": trace_id, "trace": trace}))


def _generate_turn_stream_events(
    app: Any,
    context: Any,
    session_id: str,
    request_id: str,
    body: dict[str, Any],
    session: dict[str, Any],
    user_input: str,
    character_id: str,
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
        if delta:
            event_queue.put(("gm_delta", {"delta": delta}))

    def run_turn_worker() -> None:
        try:
            with app.app_context():
                _run_turn_stream_with_lock(
                    context=context,
                    session_id=session_id,
                    request_id=request_id,
                    session=session,
                    user_input=user_input,
                    character_id=character_id,
                    sandbox_mode=sandbox_mode,
                    narrative_callback=emit_narrative_delta,
                    fallback_trace_id=worker_trace_id,
                    target_queue=event_queue,
                )
        except TurnExecutionError as err:
            logger.exception("回合执行失败: route=create_turn_stream session_id=%s request_body=%s", session_id, body)
            message = "回合执行超时：本地模型超过 3 分钟仍未完成，请稍后重试或改用更短的行动描述。" if err.error_code == "TURN_TIMEOUT" else f"回合执行失败: {err}"
            event_queue.put(("error", {"code": err.error_code, "message": message, "trace_id": err.trace_id, "trace": err.trace}))
        except Exception as err:  # noqa: BLE001
            logger.exception("SSE worker 处理失败: route=create_turn_stream session_id=%s request_id=%s", session_id, request_id)
            event_queue.put(("error", _build_worker_fallback_error_payload(trace_id=worker_trace_id, stage="api.worker", err=err)))

    worker = threading.Thread(target=run_turn_worker, daemon=True)
    worker.start()
    while True:
        try:
            event_name, payload = event_queue.get(timeout=0.5)
        except queue.Empty:
            if worker.is_alive():
                continue
            yield _sse("error", _build_worker_fallback_error_payload(trace_id=worker_trace_id, stage="api.worker", err=RuntimeError("worker exited without terminal event")))
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
    parsed = _parse_and_validate_turn_request(session_id=session_id, route_name="create_turn_stream")
    if len(parsed) == 1:
        return parsed[0]
    session, body, request_id, user_input, character_id, sandbox_mode = parsed
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
                character_id=character_id,
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
            "final_response": target["final_response"],
            "memory_summary": target["memory_summary"],
        }
    )
