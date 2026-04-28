from __future__ import annotations

from typing import Any

from flask import Blueprint

from web_api.service import (
    build_memory,
    error,
    get_runtime_context,
    get_session,
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
            payload = run_turn(
                session=session,
                user_input=action_text,
                character_id=session["character_id"],
                sandbox_mode=True,
            )
        except Exception as err:  # noqa: BLE001
            return error("INTERNAL_ERROR", f"沙盒动作执行失败: {err}", 500)
        recent_turns = context.session_store.get_recent_turns_for_memory(
            session_id=session_id,
            max_turns=int(session["memory_policy"]["max_turns"]),
        )
        draft_turn_id = max(int(payload["turn_id"]), int(session["current_turn_id"]) + 1)
        draft_turns = recent_turns + [
            {
                "turn_id": draft_turn_id,
                "user_input": action_text,
                "final_response": payload["final_response"],
            }
        ]
        memory_summary, _ = build_memory(
            turns=draft_turns,
            max_turns=int(session["memory_policy"]["max_turns"]),
        )
        persisted_turn_id = context.session_store.persist_turn_result(
            session_id=session_id,
            request_id=request_id,
            user_input=action_text,
            turn_result=payload,
            memory_summary=memory_summary,
            now_iso=now_iso(),
        )
        result = {
            "session_id": session_id,
            "turn_id": persisted_turn_id,
            flag_key: True,
        }
        context.session_store.save_idempotent_response(
            scope=scope,
            session_id=session_id,
            request_id=request_id,
            response_payload=result,
        )
        return success(result)


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
