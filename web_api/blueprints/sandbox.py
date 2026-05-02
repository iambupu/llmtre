from __future__ import annotations

from typing import Any

from flask import Blueprint
from pydantic import ValidationError

from web_api.service import (
    TurnExecutionError,
    build_memory,
    error,
    get_runtime_context,
    get_session,
    logger,
    new_trace_id,
    now_iso,
    parse_json_body,
    run_turn,
    success,
    validate_request_id,
    validate_session_id,
)

sandbox_blueprint = Blueprint("sandbox", __name__, url_prefix="/api/sessions/<session_id>/sandbox")


def _sandbox_action(
    session_id: str,
    session: dict[str, Any],
    request_id: str,
    action_text: str,
    flag_key: str,
    scope: str,
) -> tuple[Any, int]:
    """
    功能：执行沙盒控制动作（并入或丢弃）的共享逻辑。
    入参：session_id（str）：会话标识。session（dict[str, Any]）：会话对象。
        request_id（str）：幂等键。
        action_text（str）：动作文本。flag_key（str）：响应标记键。scope（str）：幂等作用域。
    出参：tuple[Any, int]，统一响应结构。
    异常：主循环异常捕获后转换为 INTERNAL_ERROR，避免异常泄漏到接口层。
    """

    def _build_sandbox_response_payload(
        payload: dict[str, Any],
        persisted_turn_id: int,
        trace: dict[str, Any],
    ) -> dict[str, Any]:
        """
        功能：构造沙盒动作响应并补齐最小 trace 阶段，供幂等缓存与最终响应复用。
        入参：payload（dict[str, Any]）：run_turn 输出；persisted_turn_id（int）：落盘回合号；
            trace（dict[str, Any]）：可回传 trace 结构。
        出参：dict[str, Any]，包含沙盒动作最小响应字段与 trace。
        异常：字段类型不合法时抛 ValidationError，交由上层统一转 500。
        """
        if not isinstance(trace, dict):
            raise ValueError("trace 结构非法")
        stages = trace.get("stages")
        if isinstance(stages, list):
            stages.append(
                {
                    "stage": "api.persisted",
                    "status": "ok",
                    "at": now_iso(),
                    "detail": {"session_turn_id": persisted_turn_id, "scope": scope},
                }
            )
        return {
            "session_id": session_id,
            "session_turn_id": persisted_turn_id,
            "runtime_turn_id": payload["runtime_turn_id"],
            "trace_id": payload["trace_id"],
            "request_id": request_id,
            "trace": trace,
            flag_key: True,
        }

    def _build_sandbox_post_run_error(
        payload: dict[str, Any] | None,
        stage: str,
        err: Exception,
    ) -> tuple[str, dict[str, Any]]:
        """
        功能：构造沙盒 post-run 异常的回传 trace，确保失败链路与 run_turn trace_id 连通。
        入参：payload（dict[str, Any] | None）：run_turn 输出；stage（str）：失败阶段；
            err（Exception）：原始异常。
        出参：tuple[str, dict[str, Any]]，分别为 trace_id 与 trace。
        异常：函数内部不抛异常；结构异常时降级为最小 trace。
        """
        trace_id = str(payload.get("trace_id")) if isinstance(payload, dict) else new_trace_id()
        trace = payload.get("trace") if isinstance(payload, dict) else None
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
                "detail": {"error": str(err), "scope": scope},
            }
        )
        errors = trace.get("errors")
        if not isinstance(errors, list):
            trace["errors"] = []
            errors = trace["errors"]
        errors.append({"stage": stage, "error": str(err)})
        return trace_id, trace

    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        existing = context.session_store.get_idempotent_response(
            scope=scope,
            session_id=session_id,
            request_id=request_id,
        )
        if existing is not None:
            return success(existing)
        try:
            trace_id = new_trace_id()
            payload = run_turn(
                session=session,
                user_input=action_text,
                character_id=session["character_id"],
                sandbox_mode=True,
                trace_id=trace_id,
                request_id=request_id,
            )
        except TurnExecutionError as err:
            return error(
                err.error_code,
                f"沙盒动作执行失败: {err}",
                err.status_code,
                trace_id=err.trace_id,
                trace=err.trace,
            )
        except Exception as err:  # noqa: BLE001
            logger.exception(
                "沙盒动作执行失败: scope=%s session_id=%s request_id=%s",
                scope,
                session_id,
                request_id,
            )
            return error("INTERNAL_ERROR", f"沙盒动作执行失败: {err}", 500)
        try:
            recent_turns = context.session_store.get_recent_turns_for_memory(
                session_id=session_id,
                max_turns=int(session["memory_policy"]["max_turns"]),
            )
            draft_turn_id = int(session["current_turn_id"]) + 1
            draft_turns = recent_turns + [
                {
                    "turn_id": draft_turn_id,
                    "session_turn_id": draft_turn_id,
                    "user_input": action_text,
                    "final_response": payload["final_response"],
                }
            ]
            memory_summary, _ = build_memory(
                turns=draft_turns,
                max_turns=int(session["memory_policy"]["max_turns"]),
            )
            response_payload, _ = context.session_store.persist_turn_result_with_idempotency(
                scope=scope,
                session_id=session_id,
                request_id=request_id,
                user_input=action_text,
                turn_result=payload,
                memory_summary=memory_summary,
                now_iso=now_iso(),
                response_builder=lambda persisted_turn_id: _build_sandbox_response_payload(
                    payload=payload,
                    persisted_turn_id=persisted_turn_id,
                    trace=payload.get("trace") if isinstance(payload.get("trace"), dict) else {},
                ),
            )
            return success(response_payload)
        except Exception as err:  # noqa: BLE001
            stage = "api.response_built" if isinstance(err, ValidationError) else "api.persisted"
            trace_id, trace = _build_sandbox_post_run_error(payload, stage, err)
            logger.exception(
                "沙盒动作 post-run 失败: stage=%s scope=%s session_id=%s request_id=%s",
                stage,
                scope,
                session_id,
                request_id,
            )
            return error(
                "INTERNAL_ERROR",
                f"沙盒动作执行失败: {err}",
                500,
                trace_id=trace_id,
                trace=trace,
            )


@sandbox_blueprint.post("/commit")
def commit_sandbox(session_id: str) -> tuple[Any, int]:
    """
    功能：触发沙盒并入主线。
    入参：session_id（path）和 JSON（request_id）。
    出参：tuple[Any, int]，成功返回 committed=true。
    异常：参数非法或会话不存在时返回标准错误码。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    return _sandbox_action(
        session_id=session_id,
        session=session,
        request_id=request_id,
        action_text="并入主线",
        flag_key="committed",
        scope="sandbox_commit",
    )


@sandbox_blueprint.post("/discard")
def discard_sandbox(session_id: str) -> tuple[Any, int]:
    """
    功能：触发沙盒回滚丢弃。
    入参：session_id（path）和 JSON（request_id）。
    出参：tuple[Any, int]，成功返回 discarded=true。
    异常：参数非法或会话不存在时返回标准错误码。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    return _sandbox_action(
        session_id=session_id,
        session=session,
        request_id=request_id,
        action_text="回滚沙盒",
        flag_key="discarded",
        scope="sandbox_discard",
    )
