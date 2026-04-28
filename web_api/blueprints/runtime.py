from __future__ import annotations

from typing import Any

from flask import Blueprint

from web_api.service import (
    error,
    get_runtime_context,
    get_session,
    now_iso,
    parse_json_body,
    success,
    validate_request_id,
    validate_session_id,
)

runtime_blueprint = Blueprint("runtime", __name__, url_prefix="/api/sessions/<session_id>")


@runtime_blueprint.post("/reset")
def reset_session(session_id: str) -> tuple[Any, int]:
    """
    功能：重置会话运行态（回合、记忆、沙盒标记），保留会话壳与幂等能力。
    入参：session_id（path）和 JSON（request_id、keep_character）。
    出参：tuple[Any, int]，成功返回 reset=true。
    异常：参数非法返回 INVALID_ARGUMENT；会话不存在返回 SESSION_NOT_FOUND。
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
    keep_character = bool(body.get("keep_character", True))

    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        existing = context.session_store.get_idempotent_response(
            scope="reset_session",
            session_id=session_id,
            request_id=request_id,
        )
        if existing is not None:
            return success(existing)
        ok = context.session_store.clear_session_turns_and_reset(
            session_id=session_id,
            keep_character=keep_character,
            now_iso=now_iso(),
        )
        if not ok:
            return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
        payload = {"session_id": session["session_id"], "reset": True, "current_turn_id": 0}
        context.session_store.save_idempotent_response(
            scope="reset_session",
            session_id=session_id,
            request_id=request_id,
            response_payload=payload,
        )
        return success(payload)
