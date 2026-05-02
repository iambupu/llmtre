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


@turns_blueprint.post("")
def create_turn(session_id: str) -> tuple[Any, int]:
    """
    功能：执行单回合主循环，并写入会话回合历史与记忆摘要。
    入参：session_id（path）和 JSON（request_id、user_input、character_id、memory 等）。
    出参：tuple[Any, int]，成功返回 200 与回合结果。
    异常：超时转换为 TURN_TIMEOUT 并返回 504；主循环异常转换为 INTERNAL_ERROR；
        参数冲突返回 TURN_CONFLICT。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)

    body = parse_json_body()
    log_post_body("create_turn", body)
    request_id = validate_request_id(body)
    if request_id is None:
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    user_input = body.get("user_input")
    if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 500:
        return error("INVALID_ARGUMENT", "user_input 不能为空且长度需在 1..500", 400)

    character_id = str(body.get("character_id", session["character_id"]))
    if not validate_character_id(character_id):
        return error("INVALID_ARGUMENT", "character_id 格式非法", 400)
    if character_id != session["character_id"]:
        return error("TURN_CONFLICT", "character_id 与会话绑定不一致", 409)
    if not ensure_character_available(character_id):
        return error("CHARACTER_NOT_FOUND", "会话绑定角色不存在，无法执行回合", 404)

    sandbox_mode = bool(body.get("sandbox_mode", session["sandbox_mode"]))
    memory_cfg = body.get("memory")
    memory_policy = dict(session["memory_policy"])
    if isinstance(memory_cfg, dict):
        mode = memory_cfg.get("mode", "auto")
        max_turns = memory_cfg.get("max_turns", DEFAULT_MEMORY_TURNS)
        if mode != "auto":
            return error("INVALID_ARGUMENT", "memory.mode 仅支持 auto", 400)
        if not isinstance(max_turns, int) or not (
            MIN_MEMORY_TURNS <= max_turns <= MAX_MEMORY_TURNS
        ):
            return error("INVALID_ARGUMENT", "memory.max_turns 需在 5..100", 400)
        memory_policy = {"mode": "auto", "max_turns": max_turns}

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
                user_input.strip(),
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
            memory_summary = str(session.get("memory_summary", ""))
            if payload["should_write_story_memory"]:
                recent_turns = context.session_store.get_recent_story_turns_for_memory(
                    session_id=session_id,
                    max_turns=int(session["memory_policy"]["max_turns"]),
                )
                # 记忆摘要展示会话内回合号；主循环 turn_id 可能是全局计数，不能污染新会话。
                draft_turn_id = int(session["current_turn_id"]) + 1
                draft_turns = recent_turns + [
                    {
                        "turn_id": draft_turn_id,
                        "session_turn_id": draft_turn_id,
                        "user_input": user_input.strip(),
                        "final_response": payload["final_response"],
                    }
                ]
                memory_summary, _ = build_memory(
                    turns=draft_turns,
                    max_turns=int(session["memory_policy"]["max_turns"]),
                )
            response_payload, _ = context.session_store.persist_turn_result_with_idempotency(
                scope="create_turn",
                session_id=session_id,
                request_id=request_id,
                user_input=user_input.strip(),
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


@turns_blueprint.post("/stream")
def create_turn_stream(session_id: str) -> tuple[Any, int] | Response:
    """
    功能：以 SSE 形式执行单回合，向前端持续报告系统运算阶段。
    入参：session_id（path）和 JSON（request_id、user_input、character_id、memory 等）。
    出参：Response(text/event-stream)，最终 `done` 事件携带普通回合响应负载。
    异常：前置参数错误返回普通 JSON 错误；执行中超时通过 TURN_TIMEOUT
        SSE `error` 事件返回，其他异常通过 INTERNAL_ERROR SSE `error` 事件返回。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)

    body = parse_json_body()
    log_post_body("create_turn_stream", body)
    request_id = validate_request_id(body)
    if request_id is None:
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    user_input = body.get("user_input")
    if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 500:
        return error("INVALID_ARGUMENT", "user_input 不能为空且长度需在 1..500", 400)

    character_id = str(body.get("character_id", session["character_id"]))
    if not validate_character_id(character_id):
        return error("INVALID_ARGUMENT", "character_id 格式非法", 400)
    if character_id != session["character_id"]:
        return error("TURN_CONFLICT", "character_id 与会话绑定不一致", 409)
    if not ensure_character_available(character_id):
        return error("CHARACTER_NOT_FOUND", "会话绑定角色不存在，无法执行回合", 404)

    sandbox_mode = bool(body.get("sandbox_mode", session["sandbox_mode"]))
    context = get_runtime_context()
    app = cast(Any, current_app)._get_current_object()

    def _generate() -> Iterator[str]:
        """
        功能：执行回合并按阶段产出 SSE 事件。
        入参：无，闭包捕获已校验的请求参数。
        出参：迭代器，逐步 yield SSE 字符串。
        异常：后台线程捕获 TimeoutError/Exception，转换为 error 事件；主生成器只负责转发。
        """
        yield _sse("received", {"message": "已收到回合输入"})
        event_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()

        def emit_narrative_delta(delta: str) -> None:
            """
            功能：接收 GM 模型片段并放入 SSE 队列。
            入参：delta（str）：Ollama 本次返回的可展示叙事片段。
            出参：None。
            异常：队列写入异常向上抛出，由 GM 回调保护逻辑捕获并记录。
            """
            if delta:
                event_queue.put(("gm_delta", {"delta": delta}))

        worker_trace_id = new_trace_id()

        def run_turn_worker() -> None:
            """
            功能：在后台线程执行完整回合，使 Flask 生成器可以实时转发 GM delta。
            入参：无，闭包捕获已校验请求参数。
            出参：None，通过 event_queue 发送 done/error 事件。
            异常：内部捕获超时和普通异常，转换为 SSE error 负载；线程内显式建立
                Flask app context，避免访问 current_app 时脱离上下文。
            """
            try:
                with app.app_context():
                    _run_turn_worker_in_app_context(
                        event_queue,
                        emit_narrative_delta,
                        worker_trace_id,
                    )
            except Exception as err:  # noqa: BLE001
                logger.exception(
                    "SSE worker 未捕获异常: route=create_turn_stream session_id=%s request_id=%s",
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

        def _run_turn_worker_in_app_context(
            target_queue: queue.Queue[tuple[str, dict[str, Any]]],
            narrative_callback: Callable[[str], None],
            fallback_trace_id: str,
        ) -> None:
            """
            功能：在线程已有 Flask app context 的前提下执行回合与持久化。
            入参：target_queue（queue.Queue）：SSE 事件队列；
                narrative_callback（Any）：GM 叙事片段回调；
                fallback_trace_id（str）：run_turn 前异常时用于兜底错误回传的追踪号。
            出参：None，通过 target_queue 发送 done/error/gm_delta。
            异常：内部捕获 TimeoutError/Exception 并转换为 error 事件。
            """
            try:
                session_lock = context.get_session_lock(session_id)
                with session_lock:
                    existing = context.session_store.get_idempotent_response(
                        scope="create_turn",
                        session_id=session_id,
                        request_id=request_id,
                    )
                    if existing is not None:
                        event_queue.put(("done", existing))
                        return
                    event_queue.put(("loading_scene", {"message": "读取场景快照"}))
                    event_queue.put(("parsing_nlu", {"message": "理解玩家意图"}))
                    event_queue.put(("validating_action", {"message": "校验动作合法性"}))
                    event_queue.put(("resolving_action", {"message": "执行确定性结算"}))
                    event_queue.put(("rendering_gm", {"message": "生成叙事响应"}))
                    payload = run_turn(
                        session,
                        user_input.strip(),
                        character_id,
                        sandbox_mode,
                        narrative_stream_callback=narrative_callback,
                        trace_id=fallback_trace_id,
                        request_id=request_id,
                    )
                    raw_scene_snapshot = payload.get("scene_snapshot")
                    scene_snapshot: dict[str, Any] = (
                        raw_scene_snapshot if isinstance(raw_scene_snapshot, dict) else {}
                    )
                    raw_current_location = scene_snapshot.get("current_location")
                    current_location: dict[str, Any] = (
                        raw_current_location
                        if isinstance(raw_current_location, dict)
                        else {}
                    )
                    target_queue.put((
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
                    ))
                    target_queue.put((
                        "parsing_nlu_detail",
                        {
                            "message": "玩家意图解析完成",
                            "detail": {
                                "action_intent": payload.get("action_intent"),
                                "outcome": payload.get("outcome"),
                                "clarification_question": payload.get("clarification_question"),
                            },
                        },
                    ))
                    target_queue.put((
                        "validating_action_detail",
                        {
                            "message": "动作合法性校验完成",
                            "detail": {
                                "is_valid": payload.get("is_valid"),
                                "errors": payload.get("errors", []),
                                "should_advance_turn": payload.get("should_advance_turn"),
                            },
                        },
                    ))
                    target_queue.put((
                        "resolving_action_detail",
                        {
                            "message": "确定性结算完成",
                            "detail": {
                                "physics_diff": payload.get("physics_diff"),
                                "should_write_story_memory": payload.get(
                                    "should_write_story_memory",
                                ),
                            },
                        },
                    ))
                    try:
                        memory_summary = str(session.get("memory_summary", ""))
                        if payload["should_write_story_memory"]:
                            recent_turns = context.session_store.get_recent_story_turns_for_memory(
                                session_id=session_id,
                                max_turns=int(session["memory_policy"]["max_turns"]),
                            )
                            # 记忆摘要展示会话内回合号；
                            # 主循环 turn_id 可能是全局计数，不能污染新会话。
                            draft_turn_id = int(session["current_turn_id"]) + 1
                            draft_turns = recent_turns + [
                                {
                                    "turn_id": draft_turn_id,
                                    "session_turn_id": draft_turn_id,
                                    "user_input": user_input.strip(),
                                    "final_response": payload["final_response"],
                                }
                            ]
                            memory_summary, _ = build_memory(
                                turns=draft_turns,
                                max_turns=int(session["memory_policy"]["max_turns"]),
                            )
                        response_payload, _ = (
                            context.session_store.persist_turn_result_with_idempotency(
                            scope="create_turn",
                            session_id=session_id,
                            request_id=request_id,
                            user_input=user_input.strip(),
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
                        )
                        target_queue.put(("done", response_payload))
                    except Exception as err:  # noqa: BLE001
                        stage = (
                            "api.response_built"
                            if isinstance(err, ValidationError)
                            else "api.persisted"
                        )
                        trace_id, trace = _build_post_run_error_payload(
                            payload,
                            stage=stage,
                            err=err,
                        )
                        logger.exception(
                            (
                                "回合 post-run 失败: route=create_turn_stream "
                                "stage=%s session_id=%s request_id=%s"
                            ),
                            stage,
                            session_id,
                            request_id,
                        )
                        target_queue.put((
                            "error",
                            {
                                "code": "INTERNAL_ERROR",
                                "message": f"回合执行失败: {err}",
                                "trace_id": trace_id,
                                "trace": trace,
                            },
                        ))
            except TurnExecutionError as err:
                logger.exception(
                    "回合执行失败: route=create_turn_stream session_id=%s request_body=%s",
                    session_id,
                    body,
                )
                message = (
                    "回合执行超时：本地模型超过 3 分钟仍未完成，请稍后重试或改用更短的行动描述。"
                    if err.error_code == "TURN_TIMEOUT"
                    else f"回合执行失败: {err}"
                )
                target_queue.put((
                    "error",
                    {
                        "code": err.error_code,
                        "message": message,
                        "trace_id": err.trace_id,
                        "trace": err.trace,
                    },
                ))
                return
            except Exception as err:  # noqa: BLE001
                logger.exception(
                    "SSE worker 处理失败: route=create_turn_stream session_id=%s request_id=%s",
                    session_id,
                    request_id,
                )
                target_queue.put(
                    (
                        "error",
                        _build_worker_fallback_error_payload(
                            trace_id=fallback_trace_id,
                            stage="api.received",
                            err=err,
                        ),
                    )
                )
                return

        worker = threading.Thread(target=run_turn_worker, daemon=True)
        worker.start()
        terminal_event_received = False
        while True:
            try:
                event_name, payload = event_queue.get(timeout=0.5)
            except queue.Empty:
                if worker.is_alive():
                    continue
                if terminal_event_received:
                    break
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
                terminal_event_received = True
                return

    return Response(stream_with_context(_generate()), mimetype="text/event-stream")


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
